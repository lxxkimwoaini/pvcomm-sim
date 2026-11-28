# ADR 0021：配对业务对照门禁与事务恢复归属修正

日期：2026-11-26。状态：实施中，原件重验结果另记。不改变异常类别、强度、业务期限或正常回归门槛。

代码审查确认原配对QC v1仅展示contrast_observed而没有纳入passed；any站点聚合可被非目标站违约误满足。v2逐目标站要求正常成员valid/false、改变成员valid/true且有预期类型finding；P04正常还须合法完成，P06正常须真实拒绝。非目标站两成员均须valid/false。invalid、uncertain、null绝不当正常负例。data_quality_passed与业务对照分别报告，最终passed同时要求二者。旧QC不覆盖，新目录重新检查原四组，不无故重跑。

标签v2确认恢复时没有区分迟到否定/肯定确认；反馈重新交付与物理执行也未独立表示，正常事务的recovery还会被运行结束证据推迟。标签契约0.4-label.3另写labels/v04-label-v3；独立QC v4另写qc/v04-label-qc-v4。旧v1/v2保留。

- confirmation_timeout仍从确认期限开始，迟到有效确认结束该时序违约，recovery_kind区分late_negative_confirmation和late_positive_confirmation，均不代表功率执行成功；重复/歧义确认不认领。
- feedback_missing从实际执行评估期限开始，当前事务收到有效1006及1001表示反馈交付恢复（recovery_kind=feedback）。execution_recovery_utc_ns另需对应已接受目标、电气功率达到min(target,p_avail)及实际收到功率一致的Oracle证据；缺证据保持空值。该字段不进入M2。
- 后续同站同IOA命令限定旧事务半开区间。旧事务未恢复时在superseded_at_utc_ns结束归属，recovery保持null且censored，不能由重复同值新命令代为恢复。观测结束仍未恢复时end保持null，不向此前窗口广播。
- 独立QC另从原件检查恢复时刻、类别、覆盖边界、截尾与证据引用，拒绝伪造的“执行恢复”和错误恢复时间；新鲜度恢复也须有相应实际接收事件。

A05固定c104 2.2.1公开接口仅验证延后正确认，已有API不能设置延后否定确认。已知配置在延后处理时拒绝的组合在配置校验阶段排除；P06两成员相同设备操作而改变成员处理前已恢复，仍支持。运行时若出现未预计实际拒绝继续明确失败，不伪造报文、不更换协议栈。

此为用户明确要求的缺陷修正与版本化字段增补，既往原始运行和历史失败结论不变。统计相似性尚不由配对QC v2判断，须后续独立冻结规则。
