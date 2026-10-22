# TeleAgent日志来源和字段

默认来源为 `%USERPROFILE%\.local\share\TeleAgent\users\账号目录\teleagent.db`，可通过命令行显式覆盖。该说明记录已核实的日志结构，不代表厂商的长期接口承诺。字段变更需要查看实际记录后修改适配器并重新验证。

先核实 users 下哪个账号数据库包含目标题目的主会话；存在多个数据库时必须显式指定 --db。多个任务可能共用一个工作空间，不能按目录把全部后台消息都算入测试。

message.data 中使用 role、time.created、time.completed、finish、modelID、tokens。累计输入为 tokens.input + tokens.cache.read + tokens.cache.write；输出为 tokens.output；缓存读取为 tokens.cache.read。存在 tokens.total 时核对合计关系。part 中 step-finish 不重复累计。 只把具有完成时间和 finish 的助手消息计为LLM完成调用。finish为空、完成时间等于创建时间、无错误且后续有正常完成消息的瞬时过渡记录，不计入调用与Token，也不影响结束判断；尾部未完成消息和运行中工具仍按记录不完整处理。

工具记录为 part.data 的 type=tool、callID、tool、state.status、state.metadata.exit、state.time。工具错误按错误状态或非零退出码识别，网页 HTTP 错误、超时、截图文件缺失、写入路径不存在等保留原始摘要及 part 标识。

结束时间使用模型消息中的完成时间，不使用 session.time_updated 或目录修改时间。独立系统记忆整理会话、未关联到所选父任务的子会话和不匹配题目的消息列入排除记录。本版未确认独立模型失败重试事件的可靠字段，因此重试数保持未记录，不填零。

专用搜索工具为 `online_search`。成功交付工具为 `report_final_files`，从 `output_paths` 或 `files` 数组读取路径。路径相对工作目录解析后去重，逐个核验磁盘存在性；检查过程中的截图、脚本、临时文件不自动算作最终交付。

原始题目仅用于定位主任务，实际委派的各层子Agent继承对应独立测试归属，不新增测试次数。通过session.parent_id及task/Agent委派记录的state.metadata.sessionId关联各层子会话；兼容session_id、task_id及输出中的明确会话标识。旧日志只有parent_id时按已记录的父子关系发现子会话；有委派调用却无法定位子日志时记录覆盖问题。系统记忆等后台会话仍排除。 同一子会话被再次调用时，按委派调用界定新的执行窗口，避免将旧历史计入下一次测试；继续执行段仍并入原测试。

模型记录按原始消息或请求标识去重；工具按调用标识及调用时间去重，防止同一执行在父摘要、子日志中重复累计，也避免不同时间复用局部标识被合并。父工具结果中的汇总Token不重复累加。无法唯一归属或标识内容冲突时停止猜测值。

同一批数据默认读取两次并比较。若仍在写入、读取不一致或Token恒等关系变化，脚本停止并返回非零退出码；应查明实际记录后重跑，不能补猜测值。目录时间、session更新时间和数据库费用零值都不替代已核实的字段。
