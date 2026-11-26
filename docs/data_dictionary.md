# 开发数据字典

当前包含0.0工程配置、0.1协议证据、0.2/0.3物理及多站证据、0.4开发场景/配对/分层标签与队列契约；未发布正式科研数据集或训练特征。标签0.4-label.3已实现，完整阶段门禁以进度/报告为准。0.0 JSON schema 位于 schemas/foundation.schema.json，由 Pydantic 生成；不是场景运行 schema 1.0。

## Oracle实验请求与队列投影

`request_version=v04-experiment-request-v1`保存受限模板参数、创建UTC、唯一experiment_id及规范化matrix_sha256；所有信息仅用于Oracle管理。模板限定N01～N10、A01/A02/A04/A05/A06/A08及四种既有三站配对；site_count为3/10/30、variant为0/1/2、seed显式记录。strength必须采用既定档位，A02单位毫秒、A04/A08周期，其余秒。站点/区域目标需在配置中实际存在；正常及配对模板不接受任意业务目标覆盖。

新队列summary.json是manifest的紧凑原子投影：保留任务/尝试状态、结果、覆盖、来源和预算，移除artifacts清单，防止轮询重复加载大文件索引。详细manifest及原件不删除。Web读取summary而非从Oracle给M2补数据。质量摘要保留passed/null和实际版本；标签HTTP分页将*_ns及available_at转为十进制字符串，保留false、null和恢复截尾语义。

`v04-batch-audit-v1`为独立离线终态汇总，先验证矩阵及留存产物哈希，再逐成员统计完整质量、合法正常负例及配对对照/相似数。失败配对中已通过独立QC的正常成员单独列出，不充当有效配对，也不自动扩大队列首次有效任务覆盖额度。未完成队列拒绝终态审计，输出须为全新外部目录，不回写原件。

| 配置字段 | 类型/单位 | 来源与语义 |
|---|---|---|
| schema_version / purpose | 固定 0.0 / foundation_validation | 仅配置验证，不可执行场景 |
| parameter_basis | simulation_assumption | 现场尚未核实 |
| run_id | 安全标识符字符串 | 预留标识；不能据此认为运行已创建，不供模型输入 |
| seed | uint32 | 可记录的实验种子；本阶段不调用随机过程 |
| clock | realtime / speed=1；duration_s、warmup_s（s） | 预热小于总时长；电气/潮流步长 1/5 s，方案仿真假设 |
| regions / feeders | 非空唯一字符串数组 | 通信区域与电气馈线分别定义 |
| device_profiles | 唯一 profile_id、telemetry_period_s、ack_deadline_s | 遥测 2/5/10 s；正数确认期限；无默认放宽 |
| stations | 唯一 station_id 和 common_address | 区域/馈线/模板引用必须存在 |
| capacity_kw / rated_kva | 正有限数，kW / kVA | S≥P；示例为 30/100/300 kW、33/110/330 kVA；不是实测 |

所有对象拒绝未知字段、重复 YAML 键、非有限值和无效引用。配置深度≤32、节点≤4096、UTF-8 大小≤128 KiB；不支持 YAML 别名，避免构造阶段展开绕过限制。无效 Unicode 返回校验错误。配置冻结后以排序键、紧凑 JSON、UTF-8 计算 SHA256；不受注释、空格和 YAML 键顺序影响。更改任何有效参数会改变哈希。哈希仅用于内容一致性，不表示实验已执行。

日志字段为 run_id（无运行时 null）、module、level、ts_event（UTC ISO8601）、sim_elapsed_ms（不适用时 null）、event、message、error（无异常时 null）。error 保留 traceback。每次由调用者提供输出流，未来运行由编排器分配独立日志路径。

ClockOrigin 的 ts_utc_ns / ts_mono_ns 为同宿主映射，mapping_uncertainty_ns 是读取 UTC 前后单调时钟夹逼的半宽，**不等于已经测得的跨进程/抓包对齐误差**。PCAP 纳秒字段精度、设备时标、source_weather_ts 与 available_at 将在后续采集契约中另行实现；不以当前 clock 探针宣称 P99≤100 ms。

## V0.1 开发证据契约修订

`schemas/v01-lab.schema.json` 由 `config/lab.py` 生成。版本 0.1 的 YAML 保持既有字段；增加可选 `reactive_kvar=0.0` 和 `voltage_kv=0.4`，明确为协议测试固定值，非潮流结果。3 个站点 ID/CA/回环端口均唯一，遥测周期严格为整数 2/5/10 s，拒绝重复键、别名及超限文件。容量、固定可用功率和斜率模型仅用于闭环联调，不能作为有气象物理依据的正式光伏实验。

