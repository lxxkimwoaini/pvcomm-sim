# V0.0 API 与 CLI

启动：`uv run uvicorn pvcomm.web.app:create_app --factory --host 127.0.0.1 --port 8000`。
OpenAPI 位于 `/openapi.json`，交互文档 `/docs`。服务仅用于本机工程开发，无运行控制和文件读取接口。

| 接口 | 输入 | 实际响应 |
|---|---|---|
| GET /api/health | 无 | status=ok、stage=V0.0、simulation_available=false |
| POST /api/scenarios/validate | JSON 对象 `{ "yaml_text": "..." }` | valid、schema_version、config_sha256、station_count；非法配置 HTTP 422 |

校验接口只支持 schema 0.0 工程契约，没有故障注入或完整场景执行能力。CLI 与 API 调用同一 config.service；配置例子为 configs/foundation.yaml。输入 UTF-8 大小、深度、展开节点均受限；输入不写磁盘。当前 API 不返回真值、标签或场景配置，尚无运行查询/WS/回放接口，不声称这些隔离功能已实现。

生成：`uv run python scripts/generate_schemas.py`，然后 `pnpm --dir frontend types:generate`。检查：`uv run python scripts/dev.py schemas`。frontend/src/types/api.generated.ts 为自动生成文件，不能手改。跨字段引用校验以共享后端服务为准，JSON schema 不表达全部关系规则。

CLI `pvcomm --version` 和 `validate-config --config <path>` 已实现。成功返回 0；配置/参数错误返回 2；预留的 run/parse/label/qc/split/features/benchmark 返回 3（not implemented）。不自动创建运行或输出数据。
