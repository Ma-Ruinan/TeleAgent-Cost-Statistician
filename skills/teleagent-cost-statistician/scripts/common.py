"""Read-only evaluation log primitives; Python 3.10+, standard library only."""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import statistics
import sys

METRICS = ('seconds', 'llm_calls', 'input_tokens', 'output_tokens', 'cache_rate',
           'tool_calls', 'tool_errors', 'search_calls', 'artifact_count')
LABELS = ('耗时', 'LLM完成调用数', '输入Token', '输出Token', '缓存命中率',
          '工具调用', '工具错误', '搜索调用', '交付文件数')
UNITS = ('时分秒', '次', '万', '万', '%', '次', '次', '次', '个')
CONTINUE = {'继续', '请继续', '继续执行', '接着做', '请接着做', 'continue'}


class DataError(ValueError):
    """Unverified schema or ambiguous task association: do not guess a value."""


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def timezone(name):
    if name in ('Asia/Shanghai', '+08:00'):
        return dt.timezone(dt.timedelta(hours=8))
    if name in ('UTC', '+00:00'):
        return dt.timezone.utc
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise DataError('时区数据库不可用，请安装tzdata或使用Asia/Shanghai、UTC') from exc


def iso(ms, tz):
    return dt.datetime.fromtimestamp(ms / 1000, tz).isoformat(timespec='milliseconds') if ms is not None else None


def date_selected(ms, args, tz):
    return not args.date or dt.datetime.fromtimestamp(ms/1000, tz).date().isoformat() == args.date


def norm(text):
    return re.sub(r'\s+', '', text)


def task_set(args):
    if args.task_manifest:
        tasks = load(args.task_manifest)
    else:
        root = Path(args.tasks)
        candidates = sorted(p for p in root.rglob('*') if p.is_file() and p.name in ('问题描述.txt', 'question.txt', 'prompt.txt'))
        tasks = []
        for p in candidates:
            name = p.parent.name
            m = re.match(r'^([A-Za-z]+\d+)(?:\b|_)', name)
            tid = m.group(1) if m else name
            title = re.sub(r'^[A-Za-z]+\d+[_\s-]*', '', name) or name
            tasks.append({'id': tid, 'name': title, 'prompt': p.read_text(encoding='utf-8-sig').strip(), 'source': str(p.resolve())})
    if not isinstance(tasks, list) or not tasks:
        raise DataError('未找到题目。请提供问题描述.txt目录或任务清单JSON数组')
    ids, prompts = set(), set()
    for t in tasks:
        if not all(isinstance(t.get(k), str) and t[k].strip() for k in ('id', 'prompt')):
            raise DataError('任务清单每项必须有非空id和prompt')
        t.setdefault('name', t['id'])
        if t['id'] in ids or norm(t['prompt']) in prompts:
            raise DataError('题号或规范化后的完整题目重复，需先消歧')
        ids.add(t['id']); prompts.add(norm(t['prompt']))
        t['prompt_sha256'] = hashlib.sha256(norm(t['prompt']).encode()).hexdigest()
    return tasks


def in_scope(directory, roots):
    def key(p):
        return str(Path(p).resolve()).replace('\\', '/').rstrip('/').casefold()
    if not directory:
        return False
    value = key(directory)
    return any(value == key(r) or value.startswith(key(r) + '/') for r in roots)


def task_match(text, tasks, strip_scene=False):
    pure = re.sub(r'\s*@scene#\d+:.*$', '', text, flags=re.S) if strip_scene else text
    hits = [t['id'] for t in tasks if norm(t['prompt']) == norm(pure)]
    if len(hits) > 1:
        raise DataError('同一用户消息匹配多道题')
    return hits[0] if hits else None


