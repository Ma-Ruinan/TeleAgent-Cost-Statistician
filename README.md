# TeleAgent-Cost-Statistician

TeleAgent专用的执行过程统计Skill。读取已经运行过的测试任务日志，生成九项过程指标Excel、具体问题与采集方法Word，以及本地回查JSON。保留每次独立测试、追加继续执行段、题目均值和对象均值。

## 使用

将 [skills/teleagent-cost-statistician](skills/teleagent-cost-statistician) 文件夹复制到所用Agent的技能目录。Codex可使用 `$CODEX_HOME/skills`，未配置时使用 `~/.codex/skills`。也可直接阅读 [SKILL.md](skills/teleagent-cost-statistician/SKILL.md) 并运行脚本。无需安装第三方Python库，要求Python 3.10及以上版本。

```powershell
python skills/teleagent-cost-statistician/scripts/collect.py --tasks "题目集目录" --workspace "工作目录或批次父目录" --db "账号数据库路径" --output "本地结果目录"
```

`--workspace`和`--session`支持重复传入；`--date`仅在明确要限定日期时使用，默认时区Asia/Shanghai。SQLite适配器只读事务读取；JSONL适配器不修改源文件。每次采集读取两遍，若数据变化或字段校验失败即停止。

## 结果和统计口径

指标为耗时、LLM完成调用数、输入Token、输出Token、缓存命中率、工具调用、工具错误、搜索调用、交付文件数。耗时显示时分秒，Token以万为单位。LLM完成调用数不包含失败重试请求，模型重试有可靠字段时另列。

完整重提同题算新测试，继续指令算原测试后续段。缺失不补零，有完整数值的失败执行参与均值。主均值按题目等权，按测试次数平均另列。详见 [日志字段](skills/teleagent-cost-statistician/references/log-schema.md) 和 [输出口径](skills/teleagent-cost-statistician/references/output-contract.md)。

当前九项不含货币金额，Token与缓存数据供后续成本分析；没有单价和可靠账单时不生成费用。页面证据可通过`--observations`保留来源。Agent阅读JSON后可用`report.py --analysis`补充Word中的有据分析。

## 验证

```powershell
python -m unittest discover -s tests -v
```

测试在临时目录生成合成日志，覆盖重复测试与继续、缓存计数、用量去重、内部工具错误、缺失与零、题目等权均值、输出单位及文件结构。仓库不包含真实测试题目、原始日志或个人运行结果。
