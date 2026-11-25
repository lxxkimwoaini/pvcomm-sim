# PVComm 开发进度

日期：2026-11-25（Asia/Shanghai）。当前阶段：**V0.0、V0.1及V0.2三站开发验收通过（2026-11-25）；未推进V0.3**。两个 30 分钟回环稳定性运行已完成，但不等同于独立 P0/P1 的正式验收。初始只有 AGENTS.md 和 V1.2/V1.3 方案；无既有实验。本文件只记录实际执行，不将计划标为完成。

## 已执行

- 完整阅读 AGENTS.md、V1.3 1323 行；保留用户原始两份方案。
- 工具检查：Windows 11 10.0.26200 x64，PowerShell 7.6.9，uv 0.11.13，Node 24.14.0，pnpm 11.5.1，Git 2.52.0.windows.1，gh 2.102.0。系统 Python 3.13.5，uv 原有 3.10.20；`uv python install 3.11` 成功安装 3.11.15。
- gh 已登录 lxxkimwoaini，用户明确确认使用该账号创建私有 pvcomm-sim；同名仓库查询未发现目标。Git 提交身份已有配置，不重写身份。
- `uv pip install --python .venv/Scripts/python.exe c104 pvlib pandapower` 成功。Python 3.11.15 导入成功，c104=2.2.1、pvlib=0.16.1、pandapower=3.5.6。构造 c104.Client；地外辐照探针 1321.6235925714136 W/m²；单母线潮流 converged=True、vm_pu=[1.0]。这是兼容性探针，不是科研结果或 IEC 104 通信验证。
- WSL Ubuntu 26.04、内核 6.6.87.2、默认 root、Python 3.14.4、Docker client/server 29.5.2、ip/tc/make 可用；tcpdump/TShark/dumpcap/uv 未发现。未修改网络、安装 WSL 系统包或创建实验资源。
- `uv lock`、`uv sync --frozen --group protocol --group power` 退出 0，锁定 59 包。前端首次安装发现 esbuild 构建脚本需显式许可；限定允许 esbuild，继续验证。

## 本地验收（实际执行，退出 0）

| 命令 | 结果 |
|---|---|
| `uv lock --check` / `uv sync --frozen` | 锁文件有效；最终 60 个解析包，默认组 37 包，不依赖 c104/电力库 |
| `uv run --frozen python scripts/dev.py bootstrap` | uv/Node/pnpm 显示正确，Python 与 pnpm frozen 安装成功 |
| `uv run --frozen python scripts/dev.py check` | 总入口退出 0；含以下所有普通检查 |
| `uv run --frozen ruff check .` / `ruff format --check .` | 通过 |
| `uv run --frozen mypy` | 18 个源文件通过 strict 检查 |
| `uv run --frozen pytest -m "not lab"` | 49 passed，无 warning/skipped；不是实验网络验收 |
| Python schema 检查 / `pnpm --dir frontend types:check` | JSON schema/OpenAPI/TS 生成契约一致 |
| `pnpm --dir frontend lint` / `format:check` / `typecheck` | ESLint/Prettier/vue-tsc 通过 |
| `pnpm --dir frontend test` | 1 文件、3 项 Vitest 测试通过；合成 HTTP fixture 仅用于单元测试 |
| `pnpm --dir frontend build` | Vite 7.3.8 构建通过；JS 160.50 kB，CSS 366.03 kB（未压缩） |
| `pnpm --dir frontend exec playwright install chromium` | 本机 Chromium 159.0.8154.6 安装成功 |
| `pnpm --dir frontend test:e2e` | 1 项真实后端浏览器流程通过；覆盖合法/非法配置、结果失效、桌面与 390px 移动视口；未 mock 后端 |
| `uv run --frozen pvcomm validate-config --config configs/foundation.yaml` | valid=true；SHA256 `15e441b1026a506b8856d27f6fa8e022304deabf6c80752d56638a3c81e1089e`；不产生运行 |

CLI 的 7 个预留命令均由子进程测试实际返回 3，没有生成输出目录。`git check-ignore` 确认 data、outputs、PCAP、.env、pem、权重、node_modules、dist 被排除。Playwright 结束后检查 8000/5173 监听为空，服务已退出。

首轮检查发现并修复：mypy 不支持浮点 Literal（改等值范围校验）；ESLint JS 工具缺少 Node 全局导入；pnpm esbuild 脚本未授权（限定 allowBuilds）；Starlette 弃用 httpx（改用其支持的 httpx2）；移动端 420px 溢出（改 grid 最小宽度和错误文本断行）。错误展示改为字段级摘要，保留原始异常链且不回显完整用户输入。没有删除失败测试、屏蔽 warning 或降低断言。最终浏览器截图人工核查桌面与移动端，错误字段可读。

