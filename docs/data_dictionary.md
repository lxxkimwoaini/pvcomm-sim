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
