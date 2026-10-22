"""Read SQLite task trees without changing source databases."""
import collections
import json
import re
from common import (DataError, assign_users, connection, date_selected, in_scope, iso, open_usage, segment)
from agent_tree import enrich


def completed_response(message):
    return message.get('role')=='assistant' and bool(message.get('time',{}).get('completed')) and bool(message.get('finish'))


def execution_finished(assistants):
    later_completed=False
    for message in reversed(assistants):
        if completed_response(message):
            later_completed=True
            continue
        times=message.get('time',{});created=times.get('created')
        # A closed, instantaneous placeholder preceding a real completion is not an
        # unfinished LLM call. Keep trailing placeholders and actual errors incomplete.
        transition=(later_completed and message.get('finish') is None and not message.get('error')
                    and isinstance(created,int) and not isinstance(created,bool)
                    and times.get('completed')==created)
        if not transition:return False
    return later_completed


class SQLiteTree:
    def __init__(self,db,source,product,search,delivery,tz):
        self.db=db;self.source=str(source);self.product=product;self.search=search;self.delivery=delivery;self.tz=tz
        self.tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'session','message','part'}<=self.tables:raise DataError('数据库缺少session、message或part表')
        self.nodes={};self.cache={};self.graph=collections.defaultdict(list)
        for row in db.execute('SELECT * FROM session ORDER BY time_created,id'):
            s=dict(row);s.update(session=s['id'],source=self.source,background=s.get('title','').startswith('_SYS_MEMORY_'))
            self.nodes[s['id']]=s
        referenced=set()
        for row in db.execute("SELECT id,session_id,time_created,data FROM part WHERE json_extract(data,'$.type')='tool' AND json_extract(data,'$.tool') IN ('task','agent','Agent','spawn_agent','delegate') ORDER BY time_created,id"):
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
            self.graph[row['session_id']].append({'child':child,'call':j.get('callID'),'time':row['time_created'],
                'evidence':self.source+'#part:'+row['id'],'not_started':st.get('status')=='error' and not child})
            if child:referenced.add(child)
        # Older schemas may retain only parent_id. This still proves lineage, not prompt similarity.
        for sid,node in self.nodes.items():
            parent=node.get('parent_id')
            if parent in self.nodes and sid not in referenced and not node['background']:
                self.graph[parent].append({'child':sid,'time':node['time_created'],'call':None,
                                          'evidence':self.source+'#session:'+sid+':parent_id'})
        self.times=collections.defaultdict(list)
        for edges in self.graph.values():
            edges.sort(key=lambda e:(e['time'],e.get('child') or ''))
            for e in edges:
                if e.get('child'):self.times[e['child']].append(e['time'])

    def edges(self,node):return self.graph[node]

    def next_edge_time(self,node,at):return min((t for t in self.times[node] if t>at),default=float('inf'))

    def records(self,node):
        if node not in self.cache:
            mm=[dict(r) for r in self.db.execute('SELECT id,time_created,data FROM message WHERE session_id=? ORDER BY time_created,id',(node,))]
            pp=[dict(r) for r in self.db.execute('SELECT id,message_id,time_created,data FROM part WHERE session_id=? ORDER BY time_created,id',(node,))]
            for r in mm+pp:r['json']=json.loads(r['data'])
            self.cache[node]=(mm,pp)
        return self.cache[node]

    def users(self,node):
        mm,pp=self.records(node)
        return [{'id':m['id'],'time':m['json'].get('time',{}).get('created',m['time_created']),
                 'text':'\n'.join(p['json'].get('text','') for p in pp if p['message_id']==m['id'] and p['json'].get('type')=='text')}
                for m in mm if m['json'].get('role')=='user']

    def piece(self,node,start,until,task='CHILD',root='CHILD',kind='子Agent',user='CHILD'):
        allm,allp=self.records(node);info=self.nodes[node]
        mm=[m for m in allm if start<=m['time_created']<until]
        pp=[p for p in allp if start<=p['time_created']<until]
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
                '_identity':self.source+'#call:'+cid+'#time:'+str(at),'_time_ms':p['time_created'],'agent_session':node,'result_complete':ready})
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