def assign_users(users, session, tasks, mapping, excluded, source, strip_scene=False):
    """Boundaries include every user message. Only explicit continuations inherit a run."""
    root = task = None
    for i, u in enumerate(users):
        manual = mapping.get((session, u['id']))
        matched = manual['task_id'] if manual else task_match(u['text'], tasks, strip_scene)
        role = manual.get('role', 'new') if manual else None
        continuation = role == 'continue' or (not matched and norm(u['text']).lower() in CONTINUE)
        if role == 'continue' and not root:
            raise DataError('人工映射为继续，但源中没有所属的首次提交')
        if matched and not continuation:
            task, root = matched, u['id']
            kind = '初次提交'
        elif continuation and root and (not matched or matched == task):
            kind = '继续执行'
        else:
            excluded.append({'session': session, 'message': u['id'], 'source': source,
                             'reason': '未匹配完整题目或明确继续指令', 'candidate_root': root, 'time_ms': u['time']})
            continue
        if not root:
            raise DataError('继续指令未找到所属测试，需提供更早日志或修正映射')
        yield u, task, root, kind, users[i+1]['time'] if i+1 < len(users) else float('inf')


def connection(path):
    c = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA query_only=ON')
    c.execute('BEGIN')
    return c


def number(value, field):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DataError(f'{field}需要非负整数，实际为{value!r}')
    return value


def open_usage(message, evidence):
    t = message.get('tokens')
    if not isinstance(t, dict) or not t:
        return {'input': None, 'output': None, 'cache': None, 'calls': None, 'evidence': evidence,
                'model': message.get('modelID'), 'warning': '已完成助手消息缺少用量字段'}
    ca = t.get('cache', {})
    inp = number(t['input'], 'tokens.input') if 'input' in t else None
    out = number(t['output'], 'tokens.output') if 'output' in t else None
    read = number(ca['read'], 'tokens.cache.read') if 'read' in ca else None
    write = number(ca['write'], 'tokens.cache.write') if 'write' in ca else None
    total_input = inp + read + write if None not in (inp, read, write) else None
    if t.get('total') is not None and total_input is not None and out is not None:
        if number(t['total'], 'tokens.total') != total_input + out:
            raise DataError('Token合计关系变化，停止输出估计值：' + evidence)
    return {'input': total_input, 'output': out, 'cache': read, 'calls': 1,
            'model': message.get('modelID'), 'evidence': evidence,
            'warning': '用量字段不完整' if None in (total_input, out, read) else None}


def sum_known(values):
    values = list(values)
    return sum(values) if values and all(v is not None for v in values) else None


def delivery_files(deliveries, directory):
    paths = {}
    for d in deliveries:
        a = d['input']
        if isinstance(a, str):
            a = json.loads(a)
        items = a.get('output_paths', a.get('files')) if isinstance(a, dict) else None
        if not isinstance(items, list):
            raise DataError('交付工具参数不含路径数组：' + d['id'])
        for item in items:
            if not isinstance(item, str):
                raise DataError('交付路径类型变化，需要核查：' + d['id'])
            p = Path(item)
            p = (p if p.is_absolute() else Path(directory) / p).resolve()
            key = str(p).casefold()
            if key not in paths:
                paths[key] = {'path': str(p), 'exists': p.is_file(),
                              'bytes': p.stat().st_size if p.is_file() else None, 'registrations': []}
            paths[key]['registrations'].append(d['id'])
    return list(paths.values())


def segment(product, task, root, kind, user, session, start, end, directory, source,
            usage, tools, deliveries, search_name, tz, retries=None, extra=None, final_text='', errors_complete=True):
    if end is not None and end < start:
        raise DataError('结束时间早于开始时间：' + session)
    counts = collections.Counter(x['name'] for x in tools)
    errs = [x for x in tools if x['failed']]
    inp = sum_known(x['input'] for x in usage)
    out = sum_known(x['output'] for x in usage)
    cache = sum_known(x['cache'] for x in usage)
    calls = sum_known(x['calls'] for x in usage)
    files = delivery_files(deliveries, directory)
    complete = end is not None
    known = {'seconds': (end-start)/1000 if complete else None, 'llm_calls': calls,
             'input_tokens': inp, 'output_tokens': out,
             'cache_rate': cache/inp if inp and cache is not None else None,
             'tool_calls': len(tools), 'tool_errors': len(errs) if errors_complete else None,
             'search_calls': counts[search_name], 'artifact_count': sum(f['exists'] for f in files)}
    values = dict(known)
    if not complete:
        for k in METRICS[:-1]:
            values[k] = None
    return {'product': product, 'task': task, 'root_user': root, 'segment_kind': kind,
            'session': session, 'user_message': user, 'start_ms': start, 'end_ms': end,
            'start': iso(start, tz), 'end': iso(end, tz), 'directory': directory, 'source': str(source),
            'values': values, 'observed_partial': known if not complete else None,
            'cache_tokens': cache if complete else None, 'usage_records': usage,
            'tools': tools, 'errors': errs, 'retries': retries, 'files': files,
            'models': sorted({x['model'] for x in usage if x.get('model')}),
            'conditions': extra, 'final_text': final_text[-2400:], 'warnings':
            [x['warning'] for x in usage if x.get('warning')] + ([] if errors_complete else ['有工具调用未找到对应结果，工具错误数缺失'])}


