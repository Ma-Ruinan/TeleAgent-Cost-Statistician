"""Synthetic recursive agent fixtures. No real prompts, paths, or session IDs."""
import argparse
import copy
import datetime as dt
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(next((ROOT/'skills').glob('*/scripts'))))
import collect as adapter
import common


class AgentTreeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.workspace=self.root/'workspace';self.workspace.mkdir()
        for name in ('final.txt','intermediate.txt'):(self.workspace/name).write_text('synthetic',encoding='utf8')
        self.at=1700000000000;self.search='WebSearch' if adapter.PRODUCT=='WorkBuddy' else adapter.SEARCH_TOOL
        self.delivery='present_files' if adapter.PRODUCT=='WorkBuddy' else adapter.DELIVERY_TOOL
        self.tasks=[dict(id='Q1',name='Synthetic question',prompt='Synthetic full question.')]
        self.args=argparse.Namespace(workspace=[str(self.workspace)],session=['root'],date=None,observations=None)
        self.nodes={}
        self.node('root',None,0,self.tasks[0]['prompt'],500,20000,100,300,10)
        self.node('c1','root',1100,'Different delegated question.',1200,9000,40,60,5)
        self.node('c2','c1',5100,'Nested question.',5500,8000,20,30,5)
        self.node('c3','root',1600,'Parallel question.',1700,8500,20,80,10)
        self.tool('root','delegate1',1000,'delegate',child='c1')
        self.tool('root','delegate3',1500,'delegate',child='c3')
        self.tool('root','final',15000,self.delivery,files=['final.txt'])
        for i,time in enumerate((2000,3000,4000)):self.tool('c1','search1-'+str(i),time,self.search,failed=i==1)
        self.tool('c1','delegate2',5000,'delegate',child='c2')
        self.tool('c1','child-final',7000,self.delivery,files=['final.txt'])
        self.tool('c2','search2',6000,self.search)
        for i,time in enumerate((2500,3500)):self.tool('c3','search3-'+str(i),time,self.search)

    def tearDown(self):self.temp.cleanup()

    def node(self,sid,parent,when,prompt,model_start,end,inp,cache,out):
        self.nodes[sid]=dict(parent=parent,users=[(sid+'-u',when,prompt)],models=[(sid+'-m',model_start,end,inp,cache,out)],tools=[],title='Synthetic '+sid)

    def tool(self,sid,cid,time,name,child=None,files=None,failed=False):
        self.nodes[sid]['tools'].append(dict(id=cid,time=time,name=name,child=child,files=files,failed=failed))

    def fixture(self):
        if adapter.PRODUCT=='WorkBuddy':return self.wb_fixture()
        path=self.root/'source.db';db=sqlite3.connect(path)
        db.executescript('CREATE TABLE session(id TEXT,parent_id TEXT,directory TEXT,title TEXT,time_created INTEGER);CREATE TABLE message(id TEXT,session_id TEXT,time_created INTEGER,data TEXT);CREATE TABLE part(id TEXT,message_id TEXT,session_id TEXT,time_created INTEGER,data TEXT);')
        for sid,n in self.nodes.items():
            db.execute('INSERT INTO session VALUES(?,?,?,?,?)',(sid,n['parent'],str(self.workspace),n['title'],self.at+n['users'][0][1]))
            for uid,when,prompt in n['users']:
                db.execute('INSERT INTO message VALUES(?,?,?,?)',(uid,sid,self.at+when,json.dumps(dict(role='user',time={'created':self.at+when}))))
                db.execute('INSERT INTO part VALUES(?,?,?,?,?)',(uid+'-text',uid,sid,self.at+when,json.dumps(dict(type='text',text=prompt))))
            for mid,start,end,inp,cache,out in n['models']:
                times={'created':self.at+start}
                if end is not None:times['completed']=self.at+end
                data=dict(role='assistant',time=times,modelID='synthetic',tokens={'input':inp,'output':out,'cache':{'read':cache,'write':0},'total':inp+cache+out})
                if end is not None:data['finish']='stop'
                db.execute('INSERT INTO message VALUES(?,?,?,?)',(mid,sid,self.at+start,json.dumps(data)))
            for t in n['tools']:
                mid=max((m for m in n['models'] if m[1]<=t['time']),key=lambda m:m[1])[0]
                name='task' if t['name']=='delegate' else t['name'];md={'exit':1 if t['failed'] else 0}
                if t['child']:md['sessionId']=t['child'];md['parentSessionId']=sid
                args={'description':'synthetic','prompt':'delegated work'} if t['child'] else {'files':t['files']} if t['files'] else {}
                data=dict(type='tool',callID=t['id'],tool=name,state={'status':'error' if t['failed'] else 'completed','input':args,'metadata':md,'time':{'start':self.at+t['time'],'end':self.at+t['time']+100},'output':'synthetic'})
                db.execute('INSERT INTO part VALUES(?,?,?,?,?)',(sid+'-'+t['id'],mid,sid,self.at+t['time'],json.dumps(data)))
        db.commit();db.close();return path

    def wb_fixture(self):
        source=self.root/'logs';source.mkdir()
        for sid,n in self.nodes.items():
            path=source/'root.jsonl' if sid=='root' else source/'root'/'subagents'/('agent-'+sid+'.jsonl') if n['parent'] else source/(sid+'.jsonl')
            path.parent.mkdir(parents=True,exist_ok=True);rows=[]
            for uid,time,prompt in n['users']:rows.append(dict(id=uid,timestamp=self.at+time,type='message',role='user',content=[{'text':prompt}],sessionId=sid,cwd=str(self.workspace)))
            for mid,start,end,inp,cache,out in n['models']:
                pd=dict(conversationRequestId=mid+'-req',messageId=mid,stepSeq=1,model='synthetic',usage={'requests':1,'inputTokens':inp+cache,'outputTokens':out,'totalTokens':inp+cache+out,'inputTokensDetails':[{'cached_tokens':cache}]})
                rows.append(dict(id=mid+'-usage',type='reasoning',timestamp=self.at+start,providerData=pd,sessionId=sid))
                if end is not None:rows.append(dict(id=mid+'-final',type='message',role='assistant',status='completed',timestamp=self.at+end,content=[{'text':'done'}],sessionId=sid))
                else:rows.append(dict(id=mid+'-pending',type='message',role='assistant',status='in_progress',timestamp=self.at+start,content=[],sessionId=sid))
            for t in n['tools']:
                name='Agent' if t['name']=='delegate' else t['name'];tr={'rawResponse':{'exitCode':1 if t['failed'] else 0}}
                if t['child']:tr['subAgent']={'sessionId':t['child'] if t['child']=='root' else 'agent-'+t['child']}
                args={'name':'worker-'+t['child'],'prompt':'delegated work'} if t['child'] else {'files':t['files']} if t['files'] else {}
                if t['name']=='SendMessage':args={'type':'message','recipient':'worker-'+t['child'],'content':'Follow-up work.'};tr.pop('subAgent',None)
                rows.append(dict(id=sid+'-'+t['id'],type='function_call',callId=t['id'],name=name,arguments=args,timestamp=self.at+t['time'],sessionId=sid))
                rows.append(dict(id=sid+'-'+t['id']+'-result',type='function_call_result',callId=t['id'],name=name,status='completed',providerData={'toolResult':tr},timestamp=self.at+t['time']+100,sessionId=sid))
            rows.sort(key=lambda r:r['timestamp'])
            path.write_text('\n'.join(json.dumps(r) for r in rows),encoding='utf8')
        return source

    def collect(self,source=None):
        source=source or self.fixture()
        ss,excluded=adapter.collect(source,self.tasks,self.args,{},dt.timezone.utc)
        return common.assemble(adapter.PRODUCT,self.tasks,ss,excluded,self.args,dt.timezone.utc)

    def test_nested_parallel_children_and_registered_file_dedup(self):
        r=self.collect()['runs'][0]
        self.assertEqual(r['values']['search_calls'],6)
        self.assertEqual(r['values']['llm_calls'],4)
        self.assertEqual(r['values']['input_tokens'],650)
        self.assertEqual(r['values']['output_tokens'],30)
        self.assertAlmostEqual(r['values']['cache_rate'],470/650)
        self.assertEqual(r['values']['tool_calls'],11)

        main=next(a for a in r['agent_executions'] if a['depth']==0)
        self.assertEqual(main['values']['search_calls'],0)
        self.assertEqual(main['values']['tool_calls'],3)
        self.assertEqual(r['values']['tool_errors'],1)
        self.assertEqual(r['values']['seconds'],20)
        self.assertEqual(r['values']['artifact_count'],1)
        self.assertEqual(len(r['agent_coverage']['child_sessions']),3)
        self.assertTrue(r['agent_coverage']['complete'])
        self.assertEqual(max(e['depth'] for e in r['agent_executions']),2)

    def test_reused_child_in_second_attempt_does_not_recount_history(self):
        self.nodes['root']['users'].append(('root-u2',40000,self.tasks[0]['prompt']))
        self.nodes['root']['models'].append(('root-m2',40500,60000,100,300,10))
        self.tool('root','resume-c1',41000,'delegate',child='c1')
        self.nodes['c1']['users'].append(('c1-u2',41100,'Follow-up delegated work.'))
        self.nodes['c1']['models'].append(('c1-m2',41200,49000,10,20,5))
        self.tool('c1','resumed-search',42000,self.search)
        runs=self.collect()['runs']
        self.assertEqual(len(runs),2)
        self.assertEqual([r['values']['search_calls'] for r in runs],[6,1])
        self.assertEqual([r['values']['llm_calls'] for r in runs],[4,2])
        self.assertEqual(runs[1]['values']['input_tokens'],430)

    def test_continuation_retains_child_followup_without_extra_attempt(self):
        self.nodes['root']['users'].append(('continue',25000,'继续'))
        self.nodes['root']['models'].append(('continue-m',25500,35000,100,300,10))
        self.nodes['c1']['users'].append(('c1-continue',26000,'Subagent follow-up.'))
        self.nodes['c1']['models'].append(('c1-followup-m',26500,30000,10,20,5))
        self.tool('c1','followup-search',27000,self.search)
        runs=self.collect()['runs']
        self.assertEqual(len(runs),1);r=runs[0]
        self.assertEqual(r['segment_count'],2)
        self.assertEqual(r['values']['search_calls'],7)
        self.assertEqual([s['values']['search_calls'] for s in r['segments']],[6,1])

    @unittest.skipUnless(adapter.PRODUCT=='WorkBuddy','WorkBuddy named-message protocol')
    def test_named_child_message_in_second_attempt_keeps_new_window_only(self):
        self.nodes['root']['users'].append(('root-u2',40000,self.tasks[0]['prompt']))
        self.nodes['root']['models'].append(('root-m2',40500,60000,100,300,10))
        self.tool('root','message-c1',41000,'SendMessage',child='c1')
        self.nodes['c1']['users'].append(('c1-u2',41100,'Follow-up delegated work.'))
        self.nodes['c1']['models'].append(('c1-m2',41200,49000,10,20,5))
        self.tool('c1','resumed-search',42000,self.search)
        runs=self.collect()['runs']
        self.assertEqual([r['values']['search_calls'] for r in runs],[6,1])
        self.assertEqual([r['values']['llm_calls'] for r in runs],[4,2])

    @unittest.skipUnless(adapter.PRODUCT=='WorkBuddy','WorkBuddy notification metadata')
    def test_system_notification_is_not_a_new_user_test_boundary(self):
        source=self.fixture();path=source/'root.jsonl'
        rows=[json.loads(line) for line in path.read_text(encoding='utf8').splitlines()]
        for cid,at,pd in [('meta',2500,{'isMeta':True}),('wake',4500,{'startsNewUserRequest':False})]:
            rows.append(dict(id=cid,type='message',role='user',timestamp=self.at+at,sessionId='root',providerData=pd,content=[]))
        path.write_text('\n'.join(json.dumps(r) for r in sorted(rows,key=lambda r:r['timestamp'])),encoding='utf8')
        r=self.collect(source)['runs'][0]
        self.assertEqual(r['values']['search_calls'],6)
        self.assertEqual(r['values']['seconds'],20)
        self.assertTrue(r['agent_coverage']['complete'])

    @unittest.skipUnless(adapter.PRODUCT=='WorkBuddy','WorkBuddy asynchronous child messages')
    def test_message_to_running_child_keeps_its_complete_call(self):
        self.tool('root','running-message',3500,'SendMessage',child='c1')
        r=self.collect()['runs'][0]
        self.assertTrue(r['agent_coverage']['complete'])
        self.assertEqual(r['values']['search_calls'],6)
        self.assertEqual(r['values']['llm_calls'],4)
        self.assertEqual(r['values']['input_tokens'],650)

    def test_missing_child_is_incomplete_instead_of_zero(self):
        del self.nodes['c2']
        r=self.collect()['runs'][0]
        self.assertFalse(r['agent_coverage']['complete'])
        self.assertTrue(all(r['values'][k] is None for k in common.METRICS[:-1]))
        self.assertEqual(r['values']['artifact_count'],1)
        self.assertEqual(r['observed_partial']['seconds'],20)
        self.assertGreater(r['segments'][0]['observed_partial']['search_calls'],0)

    def test_incomplete_child_is_not_a_complete_total(self):
        old=self.nodes['c2']['models'][0]
        self.nodes['c2']['models'][0]=(old[0],old[1],None,*old[3:])
        r=self.collect()['runs'][0]
        self.assertIsNone(r['values']['search_calls'])
        self.assertFalse(r['agent_coverage']['complete'])

    def test_unfinished_main_does_not_use_earlier_reply_as_final_completion(self):
        self.nodes['root']['models'].append(('pending-main',21000,None,1,1,1))
        r=self.collect()['runs'][0]
        self.assertIsNone(r['end_ms'])
        self.assertTrue(all(r['values'][k] is None for k in common.METRICS[:-1]))
        self.assertEqual(r['observed_partial']['search_calls'],6)

    def test_same_tool_record_in_parent_and_child_is_counted_once(self):
        self.nodes['root']['tools'].append(copy.deepcopy(self.nodes['c2']['tools'][0]))
        r=self.collect()['runs'][0]
        self.assertEqual(r['values']['search_calls'],6)
        self.assertEqual(r['values']['tool_calls'],11)
        main=next(a for a in r['agent_executions'] if a['depth']==0)
        self.assertEqual(main['values']['search_calls'],0)
        self.assertEqual(main['values']['tool_calls'],3)

    def test_unrelated_root_in_same_workspace_is_not_included(self):
        self.node('unrelated',None,0,'A different full question.',500,20000,1,1,1)
        self.tool('unrelated','unrelated-search',2000,self.search)
        self.assertEqual(self.collect()['runs'][0]['values']['search_calls'],6)

    def test_parent_filter_still_includes_descendants(self):
        self.assertEqual(self.args.session,['root'])
        self.assertEqual(self.collect()['runs'][0]['values']['llm_calls'],4)

    def test_same_local_call_id_at_different_times_remains_two_calls(self):
        self.nodes['c3']['tools'][0]['id']=self.nodes['c2']['tools'][0]['id']
        r=self.collect()['runs'][0]
        self.assertEqual(r['values']['search_calls'],6)
        self.assertEqual(r['values']['tool_calls'],11)

    def test_parallel_duration_is_last_finish_not_sum_of_agent_durations(self):
        old=self.nodes['c3']['models'][0]
        self.nodes['c3']['models'][0]=(old[0],old[1],45000,*old[3:])
        r=self.collect()['runs'][0]
        self.assertEqual(r['values']['seconds'],45)
        self.assertEqual(r['main_end_ms'],self.at+20000)
        self.assertEqual(r['values']['search_calls'],6)

    def test_cycle_is_reported_without_infinite_recursion(self):
        self.tool('c2','cycle',6500,'delegate',child='root')
        r=self.collect()['runs'][0]
        self.assertFalse(r['agent_coverage']['complete'])
        self.assertTrue(any('循环' in i['reason'] for i in r['agent_coverage']['issues']))

    def test_child_registered_files_are_traceable_without_inflating_final_deliveries(self):
        self.tool('c2','explicit-delivery',7100,self.delivery,files=['intermediate.txt'])
        r=self.collect()['runs'][0]
        self.assertEqual(r['values']['artifact_count'],1)
        self.assertTrue(any(Path(f['path']).name=='intermediate.txt' for a in r['agent_executions'] if a['depth'] for f in a['files']))

    def test_no_delegation_keeps_original_single_agent_values(self):
        self.nodes={'root':self.nodes['root']}
        self.nodes['root']['tools']=[t for t in self.nodes['root']['tools'] if t['name']!='delegate']
        r=self.collect()['runs'][0]
        self.assertEqual(r['values']['llm_calls'],1)
        self.assertEqual(r['values']['input_tokens'],400)
        self.assertEqual(r['values']['search_calls'],0)
        self.assertEqual(r['agent_coverage']['child_sessions'],[])
        self.assertTrue(r['agent_coverage']['complete'])

    def test_root_date_keeps_children_executing_after_midnight(self):
        self.at=int(dt.datetime(2026,10,22,23,59,59,tzinfo=dt.timezone.utc).timestamp()*1000)
        self.args.date='2026-10-22'
        self.assertEqual(self.collect()['runs'][0]['values']['search_calls'],6)

    def test_system_memory_background_not_counted(self):
        self.node('background',None if adapter.PRODUCT=='WorkBuddy' else 'root',2000,'System work.',2100,5000,1,1,1)
        self.nodes['background']['title']='_SYS_MEMORY_DAILY_LOG_ synthetic'
        self.tool('background','memory-search',3000,self.search)
        self.assertEqual(self.collect()['runs'][0]['values']['search_calls'],6)

    def test_missing_child_usage_does_not_turn_into_a_zero_llm_count(self):
        source=self.fixture()
        if adapter.PRODUCT=='WorkBuddy':
            path=source/'root/subagents/agent-c2.jsonl'
            rows=[json.loads(line) for line in path.read_text(encoding='utf8').splitlines()]
            for row in rows:row.get('providerData',{}).pop('usage',None)
            path.write_text('\n'.join(json.dumps(row) for row in rows),encoding='utf8')
        else:
            db=sqlite3.connect(source)
            row=json.loads(db.execute("SELECT data FROM message WHERE id='c2-m'").fetchone()[0]);row.pop('tokens')
            db.execute("UPDATE message SET data=? WHERE id='c2-m'",(json.dumps(row),));db.commit();db.close()
        r=self.collect(source)['runs'][0]
        self.assertIsNone(r['values']['llm_calls']);self.assertIsNone(r['values']['input_tokens'])
        self.assertEqual(r['values']['search_calls'],6)


if __name__=='__main__':unittest.main()
