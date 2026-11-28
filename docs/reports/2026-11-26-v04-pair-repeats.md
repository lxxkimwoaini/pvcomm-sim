# V0.4 固定12组配对复验与终态独立审计

本轮预登记矩阵原字节SHA256为`2deeb091644a9675a5a4628f2350173f9d6dccfd27f23a9c11026d4ac236851f`，规范化矩阵SHA为`3282d35ef50ab94ff4512f3a5989854d7ee03c2c87e6ddf09c0949441a254e4b`。运行源冻结`8519edb8858b57b02cda000db4476dd1f95cced0`，输出`/var/lib/pvcomm/runs/v04d_pair_repeats_s1`。种子6000～6032及开发历史日期在运行前冻结，未按匹配率重试或调整阈值。

实际命令为`PVCOMM_ALLOW_LAB=1 PYTHONPATH=src /mnt/e/project/pv/.venv-wsl/bin/python scripts/run_v04_batch.py --matrix configs/v0.4-pair-repeat-matrix.json --output /var/lib/pvcomm/runs/v04d_pair_repeats_s1`。队列状态`completed_with_failures`，退出1，墙钟1:13:32；不是全队列通过。资源原件为同根`.resources`；GNU time maxRSS421,228KiB为进程树所报告单进程峰值口径，不冒充同时驻留内存总和。当时有小规模工作台复验/归档负载，本次不是隔离吞吐测量。

| 配对类型 | 尝试对数 | 有效配对 | 业务对照成立 | 统计匹配 | 独立有效正常成员 | 独立有效改变成员 |
|---|---:|---:|---:|---:|---:|---:|
| P03 | 3 | 1 | 1 | 0 | 3 | 1 |
| P04 | 3 | 2 | 2 | 0 | 2 | 2 |
| P06 | 3 | 3 | 3 | 0 | 3 | 3 |
| 维护恢复失败（P03_recovery） | 3 | 3 | 3 | 0 | 3 | 3 |
| 合计 | 12 | 9 | 9 | 0 | 11 | 9 |

实际启动23个不同run_id：20个成员质量有效、3个无效/不完整；trial_05正常成员失败，改变成员没有启动。有效配对内18次运行与失败配对内2次有效正常运行分别计数，未把单边有效计成有效配对或异常覆盖。11个正常成员均为valid且service_violation=false；invalid、uncertain、null均未当成负例。

独立只读审计使用另一个clean冻结源`53a2f58f6f8d14728b7fb1eeb1e0856ed55da910`：`PYTHONPATH=src /mnt/e/project/pv/.venv-wsl/bin/python scripts/audit_v04_batch.py --batch /var/lib/pvcomm/runs/v04d_pair_repeats_s1 --output /var/lib/pvcomm/runs/v04d_pair_repeats_s1_audit`。退出0，墙钟1.32秒，maxRSS44,428KiB。审计原件`report.json`记录全部尝试、成员、错误及指纹；原manifest SHA为`776dd3a2f791523fccf8da3104d70754ebb8173d427dc1030a8c299224cbb72d`。终态矩阵、manifest及每项已有产物校验和核验通过，没有回写旧文件。审计执行器WSL普通403 passed、66 deselected（21.98s）。

20个有效成员共有60个valid站点标签、80份原始捕获，工具报告drop均0；QC内确认完整APDU共37,671/37,671，逐运行日志对齐P99的最大值17.141474ms。此计数仅覆盖独立质量有效成员。队列全部文件（含失败和临时解析保留原件）的当次实测留存236,212,902B，不以此推断30站或720小时峰值。

三项失败保持原状：

- trial_01改变成员`run_dc7aa237201d42fcbf9d2bc9d4c756a8`：A01 v4数据质量不通过，含恢复边界外未解决的TESTFR发送尝试，以及终端应用发送回调与P1不对应；完整解析率通过不等于完整证据质量通过。
- trial_02改变成员`run_100e5242bc6a480fa8e727593a80e8f6`：TESTFR受原内核证据条件核验，应用发送回调与P1仍不对应，独立质量不通过。未扩大TESTFR例外至应用帧。
- trial_05正常成员`run_5cd4ddf09afa42eca0b77e447fcb2f3b`：finalization报`worker 15708 required kill`；端点stderr为空，根因尚未确立。未延长关闭期限或把清理失败记为正常完成。

9个有效对照全部在固定局部及全局规则下不匹配，完整逐项距离和失败特征保存在各自`qc/v04-traffic-similarity-v1/report.json`。如trial_07 P06仅全局下行IAT P50及目标站上行IAT P95越过冻结阈值，也仍不选择。其余正常通信不能稀释目标站差异。独立开发校准文件SHA仍为`276c634a7c9664208e0a9228a5778f87c8d1a78482021f85623b97b31c15e96d`；没有依据本轮结果调整规则、增补报文或修改时间。9个未匹配有效对照全部保留，不能称为困难相似配对子集已形成。

V0.4仍未整体验收。十站六类队列已在配对结束后按预定驱动启动；三十站仅在其完整通过及资源采样有效后启动。每类40次独立有效覆盖、剩余工作台功能及并发吞吐仍待完成，未进入V0.5。