开发证据修正版把主站事件与原始回调写入 `observed/master_*.jsonl`，终端回调/事件和状态写入 `truth/`。所有记录含 run_id、唯一 event_id、ts_event_utc_ns（整数）、ts_mono_ns、sim_elapsed_ms；仅主站记录提供 available_at。回调时间不是抓包时间，不命名为 ts_capture。命令创建/发送、确认返回和真实功率反馈分开记录；positive_confirmation 不表示功率达到目标。

`parsed/tshark-pdml-v2/packets.jsonl` 保留包括握手、ACK、重传在内的网络包；`iec104_apdu.jsonl` 独立保留每个 I/S/U APDU；`iec104_objects.jsonl` 按 ASDU 内对象展开。APDU 包含 source_frame_refs、available_at、ca/oa/type_id/cot/negative_confirm/test_flag/sq；对象包含 ioa、value_num/value_status、quality_raw、可用时的 ts_device。UTC 以 Decimal 转换为整数纳秒，不经浮点数。TCP 重组引用的最大抓包时间限制 available_at。v2 与此前平铺文件不混用，旧产物保留，未迁移为正式 Parquet schema。

manifest 区分 `completed` 与 `qc_status=pending`，保存代码 SHA/dirty 标记、配置哈希、时钟映射和采集结果。独立端点模式的 captures 包含 P0 和每站 P1 的 SHA256、tcpdump 返回码、捕获计数及内核丢包数。内核丢包为零不单独证明业务交付率或全链路无丢包。

事件 ID 包含记录器端点角色、事件流及局部序号，避免各 RTU 独立进程从 1 计数导致跨文件重复；联合主键为 run_id/event_id。抓包使用 16384 KiB 缓冲区，实际值写入捕获统计 `buffer_size_kib`；不能以缓冲区配置代替实际丢包统计。

## V0.1 验收证据与 Parquet

实验配置增加 `topology=isolated` 与可选 `interruption`（station_id/start_s/duration_s/planned）。同端口只有独立端点模式可用，中断目标必须存在、在解除限发后开始且留出恢复观测。计划维护必须在实际中断前进入主站可观测事件；控制器的施加记录只进入 truth。

`parsed/parquet-v01/` 保存 packets、iec104_apdu、iec104_objects、master_events、terminal_events、electrical_state 六表，模式 `0.1-protocol-evidence`。机器契约为 schemas/v01-parquet.contract.json；packet/APDU/object 的主键和重组引用保持 JSONL，整数时间使用 int64/ns/UTC，帧引用为 list<int64>，工程单位写入 schema 元数据。事件表公共字段有类型，其他字段保存在 payload_json；Oracle 表不能作为 M2 读取来源。校验必需列非空、metadata、行数、源与产物 SHA256，源路径用 POSIX 相对路径以跨 Windows/WSL 核验。

`qc/v01-evidence-v1/report.json` 与原 manifest 分离，不改写原始 qc_status=pending。报告含完整 APDU 分母、解析对应、回调映射 P99、双端字节、期限内确认、实际功率和恢复证据。所有层次标签仅为 development_audit_only 的首周中断审计，不属于 V0.4 冻结标签、模型特征或正式划分。业务关闭尾部另列，完整帧不从解析分母删除，详见 ADR 0003。

## V0.2开发扩展

`schemas/v02-power.schema.json`独立配置；`schemas/v02-power-parquet.contract.json`为Oracle专属、非空显式Arrow字段。电气字段P正发电kW、Q正注入kvar，S为kVA；电压三相线电压kV、电流平衡近似A。source_weather_ts为原始背景推进时刻，source_interval_start为实际小时均值区间起点，model_mono_ns区分记录时间。1s电气状态不意味着1s气象实测。running/connected/faulted、command_count、constraint_reason保存约束与接受证据。潮流保留injection_mono_ns/solved_mono_ns及converged，不能把旧电压视为当前求解。

实验IOA扩展：1004电流A、1005 GHI W/m²、1008可用AC功率kW，均M_ME_NC_1。1006目标是接受设定值，不是实际出力；所有M2曲线仅投影master_observed_point并按available_at截断。新真值表不出现在API中，旧六表不原地修改。命令新增execution_deadline_ms（主站依据当前已收P、配置斜率、2个遥测周期计算，限制10–60s）；task_id是本地应用序列，不在IEC报文中。


