# V0.0 API 与 CLI

启动：`uv run uvicorn pvcomm.web.app:create_app --factory --host 127.0.0.1 --port 8000`。
OpenAPI 位于 `/openapi.json`，交互文档 `/docs`。服务仅用于本机工程开发，无运行控制接口；只读运行根由服务端指定。

| 接口 | 输入 | 实际响应 |
|---|---|---|
| GET /api/health | 无 | status=ok、stage=V0.0、simulation_available=false |
| POST /api/scenarios/validate | JSON 对象 `{ "yaml_text": "..." }` | valid、schema_version、config_sha256、station_count；非法配置 HTTP 422 |

校验接口只支持 schema 0.0 工程契约，没有故障注入或完整场景执行能力。CLI 与 API 调用同一 config.service；配置例子为 configs/foundation.yaml。输入 UTF-8 大小、深度、展开节点均受限；输入不写磁盘。当前 API 不返回真值、标签或场景配置，保留 V0.0 配置校验契约；V0.1 新增下述运行查询/回放接口，尚无 WS，不声称这些隔离功能已实现。

生成：`uv run python scripts/generate_schemas.py`，然后 `pnpm --dir frontend types:generate`。检查：`uv run python scripts/dev.py schemas`。frontend/src/types/api.generated.ts 为自动生成文件，不能手改。跨字段引用校验以共享后端服务为准，JSON schema 不表达全部关系规则。

CLI `pvcomm --version` 和 `validate-config --config <path>` 已实现。成功返回 0；配置/参数错误返回 2；run/parse/qc 实现 V0.1 实验操作；预留 label/split/features/benchmark 返回 3（not implemented）。不自动创建运行或输出数据。

## V0.1 M2 只读接口

`PVCOMM_RUN_ROOT` 为服务端预设父目录（默认 outputs）。`GET /api/runs` 返回独立端点运行的 opaque run_key/status/source，不返回文件名或场景定义。`GET /api/runs/{run_key}/snapshot?at_ns=<整数纳秒>&mode=M2` 返回三站配置白名单、最新主站功率和连接状态、每站至多 600 个曲线点、最近 100 条主站事件及 P0 APDU。只接受 M2；Oracle 返回 422；未知运行 404，损坏完整记录 409 并写服务端结构化错误日志。实时写入的最后一个未结束行暂不读取。

所有事件和 APDU 必须满足 available_at≤游标；纳秒时间在 JSON 中编码为十进制字符串。API 不读 truth/P1/QC/注入文件，不返回完整配置、payload_json、内部执行状态或标签。不提供任意路径、资源管理、命令执行、下载和写入接口。V0.0 health 的 stage 与 simulation_available 字段保留原契约，表示该兼容配置服务入口没有 Web 运行控制能力，并非 CLI 协议运行器不可用。

`parse --parquet` 为可选数据组操作；`qc --run-dir` 成功写独立报告并返回 0，仅 completed 不表示 QC 通过。QC 失败或缺失证据返回 1；同版本派生目录拒绝覆盖。
