# API 与 CLI（0.4.0.dev0）

启动：`uv run uvicorn pvcomm.web.app:create_app --factory --host 127.0.0.1 --port 8000`。
OpenAPI 位于 `/openapi.json`，交互文档 `/docs`。服务仅用于本机科研开发；M2运行根及独立Oracle实验根由服务端指定。Oracle可提交受限模板请求，网络操作由独立授权Linux执行器处理。

| 接口 | 输入 | 实际响应 |
|---|---|---|
| GET /api/health | 无 | status=ok、stage=V0.4-development、simulation_available=true；web_run_submission_available按实验根是否显式配置返回布尔值 |
| POST /api/scenarios/validate | JSON 对象 `{ "yaml_text": "..." }` | valid、schema_version、config_sha256、station_count；非法配置 HTTP 422 |

校验接口只支持 schema 0.0 工程契约。CLI 与 API 调用同一 config.service；配置例子为 configs/foundation.yaml。输入 UTF-8 大小、深度、展开节点均受限；校验输入不写磁盘。M2查询不返回真值、标签或场景配置；显式Oracle入口见下文。尚无WS；隔离实验仅由受控本地执行器执行。

生成：`uv run python scripts/generate_schemas.py`，然后 `pnpm --dir frontend types:generate`。检查：`uv run python scripts/dev.py schemas`。frontend/src/types/api.generated.ts 为自动生成文件，不能手改。跨字段引用校验以共享后端服务为准，JSON schema 不表达全部关系规则。

CLI `pvcomm --version` 和 `validate-config --config <path>` 已实现。成功返回0，配置/参数错误返回2；run/parse/qc及Oracle label已实现，split/features/benchmark仍返回3（not implemented）。校验命令不创建运行或输出数据；运行和派生命令按指定路径创建实际产物。

## V0.4 独立Oracle实验管理

全部使用`/api/diagnostics/experiments`前缀，scope固定`Oracle_experiment_management_never_M2_features`，不进入M2接口或特征。服务显式设置PVCOMM_EXPERIMENT_ROOT才允许提交；成对配置PVCOMM_SIMILARITY_POLICY和PVCOMM_SIMILARITY_CALIBRATION后开放配对。文件路径仅由服务启动环境提供，不由HTTP输入选择。

| 接口 | 实际功能 |
|---|---|
| GET /catalog | 模板、既定强度、站点规模、提交能力；不伪造执行能力 |
| POST 空后缀 | 受限ExperimentRequest；成功201仅表示已写队列；跨源Origin拒绝403，未启用503，非法参数422 |
| GET 空后缀 | offset/limit分页（最多50）；紧凑状态及覆盖，不读取完整产物哈希清单 |
| GET /{identity} | 单请求与当前summary.json；尚未执行时queue=null |
| GET /{identity}/quality | 实际已存在运行的QC摘要；缺失运行不补造报告 |
| GET /{identity}/runs/{opaque_run_key}/labels | 分页分层标签、时间和原件证据引用；纳秒为字符串；只用于事后诊断 |

网页不启动任何命令或网络操作。`scripts/run_v04_requests.py --root <预设目录> --maximum-jobs N`在授权Linux读取原子请求、核对矩阵哈希、按提交时间消费；全局SQLite锁阻止双执行器，复用run_batch的冻结代码、有限尝试及续跑。`--resume`不重复完成任务；实时协议尝试无法从中间续传。当前最大并发1；并发生产吞吐未通过。

新批次维护不含artifacts的summary.json供轮询，详细manifest和全部原件保留。模板、范围及强度由后端现有生成器和Pydantic校验；配对目前仅三站既有共享条件模板。质量、合法拒绝、资源不足、故障施加及业务违约分别保留。完整曲线配对、协议事件定位与证据导出待后续切片，不能据此宣称V0.4整体验收。

## V0.1 M2 只读接口

`PVCOMM_RUN_ROOT` 为服务端预设父目录（默认 outputs）。`GET /api/runs` 返回独立端点运行的 opaque run_key/status/source，不返回文件名或场景定义。`GET /api/runs/{run_key}/snapshot?at_ns=<整数纳秒>&mode=M2` 返回配置站点白名单、最新主站功率和连接状态、每条测量最多 600 个原始观测组成的显示窗口、最近 100 条主站事件及 P0 APDU。多站显示进一步缩减至最多 120 点；降采样保留首尾、极值和缺口，不改变原始证据。只接受 M2；Oracle 返回 422；未知运行 404，损坏完整记录 409 并写服务端结构化错误日志。实时写入的最后一个未结束行暂不读取。