0.2-power-evidence.2追加source_ghi_w_m2与optical_transmittance，区分历史小时均值和可选灰色层模型。新目录parquet-v02-power-v2、早期验收QC v02-power-v5（时间精度修正，旧版报告保留；当前版本见后文）；此前v1开发证据/报告保留在原目录，可用其冻结代码核验，不覆盖或补造旧样本字段。

## V0.3 开发点表增量（配置contract_revision=2）

pvcomm-v03继承既有点表，新增M_ME_NC_1 IOA1009=站点入线loading_percent（%），1010=变压器loading_percent（%），1011=入线首端p_from（kW，上游→下游为正）。只有实际到达主站的遥测进入M2，不能按Oracle补值。原0.1/0.2 Arrow表保持版本和字段；扩展数值仍进入统一协议对象表，馈线诊断原始事件位于truth，不能作为默认观测。场景scene.json和0.3 sim_elapsed_ms不包含初始化时间，UTC/monotonic映射独立保留。

兼容性标识pandapower_three_site_v02及历史QC项three_station_business_loop保留名称，0.3实际站数和馈线成员由配置确定。Health.stage=V0.3-development是API软件能力元数据，不是实验标签或数据发布版本；一小时冻结29aa7e0仍保存其运行时历史Health字符串，不篡改旧证据。正式验收结论见对应报告。

V0.3开发期间的历史核验输出包括qc/v03-power-v4（V0.3）、qc/v02-power-v6（V0.2），内部协议版本v01-evidence-v4；当前版本见下文V0.4a兼容性说明。旧版本目录与失败证据不改写。TCP后序完整APDU在前序缺口填补后才可交付，source_frame_refs包含该依赖帧，available_at不早于其时间；packet_index仍参与与TShark的严格对账，详ADR0009。


## V0.4a 开发兼容性

当前Health.stage=V0.4-development、包0.4.0.dev0，未重标历史运行。六张协议Arrow和0.2-power-evidence.2保持字段及版本；新写出电力Parquet采用zstd，读回逐值/元数据核验，与原JSONL投影比对可发现等行数且重算产物哈希的篡改。

协议QC更新为v01-evidence-v5，电力QC为v02-power-v7/v03-power-v5，新目录保留旧报告。命令审计拒绝期限前超时、无效品质反馈及重复证据；实际确认与应用回调矛盾时保留incomplete_or_ambiguous。新实验质量报告v04-experiment-quality-v1不要求正常业务全部成功，但不豁免工具丢包、解析、对齐、来源或物理计算失败；不是正式分层标签schema。

master_command_sent追加sent_mono_ns/sent_utc_ns/connection_epoch；master_command_confirmation保存实际raw ACT_CON、接收单调时刻、匹配结果及task_id。master_command_response的positive_confirmation可空，outcome与stack_result分离。master_command_not_sent用于连接不可用/隔离，不能伪造为已发命令。终端接受事件仍仅由实际协议回调产生。master_dispatch_allocated增加排除原因、eligible_station_count和degradation_policy，排除不代表可用功率为0。

schemas/v04-weather-source.schema.json为独立来源契约；旧WeatherConfig不改变。schemas/v04-retention.schema.json提供full_evidence/verified_archive/derived_rebuildable保留等级，delete_sources固定false。预算采用V0.3实测1,383,250,188 B/h，720h×1.5=1,493,910,203,040 B；备份另计，已知单PDML 11,275,084,964 B仅为临时空间下界，未替代峰值和并发测试。


## V0.4b显式调度契约

`schemas/v04-scenario.schema.json`版本0.4-scenario.1组合V03Config，business_schedule为必需数组，空数组没有远方控制。schedule_id、at_s、jitter_s、station_ids及kind= setpoint/dispatch/interrogation定义已知业务；setpoint明确targets_kw，dispatch明确total_kw/allocation/批次。全抖动区间内同站保守60s窗口不重叠。底层legacy command_after/release字段仅为旧契约兼容，新入口不执行它们。

truth/scenario保存source.yaml、effective.json、expanded-plan.json；manifest.scenario_contract保存源文件SHA256、规范化有效配置/计划SHA256和Oracle范围。source_group_id、pair_id和normal_scene只在该诊断区域，不投影到M2。主站仅在任务到期记录master_schedule_started/finished和实际命令或master_interrogation_requested/result；available_at仍按实际写入时刻。未知未来业务不提前发布。