提交前补充并通过 Unicode/别名回归：无效 Unicode 的 CLI 输入和 JSON API 请求均返回校验错误；修复框架默认错误响应回显无效 Unicode 导致的 500。YAML 在构造阶段拒绝别名，避免合并展开绕过节点预算。Playwright 仅提示外部 NO_COLOR/FORCE_COLOR 环境冲突，不影响测试，未禁用检查。

`git diff --cached --check` 只报告两份用户原始方案前言的 6 处 Markdown 双空格硬换行；不改写用户资料。排除这两份原文后的新增工程 diff 检查退出 0。暂存文件最大 111644 字节；凭据/私钥模式扫描无匹配；无数据、捕获、构建或依赖目录入库。

## 当前实际交付

Python src 基础包、严格不可变配置与哈希、结构化 JSON 日志、UTC/单调时钟映射、CLI；FastAPI health/配置校验；Vue/Element Plus 工作台及真实校验交互；生成 schema/TS 类型；uv/pnpm 锁；跨平台开发命令、Makefile、非特权 CI。架构、数据字典、API、开发说明、ADR 与各阶段验收计划已写入 docs。

## 阶段验收与下一任务

V0.0 本地检查、暂存审查、私有仓库创建/推送/SHA 核验与远程 CI 全部通过。没有依赖用户补做的 GitHub 授权步骤。初始化提交在 main；后续功能进入独立分支，不自动合并未审核 PR。

下一任务为 V0.1 30 分钟正常运行门禁：在同一授权 WSL 回环实验环境执行 `configs/v0.1-lab-30m.yaml`，核对双端 PCAP、TShark 解析率、事件关联和资源清理。当前未创建 namespace/veth、未修改 tc、未连接外部地址；无需修改科研范围、参数、标签或划分规则。

本次用户授权安装已完成：WSL Ubuntu 安装 tcpdump 4.99.6、TShark/Dumpcap 4.6.4、libpcap 开发包、build-essential、pkg-config；uv 0.13.10；uv 管理 CPython 3.11.15。项目环境目标为 `.venv-wsl/`，尚未把 IEC 104 进程接入网络，也未创建 namespace/veth、修改 tc 或执行抓包实验。

安装后复核（2026-11-25，WSL Ubuntu）：`tcpdump -D` 列出 13 个接口，`dumpcap -D` 列出同一组接口；`tshark -G protocols` 识别 `iec60870_104`，字段查询可见 IEC 60870 解析器；`ip`/`tc`/`make` 可用。`.venv-wsl/bin/python` 成功导入 c104、pvlib 0.16.1、pandapower 3.5.6 和 pydantic 2.14.4。`.venv-wsl/bin/python -m pytest -q -m "not lab"`：49 passed in 5.78s；`.venv-wsl/bin/python -m pvcomm.cli validate-config --config configs/foundation.yaml`：`valid=true`，配置 SHA256 为 `15e441b1026a506b8856d27f6fa8e022304deabf6c80752d56638a3c81e1089e`。上述仅证明工具和 Python 依赖可用，不构成 V0.1 协议闭环验收。

远程核验：提交 `bec54cfec8618f06246d239a40cd6f5a5d41d02d` 已推送到 `origin/main`；GitHub Actions CI run `45544659528` 对该 SHA 实际 conclusion=success。

## V0.1 冒烟实际结果（2026-11-25）

- 新增 `src/pvcomm/protocol/c104_runtime.py`：使用 c104 2.2.1 建立 1 主站 + 3 RTU 的真实 TCP/IEC 104 回环通信；点表包含有功遥测 IOA 1001、已接受设定值 IOA 1006、运行状态 IOA 2001 和有功命令 IOA 3001。
- 新增 `src/pvcomm/scenario/v01_runner.py`、V0.1 严格配置、运行清单和 tcpdump 生命周期；端口 25041/25042/25043，公共地址 1/2/3。
- 冒烟命令：`wsl.exe bash -lc 'cd /mnt/e/project/pv && .venv-wsl/bin/python -m pytest -q -m lab'`，实际 `1 passed, 47 deselected in 22.79s`。
- 真实运行 `v01_smoke_001`：manifest `status=completed`；3 次总召唤，6 次主站命令（每站限发/解除各一次），6 次终端接受；双端原始 APDU 250 条；PCAP 约 20 KB，tcpdump 返回码 0。
- `tshark` 通过端口解码识别 `iec60870_104` 128 帧、`iec60870_asdu` 110 帧；`pvcomm parse --run-dir outputs/v01_smoke_001` 实际生成 `packets.jsonl` 127 行、`iec104_apdu.jsonl` 127 行、`iec104_objects.jsonl` 115 行（每次实际调度可能有 1 帧差异）。解析字段包含帧时间、方向、TCP 长度、APDU I/S/U 控制字段、类型、COT、CA、OA、IOA。
- 运行目录和 PCAP 在 `outputs/` 下，已被 Git 忽略；没有把原始 PCAP 或运行产物提交到仓库。

