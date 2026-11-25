# PVComm

分布式光伏电力—通信联合仿真平台。研究依据为 [V1.3 方案](docs/分布式光伏电力—通信联合仿真数据集构建方案_V1.3.md) 和 [AGENTS.md](AGENTS.md)。

当前实现包含 V0.0 工程基础和 V0.1 本机 Linux 实验原型：配置校验、结构化日志、时钟映射、CLI、FastAPI 配置服务、Vue 校验工具、真实 c104 TCP/IEC 104 三站闭环、独立 P0/P1 抓包、TShark 解析、Parquet 导出、独立 QC 及 M2 只读回放。**V0.1 的 30 分钟正式门禁尚未完成，未生成正式数据集或异常检测结果。** 实际验收状态见 [进度](docs/progress.md)。

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

有 GNU make 时可用 `make bootstrap lint typecheck test web-test web-build schemas`。Windows 没有 make 时使用等价 `uv run python scripts/dev.py <target>`，完整说明见 [开发指南](docs/development.md)。`run`/`parse` 已实现实验入口；`label/split/features/benchmark` 尚未实现，明确退出 3。

V0.1 仅在授权的 WSL Linux 实验环境运行，使用已安装的 `.venv-wsl`：

```sh
.venv-wsl/bin/python -m pvcomm.cli validate-lab-config --config configs/v0.1-lab-smoke.yaml
PVCOMM_ALLOW_LAB=1 .venv-wsl/bin/python -m pytest -m lab
.venv-wsl/bin/python -m pvcomm.cli run --isolated --config configs/v0.1-lab-smoke.yaml --output-root outputs/my_first_isolated
.venv-wsl/bin/python -m pvcomm.cli parse --run-dir outputs/my_first_isolated/v01_smoke_001
```

冒烟配置用于短时联调。`--isolated` 创建仅本次运行拥有的 4 个网络命名空间及 3 对 veth，无默认路由；P0 位于主站，三份 P1 分别位于 RTU。退出后清理本次资源，取消保留部分证据。省略该标志执行回环开发模式；同一个 lo 上的两份捕获不能替代 P0/P1。冒烟回环端口为 25041–25043；验收配置在每站独立地址使用 TCP 2404。

验收进展和实测指标见进度文档。解析生成 `parsed/tshark-pdml-v2/` 下的三层 JSONL，保留全部 TCP 包、多个 APDU、对象、精确时间和重组引用；使用 `parse --parquet` 可另外生成六张显式 Arrow 类型表；属于版本 0.1 开发证据，不是正式科研数据集。重复 run_id 或同版本解析目录会失败，须选新的输出位置，禁止删除旧记录后重复运行。输出目录、PCAP 和解析产物默认被 Git 忽略。

## 科研与开发边界

正式数据必须来自真实 c104/TCP 及 PCAP，命令由终端接受后才影响电力状态。M2 主实验与 Oracle 诊断分离；故障、业务违约和可观测性分层，按 available_at 保持因果。`data/`、`outputs/`、PCAP、密钥和模型权重禁止提交。

按阶段建立有功能的模块；[架构](docs/architecture.md)、[阶段计划](docs/plans/2026-11-25-v0.0.md)、[数据字典](docs/data_dictionary.md)、[API](docs/api.md) 说明当前契约和后续边界。此项目尚未指定开源许可证；私有研究代码不代表可自由分发。

## V0.1 验收与只读工作台

在授权 WSL 内，选择全新输出根；以下命令真实运行 30 分钟，不加速协议时间：

```sh
.venv-wsl/bin/python -m pvcomm.cli run --isolated --config configs/v0.1-acceptance-30m.yaml --output-root outputs/new_acceptance
.venv-wsl/bin/python -m pvcomm.cli parse --parquet --run-dir outputs/new_acceptance/v01_acceptance_30m_001
.venv-wsl/bin/python -m pvcomm.cli qc --run-dir outputs/new_acceptance/v01_acceptance_30m_001
```

解析/QC 使用可选 `data` 组（pyarrow 23.0.1），安装方式见开发指南。维护和非计划中断配置分别为 `configs/v0.1-acceptance-maintenance.yaml`、`configs/v0.1-acceptance-outage.yaml`。它们仅验证首周协议中断与恢复，不代表 V0.4 完整标签库。

PowerShell 显式指定运行父目录后启动本机只读服务：

```powershell
$env:PVCOMM_RUN_ROOT = 'E:/project/pv/outputs/v01_frozen_acceptance_001/runs'
uv run uvicorn pvcomm.web.app:create_app --factory --host 127.0.0.1 --port 8000
# 另一个终端
pnpm --dir frontend dev
```

选择“运行与回放”，可查看真实主站遥测、拓扑、事件和已解析 P0 APDU。回放按纳秒 available_at 截断；真值、P1、注入和事后标签不进入 API。HTTP 时间戳用十进制字符串传输，前端以 BigInt 保持精度。未解析时明确显示报文未就绪，缺口不补零。