master_dispatch_allocated附加observed_available_kw（容量分配为null），记录真正消费的主站链路观测，不是可信独立M3气象/电气信息。调度QC验证这些值在分配前确已接收且未过期；不读取Oracle填补主站缺失值。六表Arrow字段不改，附加信息仍保存在已版本化payload_json中。

qc/v04-normal-schedule-v2仅为Oracle正常场景验收；command_assessments保留accepted_complete/rejected/timeout等独立证据，不生成正式异常标签。旧profile及QC目录均不变。

## V0.4c A01开发契约

`schemas/v04-fault.schema.json`版本`0.4-fault.1`组合既有正常背景与单个A01事件。fault字段为event_id、fault_type=A01、逻辑start_s、duration_s=5/20/60及互斥scope；station允许站点子集，region指定单个配置区域，global使用全部区域。未知引用、重复站点、混合范围和不足预热/恢复窗均拒绝。此旧版本仅接受A01；新增A02的`0.4-fault.2`契约见下文，A04/A05/A06/A08须使用下文0.4-fault.3契约。

`truth/fault/source.yaml/effective.json/events.jsonl`均为Oracle。manifest.fault_contract保存版本、源/有效配置SHA256、结束时事件文件SHA256及Oracle范围。背景仍独立保存truth/scenario，主站执行背景到期业务，不读取故障计划。既有六表Arrow和电力契约不变。

事件主键event_id与故障身份fault_event_id分别记录；所有操作有run_id、ts_mono_ns、映射ts_event_utc_ns、sim_elapsed_ms。apply_requested/apply_verified保存实际端口基线与disabled读回；restore_requested/restore_verified保存撤销原因及forwarding读回；fault_active与fault_finished保存整体阶段。apply_failed/restore_failed及部分结果保留，finally只恢复已触及的自有端口。状态作用时刻在请求与成功读回之间，不把请求时刻或配置时刻冒充精确生效点。

`qc/v04-a01-mechanism-v2`独立核对配置/哈希/事件身份/时间/端口/持续时间/撤销及原实验质量；另给P0/P1实际测量交付、物理更新与主站恢复观测事实。v1原报告保留。`verified_blocked_interval_utc_ns`是实际全部端口施加后至首个撤销请求的半开区间；其中真实P1发送按方向/地址/端口/完整APDU与P0逐条独占匹配，未在撤销前交付者记录`withheld_measurement_apdus`及`withheld_evidence`中的双端帧索引/available_at，允许TCP背压后不再产生新发送。没有实际发送不算withheld。排除最初1秒在途包的`diagnostic_interval_utc_ns`仍用于统计P0交付缺口；两种区间均不定义在线异常起点、新鲜度门槛或service_violation标签。注入成功与实际影响分开报告，label_quality仍not_evaluated，不能将机制核验通过当正式标签或完整V0.4c验收。

后续`v04-a01-mechanism-v3`新增Oracle `socket_audit`契约`0.4-socket-audit.1`，记录固定strace版本、角色到worker_pid/tracer_pid映射、completed及文件path/sha256清单。原始系统调用仅存`truth/socket_audit/<role>.<tid>`；禁止作为M0/M1/M2/M3输入。独立核验逐文件哈希/清单、worker主文件、完整退出、解析能力及每端TCP发送覆盖。`socket_evidence`只解析实际文本，不生成网络帧。窄范围TESTFR写入尝试核算规则见ADR0017；strict_normal_comparison保留旧检查，normal_regression不改，其他质量门槛不变。缺少新内核证据的旧失败不能经v3强行放行。`recovery_event/recovery_utc_ns/recovery_after_restoration_s`仅表示撤销后首次有效主站有功观测，可能仍为排队旧报文，不等同于最新状态或完整恢复标签。

socket_audit文件项另保留`trace_exit_code`；completed仅表示跟踪有完整退出记录，不能代替worker成功。显式取消时现有主站可能以InterruptedError退出1，该退出码如实保存；completed正常运行的QC仍要求每条退出记录及元数据均为0，取消/部分运行不进入parse或正式质量通过计数。

`v04-a01-mechanism-v4`保持v3核验规则，从实际加载模块的仓库记录`evaluation_code_version/evaluation_code_dirty`，而非调用者工作目录；dirty核验不能通过。`run_code_version`继续取原始manifest，两者可以不同。旧v1/v2/v3报告不可覆盖。A01开发验收另保留`a01-background-schedule.json`，复用正常调度的逐事务证据核验恢复后总召唤及未受影响站业务；它是Oracle诊断报告，不是正式恢复或业务异常标签。

