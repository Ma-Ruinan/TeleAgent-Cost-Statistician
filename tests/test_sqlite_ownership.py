"""Execution ownership is proved by messages and delegation, not write latency."""
from contextlib import closing
import json
import sqlite3
import unittest
import test_agent_tree as fixtures


class OwnershipTests(unittest.TestCase):
    def setUp(self):self.case=fixtures.AgentTreeTests();self.case.setUp()
    def tearDown(self):self.case.tearDown()

    def delay_part(self,db,call,offset,delay_start=False):
        db.execute("UPDATE part SET time_created=? WHERE json_extract(data,'$.callID')=?",(self.case.at+offset,call))
        if delay_start:
            row=db.execute("SELECT id,data FROM part WHERE json_extract(data,'$.callID')=?",(call,)).fetchone();value=json.loads(row[1])
            value['state']['time']={'start':self.case.at+offset,'end':self.case.at+offset+1000}
            db.execute('UPDATE part SET data=? WHERE id=?',(json.dumps(value),row[0]))

    def add_user(self,db,uid,offset,parts):
        at=self.case.at+offset
        db.execute('INSERT INTO message VALUES(?,?,?,?)',(uid,'root',at,json.dumps({'role':'user','time':{'created':at}})))
        for i,p in enumerate(parts):db.execute('INSERT INTO part VALUES(?,?,?,?,?)',(uid+'-'+str(i),uid,'root',at,json.dumps(p)))

    def test_new_child_initial_records_survive_delayed_delegation_write(self):
        source=self.case.fixture();baseline=self.case.collect(source)
        with closing(sqlite3.connect(source)) as db:
            self.delay_part(db,'delegate1',5000,delay_start=True);db.commit()
        actual=self.case.collect(source)
        self.assertEqual(actual['runs'][0]['values'],baseline['runs'][0]['values'])
        self.assertEqual(actual['summary'],baseline['summary'])

    def test_nested_delegation_writes_can_be_late_at_each_level(self):
        source=self.case.fixture();baseline=self.case.collect(source)
        with closing(sqlite3.connect(source)) as db:
            self.delay_part(db,'delegate1',13000,delay_start=True);self.delay_part(db,'delegate2',15000,delay_start=True);db.commit()
        self.assertEqual(self.case.collect(source)['runs'][0]['values'],baseline['runs'][0]['values'])

    def test_parent_message_keeps_late_part_in_its_original_attempt(self):
        self.case.nodes['root']['users'].append(('root-u2',40000,self.case.tasks[0]['prompt']))
        self.case.nodes['root']['models'].append(('root-m2',40500,60000,100,300,10))
        source=self.case.fixture();baseline=self.case.collect(source)
        with closing(sqlite3.connect(source)) as db:
            self.delay_part(db,'delegate1',45000);db.commit()
        actual=self.case.collect(source)
        self.assertEqual([r['values'] for r in actual['runs']],[r['values'] for r in baseline['runs']])
        self.assertEqual([r['values']['search_calls'] for r in actual['runs']],[6,0])

    def test_reuse_aligns_to_its_own_exact_prompt_without_old_history(self):
        self.case.nodes['root']['users'].append(('root-u2',40000,self.case.tasks[0]['prompt']))
        self.case.nodes['root']['models'].append(('root-m2',40500,60000,100,300,10))
        self.case.tool('root','resume-c1',41000,'delegate',child='c1')
        self.case.nodes['c1']['users'].append(('c1-u2',41100,'Follow-up delegated work.'))
        self.case.nodes['c1']['models'].append(('c1-m2',41200,49000,10,20,5))
        self.case.tool('c1','resumed-search',42000,self.case.search)
        source=self.case.fixture();baseline=self.case.collect(source)
        with closing(sqlite3.connect(source)) as db:
            row=db.execute("SELECT id,data FROM part WHERE json_extract(data,'$.callID')='resume-c1'").fetchone()
            value=json.loads(row[1]);value['state']['input']={'task_id':'c1','prompt':'Follow-up delegated work.'}
            value['state']['time']={'start':self.case.at+45000,'end':self.case.at+50000}
            db.execute('UPDATE part SET data=?,time_created=? WHERE id=?',(json.dumps(value),self.case.at+45000,row[0]));db.commit()
        actual=self.case.collect(source)
        self.assertEqual([r['values'] for r in actual['runs']],[r['values'] for r in baseline['runs']])
        self.assertEqual([r['values']['search_calls'] for r in actual['runs']],[6,1])

    def test_creation_is_still_first_when_its_part_is_written_after_reuse(self):
        self.case.nodes['root']['users'].append(('root-u2',40000,self.case.tasks[0]['prompt']))
        self.case.nodes['root']['models'].append(('root-m2',40500,60000,100,300,10))
        self.case.tool('root','resume-c1',41000,'delegate',child='c1')
        self.case.nodes['c1']['users'].append(('c1-u2',41100,'Follow-up delegated work.'))
        self.case.nodes['c1']['models'].append(('c1-m2',41200,49000,10,20,5))
        self.case.tool('c1','resumed-search',42000,self.case.search)
        source=self.case.fixture();baseline=self.case.collect(source)
        with closing(sqlite3.connect(source)) as db:
            self.delay_part(db,'delegate1',45000,delay_start=True);db.commit()
        self.assertEqual([r['values'] for r in self.case.collect(source)['runs']],[r['values'] for r in baseline['runs']])

    def test_old_child_without_new_creation_or_prompt_proof_is_not_rewound(self):
        source=self.case.fixture()
        with closing(sqlite3.connect(source)) as db:
            db.execute("UPDATE session SET time_created=? WHERE id='c1'",(self.case.at+100,))
            self.delay_part(db,'delegate1',5000,delay_start=True);db.commit()
        actual=self.case.collect(source)['runs'][0]
        self.assertIsNone(actual['values']['search_calls'])
        self.assertFalse(actual['agent_coverage']['complete'])

    def test_unproved_reuse_start_cannot_hide_a_prefix_even_with_later_stop(self):
        self.case.nodes['root']['users'].append(('root-u2',40000,self.case.tasks[0]['prompt']))
        self.case.nodes['root']['models'].append(('root-m2',40500,60000,100,300,10))
        self.case.tool('root','resume-c1',41000,'delegate',child='c1')
        self.case.nodes['c1']['users'].append(('c1-u2',41100,'Follow-up delegated work.'))
        self.case.nodes['c1']['models'].extend([('c1-early',41200,44000,10,20,5),('c1-late',46000,49000,10,20,5)])
        self.case.tool('c1','early-search',42000,self.case.search);self.case.tool('c1','late-search',47000,self.case.search)
        source=self.case.fixture();baseline=self.case.collect(source)
        with closing(sqlite3.connect(source)) as db:
            row=db.execute("SELECT id,data FROM part WHERE json_extract(data,'$.callID')='resume-c1'").fetchone()
            value=json.loads(row[1]);value['state']['input']={'task_id':'c1','prompt':'Unmatched delegated prompt.'}
            db.execute('UPDATE part SET data=?,time_created=? WHERE id=?',(json.dumps(value),self.case.at+45000,row[0]));db.commit()
        # The actual call start still proves the full new window despite late storage.
        self.assertEqual(self.case.collect(source)['runs'][1]['values'],baseline['runs'][1]['values'])
        with closing(sqlite3.connect(source)) as db:
            self.delay_part(db,'resume-c1',45000,delay_start=True);db.commit()
        actual=self.case.collect(source)['runs'][1]
        self.assertFalse(actual['agent_coverage']['complete']);self.assertIsNone(actual['values']['search_calls'])
        self.assertTrue(any('起点' in i['reason'] for i in actual['agent_coverage']['issues']))

    def test_same_attempt_reuse_does_not_invalidate_an_already_covered_prefix(self):
        self.case.tool('root','repeat-c1',11000,'delegate',child='c1')
        self.case.nodes['c1']['users'].append(('c1-followup',11150,'Another delegated question.'))
        self.case.nodes['c1']['models'].append(('c1-followup-m',11200,14000,10,20,5))
        self.case.tool('c1','followup-search',12000,self.case.search)
        actual=self.case.collect()['runs'][0]
        self.assertTrue(actual['agent_coverage']['complete'])
        self.assertEqual(actual['values']['search_calls'],7)
        self.assertEqual(actual['values']['llm_calls'],5)
        self.assertEqual(actual['values']['tool_calls'],13)

    def test_automatic_compaction_and_recovery_do_not_split_a_test(self):
        source=self.case.fixture();baseline=self.case.collect(source)
        with closing(sqlite3.connect(source)) as db:
            self.add_user(db,'auto-compaction',2500,[{'type':'compaction','auto':True}])
            self.add_user(db,'auto-recovery',3000,[{'type':'text','synthetic':True,'text':'Synthetic internal recovery.'}]);db.commit()
        actual=self.case.collect(source)
        self.assertEqual(actual['runs'][0]['values'],baseline['runs'][0]['values'])
        self.assertEqual(actual['summary'],baseline['summary'])
        self.assertEqual(actual['runs'][0]['segment_count'],1)

    def test_real_prompt_with_synthetic_context_is_preserved(self):
        source=self.case.fixture();baseline=self.case.collect(source)
        with closing(sqlite3.connect(source)) as db:
            db.execute('INSERT INTO part VALUES(?,?,?,?,?)',('context','root-u','root',self.case.at,json.dumps({'type':'text','synthetic':True,'text':'Tool context, not a second question.'})));db.commit()
        actual=self.case.collect(source)
        self.assertEqual(actual['runs'][0]['values'],baseline['runs'][0]['values'])

    def test_files_unknown_parts_and_unmarked_empty_messages_remain_user_boundaries(self):
        source=self.case.fixture()
        with closing(sqlite3.connect(source)) as db:
            self.add_user(db,'attachment',40000,[{'type':'file','url':'synthetic.txt'},{'type':'text','synthetic':True,'text':'Synthetic attachment context.'}])
            self.add_user(db,'future-part',41000,[{'type':'future_type'}])
            self.add_user(db,'empty-human',42000,[]);db.commit()
        actual=self.case.collect(source)
        excluded={r.get('message') for r in actual['excluded']}
        self.assertTrue({'attachment','future-part','empty-human'}<=excluded)


if __name__=='__main__':unittest.main()