## 尚未实现/测量

独立 P0/P1 30 min 完整验收、断联/维护对照、正式 Parquet、独立 QC 和报文—日志精确关联、只读运行 Web 尚未完成。未以最近回调匹配估计替代正式 P99 指标。气象/潮流、标签/划分/特征属于后续阶段；本切片未执行故障注入或 NetEm。

## V0.1 证据完整性修正（2026-11-25，最新记录）

复核发现并修正：两份同一 lo 捕获被错误描述为双端证据；旧解析包/APDU 一对一、丢纳秒精度；终端真值进入 observed；记录器未同步；斜率把到达前时间计入新指令；tcpdump 无 readiness 和丢包统计。旧版 30 分钟记录仅保留为开发稳定性证据，不用于正式研究特征或阶段验收。

- 新增 `network/isolated.py` 与 `scenario/isolated_runner.py`：4 个本次拥有的 namespace、3 对内部 veth、主站和 3 RTU 独立进程；无默认路由，不修改宿主网络或 NAT；P0 位于主站，3 份 P1 各位于终端。
- 新增同步 `storage/events.py`：主站事件/原始回调进 observed，终端事件/原始回调及设备状态进 truth；统一 UTC/单调/逻辑时间映射和主站 available_at；命令发送、确认、反馈分别记录。
- 配置移至 `config/lab.py`，复用有限大小、重复键/别名防护；生成 `schemas/v01-lab.schema.json`。Q=0 kvar、U=0.4 kV 为显式协议测试默认值，不是电力模型结果。
- TShark PDML v2 保留全部包、独立 APDU、多对象和值/品质/重组引用，Decimal 精确转换纳秒。新输出在 `parsed/tshark-pdml-v2/` 原子提交，同版拒绝覆盖；旧文件保留。尚非正式 Parquet schema。
- tcpdump 以 listening 事件判定 readiness，持续读取 stderr，采用 immediate mode；清单保留捕获/过滤接收/内核丢包计数。进程失败、取消、清理错误不能标记 completed；QC 仍独立 pending。

实际验证：

| 命令/证据 | 结果 |
|---|---|
| `uv run --frozen pytest -q -m "not lab"` | 55 passed，3 deselected；含并发、观测隔离、斜率、重复键、多 APDU、多对象、时间精度回归 |
| `uv run --frozen mypy` | 37 个源文件通过 |
| Ruff lint/format | 检查通过；最终完整总入口结果见下方补记 |
| `uv run --frozen python scripts/generate_schemas.py --check` / `pnpm --dir frontend types:check` | 通过 |
| `PVCOMM_ALLOW_LAB=1 .venv-wsl/bin/python -m pytest -q -m lab`（WSL） | 3 passed，55 deselected，50.37s；回环、独立端点及取消清理 |
| namespace lab 路由/资源断言 | 宿主路由前后一致，本次 namespace 全部清理 |

保留的实际运行：

- `outputs/v01_normal_30m_001`（代码 3bb3a71）：单回环捕获约 1.1 MB，12,136 包；3 次显式总召唤、6 次命令及终端接受，真实计时约 1800 s。不是 P0/P1 验收。
- `outputs/v01_normal_30m_002`（代码 d6526c8）：两个 tcpdump 均在 lo，自然完成；PDML v2 读取 primary 得到 **11,772 包、6,118 APDU、5,446 对象、0 个缺 ASDU 的 I APDU**；primary SHA256 `195977d0771a8671ca2242987b1ecf0dfce896b19231fe1a7a16db63f1c75687`。此计数不等于 QC 解析率。
- `outputs/isolated_integrity_001/v01_smoke_001`：独立端点修正版开发冒烟；4 份真实 PCAP，内核丢包统计均为 0；primary **203 包、123 APDU、180 对象**；manifest 诚实记录 code_dirty=true。随后 3 项 lab 回归进一步核验 immediate mode 和取消路径。

开发纪律修正：初轮功能曾直接推送 main，且曾删除并重跑同名开发冒烟目录；不回写或隐藏该历史。本切片转至 `fix/v01-evidence-integrity`，后续拒绝覆盖运行目录、使用新输出根，提交 PR 审核，不自动合并。PCAP、日志、输出及依赖环境始终不入 Git。下一步先完成本切片 PR，再在审核后的冻结版本上执行独立端点 30 min、对照与 QC/只读 Web。