## V0.4c A02增量契约

v04-fault.schema.json新增0.4-fault.2与LinkDelay（A02）：start_s、duration_s（开发作用时长）、additional_delay_ms严格取200/1000/3000、direction=master_to_rtu/rtu_to_master及原scope。旧0.4-fault.1 A01保持有效；A04/A05/A06/A08须使用下文0.4-fault.3契约。每个选定方向必须有显式NormalLink基线。

复用truth/fault及manifest哈希，A02操作before/after保存namespace/interface和完整tc JSON actual（options/handle/qlen/drops/backlog等）。queue_drained保留恢复参数后真实排空读回；取消的fault_finished含recovery_censored=true，不当作成功恢复。诊断证据不进入原六表的观测白名单。

qc/v04-a02-mechanism-v1分别给出configuration/operations、原experiment_quality、无未解释双端丢失、双向三阶段实测分布及逐APDU双端帧索引/available_at、恢复GI、物理持续更新和label_quality=not_evaluated。重复内容无法唯一归属时列ambiguous，缺失列unmatched，不填0；每端TCP重组去重重传，时延含TCP有序交付等待，不能当作独立单包传播时延或正式业务标签。evaluation_code_version/dirty从加载模块仓库查询，dirty不能通过。

A02 v2另记录manifest.network.delay_segments（配置端点、内部namespace/双向出口、透明桥及实际ip link读回）。原链路双向基线只迁移一次，不叠加；A01与默认正常网络不启用该内部段。v04-a02-mechanism-v2拒绝将源端AF_PACKET时间之后的qdisc视为双端可测时延，原v1报告保留。

## V0.4c 终端机制增量契约

0.4-fault.3新增TerminalFault（A04/A05/A06/A08），保留旧A01/A02配置。强度字段分别为missing_cycles=2/5/12、response_delay_s=2/5/15、feedback_duration_s=10/30/60、recovery_cycles=1/3/5，拒绝混用及非整数。A04各站持续自身周期数，外层duration_s为最大值；A06从接受触发保持，A08沿用15秒恢复观察周期且首片只接受与计划维护相同的单站。

truth/fault/<station>.state.json保存有界最新状态；terminal_fault_applied/feedback_hold/restored记录原始事件、单调时间、受抑制IOA和实际注册点；truth/fault/control.json仅允许本次事件restore。主站不加载这些文件。v04-terminal-mechanism-v2为独立机制核验，label_quality仍not_evaluated，正式标签另在V0.4d实现。


## V0.4d配对与正式标签开发契约

`v04-pair.schema.json`为0.4-pair.1；pair_type=P03/P04/P06/P03_recovery，pair_id/source_group_id供Oracle分组，不能作为模型输入。normal/changed分别引用正常场景和单故障。共享条件校验仅排除run_id、诊断normal_scene，P03另允许维护授权字段差异。天气、设备、网络、种子、初态、业务计划不得排除。实际run_id使用独立UUID，不编码成员类别。业务起点使用已有场景时钟，不能以进程启动时间代替。

`v04-label.schema.json`为0.4-label.1，输出`labels/v04-label-v1/event_labels.jsonl`及Zstandard Parquet（Oracle）。每站记录run_id、anomaly_id、station_id、region_id、scope、fault_applied/fault_type、实际施加区间、transport_impact、service_violation、observable_anomaly、operating_context、label_status、findings、evidence、M2观测模式与独立信息不可用标志。未知保留null，invalid不作正常负例。

findings记录命令/接收新鲜度约束，含task_id或IOA、实际outcome、独立违约布尔、UTC纳秒半开区间、observable_at_utc_ns、recovery_utc_ns和recovery_censored。确认依据实际3秒期限，反馈沿用每条命令execution_deadline_ms。功率未达到目标但符合可用功率限制不等于反馈异常；消息已交付而物理执行不符时为execution_result_uncertain，不能直接强标通信缺失。新鲜度按主站既有2周期+1秒的有效接收政策，维护按提前发布计划加15秒恢复宽限；不声称无时标数据的设备采样年龄。

