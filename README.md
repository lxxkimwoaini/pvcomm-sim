# PVComm

分布式光伏电力—通信联合仿真平台。研究依据为 [V1.3 方案](docs/分布式光伏电力—通信联合仿真数据集构建方案_V1.3.md) 和 [AGENTS.md](AGENTS.md)。

当前实现 V0.0 工程基础：配置校验、结构化日志、时钟映射、CLI、FastAPI 配置服务、Vue 校验工具及普通 CI。**尚未实现仿真运行、IEC 104 闭环、抓包、正式数据或异常检测。** 实际验收状态见 [进度](docs/progress.md)。

## 安装与复现

工具：uv 0.11.13、Python 3.11.15、Node 24.14.0、pnpm 11.5.1。Windows PowerShell 和 Linux 均可执行以下命令：

```sh
uv python install 3.11.15
uv sync --frozen
pnpm --dir frontend install --frozen-lockfile
uv run pvcomm validate-config --config configs/foundation.yaml
uv run python scripts/dev.py check
```

`validate-config` 只验证版本 0.0 的工程示例，输出规范化配置 SHA256，不产生运行目录。此文件中的容量、周期、期限为仿真假设，不是现场参数。后续运行配置将另设版本，不将此示例当作方案 1.0 的完整场景。

```sh
uv run uvicorn pvcomm.web.app:create_app --factory --host 127.0.0.1 --port 8000
# 另一个终端：
pnpm --dir frontend dev
```

打开终端显示的本地前端地址，粘贴 `configs/foundation.yaml` 并校验。前端通过本机 Vite 代理调用真实后端，不生成任何遥测。网页关闭不影响独立后端。

有 GNU make 时可用 `make bootstrap lint typecheck test web-test web-build schemas`。Windows 没有 make 时使用等价 `uv run python scripts/dev.py <target>`，完整说明见 [开发指南](docs/development.md)。`run/parse/label/qc/split/features/benchmark` 当前明确退出 3；`test-lab` 当前退出 3，不产生通过记录。

## 科研与开发边界

正式数据必须来自真实 c104/TCP 及 PCAP，命令由终端接受后才影响电力状态。M2 主实验与 Oracle 诊断分离；故障、业务违约和可观测性分层，按 available_at 保持因果。`data/`、`outputs/`、PCAP、密钥和模型权重禁止提交。

按阶段建立有功能的模块；[架构](docs/architecture.md)、[阶段计划](docs/plans/2026-11-25-v0.0.md)、[数据字典](docs/data_dictionary.md)、[API](docs/api.md) 说明当前契约和后续边界。此项目尚未指定开源许可证；私有研究代码不代表可自由分发。