### 最终本地复核补记

- 复验原零丢包断言实际失败：`1 failed, 2 passed, 55 deselected in 50.93s`。P0 为 224 captured/231 received/7 dropped，三份 P1 均为 0 dropped。该次运行只说明进程完成，QC 不通过。失败输出及统计在此保留；pytest 的 `/tmp` 临时原始目录随后不可用，尝试复制失败，未伪造归档。后续实验测试显式使用全新的持久输出根。
- tcpdump 显式设置 16384 KiB 缓冲并记录 `buffer_size_kib`；保留零丢包断言。修复多进程日志 ID 缺端点角色造成的跨 RTU 重复，并增加普通回归。真实 TShark PDML 抽查确认 SQ/negative/test 字段实际使用 True/False。
- WSL 最终命令：`PVCOMM_ALLOW_LAB=1 .venv-wsl/bin/python -m pytest -q -m lab --basetemp=outputs/lab_integrity_buffer_001`，退出 0，**3 passed, 56 deselected in 50.60s**。该输出根为首次使用，后续不得重复作为 basetemp（pytest 会清空指定 basetemp）。独立端点 P0 捕获 232 包，三份 P1 分别为 76/77/79 包；四路 dropped=0、返回码 0；P0 SHA256 `c663f623b464c2039daf53be66574b0ae7c12136409ef01fc22a04b1d01e8e9d`。完整原始记录保留于该输出根，Git 忽略。
- `uv run --frozen python scripts/dev.py check` 最终退出 0：Ruff lint/format（50 文件）、strict mypy（37 源文件）、**56 passed / 3 lab deselected**、schema/OpenAPI/TS 一致性、ESLint/Prettier/vue-tsc、Vitest 3 项及 Vite 生产构建通过。此总入口不包含 Playwright；本轮浏览器验收由后续实际远程 CI 单独记录。
- 当前 V0.1 仍未验收：独立端点 30 min、计划维护/中断对照、日志—报文精确对齐及独立 QC、正式 Parquet、只读运行 Web 待实现/验证。本次没有正式异常标签、检测算法结果或电力模型验收。

### 修复分支与远程验证

- 代码提交 `5269d1e92cae7fdf2261cf6ab90b92ba003d25b0`，`fix: preserve independent IEC 104 evidence and causal records`；已推送 `origin/fix/v01-evidence-integrity`，`git ls-remote` 与本地 SHA 一致。
- 已创建 PR #1：https://github.com/lxxkimwoaini/pvcomm-sim/pull/1 ，base=main，head=fix/v01-evidence-integrity，状态 OPEN，未合并。仓库核验仍为 lxxkimwoaini/pvcomm-sim、PRIVATE、默认 main。
- 该代码 SHA 的 push CI `45564634514` 与 pull_request CI `45564679157` 均实际 conclusion=success。两次均含 Ubuntu/Windows Python 和 Frontend and browser 三个成功 job，真实后端 Playwright 流程通过；CI 不执行特权 lab。
- 本段是上述已完成运行的事后记录；随后仅文档提交的 SHA 不与代码测试 SHA 混称。暂存区检查与密钥模式扫描无匹配，无 outputs/data/PCAP/依赖环境入库，实验结束无本次 namespace 或 worker 残留。

## GitHub

V0.1 验收补齐启动：在 `feat/v01-acceptance` 上实施 `docs/plans/2026-11-25-v01-acceptance.md`。增加独立端点 2404 配置、实际协议参数记录和单站受控停止/恢复。普通回归 58 passed、3 lab deselected；Ruff/mypy（38 文件）通过。90 s 维护预检 `outputs/v01_pair_preflight_001/v01_case_001` 实际 completed，已观测停止、重连和恢复总召唤；该预检 code_dirty=true，不计入冻结版本正式验收。数据导出依赖 pyarrow 23.0.1 已由 uv 正常解析写入锁文件，后续单独验证。

仓库：https://github.com/lxxkimwoaini/pvcomm-sim 。所有者 lxxkimwoaini，visibility=PRIVATE，defaultBranchRef=main，viewerPermission=ADMIN。`gh repo create pvcomm-sim --private --source=. --remote=origin --push` 实际退出 0。

