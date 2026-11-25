# PVComm

分布式光伏电力—通信联合仿真平台。研究依据为 [V1.3 方案](docs/分布式光伏电力—通信联合仿真数据集构建方案_V1.3.md) 和 [AGENTS.md](AGENTS.md)。

当前版本为 0.3.0.dev0（V0.3 开发中，尚未通过规模门禁）：已保留 V0.0 工程基础和通过验收的 V0.1 本机 Linux 实验原型，已通过 V0.2 三站物理闭环验收，并扩展配置驱动的多站点开发功能。基础功能包括配置校验、结构化日志、时钟映射、CLI、FastAPI 配置服务、Vue 校验工具、真实 c104 TCP/IEC 104 三站闭环、独立 P0/P1 抓包、TShark 解析、Parquet 导出、独立 QC 及 M2 只读回放。**V0.1 首周协议开发门禁已通过；未生成正式科研数据集或异常检测结果。** 实际验收状态见 [进度](docs/progress.md)。

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

按阶段建立有功能的模块；[架构](docs/architecture.md)、[阶段计划](docs/plans/2026-11-25-v0.0.md)、[数据字典](docs/data_dictionary.md)、[API](docs/api.md) 说明当前契约和后续边界。仓库经授权公开，但尚未指定代码开源许可证；公开可见不代表授予自由分发许可。

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

V0.1 逐门禁实测见 [验收报告](docs/reports/2026-11-25-v01-acceptance.md)。


## V0.2 开发物理闭环（0.2.0.dev0）

新增历史 NASA POWER 小时气象、pvlib POA/温度/PVWatts AC、逆变器 P/Q/斜率约束及 pandapower 三站径向潮流。参数为开发仿真假设，未现场校准；固定 available_kw 路径仍只用于 V0.1 协议回归。详细设计与门禁见 [V0.2计划](docs/plans/2026-11-25-v02.md) 和 [ADR0005](docs/decisions/0005-v02-physical-model.md)。**V0.2 三站开发验收通过**，实测和边界见 [V0.2验收报告](docs/reports/2026-11-25-v02-acceptance.md)。

```sh
# 仓库根目录，输入仅下载至 Git 忽略的 data；已有目录拒绝覆盖。
uv run --frozen python scripts/fetch_v02_weather.py
# WSL：明确项目环境，勿让 uv 选择 Windows .venv。
UV_PROJECT_ENVIRONMENT=.venv-wsl uv sync --frozen --group protocol --group power --group data
.venv-wsl/bin/python -m pvcomm.cli validate-power-config --config configs/v0.2-clear.yaml
.venv-wsl/bin/python -m pvcomm.cli run --isolated --config configs/v0.2-clear.yaml --output-root outputs/new_v02
.venv-wsl/bin/python -m pvcomm.cli parse --parquet --run-dir outputs/new_v02/v02_clear_001
.venv-wsl/bin/python -m pvcomm.cli qc --run-dir outputs/new_v02/v02_clear_001
uv run --frozen python scripts/dev.py power-test
```

多云/小时辐照边界测试分别使用 `v0.2-cloudy.yaml`、`v0.2-irradiance_transition.yaml`；均为90s实时、三站限发/重复同值限发/解除。小时均值零阶保持，不加速源时间；跨小时阶跃不是实测秒级云影。源ID/版本/许可使用依据、下载日期、原始与规范化哈希留在 truth/weather 和manifest，不进入M2。

运行后设置 `PVCOMM_RUN_ROOT` 为输出父目录打开既有工作台。在“光伏电气过程”选择站点及功率/辐照/电压指标；IOA1004/1005/1008新增实验点经实际IEC104上送后显示，缺失为未知。同链路天气与可用功率不构成独立M3。Web不能启动特权实验，浏览器关闭不影响采集。

六张协议Arrow表保持版本不变，新增 `parsed/parquet-v02-power-v2/` 为独立0.2 Oracle电气/潮流契约，QC分别验证来源/单位/物理计算和真实协议。普通测试不安装科学库，`power-test`与`data-test`单独运行，CI不抓包。


`configs/v0.2-cloud_shadow.yaml`是历史晴朗背景加显式灰色光学层平流模型，用于秒级辐照扰动验收。衰减由已记录光学厚度/宽度/速度计算，不是随机电气量或实测秒级云影。来源GHI、透过率与原始文件保留Oracle证据。Oahu原站对再分发的限制与聚合目录许可标记冲突，本项目未将其接入、提交或再分发。


## V0.3 多站点开发（尚未验收）

配置驱动 N 站点、区域聚合/独立接入链路、与通信分组独立的电气馈线、空间平流云影、差异周期/死区/事件上送、区域分批调控与显式资源不足反馈。参数均为仿真假设，非辉县现场配置。历史小时背景和模型生成秒级扰动严格区分；固定 available_kw 仍仅用于 V0.1 回归。

```sh
.venv-wsl/bin/python -m pvcomm.cli validate-power-config --config configs/v0.3-03.yaml
.venv-wsl/bin/python -m pvcomm.cli run --isolated --config configs/v0.3-03.yaml --output-root outputs/unique_v03_run
.venv-wsl/bin/python -m pvcomm.cli parse --parquet --run-dir outputs/unique_v03_run/v03_03_normal_001
.venv-wsl/bin/python -m pvcomm.cli qc --run-dir outputs/unique_v03_run/v03_03_normal_001
```

按 3→10→20→30 逐级验证，`v0.3-30-short.yaml` 为360秒业务预检，`v0.3-30-1h.yaml` 为3600秒验收候选，不能仅凭命令结束宣称QC通过。冻结干净代码、独立输出目录和源文件哈希；抓包先落Linux原生spool，停止后逐份核对SHA256并持久化。失败证据保留，禁止覆盖。计划、实际结果与未完成项见 [V0.3计划](docs/plans/2026-11-25-v03.md) 及进度文档。

Web继续只读实际M2，新增区域覆盖率、配置双拓扑、空间遥测、区域任务、原始回调计数、联合游标事件分页及曲线min/max降采样；原始Arrow/科学数据不降采样。资源诊断位于独立 `/api/diagnostics/runs/{key}/resources`，不进入M2输入。主站图中缺测不读取truth补齐，累计回调数不冒称独立解析APDU数。
