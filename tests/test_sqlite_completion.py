"""SQLite completion semantics using synthetic task logs."""
import json
from contextlib import closing
import sqlite3
import unittest
import test_agent_tree as fixtures


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.AgentTreeTests();self.case.setUp()
        self.source=self.case.fixture()

    def tearDown(self):self.case.tearDown()

    def add_unfinished(self,node,offset,duration=0,error=None):
        at=self.case.at+offset
        message=dict(role='assistant',time={'created':at,'completed':at+duration},finish=None,
            modelID='synthetic',tokens={'input':111,'output':22,'cache':{'read':33,'write':0},'total':166})
        if error:message['error']=error
        with closing(sqlite3.connect(self.source)) as db:
            db.execute('INSERT OR REPLACE INTO message VALUES(?,?,?,?)',(node+'-transition',node,at,json.dumps(message)))
            db.commit()

    def assert_incomplete(self,data):
        run=data['runs'][0]
        self.assertTrue(all(run['values'][key] is None for key in fixtures.common.METRICS[:-1]))
        self.assertEqual(data['summary']['search_calls']['run_n'],0)

    def test_main_transition_preserves_all_metrics_and_mean_sample(self):
        baseline=self.case.collect(self.source)
        self.add_unfinished('root',499)
        actual=self.case.collect(self.source)
        self.assertEqual(actual['runs'][0]['values'],baseline['runs'][0]['values'])
        self.assertEqual(actual['summary'],baseline['summary'])
        self.assertEqual(actual['runs'][0]['values']['llm_calls'],4)

    def test_child_transition_preserves_all_metrics_and_mean_sample(self):
        baseline=self.case.collect(self.source)
        self.add_unfinished('c1',1199)
        actual=self.case.collect(self.source)
        self.assertEqual(actual['runs'][0]['values'],baseline['runs'][0]['values'])
        self.assertEqual(actual['summary'],baseline['summary'])
        self.assertTrue(actual['runs'][0]['agent_coverage']['complete'])

    def test_transition_without_later_completion_stays_incomplete(self):
        self.add_unfinished('root',21000)
        self.assert_incomplete(self.case.collect(self.source))

    def test_instant_message_with_error_is_not_a_transition(self):
        self.add_unfinished('c1',1199,error={'name':'SyntheticFailure'})
        self.assert_incomplete(self.case.collect(self.source))

    def test_closed_intermediate_messages_have_no_duration_threshold(self):
        baseline=self.case.collect(self.source)
        for duration in (230,247,362,744,5000):
            with self.subTest(duration=duration):
                self.add_unfinished('c1',1150,duration=duration)
                actual=self.case.collect(self.source)
                self.assertEqual(actual['runs'][0]['values'],baseline['runs'][0]['values'])
                self.assertEqual(actual['summary'],baseline['summary'])

    def test_intermediate_missing_completion_stays_incomplete(self):
        self.add_unfinished('c1',1150)
        with closing(sqlite3.connect(self.source)) as db:
            value=json.loads(db.execute("SELECT data FROM message WHERE id='c1-transition'").fetchone()[0])
            value['time'].pop('completed');db.execute("UPDATE message SET data=? WHERE id='c1-transition'",(json.dumps(value),));db.commit()
        self.assert_incomplete(self.case.collect(self.source))

    def test_intermediate_ending_after_final_response_stays_incomplete(self):
        self.add_unfinished('c1',1150,duration=10000)
        self.assert_incomplete(self.case.collect(self.source))

    def test_invalid_completion_order_stays_incomplete(self):
        self.add_unfinished('c1',1150,duration=-1)
        self.assert_incomplete(self.case.collect(self.source))

    def test_transition_does_not_hide_running_tool(self):
        self.add_unfinished('c1',1199)
        with closing(sqlite3.connect(self.source)) as db:
            row=db.execute("SELECT id,data FROM part WHERE session_id='c1' AND json_extract(data,'$.tool')=? LIMIT 1",(self.case.search,)).fetchone()
            value=json.loads(row[1]);value['state']['status']='running'
            db.execute('UPDATE part SET data=? WHERE id=?',(json.dumps(value),row[0]))
            db.commit()
        self.assert_incomplete(self.case.collect(self.source))


if __name__=='__main__':unittest.main()