- 首次提交：`25bf8c4848d110f771af14cff20090fbefb377ea`，`chore: initialize PVComm research foundation`。
- `git remote -v`：fetch/push 均为 `https://github.com/lxxkimwoaini/pvcomm-sim.git`。
- `git ls-remote --heads origin main` 与首次本地 HEAD 均为上述 SHA；推送后 `git status -sb` 为 `main...origin/main`，工作区干净。
- GitHub CI run `45539355121`：https://github.com/lxxkimwoaini/pvcomm-sim/actions/runs/45539355121 ，实际 conclusion=success；Python (ubuntu-latest)、Python (windows-latest)、Frontend and browser 三个 job 全部 success。CI 检查的代码 SHA 为上述首次提交；本验收记录随后以仅文档提交入库，避免自引用尚未产生的本提交 SHA。
- `uv run --frozen python scripts/dev.py test-lab` 实际退出 3，提示实验 harness 尚未实现；不是通过或已执行特权测试。

## V0.1 验收补齐：独立 QC、Arrow 与 M2 Web（2026-11-25）

- 冻结运行提交 `0847def76bab3082f20e7535cb1b9f01c5589f42`。正常独立端点 1800s 运行在 15:10 启动，当前仍在进行，尚不标记整阶段通过。
- 新增独立 classic-PCAP/TCP 重组、完整 APDU 与 TShark 比对、双端与回调逐字节对应、P99、6条命令确认期限及功率反馈核验。新增可选 Arrow 六表、源/产物哈希与严格 schema 核验，CLI parse --parquet 和 qc，普通 CI 新增数据契约 job。
- 新增实际 M2 运行 API/Vue 工作台，因果纳秒游标、3站拓扑/曲线/事件/P0列表；服务端拒绝 Oracle、真值和任意路径。ADR0003记录证据边界、测试模型参数及关闭尾帧。
- Windows `scripts/dev.py check` 实际退出0：64 passed、6 deselected；Ruff、mypy46文件、schema/TS、ESLint/Prettier/vue-tsc、Vitest3项和构建通过。`data-test` 最终3 passed；首次新增测试发现列表子字段 item/element 的严格 schema 不一致，修复显式 element 后通过。
- 真实根 Playwright 最终2 passed/1 skipped：真实配置和保留 c104 数据流程通过，空态因真实模式跳过；含选择运行、原始数据比对、回放、390px、离线与恢复。首次点击隐藏 combobox 被可见标签遮挡，改为真实选择框外壳；移动图表 ResizeObserver 尚未完成时曾瞬时溢出，图表容器约束与等待可测尺寸后通过，未延长超时或强制点击。
- 维护 `v01_case_001` 与中断 `v01_case_002` completed，90s；P0分别620/611包、351/349完整APDU、578/574对象，解析率100%，4路内核丢包0。日志映射P99=6.045663/3.987475ms；每运行6条命令期限内确认与实际反馈，恢复核验通过。报告实际写入各自 qc/v01-evidence-v1，重复写拒绝。
- 维护首次全回调对应失败，经原始字节定位2条I和2条S是业务结束后的关闭尾部。ADR0003公开覆盖边界；全部帧仍留在解析分母和双端核验中，未声称全时段主站回调覆盖。
- 原两对照 manifest code_dirty=true，未改写；source-provenance旁证核对91个快照文件与Git blob完全一致，3份运行源配置与快照一致；archive SHA256=0daf969a550c75c511ee86a310fce2ec9a3b85b7515e20de1dc7bcfb761425d7。配对共享条件一致，保留group和source组审计；均仅开发证据，不创建正式划分。
- Vite实际构建JS约808kB，保留>500kB构建提示；尚无30站资源测试，属于V0.3范围。剩余：正常1800s解析/QC、Linux lab回归、远程CI和逐门禁总报告。

## V0.1 最终门禁（2026-11-25 15:40后）

- 真实独立端点1800s配置运行completed、code_dirty=false、cleanup_errors=[]，实际业务区间1799.997721735s；P0=11831包/6123完整APDU/10848对象，完整解析率100%，四路内核丢包0；三站各2041 APDU与P1/终端回调逐字节对账。
- 正常运行parse --parquet与qc均实际退出0；P99=4.325865ms、max=208.659407ms（极端点保留）；6命令确认期限/设定值与实际反馈通过；6表类型、行数、源/产物哈希通过。机器汇总保留在outputs/v01_frozen_acceptance_001/acceptance-v01.json，原始manifest不改写。
- WSL真协议lab回归3 passed/67 deselected，51.51s，输出outputs/v01_lab_acceptance_002；包括取消与清理。最终ip netns list为空、无worker残留；真实正常运行M2 API、最新功率及纳秒因果游标再次实测通过。
- 协议/对照冻结SHA0847def；QC/API/UI代码SHA60cdbae2c9ef533166952547f16ca950a47f2d76，已推送origin/feat/v01-acceptance。PR #2为OPEN、base=fix/v01-evidence-integrity，依赖PR #1，未合并。仓库仍PRIVATE、默认main。
- 60cdbae的push CI45569491739与PR CI45569549685实际success，均4个job全部success；普通CI64项、Arrow3项、Vitest3项、浏览器2 passed/1 skipped，真实证据用例只在本地授权数据模式运行。
- 逐门禁实测、指令和证据路径见docs/reports/2026-11-25-v01-acceptance.md。V0.1只完成协议开发验收，不代表正式物理模型、数据集或V0.4标签管线。下一任务：V0.2气象输入、物理参数与潮流模型设计和验证。此后仅文档提交单独记录，不混用代码测试SHA。

