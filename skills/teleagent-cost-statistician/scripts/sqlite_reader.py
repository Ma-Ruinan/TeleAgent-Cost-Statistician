"""Read SQLite task trees without changing source databases."""
import collections
import json
import re
from common import (DataError, assign_users, connection, date_selected, in_scope, iso, norm, open_usage, segment)
from agent_tree import enrich


def completed_response(message):
    return message.get('role')=='assistant' and bool(message.get('time',{}).get('completed')) and bool(message.get('finish'))


def execution_finished(assistants):
    later_completed=None
    for message in reversed(assistants):
        if completed_response(message):
            later_completed=max(later_completed or 0,message['time']['completed'])
            continue
        times=message.get('time',{});created=times.get('created');ended=times.get('completed')
        # Closed intermediate records need no duration threshold. A later real
        # completion must cover their end; an open or trailing record still blocks.
        transition=(later_completed is not None and message.get('finish') is None and not message.get('error')
                    and isinstance(created,int) and not isinstance(created,bool)
                    and isinstance(ended,int) and not isinstance(ended,bool)
                    and created<=ended<=later_completed)
        if not transition:return False
    return later_completed is not None


class SQLiteTree:
    def __init__(self,db,source,product,search,delivery,tz):
        self.db=db;self.source=str(source);self.product=product;self.search=search;self.delivery=delivery;self.tz=tz
        self.tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'session','message','part'}<=self.tables:raise DataError('数据库缺少session、message或part表')
        self.nodes={};self.cache={};self.first_times={};self.graph=collections.defaultdict(list)
        for row in db.execute('SELECT * FROM session ORDER BY time_created,id'):
            s=dict(row);s.update(session=s['id'],source=self.source,background=s.get('title','').startswith('_SYS_MEMORY_'))
            self.nodes[s['id']]=s
        referenced=set()
        for row in db.execute("SELECT p.id,p.message_id,p.session_id,p.time_created,p.data,m.time_created AS owner_time FROM part p LEFT JOIN message m ON m.id=p.message_id WHERE json_extract(p.data,'$.type')='tool' AND json_extract(p.data,'$.tool') IN ('task','agent','Agent','spawn_agent','delegate') ORDER BY m.time_created,COALESCE(json_extract(p.data,'$.state.time.start'),p.time_created),p.id"):
            j=json.loads(row['data']);st=j.get('state',{});md=st.get('metadata') or {};args=st.get('input') or {}
            if isinstance(args,str):args=json.loads(args)
            child=md.get('sessionId') or md.get('session_id') or args.get('session_id') or args.get('sessionID') or args.get('task_id')
            if not child:
                match=re.search(r'(?:task_id|session_id)\s*:\s*(ses_[A-Za-z0-9_]+)',str(st.get('output','')))
                child=match.group(1) if match else None
            if child and not isinstance(child,str):raise DataError('子会话标识字段类型变化：part:'+row['id'])
            if child in self.nodes and self.nodes[child].get('parent_id') not in (None,row['session_id']):
                # Reuse via an explicit task_id is allowed; validate the call rather than changing parent metadata.
                if args.get('task_id')!=child and args.get('session_id')!=child:raise DataError('委派记录与子会话父标识冲突：part:'+row['id'])
            if row['owner_time'] is None:raise DataError('委派记录缺少所属模型消息：part:'+row['id'])
            start,uncertain=self.child_start(row['session_id'],child,args,row['owner_time'],st.get('time',{}),row['time_created'],child not in referenced)
            self.graph[row['session_id']].append({'child':child,'call':j.get('callID'),'time':row['time_created'],
                'owner_time_ms':row['owner_time'],'owner_message':row['message_id'],'child_start_ms':start,'boundary_uncertain_from_ms':uncertain,
                'evidence':self.source+'#part:'+row['id'],'not_started':st.get('status')=='error' and not child})
            if child:referenced.add(child)
        # Older schemas may retain only parent_id. This still proves lineage, not prompt similarity.
        for sid,node in self.nodes.items():
            parent=node.get('parent_id')
            if parent in self.nodes and sid not in referenced and not node['background']:
                first=self.first_record_time(sid) or node['time_created']
                self.graph[parent].append({'child':sid,'time':node['time_created'],'call':None,'child_start_ms':min(first,node['time_created']),'owner_time_ms':first,
                                          'evidence':self.source+'#session:'+sid+':parent_id'})
        self.times=collections.defaultdict(list)
        for edges in self.graph.values():
            edges.sort(key=lambda e:(e['time'],e.get('child') or ''))
            for e in edges:
                if e.get('child'):self.times[e['child']].append(e['time'])

    def edges(self,node):return self.graph[node]

    def next_edge_time(self,node,at):return min((t for t in self.times[node] if t>at),default=float('inf'))

    def first_record_time(self,node):
        if node not in self.first_times:
            self.first_times[node]=self.db.execute('SELECT MIN(at) FROM (SELECT MIN(time_created) AS at FROM message WHERE session_id=? UNION ALL SELECT MIN(time_created) AS at FROM part WHERE session_id=?)',(node,node)).fetchone()[0]
        return self.first_times[node]

    def child_start(self,parent,child,args,owner,times,recorded,first_reference):
        ended=times.get('end');started=times.get('start')
        end=ended if isinstance(ended,int) and not isinstance(ended,bool) else recorded
        start=min(recorded,started) if isinstance(started,int) and not isinstance(started,bool) and owner<=started<=end else recorded
        if child not in self.nodes:return start,None
        first=self.first_record_time(child);info=self.nodes[child]
        if first is None or first>=start:return start,None
        reused=any(args.get(k) for k in ('task_id','session_id','sessionID'))
        # Creation is proved by the parent message, relationship and first reference.
        if (first_reference and not reused and info.get('parent_id')==parent
            and owner<=info['time_created']<=end and owner<=first<=end):return min(start,first),None
        prompt=args.get('prompt')
        if isinstance(prompt,str) and norm(prompt):
            matches=[u for u in self.users(child,include_internal=True) if owner<=u['time']<=end and norm(u['text'])==norm(prompt)]
            if len(matches)==1:return min(start,matches[0]['time']),None
        mm,_=self.records(child)
        uncertain=min((m['time_created'] for m in mm if owner<=m['time_created']<start and m['json'].get('role') in ('user','assistant')),default=None)
        return start,uncertain

    def records(self,node):
        if node not in self.cache:
            mm=[dict(r) for r in self.db.execute('SELECT id,time_created,data FROM message WHERE session_id=? ORDER BY time_created,id',(node,))]
            pp=[dict(r) for r in self.db.execute('SELECT id,message_id,time_created,data FROM part WHERE session_id=? ORDER BY time_created,id',(node,))]
            for r in mm+pp:r['json']=json.loads(r['data'])
            self.cache[node]=(mm,pp)
        return self.cache[node]

    def users(self,node,include_internal=False):
        mm,pp=self.records(node)
        by_message=collections.defaultdict(list)
        for p in pp:by_message[p['message_id']].append(p['json'])
        result=[]
        for m in mm:
            if m['json'].get('role')!='user':continue
            parts=by_message[m['id']]
            internal=bool(parts) and all((p.get('type')=='compaction' and p.get('auto') is True) or (p.get('type')=='text' and p.get('synthetic') is True) for p in parts)
            if internal and not include_internal:continue
            texts=[p for p in parts if p.get('type')=='text']
            visible=[p for p in texts if p.get('synthetic') is not True]
            result.append({'id':m['id'],'time':m['json'].get('time',{}).get('created',m['time_created']),
                           'text':'\n'.join(p.get('text','') for p in (visible or texts))})
        return result

    def piece(self,node,start,until,task='CHILD',root='CHILD',kind='子Agent',user='CHILD'):
        allm,allp=self.records(node);info=self.nodes[node]
        mm=[m for m in allm if start<=m['time_created']<until]
        selected_messages={m['id'] for m in mm}
        pp=[p for p in allp if p['message_id'] in selected_messages]
        completed=[m for m in mm if completed_response(m['json'])]
        end=max((m['json']['time']['completed'] for m in completed),default=None)
        if end is not None and end>until:raise DataError('执行跨越另一独立任务或委派窗口：'+node)
        usage=[]
        for m in completed:
            u=open_usage(m['json'],self.source+'#message:'+m['id'])
            u.update(_identity=self.source+'#message:'+m['id'],_time_ms=m['time_created'],agent_session=node)
            usage.append(u)
        by_id={}
        for p in pp:
            j=p['json']
            if j.get('type')!='tool':continue
            cid=j.get('callID')
            if not cid or not j.get('tool'):raise DataError('工具记录缺少callID或tool：part:'+p['id'])
            at=j.get('state',{}).get('time',{}).get('start',p['time_created']);key=(cid,at)
            if key in by_id and by_id[key]['json']['tool']!=j['tool']:raise DataError('同一callID对应多个工具：'+cid)
            by_id[key]=p
        tools=[];deliveries=[];errors_complete=True
        for (cid,at),p in by_id.items():
            j=p['json'];st=j.get('state',{});md=st.get('metadata') or {};status=st.get('status');code=md.get('exit',md.get('exitCode'))
            if status not in ('completed','error','pending','running'):raise DataError('工具状态字段变化：part:'+p['id'])
            if code is not None and (not isinstance(code,int) or isinstance(code,bool)):raise DataError('工具退出码类型变化：part:'+p['id'])
            ready=status not in ('pending','running');errors_complete &= ready
            failed=status=='error' or code not in (None,0);msg=str(st.get('error') or md.get('output') or st.get('output') or '')
            tools.append({'id':cid,'name':j['tool'],'status':status,'exit_code':code,'failed':failed,
                'time':iso(st.get('time',{}).get('start',p['time_created']),self.tz),'message':msg[:1600] if failed else '',
                'message_truncated':failed and len(msg)>1600,'evidence':self.source+'#part:'+p['id'],
                '_identity':self.source+'#call:'+cid+'#time:'+str(at),'_time_ms':at,'agent_session':node,'result_complete':ready})
            if j['tool']==self.delivery and status=='completed' and not failed:deliveries.append({'id':cid,'input':st.get('input')})
        retries=None
        if self.product=='MobileWork' and 'event' in self.tables:
            retries=[]
            for ev in self.db.execute("SELECT id,data FROM event WHERE aggregate_id=? AND type='session.next.retried.1' ORDER BY seq",(node,)):
                payload=json.loads(ev['data']);at=payload['timestamp']
                if start<=at<until:retries.append({'id':ev['id'],'timestamp':at,'time':iso(at,self.tz),'attempt':payload.get('attempt'),
                    'message':str(payload.get('error',{}).get('message','')),'agent_session':node,'source':self.source})
        assistant_ids={m['id'] for m in completed}
        text=next((p['json'].get('text','') for p in reversed(pp) if p['message_id'] in assistant_ids and p['json'].get('type')=='text'),'')
        value=segment(self.product,task,root,kind,user,node,start,end,info['directory'],self.source,usage,tools,deliveries,self.search,self.tz,
                      retries=retries,final_text=text,errors_complete=errors_complete)
        assistants=[m['json'] for m in mm if m['json'].get('role')=='assistant']
        value['_terminal']=errors_complete and execution_finished(assistants)
        value['_node']=node;value['_until']=until
        return value


def collect_sqlite(source,tasks,args,mapping,tz,product,search,delivery):
    db=connection(source);excluded=[];segments=[]
    try:
        store=SQLiteTree(db,source,product,search,delivery,tz)
        for sid,info in store.nodes.items():
            if info.get('parent_id') or info['background']:
                if in_scope(info['directory'],args.workspace):excluded.append({'session':sid,'source':str(source),
                    'reason':'子会话由父任务委派链关联；后台或未被关联的会话不单列独立测试','time_ms':info['time_created']})
                continue
            if not in_scope(info['directory'],args.workspace) or (args.session and sid not in args.session):continue
            users=store.users(sid)
            for user,task,root,kind,until in assign_users(users,sid,tasks,mapping,excluded,str(source)):
                if not date_selected(next(u['time'] for u in users if u['id']==root),args,tz):continue
                segments.append(store.piece(sid,user['time'],until,task,root,kind,user['id']))
        enrich(segments,store,search)
        included={e['session'] for s in segments for e in s['agent_executions'] if e['depth']}
        excluded=[e for e in excluded if e.get('session') not in included]
        return segments,excluded
    finally:db.rollback();db.close()
