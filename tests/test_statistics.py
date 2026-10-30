"""Synthetic logs only. No personal paths, prompts or real evaluation results."""
import argparse
import datetime as dt
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import xml.etree.ElementTree as E
from zipfile import ZipFile

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=next((ROOT/'skills').glob('*/scripts'))
sys.path.insert(0,str(SCRIPTS))
import common
import collect as adapter
import report


class StatisticsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.workspace=self.root/'workspace';self.workspace.mkdir()
        (self.workspace/'result.txt').write_text('synthetic result',encoding='utf8')
        self.tasks=[{'id':'Q1','name':'Synthetic task','prompt':'Find a primary source.'}]
        self.args=argparse.Namespace(workspace=[str(self.workspace)],session=[],date=None,observations=None)
        self.tz=dt.timezone.utc;self.start=1700000000000

    def tearDown(self):self.temp.cleanup()

    def db_fixture(self):
        path=self.root/'events.db';c=sqlite3.connect(path)
        c.executescript('CREATE TABLE session(id TEXT,parent_id TEXT,directory TEXT,title TEXT,time_created INTEGER);CREATE TABLE message(id TEXT,session_id TEXT,time_created INTEGER,data TEXT);CREATE TABLE part(id TEXT,message_id TEXT,session_id TEXT,time_created INTEGER,data TEXT);CREATE TABLE event(id TEXT,aggregate_id TEXT,type TEXT,data TEXT,seq INTEGER);')
        c.execute('INSERT INTO session VALUES(?,?,?,?,?)',('s1',None,str(self.workspace),'Synthetic',self.start))
        for i,(offset,prompt) in enumerate([(0,self.tasks[0]['prompt']),(10000,'继续'),(30000,self.tasks[0]['prompt'])],1):
            at=self.start+offset;uid=f'u{i}';aid=f'a{i}'
            def message(mid,when,data):c.execute('INSERT INTO message VALUES(?,?,?,?)',(mid,'s1',when,json.dumps(data)))
            def part(pid,mid,when,data):c.execute('INSERT INTO part VALUES(?,?,?,?,?)',(pid,mid,'s1',when,json.dumps(data)))
            message(uid,at,{'role':'user','time':{'created':at}})
            part(f'p{i}',uid,at,{'type':'text','text':prompt})
            message(aid,at+1000,{'role':'assistant','time':{'created':at+1000,'completed':at+7000},'finish':'stop','modelID':'synthetic','tokens':{'input':100,'output':10,'cache':{'read':300,'write':0},'total':410}})
            part(f't{i}',aid,at+2000,{'type':'tool','callID':f'c{i}','tool':adapter.SEARCH_TOOL,'state':{'status':'completed','metadata':{'exit':1 if i==2 else 0},'time':{'start':at+2000},'output':'synthetic failed command' if i==2 else ''}})
            part(f'd{i}',aid,at+3000,{'type':'tool','callID':f'dc{i}','tool':adapter.DELIVERY_TOOL,'state':{'status':'completed','input':{'output_paths':['result.txt','result.txt']},'metadata':{},'time':{'start':at+3000}}})
        c.execute('INSERT INTO event VALUES(?,?,?,?,?)',('retry1','s1','session.next.retried.1',json.dumps({'timestamp':self.start+3000,'attempt':1,'error':{'message':'synthetic model failure'}}),1))
        c.commit();c.close();return path

    def wb_fixture(self):
        path=self.root/'events.jsonl';rows=[]
        for i,(offset,prompt) in enumerate([(0,self.tasks[0]['prompt']+'\n@scene#7:制作PPT'),(10000,'继续'),(30000,self.tasks[0]['prompt'])],1):
            at=self.start+offset;uid=f'u{i}';cid=f'c{i}'
            rows.append({'id':uid,'type':'message','role':'user','sessionId':'s1','cwd':str(self.workspace),'timestamp':at,'content':[{'text':'<user_query>'+prompt+'</user_query>'}]})
            usage={'requests':1,'inputTokens':400,'outputTokens':10,'totalTokens':410,'inputTokensDetails':[{'cached_tokens':300}]}
            pd={'usage':usage,'conversationRequestId':f'q{i}','messageId':f'm{i}','stepSeq':1,'model':'synthetic','rawUsage':{'prompt_tokens_details':None}}
            rows.append({'type':'function_call','callId':cid,'name':'WebSearch','arguments':{},'timestamp':at+1000,'providerData':pd})
            rows.append({'type':'function_call_result','callId':cid,'name':'WebSearch','status':'completed','timestamp':at+2000,'providerData':{'toolResult':{'rawResponse':{'is_error':i==2,'exitCode':1 if i==2 else 0},'content':'synthetic error'}}})
            # Same LLM output emits another tool call: count usage once, tools twice.
            rows.append({'type':'function_call','callId':'d'+cid,'name':'present_files','arguments':{'files':['result.txt','result.txt']},'timestamp':at+3000,'providerData':pd})
            rows.append({'type':'function_call_result','callId':'d'+cid,'name':'present_files','status':'completed','timestamp':at+4000,'output':[{'type':'text','text':'saved'}]})
            rows.append({'type':'message','role':'assistant','status':'completed','timestamp':at+7000,'content':[{'text':'done'}]})
        path.write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in rows),encoding='utf8');return path

    def collect_data(self):
        path=self.wb_fixture() if adapter.PRODUCT=='WorkBuddy' else self.db_fixture()
        ss,excluded=adapter.collect(path,self.tasks,self.args,{},self.tz)
        return common.assemble(adapter.PRODUCT,self.tasks,ss,excluded,self.args,self.tz),path

    def test_repeated_tests_continuations_dedup_internal_errors_and_units(self):
        data,path=self.collect_data();runs=data['runs']
        self.assertEqual(len(runs),2);self.assertEqual([r['segment_count'] for r in runs],[2,1])
        self.assertEqual([r['attempt'] for r in runs],[1,2])
        self.assertEqual(runs[0]['values']['seconds'],17)
        self.assertEqual(runs[0]['active_seconds'],14);self.assertEqual(runs[0]['gap_seconds'],3)
        self.assertEqual(runs[0]['values']['llm_calls'],2)
        self.assertEqual(runs[0]['values']['input_tokens'],800)
        self.assertEqual(runs[0]['values']['tool_calls'],4)
        self.assertEqual(runs[0]['values']['tool_errors'],1)
        self.assertEqual(runs[0]['values']['search_calls'],2)
        self.assertEqual(runs[0]['values']['artifact_count'],1)
        self.assertEqual(runs[0]['values']['cache_rate'],.75)
        self.assertEqual(data['summary']['seconds']['task_mean'],12)
        data['meta']={'timezone':'UTC','collected_at':'2026-01-01T00:00:00+00:00','source':str(path),'method':adapter.METHOD,'read_twice_equal':True,'snapshot_sha256':'synthetic'}
        out=self.root/'report';report.export_reports(data,out)
        with ZipFile(out/(adapter.PRODUCT+'_过程指标统计.xlsx')) as z:
            self.assertIsNone(z.testzip())
            ns={'s':report.S};sheet=E.fromstring(z.read('xl/worksheets/sheet2.xml'))
            cells={c.attrib['r']:c for c in sheet.findall('.//s:c',ns)}
            self.assertAlmostEqual(float(cells['H2'].find('s:v',ns).text),17/86400)
            self.assertAlmostEqual(float(cells['J2'].find('s:v',ns).text),.08)
            self.assertIn('时',z.read('xl/styles.xml').decode())
            for name in z.namelist():
                if name.endswith('.xml') or name.endswith('.rels'):E.fromstring(z.read(name))
        with ZipFile(out/(adapter.PRODUCT+'_过程问题与采集方法.docx')) as z:
            self.assertIsNone(z.testzip());E.fromstring(z.read('word/document.xml'))
            self.assertIn('LLM完成调用数',z.read('word/document.xml').decode())

    def test_full_dataset_loads_questions_in_multiple_dimensions(self):
        dataset=self.root/'dataset'
        for dimension,tid,prompt in [('dimension1','W1','Find source one.'),('dimension2','D1','Create a document.')]:
            folder=dataset/dimension/(tid+'_task');folder.mkdir(parents=True)
            (folder/'问题描述.txt').write_text(prompt,encoding='utf8')
        tasks=common.task_set(argparse.Namespace(tasks=str(dataset),task_manifest=None))
        self.assertEqual({t['id'] for t in tasks},{'W1','D1'})
        self.assertEqual({t['prompt'] for t in tasks},{'Find source one.','Create a document.'})

    def test_workspace_without_date_keeps_cross_day_runs_and_excludes_other_workspaces(self):
        path=self.wb_fixture() if adapter.PRODUCT=='WorkBuddy' else self.db_fixture()
        other=self.root/'unrelated-workspace';other.mkdir()
        shift=86400000
        if adapter.PRODUCT=='WorkBuddy':
            rows=[json.loads(line) for line in path.read_text(encoding='utf8').splitlines()]
            for row in rows:
                if row.get('timestamp',0)>=self.start+30000:row['timestamp']+=shift
            path.write_text('\n'.join(json.dumps(row) for row in rows),encoding='utf8')
            foreign=json.loads(json.dumps(rows))
            for row in foreign:
                row['sessionId']='outside'
                if 'cwd' in row:row['cwd']=str(other)
                if row.get('providerData',{}).get('usage'):row['providerData']['usage']['requests']=99
            (self.root/'outside.jsonl').write_text('\n'.join(json.dumps(row) for row in foreign),encoding='utf8')
            source=self.root
        else:
            c=sqlite3.connect(path)
            for mid in ('u3','a3'):
                row=c.execute('SELECT time_created,data FROM message WHERE id=?',(mid,)).fetchone()
                data=json.loads(row[1])
                for key in ('created','completed'):
                    if key in data.get('time',{}):data['time'][key]+=shift
                c.execute('UPDATE message SET time_created=?,data=? WHERE id=?',(row[0]+shift,json.dumps(data),mid))
            c.execute("UPDATE part SET time_created=time_created+? WHERE message_id IN ('u3','a3')",(shift,))
            c.execute('INSERT INTO session VALUES(?,?,?,?,?)',('outside',None,str(other),'Unrelated',self.start))
            c.execute('INSERT INTO message SELECT id||?, ?,time_created,data FROM message WHERE session_id=?',('-outside','outside','s1'))
            c.execute('INSERT INTO part SELECT id||?,message_id||?, ?,time_created,data FROM part WHERE session_id=?',('-outside','-outside','outside','s1'))
            c.commit();c.close();source=path
        ss,excluded=adapter.collect(source,self.tasks,self.args,{},self.tz)
        data=common.assemble(adapter.PRODUCT,self.tasks,ss,excluded,self.args,self.tz)
        self.assertEqual(len(data['runs']),2)
        self.assertEqual([r['segment_count'] for r in data['runs']],[2,1])
        self.assertGreaterEqual(data['runs'][1]['start_ms']-data['runs'][0]['start_ms'],shift)
        self.assertTrue(all(r['session']=='s1' for r in data['runs']))
        self.assertEqual(data['summary']['seconds']['task_mean'],12)

    def test_missing_is_not_zero_and_refusal_is_external_evidence(self):
        s=common.segment(adapter.PRODUCT,'Q1','u1','初次提交','u1','s1',self.start,None,str(self.workspace),'synthetic',[],[],[],'search',self.tz)
        self.assertTrue(all(s['values'][k] is None for k in common.METRICS[:-1]))
        self.assertEqual(s['values']['artifact_count'],0)
        obs=self.root/'observations.json';common.dump(obs,[{'session':'s1','page_status':'已完成','page_response':'暂时无法回答','outcome':'refused','evidence':'synthetic user observation'}]);self.args.observations=str(obs)
        data=common.assemble(adapter.PRODUCT,self.tasks,[s],[],self.args,self.tz)
        self.assertIn('拒答',data['runs'][0]['status']);self.assertIsNone(data['summary']['llm_calls']['task_mean'])
        self.assertEqual(data['summary']['artifact_count']['task_mean'],0)

    def test_token_schema_change_stops_instead_of_guessing(self):
        with self.assertRaises(common.DataError):
            common.open_usage({'tokens':{'input':100,'output':10,'cache':{'read':300,'write':0},'total':999}},'synthetic')

    def test_missing_cache_field_is_not_a_zero_cache_rate(self):
        usage=common.open_usage({'tokens':{'input':100,'output':10,'cache':{'write':0}}},'synthetic')
        self.assertIsNone(usage['cache']);self.assertIsNone(usage['input'])
        self.assertEqual(usage['output'],10)

    def test_date_scope_excludes_older_incompatible_records_before_metrics(self):
        path=self.wb_fixture() if adapter.PRODUCT=='WorkBuddy' else self.db_fixture()
        self.args.date='2023-11-15'
        if adapter.PRODUCT=='WorkBuddy':
            rows=[json.loads(s) for s in path.read_text(encoding='utf8').splitlines()]
            for r in rows:
                if r.get('providerData',{}).get('usage'):r['providerData']['usage']['requests']=99
            path.write_text('\n'.join(json.dumps(r) for r in rows),encoding='utf8')
        else:
            c=sqlite3.connect(path)
            row=json.loads(c.execute("SELECT data FROM message WHERE id='a1'").fetchone()[0]);row['tokens']['total']=999
            c.execute("UPDATE message SET data=? WHERE id='a1'",(json.dumps(row),));c.commit();c.close()
        ss,_=adapter.collect(path,self.tasks,self.args,{},self.tz)
        self.assertEqual(ss,[])

    def test_manual_continuation_without_original_submission_stops(self):
        mapping={('s1','u1'):{'task_id':'Q1','role':'continue'}}
        with self.assertRaises(common.DataError):
            list(common.assign_users([{'id':'u1','time':1,'text':'edited prompt'}],'s1',self.tasks,mapping,[],'synthetic'))

    def test_unknown_followup_not_silently_included(self):
        excluded=[];users=[{'id':'u1','time':1,'text':self.tasks[0]['prompt']},{'id':'u2','time':10,'text':'unrelated new question'}]
        assigned=list(common.assign_users(users,'s1',self.tasks,{},excluded,'synthetic'))
        self.assertEqual(len(assigned),1);self.assertEqual(assigned[0][-1],10)
        self.assertEqual(excluded[0]['message'],'u2')

    def test_independent_read_consistency(self):
        data,path=self.collect_data()
        ss,ex=adapter.collect(path,self.tasks,self.args,{},self.tz)
        second=common.assemble(adapter.PRODUCT,self.tasks,ss,ex,self.args,self.tz)
        self.assertEqual(common.canonical(data),common.canonical(second))

    def test_mean_keeps_task_weights_distinct_from_run_weights(self):
        data,_=self.collect_data();runs=data['runs']
        extra=json.loads(json.dumps(runs[1]['segments'][0]));extra['root_user']='u4';extra['user_message']='u4';extra['task']='Q2';extra['values']['seconds']=60;extra['start_ms']+=100000;extra['end_ms']=extra['start_ms']+60000
        tasks=self.tasks+[{'id':'Q2','name':'Second task','prompt':'Another task.'}]
        ss=[s for r in runs for s in r['segments']]+[extra]
        result=common.assemble(adapter.PRODUCT,tasks,ss,[],self.args,self.tz)
        self.assertEqual(result['summary']['seconds']['task_mean'],36)
        self.assertEqual(result['summary']['seconds']['run_mean'],28)


if __name__=='__main__':unittest.main()