所有事件和 APDU 必须满足 available_at≤游标；纳秒时间在 JSON 中编码为十进制字符串。API 不读 truth/P1/QC/注入文件，不返回完整配置、payload_json、内部执行状态或标签。不提供任意路径、资源管理、命令执行、下载和写入接口。health 明确区分 CLI 仿真能力和 Web 任务提交能力，字段含义见下文。

`parse --parquet` 为可选数据组操作；`qc --run-dir` 成功写独立报告并返回 0，仅 completed 不表示 QC 通过。QC 失败或缺失证据返回 1；同版本派生目录拒绝覆盖。

## V0.2开发能力声明

simulation_available=true指CLI支持真实仿真，web_run_submission_available=false。0.0配置校验服务保留；0.2/0.3由validate-power-config校验。snapshot.stations[].measurements包含白名单IOA、单位及master_iec104_telemetry来源；无观测为空数组。功率、辐照、电压界面不读取truth/IPC。limitations按实际模型版本显示，Oracle依旧拒绝。阶段标识是软件能力元数据，验收结论以对应报告为准。

## V0.3 多站点及增量查询

snapshot增加regions、dispatches、protocol_statistics，以及站点馈线/电气父节点/空间坐标/新鲜度和实际遥信。区域聚合注明有效容量覆盖率；缺失、过期观测不由Oracle补齐。通信统计来自主站真实raw回调累计记录，尚未parse时protocol_status明确为not_yet_parsed。正常资源不足的unallocated_kw和传输失败分列，positive_confirmations不等于物理执行完成。

`GET /api/runs/{run_key}/events?at_ns=<ns>&limit=100&mode=M2`提供历史分页。后续页同时传before_ns和before_id，按时间/ID联合游标严格排除边界，limit为1–100；只传其中一个返回422。索引按新增observed字节摄入，原始文件截断或重复ID返回409。

`GET /api/diagnostics/runs/{run_key}/resources`仅提供独立基础设施诊断，scope=infrastructure_only_not_M2_features，包含时间、单核口径CPU、RSS、磁盘IO及UTC映射漂移；不进入M2快照或特征。没有资源记录时字段为null，不伪造零值。Web不操作命名空间或执行命令，M2接口不读Oracle；独立管理入口见上文。完整机器契约见schemas/openapi.json。

## V0.4a 前置契约

包版本0.4.0.dev0、Health.stage=V0.4-development表示当前开发能力，V0.4未验收。`qc --profile normal`为默认严格正常回归；显式`--profile experiment`写入`qc/v04-experiment-quality-v1/report.json`，独立报告run_integrity、data_quality、normal_regression、transport_observations、command_assessments。fault_verification和label_quality当前为not_evaluated，不能用本报告代替标签验收。同版报告拒绝覆盖。

主站命令结果区分accepted、negative_confirmation、confirmation_timeout、connection_unavailable、connection_lost、session_quarantined和ambiguous_confirmation。positive_confirmation仅实际确认时为布尔值，未观察为null；stack_result为c104 transmit返回事实。超时站点隔离到真实TCP重连，避免迟到同值确认污染下一任务。available_power按missing/stale/invalid抛出预期业务不可用；区域任务排除该站并记录未分配功率，其余站和后续任务继续。设备/程序错误仍传播。

M2 EventView追加quality_valid/outcome，DispatchView追加negative_confirmations/confirmation_timeouts/unavailable_commands/excluded_stations；failed_transmissions旧字段保留兼容。无效品质最新遥测显示未知，曲线保留缺口，不能用过去或truth补值。所有结果仍按available_at截断。

`validate-weather-source --config JSON`验证0.4-weather-source.1离线描述符与来源文件；`storage-audit --run-dir PATH --policy JSON`只读清单与预算；`archive-lossless --source PATH --destination NEW_DIR`生成gzip及逐字节哈希manifest，保留原件，不覆盖已有目录。后两者不是批量数据集发布或自动清理入口。
