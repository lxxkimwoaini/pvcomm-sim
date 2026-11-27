# V0.5.1 字段与兼容性补充

基础39个输入的单位及缺失语义继承[data-dictionary-v05.md](data-dictionary-v05.md)。无标签/场景/配置/站点身份进入输入，主站量来自同一IEC104链路，不是独立可信信息。原V0.5 policy只接受W60/5秒，V0.5.1 schema为0.5.1-dataset.1，只接受W10/1秒、W30/1秒、W60/5秒。

| 字段或产物 | V0.5.1 契约 |
|---|---|
| window_start/end_utc_ns | 实际半开[t-W,t)，warmup之后积累完整历史；使用实际业务结束上界 |
| sample_id | run、站、端点、W、step的SHA；仅关联键，跨尺度不得误认为同一训练行 |
| packet_rate | 实际窗口包数/W，单位packet/s；IAT只计算窗口内相邻包，空窗保留null统计及0计数 |
| available_at | P0 APDU/对象不早于重组必要帧最大到达；主站记录取事件与可用时间较晚者严格<t |
| label_at_end | t-1ns service_violation，主任务；不是故障运行整体标签 |
| observable_at_end | 各finding的在线可辨识起点到结束，独立于真实违约起点 |
| label_any / anomaly_overlap_ratio | 实际[t-W,t)内违约区间并集及覆盖比例，随尺度变化 |
| event_id / findings | 重叠/相接站点finding合并事件及全部原始finding/证据；共同run不是多个独立实验 |
| evaluation_status | evaluable、insufficient_history、no_sample_endpoint、not_online_observable_at_endpoint |
| raw_event_count / event_count | 全部原始合并站点事件数/具有实际合法采样端点事件数；原始数不因不可评估删除 |
| independent_violation_runs / independent_evaluable_runs | 发生违约/可评估违约的独立运行数，不能用站点展开数代替 |
| start_utc_ns / observable_at_utc_ns | 真实违约起点/首次finding在线起点；匹配按各finding自己的在线区间 |
| run_end_censored | 证据结束仍未恢复；漏检不会被赋予截尾标签 |
| recovery_censored | 合并事件内存在未证明恢复finding；被后继任务覆盖也保留此信息 |
| delay_s / delay_from_violation_s | 秒，分别相对在线起点/真实违约起点 |
| history_wait_s / sampling_wait_s | 秒，完整历史等待/首个有效端点采样等待 |
| detection_after_first_endpoint_s | 秒，首个有效端点后的检测等待；不从总延迟中隐式扣除 |
| normal_endpoint_exposure_station_hours | 正常端点前最多step的非重叠槽总站点小时；不是重叠W窗口之和 |
| duplicate_alert_episodes | 同一事件可匹配但未重复计检出的额外告警段 |
| evaluation_set | 原始分数中primary_three_site_test或ten_site_diagnostic，严禁混合宣称泛化 |

独立QC增加固定抽样39字段重算、完整APDU/对象可用时间核验、真实采样格点、原件哈希和重复原PCAP跨分区检查。模型和结果报告版本0.5.1-benchmark.1、QC 0.5.1-dataset-qc.1、特征metadata 0.5.1-features.1。旧schema及原发布保留，需旧行为时用旧policy且不加scale。

主站确认归属依赖日志task_id/connection_epoch，IEC104线上没有自定义task_id；重复同值迟到确认不能唯一关联时维持未知等待。反馈仅表示主站在当前命令之后收到满足值/品质条件的1001和1006，不能被解释为Oracle终端执行证明。未在历史主站点记录中存在的连接轮次或终端真值不得补造。
