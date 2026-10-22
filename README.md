# TeleAgent-Cost-Statistician

TeleAgent专用的执行过程统计Skill。读取已经运行过的测试任务日志，生成九项过程指标Excel、具体问题与采集方法Word，以及本地回查JSON。保留每次独立测试、追加继续执行段、题目均值和对象均值。

## 使用

### 自然语言安装（首次使用）

```text
请从 https://github.com/Ma-Ruinan/TeleAgent-Cost-Statistician 安装 teleagent-cost-statistician 技能。
将仓库中的 skills/teleagent-cost-statistician 完整文件夹安装到当前 Agent 的技能目录，保留脚本和参考资料，并确认技能已被识别和启用。
```

### 自然语言调用

替换尖括号中的内容，括号内仅为填写示例。

```text
请使用 teleagent-cost-statistician，收集 TeleAgent 在本次测试工作空间中执行完整数据集的过程数据。

数据集目录：<包含各个维度的完整数据集路径>（例如：E:\evaluation\综合测试数据集\常规能力测试数据集）
测试工作空间：<本次测试使用的工作空间完整路径>（例如：C:\Users\Administrator\.local\share\TeleAgent\TeleAgent的工作空间）
测试时间：<具体日期或不限定>（例如：不限定；指定日期时可填2026-10-22）
结果保存目录：<结果目录完整路径>（例如：E:\evaluation\统计结果\TeleAgent）

请自行发现日志位置、关联题目与运行记录，并完成统计和报告生成，不要中途向我确认信息。以指定工作空间为收集范围，递归读取数据集的各个维度，保留每道题的全部独立运行和继续执行段；未提供测试时间时，不按时间筛选。

未提供工作空间时，请从本地日志发现与数据集匹配的工作空间并列明实际收集范围；未提供结果目录时，请在当前任务目录下新建结果文件夹。遇到无法可靠确定的记录，说明原因和影响，在能够独立完成的范围内继续执行，最终集中列出未完成事项。

按技能要求输出九项指标及均值的 Excel、包含具体问题与日志证据的 Word，并保留 JSON 核查数据。
```

填写说明：

| 信息 | 作用与填写方式 |
| --- | --- |
| 数据集目录 | 必填。提供包含各个维度的完整数据集根目录，Agent 会递归查找题目。 |
| 测试工作空间 | 可选，建议填写。提供本次测试使用的工作文件夹；上述路径是各工具工作文件夹的示例。若其中还有其他批次，应填本批子目录或提供多个本批路径。省略时由 Agent 从日志发现全部匹配工作空间，并在结果中列明。 |
| 测试时间 | 可选。填写具体日期或不限定；省略时收集工作空间中与数据集匹配的全部历史运行。指定日期时按独立测试开始日期筛选；当前不支持小时区间或最近几天等模糊时间。 |
| 结果保存目录 | 可选。最终 Excel、Word 和 JSON 的保存文件夹，不是源日志目录。省略时使用当前任务目录下的统计结果/TeleAgent/采集时间，并在交付时告知路径。 |

同一次测试都在独立工作空间中进行时，提供该工作空间即可，测试时间可以不填写。一个空间包含多批测试且不限定时间时，各批中匹配题目的运行都会计入。

### 命令行使用

将 [skills/teleagent-cost-statistician](skills/teleagent-cost-statistician) 文件夹复制到所用Agent的技能目录。Codex可使用 `$CODEX_HOME/skills`，未配置时使用 `~/.codex/skills`。也可直接阅读 [SKILL.md](skills/teleagent-cost-statistician/SKILL.md) 并运行脚本。无需安装第三方Python库，要求Python 3.10及以上版本。

```powershell
python skills/teleagent-cost-statistician/scripts/collect.py --tasks "题目集目录" --workspace "工作目录或批次父目录" --db "账号数据库路径" --output "本地结果目录"
```

`--workspace`和`--session`支持重复传入；`--date`仅在明确要限定日期时使用，默认时区Asia/Shanghai。SQLite适配器只读事务读取；JSONL适配器不修改源文件。每次采集读取两遍，若数据变化或字段校验失败即停止。

## 结果和统计口径

统计包括主任务及其实际委派的各层子Agent，支持多层、并行及子会话复用；没有子调用的任务沿用同一口径。Excel新增Agent明细便于回查，九项指标保持不变。子日志缺失时相关总数保留缺失，并记录已取得的数据。

指标为耗时、LLM完成调用数、输入Token、输出Token、缓存命中率、工具调用、工具错误、搜索调用、交付文件数。耗时显示时分秒，Token以万为单位。LLM完成调用数不包含失败重试请求，模型重试有可靠字段时另列。

完整重提同题算新测试，继续指令算原测试后续段。缺失不补零，有完整数值的失败执行参与均值。主均值按题目等权，按测试次数平均另列。详见 [日志字段](skills/teleagent-cost-statistician/references/log-schema.md) 和 [输出口径](skills/teleagent-cost-statistician/references/output-contract.md)。

当前九项不含货币金额，Token与缓存数据供后续成本分析；没有单价和可靠账单时不生成费用。页面证据可通过`--observations`保留来源。Agent阅读JSON后可用`report.py --analysis`补充Word中的有据分析。

## 验证

```powershell
python -m unittest discover -s tests -v
```

测试在临时目录生成合成日志，覆盖多层及并行子Agent、子会话复用、重复记录去重、子日志缺失、普通任务、重复测试与继续、缓存计数、工具错误、均值、输出单位及文件结构。仓库不包含真实测试题目、原始日志或个人运行结果。
