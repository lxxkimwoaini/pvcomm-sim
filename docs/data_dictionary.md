# V0.0 数据字典

当前只有工程配置与结构化日志，未发布任何正式数据集/标签/特征 schema。方案第 11–12 节保留为后续实现依据。本次 JSON schema 位于 schemas/foundation.schema.json，由 Pydantic 生成；不是场景运行 schema 1.0。

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
