"""SQLite adapter. Product constants are specialized when this skill is packaged."""
import json
from pathlib import Path
from common import (DataError, assign_users, connection, date_selected, in_scope, iso, open_usage, segment)

PRODUCT = 'TeleAgent'
SEARCH_TOOL = 'online_search'
DELIVERY_TOOL = 'report_final_files'
METHOD = 'ֻ���˺�teleagent.db��session��message��part�����Ự����Ŀ�������ų�������̨�Ự�������Ϣ��ȡToken��callIDȥ�أ�report_final_files·�����������'


def default_source(args):
    if args.logs_root:
        raise DataError('������ʹ��SQLite����ʹ��--dbָ��Դ')
    if args.db:
        return Path(args.db).resolve()
    candidates=sorted((Path.home()/'.local'/'share'/'TeleAgent'/'users').glob('*/teleagent.db'))
    if len(candidates)!=1:
        raise DataError('�˺����ݿⲻ��Ψһ��ѡ�����ʵ��Ŀ��Ự���˺ź�ʹ��--db')
    return candidates[0]


def collect(source, tasks, args, mapping, tz):
    c = connection(source)
    result, excluded = [], []
    try:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'session', 'message', 'part'} <= tables:
            raise DataError('���ݿ�ȱ��session��message��part��')
        for session in c.execute('SELECT id,parent_id,directory,title,time_created FROM session ORDER BY time_created,id').fetchall():
            sid, directory = session['id'], session['directory']
            if not in_scope(directory, args.workspace) or (args.session and sid not in args.session):
                continue
            if session['parent_id']:
                excluded.append({'session': sid, 'source': str(source), 'reason': '�����ӻỰ���̨�Ự��δ����������', 'time_ms': session['time_created']})
                continue
            messages = [dict(r) for r in c.execute('SELECT id,time_created,data FROM message WHERE session_id=? ORDER BY time_created,id', (sid,))]
            parts = [dict(r) for r in c.execute('SELECT id,message_id,time_created,data FROM part WHERE session_id=? ORDER BY time_created,id', (sid,))]
            for r in messages + parts:
                r['json'] = json.loads(r['data'])
            users = []
            for m in messages:
                j = m['json']
                if j.get('role') == 'user':
                    text = '\n'.join(p['json'].get('text', '') for p in parts if p['message_id'] == m['id'] and p['json'].get('type') == 'text')
                    users.append({'id': m['id'], 'text': text, 'time': j.get('time', {}).get('created', m['time_created'])})
            retry_events = None
            pass  # ����û��ȷ�Ͽɶ��������ģ�������¼��ֶ�
            for user, task, root, kind, until in assign_users(users, sid, tasks, mapping, excluded, str(source)):
                root_time = next(u['time'] for u in users if u['id'] == root)
                if not date_selected(root_time, args, tz):
                    continue
                start = user['time']
                mm = [m for m in messages if start <= m['time_created'] < until]
                pp = [p for p in parts if start <= p['time_created'] < until]
                completed = [m for m in mm if m['json'].get('role') == 'assistant' and m['json'].get('time', {}).get('completed') and m['json'].get('finish')]
                end = max((m['json']['time']['completed'] for m in completed), default=None)
                if end is not None and end > until:
                    raise DataError('�û�����ִ�������ص�����˲���Ϣ�͹��߹�����'+sid)
                usage = [open_usage(m['json'], 'message:'+m['id']) for m in completed]
                by_id = {}
                for p in pp:
                    j = p['json']
                    if j.get('type') != 'tool':
                        continue
                    cid = j.get('callID')
                    if not cid or not j.get('tool'):
                        raise DataError('���߼�¼ȱ��callID��tool��part:'+p['id'])
                    if cid in by_id and by_id[cid]['json']['tool'] != j['tool']:
                        raise DataError('ͬһcallID��Ӧ������ߣ�'+cid)
                    by_id[cid] = p
                tools, deliveries = [], []
                errors_complete = True
                for cid, p in by_id.items():
                    j = p['json']; st = j.get('state', {}); md = st.get('metadata', {})
                    status = st.get('status'); exitcode = md.get('exit', md.get('exitCode'))
                    if status not in ('completed', 'error', 'pending', 'running'):
                        raise DataError('����״̬�ֶα仯��part:'+p['id'])
                    if exitcode is not None and (not isinstance(exitcode, int) or isinstance(exitcode, bool)):
                        raise DataError('�����˳������ͱ仯��part:'+p['id'])
                    if status in ('pending', 'running'):
                        errors_complete = False
                    failed = status == 'error' or exitcode not in (None, 0)
                    msg = str(st.get('error') or md.get('output') or st.get('output') or '')
                    tools.append({'id': cid, 'name': j['tool'], 'status': status, 'exit_code': exitcode,
                                  'failed': failed, 'time': iso(st.get('time', {}).get('start', p['time_created']), tz),
                                  'message': msg[:1600] if failed else '', 'message_truncated': failed and len(msg)>1600,
                                  'evidence': 'part:'+p['id']})
                    if j['tool'] == DELIVERY_TOOL and status == 'completed' and not failed:
                        deliveries.append({'id': cid, 'input': st.get('input')})
                retries = None if retry_events is None else [r for r in retry_events if start <= r['timestamp'] < until]
                assistant_ids = {m['id'] for m in completed}
                final_text = next((p['json'].get('text', '') for p in reversed(pp) if p['message_id'] in assistant_ids and p['json'].get('type') == 'text'), '')
                result.append(segment(PRODUCT, task, root, kind, user['id'], sid, start, end,
                                      directory, source, usage, tools, deliveries, SEARCH_TOOL, tz,
                                      retries=retries, final_text=final_text, errors_complete=errors_complete))
    finally:
        c.rollback(); c.close()
    return result, excluded


if __name__ == '__main__':
    from common import run_cli
    raise SystemExit(run_cli(PRODUCT, collect, default_source, METHOD))