def assemble(product, tasks, segments, excluded, args, tz):
    excluded = [e for e in excluded if 'time_ms' not in e or date_selected(e['time_ms'],args,tz)]
    grouped = collections.defaultdict(list)
    for s in segments:
        grouped[(s['source'], s['session'], s['root_user'])].append(s)
    runs = []
    for ss in grouped.values():
        ss.sort(key=lambda s: s['start_ms'])
        r = json.loads(json.dumps(ss[0])); last = ss[-1]
        if args.date and dt.datetime.fromtimestamp(r['start_ms']/1000, tz).date().isoformat() != args.date:
            continue
        r['end_ms'], r['end'] = last['end_ms'], last['end']
        r['values'] = {k: sum_known(s['values'][k] for s in ss) for k in METRICS}
        r['values']['seconds'] = (last['end_ms']-r['start_ms'])/1000 if last['end_ms'] is not None else None
        r['cache_tokens'] = sum_known(s['cache_tokens'] for s in ss)
        r['values']['cache_rate'] = r['cache_tokens']/r['values']['input_tokens'] if r['values']['input_tokens'] and r['cache_tokens'] is not None else None
        r['files'] = list({f['path'].casefold(): f for s in ss for f in s['files']}.values())
        r['values']['artifact_count'] = sum(f['exists'] for f in r['files'])
        for key in ('usage_records', 'tools', 'errors', 'warnings'):
            r[key] = [x for s in ss for x in s[key]]
        r['retries'] = [x for s in ss for x in s['retries']] if all(s['retries'] is not None for s in ss) else None
        r['models'] = sorted({x for s in ss for x in s['models']})
        r['active_seconds'] = sum_known(s['values']['seconds'] for s in ss)
        r['gap_seconds'] = r['values']['seconds']-r['active_seconds'] if None not in (r['values']['seconds'], r['active_seconds']) else None
        r['final_text'] = last['final_text']; r['segment_count'] = len(ss)
        r['segments'] = ss
        r['status'] = '记录不完整' if last['end_ms'] is None else ('有结束记录且找到交付文件' if r['values']['artifact_count'] else '有结束记录但未找到登记交付')
        r['task_name'] = next(t['name'] for t in tasks if t['id'] == r['task'])
        runs.append(r)
    runs.sort(key=lambda r: (next(i for i,t in enumerate(tasks) if t['id']==r['task']), r['start_ms'], r['source'], r['root_user']))
    counters = collections.Counter()
    for r in runs:
        counters[r['task']] += 1
        r['attempt'] = counters[r['task']]
        r['run_id'] = f"{product}-{r['task']}-{r['attempt']}"
        for i,s in enumerate(r['segments'], 1):
            s.update(run_id=r['run_id'], attempt=r['attempt'], segment=i)
    if args.observations:
        for o in load(args.observations):
            selectors = ('run_id', 'session', 'user_message')
            if not any(o.get(k) for k in selectors):
                raise DataError('页面观察需给出run_id、session或user_message以定位')
            hits = [r for r in runs if all(not o.get(k) or r[k] == o[k] for k in selectors)]
            if len(hits) != 1:
                raise DataError('页面观察未唯一定位一次测试，不能套用到其他次')
            if not o.get('evidence'):
                raise DataError('页面观察需要注明evidence来源')
            hits[0].setdefault('page_observations', []).append(o)
            if o.get('outcome') == 'refused':
                hits[0]['status'] = '拒答；' + hits[0]['status']
    means = {}
    for key in METRICS:
        by_task = {}
        for t in tasks:
            vv = [r['values'][key] for r in runs if r['task'] == t['id'] and r['values'][key] is not None]
            by_task[t['id']] = {'n': len(vv), 'mean': statistics.mean(vv) if vv else None}
        vv = [r['values'][key] for r in runs if r['values'][key] is not None]
        tm = [v['mean'] for v in by_task.values() if v['mean'] is not None]
        means[key] = {'by_task': by_task, 'task_n': len(tm), 'run_n': len(vv),
                      'task_mean': statistics.mean(tm) if tm else None, 'run_mean': statistics.mean(vv) if vv else None}
    valid = [r for r in runs if r['cache_tokens'] is not None and r['values']['input_tokens'] is not None]
    den = sum(r['values']['input_tokens'] for r in valid)
    return {'schema_version': '1.0', 'product': product, 'tasks': tasks, 'runs': runs,
            'summary': means, 'pooled_cache_rate': sum(r['cache_tokens'] for r in valid)/den if den else None,
            'excluded': excluded, 'missing_tasks': [t['id'] for t in tasks if not counters[t['id']]]}


