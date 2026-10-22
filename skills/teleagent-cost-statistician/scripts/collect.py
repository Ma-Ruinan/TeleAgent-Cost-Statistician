"""SQLite adapter. Product constants are specialized when this skill is packaged."""
from pathlib import Path
from common import DataError

PRODUCT = 'TeleAgent'
SEARCH_TOOL = 'online_search'
DELIVERY_TOOL = 'report_final_files'
METHOD = '只读账号teleagent.db的session、message、part；主任务及递归委派子Agent关联，排除系统后台会话；完成消息读取Token，callID去重，report_final_files路径核验存在性'


def default_source(args):
    if args.logs_root:
        raise DataError('本对象使用SQLite，请使用--db指定源')
    if args.db:
        return Path(args.db).resolve()
    candidates=sorted((Path.home()/'.local'/'share'/'TeleAgent'/'users').glob('*/teleagent.db'))
    if len(candidates)!=1:
        raise DataError('账号数据库不是唯一候选，请核实含目标会话的账号后使用--db')
    return candidates[0]


def collect(source, tasks, args, mapping, tz):
    from sqlite_reader import collect_sqlite
    return collect_sqlite(source, tasks, args, mapping, tz, PRODUCT, SEARCH_TOOL, DELIVERY_TOOL)


if __name__ == '__main__':
    from common import run_cli
    raise SystemExit(run_cli(PRODUCT, collect, default_source, METHOD))
