# 开发数据字典

当前包含0.0工程配置、0.1协议开发证据和0.2物理开发证据，未发布正式数据集/标签/特征 schema。方案第 11–12 节保留为后续实现依据。0.0 JSON schema 位于 schemas/foundation.schema.json，由 Pydantic 生成；不是场景运行 schema 1.0。

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


0.2-power-evidence.2追加source_ghi_w_m2与optical_transmittance，区分历史小时均值和可选灰色层模型。新目录parquet-v02-power-v2、新QC v02-power-v2；此前v1开发证据/报告保留在原目录，可用其冻结代码核验，不覆盖或补造旧样本字段。