evidence包含run相对路径、SHA256、原event_id或apdu_id:object_index、tier、available_at及online/retrospective用途。在线引用不能来自Oracle或晚于可判定点；恢复/事后设备证据不回填在线输入。manifest记录原PCAP、原事件、配置、机制/正常QC哈希及标签计算代码归属。Arrow显式run_id/station_id/service_violation/label_status索引及完整嵌套JSON负载逐值读回。独立qc/v04-label-qc-v1重新核对外键、覆盖、期限、维护、新鲜度及业务证据，不调用标签生成器自证。

独立配对QC记录共享条件、起点、成员与标签质量，并报告固定上下行包数/字节数、窗口及活动持续时间、到达间隔均值/P50/P95及相对距离。contrast_observed与配对条件有效性分开，不伪造相似度或修改报文。该首片不声称完成统计匹配、正式划分或40次/类覆盖。

### 标签0.4-label.2增量

当前label入口写labels/v04-label-v2，独立核验写qc/v04-label-qc-v3。v2原核验报告继续保留。历史v1输出继续通过其冻结代码复核，两个版本不互相覆盖。A06故障区间从实际feedback_hold起算。communication_impact及其start/end为Oracle机制证据覆盖的通信影响诊断区间，不能代替业务违约起点。A05使用真实延后处理事件的等待区间，A01/A02使用既有双端核验区间。transport_impact在缺乏传输层证明时保留null。

recovery_complete_utc_ns为撤销后新GI成功结束且包含1001/1002/1003/1006/1008/2001实际测量对象的完成时间。缺少完整证据时为null并保留recovery_censored=true。正常成员未施加故障时该恢复字段不适用，也为空。业务findings仍独立记录各约束的恢复与截尾。所有增量仅供Oracle标签核验。


## 配对QC v2及标签v3修正

配对报告新增`data_quality_passed`、`business_contrast.stations`、`targets_passed`和`controls_passed`；`passed`同时要求数据质量及逐站预期对照。正常/改变身份、目标及控制站集合须完整，非valid或null不作正常负例。

标签契约`0.4-label.3`另写`labels/v04-label-v3`，独立QC为`v04-label-qc-v4`。命令finding新增`recovery_kind`（late_positive_confirmation/late_negative_confirmation/feedback/null）、`execution_recovery_utc_ns`（Oracle物理执行旁证，缺证据为空）、`superseded_at_utc_ns`（后续同站同IOA任务覆盖边界）。迟到确认只恢复确认交付，1001/1006只恢复反馈交付，均不得直接推断功率执行成功。未恢复事件保持截尾；被后续任务覆盖时end等于覆盖时刻且recovery为空。版本2文件和历史QC不可覆盖；默认M2接口不新增任何标签字段。

## 分组维护、统计匹配和有限队列

场景0.4-scenario.2新增maintenance_windows：maintenance_id、scope=stations/region、显式station_ids、可选region_id、start_s/duration_s；固定15秒恢复期限沿用原政策。区域成员必须完整，每站当前限一个窗口。旧.1序列化不新增字段，不改变旧规范哈希。主站提前发布逐站合法维护计划，不加载故障目标。分组维护QC写normal-schedule-v3/terminal-mechanism-v3；旧单站继续使用v2。

authorized_maintenance.stations逐站区分connection_recovered_utc_ns、protocol_recovered_utc_ns、business_recovered_utc_ns、recovery_deadline_utc_ns及business_recovered_within_deadline，保留原事件和APDU引用。连接及GI不代替1001/1006有效接收，业务接收也不代替物理命令执行。无故障站的顶层recovery_complete_utc_ns为空及recovery_censored=true表示该故障恢复字段不适用，不能解释为正常业务失败；其维护恢复按上述专用证据及业务finding核验。

相似性0.4-similarity.1及0.4-similarity-calibration.1固定P0特征、窗口、距离和local/global逐项阈值。v04-traffic-similarity-v1记录evaluation_valid、business_contrast、matched、hard_pair_selected、特征和失败原因；matched=false不使有效实验变invalid。报告仅供Oracle离线配对子集选择，不进入M2在线特征。

0.4-batch.1包含matrix_id、normal/fault/pair任务、最多3次尝试、固定串行并发、RetentionPolicy、容量限额和可选冻结matching输入。manifest记录矩阵/代码指纹、每次尝试状态、产物路径/字节/哈希及覆盖计数。实际run_id/pair_id为UUID，source_group_id保持历史来源；一次任务的首次有效尝试贡献运行覆盖，不以站点或窗口膨胀数量。中断的实时协议尝试不从中间续传，保留并消耗预算。所有队列配置及结果均为Oracle管理数据，当前M2 API不读取这些字段。