## 仓库可见性变更（2026-11-25）

用户明确要求改为公开仓库。核实当前账号 lxxkimwoaini 对目标仓库具有 ADMIN 权限后，`gh repo edit lxxkimwoaini/pvcomm-sim --visibility public --accept-visibility-change-consequences` 实际退出0；随后 `gh repo view` 核验 visibility=PUBLIC，地址 https://github.com/lxxkimwoaini/pvcomm-sim 。此前 PRIVATE 记录是历史状态。本次仅更新仓库设置和进度记录，diff检查通过，不涉及代码测试；未将既往CI结果算作本次文档提交的CI结果。

## V0.2 前置审查与关联修复（2026-11-25）

分支 feat/v02-power-cosimulation 从9b2de32堆叠，PR #1→#2均OPEN且未合并。计划 docs/plans/2026-11-25-v02.md、审查报告 docs/reports/2026-11-25-pr01-pr02-review.md、ADR0004。修复重复action任务ID、同值接受/反馈复用、跨任务/会话及时间边界；独立QC版本v01-evidence-v2，旧产物未改。

实际 Ruff退出0、mypy48文件通过；pytest -m "not lab and not data" 80 passed/6 deselected，专项22 passed。WSL对保留正常1800s/维护/中断执行新版qc_run，三份报告passed=true。新代码真实重复命令实验尚未执行，V0.2物理模块尚未实现；不重报V0.1长运行或本分支CI通过。

## V0.2物理实现及预检（2026-11-25）

已实施独立power模块：NASA POWER历史输入SHA/单位/缺测校验、pvlib POA/SAPM/PVWatts、设备P/Q/容量/斜率/运行状态、pandapower三站径向潮流；RTU经c104接受命令后才更新目标。真实端点、原六表Arrow、P0/P1和M2白名单保留。新增0.2配置、独立Oracle电气/潮流Arrow、重算QC和实际遥测曲线，包0.2.0.dev0明确开发版；Web不提交特权运行。

最初历史三天预检扩展为独立整月目录（data/nasa-power-huixian-202406-month），没有覆盖原输入。NASA POWER Hourly v2.10.5，MERRA2/SYN1DEG，规范化SHA72a8d6275ef89029b6b34e73d0cc68708ee749eef69dcf59abe65d0fe13346bd。非辉县现场实测，使用依据及版本见ADR0005；Oahu下载403/TLS失败，不宣称秒级云影验收。

预检001实际失败：drvfs替换IPC文件瞬时ENOENT，manifest=failed，解析拒绝未完成运行；原目录保留。改为单写者追加完整JSONL并回归部分尾行；完整坏行仍失败。预检002实际90s completed，新增电气425行、潮流57行，真实PCAP+六表+业务独立QC通过，code_dirty=true诚实保留，不作为冻结最终验收。Arrow初次导出发现pa.bool名称错误，修复为bool_并新增读回/源哈希/拒绝覆盖测试通过；已生成六表未重建。

实际普通测试91 passed/9 deselected，mypy60文件、Ruff通过；Arrow4 passed，科学2 passed（PVWatts独立公式、潮流符号/电压）；WSL真实V0.1回归3 passed/97 deselected，51.03s，持久outputs/v02_protocol_regression_001。总check最终前端结果及冻结三场景/重复同值命令/物理QC/UI还待记录。当前未宣布V0.2验收通过，不进入V0.3。

## V0.2 历史三场景冻结验证及模型扰动准备（2026-11-25）

