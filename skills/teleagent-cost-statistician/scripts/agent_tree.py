"""Attribute recursive delegated execution to one independent test, then deduplicate."""
import collections
import copy
from common import DataError, METRICS, sum_known, iso


def observed(usage, tools, search):
    inp=sum_known(x['input'] for x in usage); cache=sum_known(x['cache'] for x in usage)
    return {'llm_calls':sum_known(x['calls'] for x in usage),
            'input_tokens':inp,'output_tokens':sum_known(x['output'] for x in usage),
            'cache_rate':cache/inp if inp and cache is not None else None,
            'tool_calls':len(tools),
            'tool_errors':sum(bool(x['failed']) for x in tools) if all(x.get('result_complete',True) for x in tools) else None,
            'search_calls':sum(x['name']==search for x in tools)},cache


def enrich(segments, store, search):
    """store provides nodes, edges(node), piece(node,lo,hi), and source identity."""
    groups=collections.defaultdict(list)
    for s in segments:groups[(s['source'],s['session'],s['root_user'])].append(s)
    owners={};incoming=collections.defaultdict(list)
    for node in store.nodes:
        for edge in store.edges(node):
            if edge.get('child'):incoming[edge['child']].append((node,edge['time']))
    for identity,ss in groups.items():
        ss.sort(key=lambda x:x['start_ms']);root=ss[0]['_node'];lo=ss[0]['start_ms'];hi=ss[-1]['_until']
        for s in ss:
            if s['end_ms'] is not None and not s.get('_terminal',True):
                s['last_completed_ms']=s['end_ms'];s['end_ms']=None;s['end']=None;s['values']['seconds']=None
                s['warnings'].append('主Agent执行缺少完整结束记录')
        issues=[];links=[];pieces=[];visited=set();active=set();windows=collections.defaultdict(list);link_keys=set()
        related={root};pending=[root]
        while pending:
            current=pending.pop()
            for edge in store.edges(current):
                target=edge.get('child')
                if target in store.nodes and target not in related:
                    related.add(target);pending.append(target)

        def walk(node,lower,upper,depth,parent=None):
            if node in active:
                issues.append({'reason':'父子关联形成循环','node':node});return
            key=(node,lower,upper)
            if key in visited:return
            visited.add(key)
            if node not in store.nodes:
                issues.append({'reason':'委派记录引用的子日志缺失','node':node,'parent':parent});return
            info=store.nodes[node]
            if info.get('background'):
                issues.append({'reason':'委派引用系统后台会话，未计入','node':node});return
            active.add(node)
            if depth:
                windows[node].append((lower,upper,depth,parent))
            for edge in store.edges(node):
                if not lower<=edge['time']<upper:continue
                target=edge.get('child')
                link_key=(node,edge.get('call'),edge['time'],edge['evidence'])
                if link_key not in link_keys:
                    links.append({'parent':node,'child':target,'call':edge.get('call'),
                                  'time_ms':edge['time'],'evidence':edge['evidence'],'depth':depth+1})
                    link_keys.add(link_key)
                if not target:
                    if not edge.get('not_started'):
                        issues.append({'reason':'委派调用未能定位子日志','parent':node,'evidence':edge['evidence']})
                    continue
                # Messages within one test may overlap an active invocation. Only another
                # independent owner cuts its window; overlapping windows are read once below.
                future=min((time for owner,time in incoming[target] if owner not in related and time>=edge['time']),default=float('inf'))
                upper_child=min(upper,future)
                if edge.get('resume') and target in active:continue
                walk(target,edge['time'],upper_child,depth+1,node)
            active.remove(node)

        walk(root,lo,hi,0)
        for node,intervals in windows.items():
            merged=[]
            for lower,upper,depth,parent in sorted(intervals,key=lambda x:(x[0],x[1],x[2])):
                if merged and lower<=merged[-1][1]:
                    merged[-1][1]=max(merged[-1][1],upper)
                else:merged.append([lower,upper,depth,parent])
            for lower,upper,depth,parent in merged:
                piece=store.piece(node,lower,upper)
                pieces.append((node,depth,parent,piece,lower,upper))
                if piece['end_ms'] is None or not piece.get('_terminal',True):
                    issues.append({'reason':'子执行缺少完整结束记录','node':node,'source':piece['source']})
        buckets=[{'usage':[],'tools':[],'agents':[],'files':[]} for _ in ss]
        seen={'usage':{},'tools':{}}

        def add(kind,item,bucket):
            token=item['_identity']; prior=seen[kind].get(token)
            fields=('input','output','cache','calls') if kind=='usage' else ('name','failed','status','exit_code')
            signature=tuple(item.get(k) for k in fields)
            if prior is not None:
                if prior!=signature:raise DataError('同一执行标识记录不一致：'+token)
                return False
            global_key=(kind,token)
            if global_key in owners and owners[global_key]!=identity:
                raise DataError('同一执行被不同独立测试引用，无法唯一归属：'+token)
            owners[global_key]=identity;seen[kind][token]=signature
            buckets[bucket][kind].append(item);return True

        def allocate(node,depth,parent,piece,lower,upper,own_index=None):
            contributions=collections.defaultdict(lambda:{'usage':[],'tools':[]})
            for kind,field in [('usage','usage_records'),('tools','tools')]:
                for item in piece[field]:
                    index=own_index
                    if index is None:
                        index=max((i for i,s in enumerate(ss) if s['start_ms']<=item['_time_ms']),default=0)
                    if add(kind,item,index):contributions[index][kind].append(item)
            if not contributions:contributions[own_index or 0]
            for file in piece['files']:
                if depth:continue
                at=next((t['_time_ms'] for t in piece['tools'] if t['id'] in file['registrations']),lower)
                index=own_index if own_index is not None else max((i for i,s in enumerate(ss) if s['start_ms']<=at),default=0)
                buckets[index]['files'].append(copy.deepcopy(file))
            for index,values in contributions.items():
                values_obs,cache=observed(values['usage'],values['tools'],search)
                if not values['usage']:
                    values_obs.update(llm_calls=0,input_tokens=0,output_tokens=0);cache=0
                info=store.nodes[node]
                buckets[index]['agents'].append({'session':info['session'],'node':node,'parent':parent,
                    'depth':depth,'source':info['source'],'directory':info.get('directory'),
                    'version':info.get('version'),'window_start_ms':lower,
                    'window_end_ms':upper if upper!=float('inf') else None,
                    'end_ms':piece['end_ms'],
                    'files':copy.deepcopy(piece['files']),
                    'values':values_obs,'cache_tokens':cache,'complete':piece['end_ms'] is not None and piece.get('_terminal',True)})

        # Prefer the originating deeper execution when parent logs mirror its records.
        for node,depth,parent,piece,lower,upper in sorted(pieces,key=lambda p:-p[1]):allocate(node,depth,parent,piece,lower,upper)
        for index,s in enumerate(ss):allocate(root,0,None,s,s['start_ms'],s['_until'],index)
        child_retries=[p['retries'] for _,_,_,p,_,_ in pieces]
        ss[0]['warnings'].extend(w for _,_,_,p,_,_ in pieces for w in p['warnings'])
        if ss[-1]['end_ms'] is not None and pieces:
            last=max([ss[-1]['end_ms']]+[p['end_ms'] for _,_,_,p,_,_ in pieces if p['end_ms'] is not None])
            if last>ss[-1]['end_ms']:
                ss[-1]['_main_values']=copy.deepcopy(ss[-1]['values'])
                ss[-1]['main_end_ms']=ss[-1]['end_ms'];ss[-1]['end_ms']=last
                ss[-1]['end']=iso(last,store.tz)
                ss[-1]['values']['seconds']=(last-ss[-1]['start_ms'])/1000
                ss[-1]['warnings'].append('关联子Agent晚于主会话结束，整体耗时计至最后一个关联执行完成')
        for index,s in enumerate(ss):
            s['main_values']=s.pop('_main_values',copy.deepcopy(s['values']))
            s['usage_records']=sorted(buckets[index]['usage'],key=lambda x:(x['_time_ms'],x['_identity']))
            s['tools']=sorted(buckets[index]['tools'],key=lambda x:(x['_time_ms'],x['_identity']))
            s['errors']=[x for x in s['tools'] if x['failed']]
            raw,cache=observed(s['usage_records'],s['tools'],search)
            s['values'].update(raw)
            s['cache_tokens']=cache if s['end_ms'] is not None else None
            s['models']=sorted({x['model'] for x in s['usage_records'] if x.get('model')})
            files={}
            for f in buckets[index]['files']:
                key=f['path'].casefold()
                if key not in files:files[key]=f
                else:files[key]['registrations']=sorted(set(files[key]['registrations']+f['registrations']))
            s['files']=list(files.values());s['values']['artifact_count']=sum(f['exists'] for f in s['files'])
            s['agent_executions']=sorted(buckets[index]['agents'],key=lambda x:(x['depth'],x['window_start_ms'],x['session']))
            s['agent_links']=links if index==0 else []
            s['agent_coverage']={'complete':not issues,'issues':issues if index==0 else [],
                                 'child_sessions':sorted({store.nodes[n]['session'] for n,_,_,_,_,_ in pieces})}
            if index==0 and child_retries:
                s['retries']=[*s['retries'],*(x for rr in child_retries for x in rr)] if s['retries'] is not None and all(x is not None for x in child_retries) else None
            if s['end_ms'] is None or issues:
                s['observed_partial']={**s['values']}
                for metric in METRICS[:-1]:s['values'][metric]=None
                s['cache_tokens']=None
            if issues:s['warnings'].append('子Agent执行记录不完整，相关总数保留缺失；已观察数值见observed_partial')
            s.pop('_node',None);s.pop('_until',None);s.pop('_terminal',None)
    return segments
