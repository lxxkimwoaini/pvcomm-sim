# V0.5 训练数据字典

契约`0.5-features.1`，60秒窗口、5秒步长，端点t仅消费available_at<t的P0与主站实际观测。M1累计包含M0，M2累计包含M0/M1；表中sample_id只是关联键，不能加入X。现场参数未校准，数据为真实协议驱动的物理仿真。

| 字段 | 单位与含义 | 缺失/边界 |
|---|---|---|
| up_packets / down_packets | 包；站点上行/主站下行计数 | 无通信为0 |
| up_bytes / down_bytes | byte；相应方向捕获包长之和 | 无通信为0 |
| payload_bytes | byte；双向TCP载荷之和 | 无通信为0 |
| packet_length_mean / packet_length_std | byte；包长均值/总体标准差 | 无包为null |
| iat_mean_s / iat_p95_s | 秒；相邻捕获包间隔均值/最近秩P95 | 少于2包为null |
| packet_rate | 包/秒；窗口包数除60 | 无通信为0 |
| retransmission_fraction | 比例；TShark标记的重传包占比 | 无包为0，受抓包位置影响 |
| burst_fraction | 比例；包间隔小于10ms的占比 | 无间隔为0 |
| active_span_s | 秒；末包与首包时间差 | 少于2包为0 |
| i_frames / s_frames / u_frames | 帧；实际IEC104 I/S/U帧数 | 无帧为0 |
| negative_confirmations | 次；协议否定确认数 | 合法拒绝不直接等于异常 |
| commands / interrogations / clock_syncs | 次；有功激活命令/总召唤激活/时钟同步 | 无报文为0 |
| telemetry_objects | 个；类型1/13/30/36的对象数 | 无对象为0 |
| invalid_quality_objects | 个；非零品质字段的对象数 | 是可观测品质统计，不是最终标签 |
| power_kw | kW；IOA1001最近已收有功 | 未收到或品质无效为null；陈旧保留值并携带年龄 |
| available_kw | kW；IOA1008最近已收可用功率 | 同链路遥测，不能当独立可信信息 |
| voltage_kv | kV；IOA1003最近已收电压 | 未收到或品质无效为null |
| power_age_s / available_age_s | 秒；游标距最近接收时间 | 缺测为null，陈旧不置零 |
| power_quality_known | 0/1；最近有功是否有有效数值 | 缺测/无效为0，实际零功率仍为1 |
| power_age_budget_ratio | 年龄/(2×遥测周期+1秒) | 缺测为null |
| fresh_point_coverage | 比例；1001/1002/1003/1008/2001中有效且未过期占比 | 明示覆盖率，不用真值补缺 |
| last_target_kw | kW；最后已知发送目标 | 从未发命令为null，不代表终端执行成功 |
| command_age_s | 秒；距最后已知命令发送 | 从未发命令为null |
| confirmation_wait_s | 秒；当前命令未关联确认的等待 | 无等待为0，超时后仍保留实际等待；迟到恢复须唯一关联 |
| execution_wait_s | 秒；已肯定确认但缺有效设定值/功率回送的等待 | 不以Oracle判断执行，合法否定确认不进入等待 |
| known_maintenance_active | 0/1；提前实际可用的授权维护计划是否生效 | 不读故障注入计划 |
| maintenance_recovery_remaining_s | 秒；已知维护/恢复窗口剩余 | 无窗口为0 |
| peer_fresh_power_coverage | 比例；其他站有效且新鲜的有功覆盖 | 站点同一游标，不使用未来样本 |
| peer_mean_power_kw / peer_mean_power_age_s | kW/秒；上述有效站均值 | 无有效站为null；并非独立气象真值 |

## 标签及索引

| 表/字段 | 定义 |
|---|---|
| labels.label_at_end | t-1ns的service_violation，0/1；未知为null |
| labels.observable_at_end | t-1ns是否已到独立finding的在线可辨识时刻，与实际违约起点分别保存 |
| labels.label_any | [t-60s,t)中是否存在业务违约 |
| labels.anomaly_overlap_ratio | 该窗口违约区间并集占比，重叠finding不重复计时 |
| labels.finding_ids | 原始逐站正式标签finding_id，借runs.jsonl的label_path定位证据；禁止加入X |
| labels.label_status | valid/uncertain；不确定和无效不作为正常负例 |
| index | run_id、station_id、真实UTC纳秒窗口、partition、group_id、latest_input_available_at；只管理样本，不加入X |
| rules.business_rule | 独立M2规则输出，只作比较基线，禁止加入X |

原始标签通过有效运行索引保留fault_applied、transport_impact、service_violation、observable_anomaly、恢复和截尾、scope及逐事件证据。训练末端标签不能代替这些分层事实，不能把整个异常运行广播成阳性。0功率、缺测、未知和过期各自保留。

训练预处理：中位数填补并附缺失指示，全部空列保留；标准化只拟合训练分区。IF/EWMA仅拟合训练正常行，RF使用训练监督标签。特征表中的null在读取NumPy时变为NaN，未预处理前不应直接丢掉缺测行。

同运行、同pair、同来源和天气原始日期的传递连通组先划分；不随机拆窗口。模型种子标准差只表示训练随机性，不是独立实验置信区间。原始PCAP/Oracle只供审计，不可直接拼入X。
