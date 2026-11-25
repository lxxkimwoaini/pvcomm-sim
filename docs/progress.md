# PVComm 开发进度

日期：2026-11-25（Asia/Shanghai）。当前阶段：**V0.0 通过；V0.1 已有真实独立端点短时闭环，整阶段未验收**。两个 30 分钟回环稳定性运行已完成，但不等同于独立 P0/P1 的正式验收。初始只有 AGENTS.md 和 V1.2/V1.3 方案；无既有实验。本文件只记录实际执行，不将计划标为完成。

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