- 代码 a4b7039ef2b9b657b67d7e408e6b8713ce045474，三份90s实时运行位于 outputs/v02_frozen_001，全部 completed、code_dirty=false、cleanup_errors=[]。晴朗/多云/小时边界 P0分别725/754/718包、363/378/363完整APDU、各987对象；解析率100%，四路捕获 dropped=0；日志映射P99分别50.007106/50.608219/45.546582ms。每运行9条不同task_id命令（各站限发、同值重复、解除），接受/确认/反馈与wire证据一对一通过。
- v02-power-v1逐份独立QC通过；物理各431行，潮流60/60/57行（20/20/19次三站求解），pvlib与pandapower全量重算通过。原六表Arrow及新增Oracle表读回/哈希通过。冻结源码archive SHA256=38e8ae14db516cea3d8d2b5e1af7731f73bea7754d6b9e2010a80306e180b2d7，本地source.tar不入库。旧报告/契约不覆写。
- c5c9464的CI45574379306实际success。b463e68的CI45577492912实际failure：遗漏提交三个配置，配置测试失败；补齐配置提交a4b7039后CI45577551377实际success，Ubuntu/Windows、Arrow、Physical models、Frontend/browser五job全部成功。没有把失败记录算作通过。
- 本地最新check退出0：普通93 passed/9 deselected、mypy60文件、Ruff、schema/TS、ESLint/Prettier/vue-tsc、Vitest3与build通过；data4 passed/98 deselected、power2 passed/100 deselected。Vite约810kB的构建提示保留。真实V0.2 API主站测点与原始接收日志对账通过，Playwright2 passed/1 skipped（真实模式跳过空态），截图保留 outputs/v02_frozen_001/web-evidence。
- 浏览器首次环境未同时设置PVCOMM_RUN_ROOT导致404，第二次点击隐藏的指标输入被组件外壳拦截；按trace修正为真实可见wrapper后通过，没有force或延长超时。未隐藏失败。
- Oahu新NLR入口可下载，但原站禁止再分发与OEDI许可标记冲突，未接入/再分发；ADR0005追加实情。增加明确的灰色光学层平流模型，测试Beer–Lambert衰减和边界；它是历史小时背景上的物理简化扰动，不是实测秒级云影。当前新增source_ghi/透过率的Oracle契约0.2-power-evidence.2及QC v02-power-v3，与旧证据分目录。新模型真实运行尚待执行，暂不声明V0.2总验收通过。

### 快速扰动核验的时间精度修正

云影001（7410fe7）与002（44adf12）均真实90s completed、协议QC通过，但独立物理QC v2/v3失败：ISO source_weather_ts微秒化后重算秒级光学层，GHI/POA出现约3.2e-5数值偏差。临时v4将模型/天气容差改为1e-4，003（76e73ad）通过；该放宽不作为最终验收依据。最终v5改用已保留model_mono_ns与clock_origin精确映射重算，源ISO时间仅检查1µs映射误差，物理/天气原1e-6容差恢复。原始证据和各版失败报告全部保留，最终v5结果另记。新增纳秒回放回归。此修正不改变运行物理轨迹、协议参数或标签。

## V0.2 最终三站开发门禁（2026-11-25）

最终代码/QC da7b5262c24e0ae5c991692b5cc0f8f23fd69f21；cloud_frozen_003运行代码76e73ad、code_dirty=false、completed、cleanup_errors=[]。独立v5在原1e-6天气/物理门槛下passed=true，pvlib及pandapower重算误差0，单调纳秒映射回归通过；此前临时放宽的v4不作为验收依据。P0=726包/363APDU/987对象，四路dropped=0，P99=61.827776ms，9条真实不同任务逐项闭环通过；电气431行、20次三站潮流全部收敛。历史晴朗/多云/小时边界三份v1报告及冻结代码保留。详细复现、SHA与失败记录见docs/reports/2026-11-25-v02-acceptance.md。

最终check退出0（94 passed/9 deselected、Ruff/mypy60/schema/前端质量/Vitest3/build）；data4 passed/99 deselected，power2 passed/101 deselected。云影真实浏览器2 passed/1 skipped（空态），逐值对账与移动回放通过。独立负测试副本故意越界后物理/QC源哈希拒绝，原始证据不改。ip netns list为空，没有实际worker残留。

远程仍为 https://github.com/lxxkimwoaini/pvcomm-sim 、lxxkimwoaini所有、PUBLIC、默认main。功能分支已推送；da7b526的CI45582627395实际success，Ubuntu/Windows Python、Arrow、Physical models、Frontend/browser五job全部success。76e73ad的CI45581377368因长行Ruff失败，最终已按原标准修复；不隐瞒失败。PR #1/#2保持OPEN且未合并。本阶段通过开发门禁，不代表正式研究参数冻结、现场校准、秒级实测云影或正式数据集发布。下一步按堆叠顺序审核PR，并确定后续校准/阶段范围；不自动进入30站或训练。

### V0.2 审核交付与远程核验