def run_cli(product, collector, source_default, method):
    parser = argparse.ArgumentParser(description=product+'执行过程统计，所有应用日志只读')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--tasks', help='包含问题描述.txt的题目目录')
    group.add_argument('--task-manifest', help='[{id,name,prompt}]任务清单')
    parser.add_argument('--workspace', action='append', required=True, help='工作目录或批次父目录，可多次给出')
    parser.add_argument('--db', help='显式指定SQLite源，仅数据库型采集器使用')
    parser.add_argument('--logs-root', help='显式指定会话JSONL根目录')
    parser.add_argument('--session', action='append', default=[], help='限定会话ID，可多次给出')
    parser.add_argument('--date', help='仅保留该时区中此日期开始的独立测试，默认不按日期截断')
    parser.add_argument('--timezone', default='Asia/Shanghai')
    parser.add_argument('--mapping', help='人工核实的会话、用户消息、题号映射JSON')
    parser.add_argument('--observations', help='人工确认的页面状态及证据JSON')
    parser.add_argument('--output', required=True, help='本地结果目录')
    parser.add_argument('--collect-only', action='store_true', help='仅输出原始单位JSON，再由report.py输出报告')
    args = parser.parse_args()
    try:
        tz = timezone(args.timezone)
        if args.date: dt.date.fromisoformat(args.date)
        tasks = task_set(args)
        mapping = {}
        for m in load(args.mapping) if args.mapping else []:
            key = (m['session'], m['user_message'])
            if key in mapping or m['task_id'] not in {t['id'] for t in tasks} or m.get('role', 'new') not in ('new', 'continue'):
                raise DataError('人工映射含重复项、未知题号或无效role')
            mapping[key] = m
        source = source_default(args)
        def collect():
            ss, excluded = collector(source, tasks, args, mapping, tz)
            return assemble(product, tasks, ss, excluded, args, tz)
        data = collect(); second = collect()
        if canonical(data) != canonical(second):
            raise DataError('两次读取结果发生变化。请待本批任务停止写入后重新采集')
        if not data['runs']:
            raise DataError('没有匹配任务，检查工作目录、题目文本、日期、账号数据库或人工映射')
        data['meta'] = {'collected_at': dt.datetime.now(tz).isoformat(timespec='seconds'),
                        'timezone': args.timezone, 'scope': args.workspace, 'date_filter': args.date,
                        'source': str(source), 'read_twice_equal': True, 'method': method,
                        'snapshot_sha256': hashlib.sha256(canonical(data).encode()).hexdigest()}
        output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
        target = output/(product+'_执行数据.json')
        dump(target, data)
        if not args.collect_only:
            from report import export_reports
            export_reports(data, output)
        print(f"{product}: {len(data['runs'])}次独立测试，{sum(r['segment_count'] for r in data['runs'])}个执行段；两次读取一致")
        print(target.resolve())
    except (DataError, OSError, sqlite3.Error, json.JSONDecodeError, KeyError) as exc:
        print(f'采集停止：{exc}', file=sys.stderr)
        return 2
    return 0
