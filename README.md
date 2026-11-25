# PVComm

分布式光伏电力—通信联合仿真平台。研究依据为 [V1.3 方案](docs/分布式光伏电力—通信联合仿真数据集构建方案_V1.3.md) 和 [AGENTS.md](AGENTS.md)。

当前实现包含 V0.0 工程基础和 V0.1 本机 Linux 实验原型：配置校验、结构化日志、时钟映射、CLI、FastAPI 配置服务、Vue 校验工具、真实 c104 TCP/IEC 104 三站闭环、tcpdump 抓包和 TShark 解析。**V0.1 的 30 分钟正式门禁尚未完成，未生成正式数据集或异常检测结果。** 实际验收状态见 [进度](docs/progress.md)。

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

有 GNU make 时可用 `make bootstrap lint typecheck test web-test web-build schemas`。Windows 没有 make 时使用等价 `uv run python scripts/dev.py <target>`，完整说明见 [开发指南](docs/development.md)。`run`/`parse` 已实现实验入口；`label/qc/split/features/benchmark` 尚未实现，明确退出 3。

V0.1 仅在授权的 WSL Linux 实验环境运行，使用已安装的 `.venv-wsl`：

```sh
.venv-wsl/bin/python -m pvcomm.cli validate-lab-config --config configs/v0.1-lab-smoke.yaml
PVCOMM_ALLOW_LAB=1 .venv-wsl/bin/python -m pytest -m lab
.venv-wsl/bin/python -m pvcomm.cli run --isolated --config configs/v0.1-lab-smoke.yaml --output-root outputs/my_first_isolated
.venv-wsl/bin/python -m pvcomm.cli parse --run-dir outputs/my_first_isolated/v01_smoke_001
```

冒烟配置用于短时联调。`--isolated` 创建仅本次运行拥有的 4 个网络命名空间及 3 对 veth，无默认路由；P0 位于主站，三份 P1 分别位于 RTU。退出后清理本次资源，取消保留部分证据。省略该标志执行回环开发模式；同一个 lo 上的两份捕获不能替代 P0/P1。当前实验端口仍为 25041–25043，非正式 2404 点表模板。

30 分钟开发稳定性记录已产生，但尚未满足独立端点 30 分钟、对照、QC 和只读 Web 全部门禁。解析生成 `parsed/tshark-pdml-v2/` 下的三层 JSONL，保留全部 TCP 包、多个 APDU、对象、精确时间和重组引用；不是正式 Parquet 数据集。重复 run_id 或同版本解析目录会失败，须选新的输出位置，禁止删除旧记录后重复运行。输出目录、PCAP 和解析产物默认被 Git 忽略。

## 科研与开发边界

正式数据必须来自真实 c104/TCP 及 PCAP，命令由终端接受后才影响电力状态。M2 主实验与 Oracle 诊断分离；故障、业务违约和可观测性分层，按 available_at 保持因果。`data/`、`outputs/`、PCAP、密钥和模型权重禁止提交。

按阶段建立有功能的模块；[架构](docs/architecture.md)、[阶段计划](docs/plans/2026-11-25-v0.0.md)、[数据字典](docs/data_dictionary.md)、[API](docs/api.md) 说明当前契约和后续边界。此项目尚未指定开源许可证；私有研究代码不代表可自由分发。