验收文档提交bfab4859ab2ede3975547d389e28bd2f79d07c7b已推送；其push CI45583409443及PR CI45583485587均实际success，各五job全部成功。草稿PR #3：https://github.com/lxxkimwoaini/pvcomm-sim/pull/3 ，OPEN、isDraft=true，base=feat/v01-acceptance、head=feat/v02-power-cosimulation；依赖#2、#2依赖#1，均未合并或代用户批准。git ls-remote与本地bfab485一致，工作区干净。全分支55文件路径/凭据模式检查及本地文档链接检查通过，无data/outputs/PCAP/Parquet/外部输入入库。此补记仅文档，不把上述代码/CI SHA混称为补记本身SHA。

## V0.3 启动与前置切片（2026-11-25）

读取AGENTS、V1.3、V0.2报告/进度及ADR0001–0005；基线e4e14b9，当前feat/v03-multisite。核查PR #1→#2→#3仍OPEN（#3 draft），不合并；远程PUBLIC和main不变。环境Git2.52、gh2.102、uv0.11.13、Python3.11.15、Node24.14、pnpm11.5，WSL抓包/命名空间与科学依赖可用。计划docs/plans/2026-11-25-v03.md，ADR0006记录场景屏障及增量读取。

新增严格0.3配置、配置驱动区域聚合/站点接入资源、通用馈线适配；尚未做规模验收。0.1/0.2原配置不改。修正IPC为最多128KiB尾读；M2投影采用只读observed的临时SQLite增量索引，每次只摄入完整新增行，Windows退出连接回归通过。新增宿主映射与场景起点分离，0.3初始化时气象逻辑固定0，主站/RTU准备后屏障启动1×。新增物理电压/热限/功率平衡/反向潮流/Q斜率/快照时序QC；电气越限不推断通信标签。事务保留严格成功标准，另报告拒绝/超时/缺反馈，不将失败当成功。

实际本地普通pytest106 passed/10 deselected，mypy69文件通过；Ruff检查/格式化执行后须最终检查；schema已生成0.3。初次新增SQLite测试揭示Windows退出句柄问题已修复；并站fixture的CA引用错误已修正，不调整验收门槛。真实断联120s回归已编写但此记录时尚未执行，V0.3未验收。下一步冻结代码后运行tests/lab/test_v03_prerequisites.py，持久化outputs，检查物理连续、恢复最新遥测、真实双端PCAP及独立QC；通过后方可扩大站点规模。

首轮冻结834a7c0真实120s断联实验：outputs/v03_prerequisite_001/test_physics_continues_without0/v03_03_disconnect_001，completed且code_dirty=false，物理持续/恢复最新值断言通过，独立QC失败于PV01终端回调少一个停止期间S帧；P0/P1报文一致且无工具丢包。失败报告prerequisite-qc.json保留，不计通过。增加rtu_stopping精确边界，QC v3单列停止调用内的入站控制帧；边界外/I帧仍拒绝的回归通过，详ADR0006。普通107 passed/10 deselected、Ruff/mypy69通过；WSL power2 passed/114 deselected。834a7c0 push CI45604695138五job实际success。下一轮独立冻结重验不覆盖001。

第二轮a15824f冻结三站断联：outputs/v03_prerequisite_002/test_physics_continues_without0/v03_03_disconnect_001，真实test1 passed，143.21s；completed/code_dirty=false/cleanup=[]，物理继续、云影变化、恢复最新遥测、独立协议/物理/Arrow全部通过。第一轮失败保留。下一切片增加空间平流、每站历史背景覆盖、独立馈线诊断、周期/死区上送、分站并行命令及区域分批容量/主站可观测资源分配，新增0.3规模配置/资源采样；普通113 passed/10 deselected、mypy73、Ruff通过，真实新功能尚待逐级验证。详ADR0007。

新增独立tick/空间模型/死区报告后的第三轮f9afe25真实三站断联test1 passed/146.28s，outputs/v03_prerequisite_003；仍为冻结code_dirty=false。三区独立聚合通信和物理QC通过。后续revision2增加通过真实通信进入M2的线路/变压器负载及入线功率点，保持revision1复现；普通113 passed、Ruff/mypy73通过。将基于独立冻结checkout进行3/10/20/30逐级测试，开发UI不得修改运行中的冻结源码。

e27c44b冻结checkout outputs/v03_source_s1，3站120s真实运行outputs/v03_scale_s1/v03_03_normal_001，completed/clean、全QC通过（2344完整APDU、2785包、2137对象）；10站120s completed且物理/调控闭环通过，但P0工具丢包54，独立协议QC失败，目录保留，不进入20站。规模脚本首尝试被PowerShell展开Bash变量，未产生运行证据；后续显式参数调用。新增ADR0008抓包原生Linux spool/核对哈希后归档，关闭V0.3 immediate模式，门槛不改；待冻结重验10站。普通113 passed/10 deselected，Ruff/mypy73通过。
