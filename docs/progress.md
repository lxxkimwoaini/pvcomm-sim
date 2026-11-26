# PVComm 开发进度

日期：2026-11-26（Asia/Shanghai）。当前阶段：**V0.4接续实施中，整体V0.4尚未验收，V0.5未启动**。V0.0–V0.3、V0.4a/b及六类P0三站机制已有开发验收；首四组配对已用QC v2及标签v3/QC v4逐站复核通过，历史原件保留。独立12次正常流量校准通过；原四组业务对照有效，但固定统计规则下均未匹配，全部保留。A08分组维护真实验证正在执行。新重复配对、每类40次独立覆盖、30站复验、批量资源与科研工作台仍待完成。本段为当前状态，下文旧记录为历史。

## 独立校准完成及回顾性匹配

3ef4f83冻结实验完整退出0，六组12次运行通过原正常门禁及独立标签QC；阈值SHA256为276c634a7c9664208e0a9228a5778f87c8d1a78482021f85623b97b31c15e96d。按预冻结规则检查旧四组：有效4/4、业务对照4/4、统计匹配0/4，不改原件、不改阈值、不删除未匹配样本。详细报告见`docs/reports/2026-11-26-v04-similarity-calibration.md`。

7666306已推送并核验Actions 45816183448、45816175204为success；冻结至`/var/lib/pvcomm/sources/v04d_maintenance_s1`，WSL普通测试350 passed、64 deselected（19.46s）。三项分组维护真实实验执行中，持久目录`/var/lib/pvcomm/runs/v04d_maintenance_s1/experiments`，尚无最终通过结论。

### 分组维护首轮失败及修复（后续实际记录）

首轮pytest实际3 failed（650.32s）。区域全部失败及跨区域站点组的机制/数据/独立标签QC均通过，但末尾测试误要求没有故障的控制站也有故障恢复记录；已按原字段不适用契约修正测试，不放宽受影响站的恢复门禁。混合恢复则是实际机制失败：PV01连接OPEN迟到约66秒、没有相应恢复GI/完整遥测，PV02正常恢复，实验数据QC仍通过；不能把这次混合运行计为机制验收通过。

bade029修正启动顺序：实际RTU恢复启动后发布实验进程就绪标记，再由主站连接；依然受原15秒预算约束，不读取电气真值或未来反馈。旧原件保留，新冻结源及实验目录分别为`/var/lib/pvcomm/sources/v04d_maintenance_s2`、`/var/lib/pvcomm/runs/v04d_maintenance_s2`，三项真实复验执行中。7b85cfc验收聚合中的临时机制门禁遗漏已在bade029恢复，未使用其发布任何通过报告。bade029 Actions 45821147355、45821134130实际success。

队列代码及固定12组配对矩阵已建立，普通测试包含有限重试、取消/续跑、路径/哈希、并发锁、容量前置阻断和未匹配样本保留；真实队列、覆盖、吞吐仍未验收，不用CI代替真实运行。

冻结队列验证前复核：Windows普通测试361 passed、65 deselected（13.63s），Ruff/format208文件、mypy146源文件及schema检查退出0；后新增真实队列测试由lab标记隔离。全矩阵（含有限重试）留存预估及单次临时PDML下限均纳入存储前置检查，复用既有保留策略，不把下限冒充峰值。CLI在completed_with_failures时退出1。当前PR #1→#2→#3→#4→#5依次堆叠，均OPEN，#3～#5为Draft；未合并任何PR。

## 接续切片：分组维护代码冻结准备

- 统计规则冻结提交3ef4f83已推送；Actions 45812668204、45812656897实际success。冻结源码`/var/lib/pvcomm/sources/v04d_similarity_s1`，WSL普通测试332 passed、61 deselected（22.18s）。六组独立正常重复校准在`/var/lib/pvcomm/runs/v04d_similarity_s1/calibration`执行中，阈值尚未完成，不宣称统计相似性通过。
- 新增场景.2分组维护、逐站合法授权/STOPDT握手/信号及恢复；A08目标可为维护组子集。新增独立维护QC，连接、协议和业务恢复分别记录；旧场景.1哈希及v2核验路径保持兼容。ADR0023记录边界。没有重建运行器。
- Windows实际普通测试350 passed、64 deselected（14.48s），包含18项新维护用例及相关74项回归。首次静态检查发现测试闭包绑定和报告字典类型问题，已修正，Ruff/mypy139源文件/schema检查重跑退出0。三项真实分组维护实验尚未运行，待校准结束后串行执行，避免其他实验负载影响校准。此段为代码冻结准备，不算A08多站验收通过。

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


### V0.3 工作台、正常设备操作及规模门禁跟踪

a2187a6十站第二轮120s全QC通过（outputs/v03_scale_s2/v03_10_normal_001），6830完整APDU、8009包、6250对象，11路dropped=0。二十站同冻结代码120s completed/clean/cleanup=[]且21路dropped=0，但日志对齐P99=164.241582ms（max310.274496ms）失败；outputs/v03_scale_s2/v03_20_normal_001保留，不进入30站。对照目录改用WSL持久Linux文件系统/var/lib/pvcomm/runs/v03_scale_s3，仅验证存储开销影响，结果待核验。a2187a6 push CI45610679904实际success。

工作台新增配置双拓扑、区域覆盖率/聚合、空间遥测、调控确认状态、原始回调统计、基础设施资源独立端点、100条联合游标分页及最多120点极值/首尾/缺口保留。M2不读truth补齐；新契约回归验证时间因果/并列事件分页/资源隔离/重复证据409及曲线峰值。新增显式正常本地enable操作，不改已接受设定值及connected/faulted；30站候选310s停机、330s恢复，由真实遥信上送，独立QC v03-power-v2保留旧报告。此功能真实验收尚待30站短运行。

普通检查已实际116 passed/10 deselected（新增设备操作单测后待最终总检）、mypy74文件、Ruff/schema、前端类型/Vitest3/build通过；Vite约817kB提示保留。发现并修复测试类型注解及package.json格式失败，没有降低检查。包更新0.3.0.dev0，README/CLI明确V0.3开发状态与未验收，不重标旧版本证据。PR #1/#2/#3仍OPEN、#3 draft，未合并。


二十站Linux持久目录对照a2187a6，/var/lib/pvcomm/runs/v03_scale_s3/v03_20_normal_001，120s completed/clean、全部QC通过：12849完整APDU=100%、21路dropped=0、日志P99=14.146801ms/max38.434775ms，cleanup=[]。后续规模验收使用同一Linux存储环境。普通最终117 passed/10 deselected、mypy77、Ruff通过。工作台首次10站浏览器失败定位为1s轮询频繁disabled关闭选择菜单；修正前台选择与后台轮询隔离、generation保护过期响应，重验结果另记。

真实10站浏览器复验2 passed/1 skipped（空态），逐值对账、物理指标切换、因果游标、桌面/移动与离线恢复通过。保留最初菜单关闭及忙时回放丢失失败记录，修复实际UI竞态。


工作台/正常操作提交55a2d1b已推送，CI45613146105实际success。冻结checkout=/var/lib/pvcomm/sources/v03-s4，30站360秒预检=/var/lib/pvcomm/runs/v03_scale_s4/v03_30_normal_001运行中。独立Web测量预检outputs/v03_web_preflight_002已实际完成off/live/replay 30秒负载流程（仅测量脚本预检，不替代1h门禁）：WSL localhost真实API、Windows Chromium/Vite，保留请求端到端时间/服务时间/字节、Linux API及Windows进程树CPU/内存、浏览器CDP指标和截图，无fatal错误。脚本尚需与最终冻结SHA一起正式运行。

新增正常设备状态变化触发潮流补算（不等待5秒周期），调度回归通过。普通119 passed/10 deselected、mypy79/Ruff/格式通过；WSL科学及Arrow6 passed/121 deselected；旧V0.1真实协议/隔离/取消清理回归3 passed/47.66s，持久目录/var/lib/pvcomm/runs/v03_legacy_regression_001。没有重报旧30分钟/90秒实验。当前仍未通过V0.3门禁。


30站360s预检55a2d1b全QC通过：/var/lib/pvcomm/runs/v03_scale_s4/v03_30_normal_001，41138完整APDU=100%、31路dropped=0、P99=3.335434ms/max56.908908ms，90命令正确认/接受/反馈独占证据通过，2次本地状态变化真实收到（8.683161/58.346122ms）。正常资源不足2044.4795kW明确保留；completed/clean/cleanup=[]。真实30站Web2 passed/1 skipped（空态），18.4秒真实M2/指标/回放/移动验证通过。

方案6.1正常链路补齐：新增每区域/站点可选双向NetEm，开发配置合计名义10/40/100ms、抖动3/8/20ms、显式分方向seed，0额外丢包；全部仿真假设，非现场参数。独立probe实际确认内核支持seed，自动清理。ADR0009记录版本/参数及按规模重验要求；QC升v03-power-v3单独目录。0.1/0.2不受影响。当前120普通passed、mypy79/Ruff/schema及前端检查实际通过，新增NetEm真实规模结果待记录，仍未通过1h门禁。


NetEm首轮三站4cea408运行completed，独立QC v3失败于重组归属帧（matched1090/2344），自动停止规模脚本；路径v03_scale_s5原始PCAP/解析/失败报告保留。定位独立TCP核验器未把补齐前序缺口的帧计入后序完整PDU的依赖时间，修复后续TCP在线因果边界，协议字段和packet_index严格对账不改。新增完整APDU乱序回归；版本v01-evidence-v4/v03-power-v4，待原证据重验。普通122 passed/10 deselected，mypy80、Ruff通过。新增NetEm读回错误seed拒绝回归，tc预检查移到创建运行目录前。V0.2原三站90秒新代码回归4cea408已真实completed，parse/Arrow/QC退出0，目录/var/lib/pvcomm/runs/v03_v02_regression_001/v02_clear_001，保留V0.2复现能力。

### NetEm逐级实测与生命周期修正

6e42aa9的CI45616151461实际success。新冻结源码`/var/lib/pvcomm/sources/v03-s6`对S5三站原始PCAP执行v4 QC，全部通过：2344完整APDU严格对账，0工具丢失，P99=3.970862ms/max426.785181ms；旧v3失败不覆盖。运行代码4cea408和核验代码6e42aa9分别记录。十站S6（120s）全部QC通过：6772完整APDU=100%、11路0drop、P99=73.565552ms/max1251.581420ms。二十站S6（120s）completed、物理及解析通过，但日志P99=860.012185ms/max2128.690613ms、PV15少一个入站S回调，独立QC失败；自动停止，不进入30站。

逐帧及c104 v2.2.1源码核对后，修正V0.3初始化/停止生命周期，详ADR0010；保留原V0.1/V0.2默认。新源码待实际逐级重验。测量脚本增加真实DOM游标/功率时间、近似绘制时间和干净代码要求，实时与回放年龄分开。预检003因重复辅助输出目录失败，记录保留；独立测量编号修正后004完成30秒真实off/live/replay，无fatal，不能作为一小时验收。Windows总check实际122普通测试、mypy80、Ruff/schema/前端质量/Vitest3/build通过；随后新增测量汇总4项单元测试通过，生命周期回归及最终总检另记。当前V0.3仍未通过。

生命周期修正后的Windows完整check退出0：129 passed/10 deselected、mypy81、Ruff/format/schema、前端ESLint（仍2条缩进warning）/Prettier/vue-tsc/Vitest3/build通过，Vite817kB提示保留。首次类型检查发现单元替身lambda依赖append返回值，改为显式返回bool后通过，未删测试。该普通check不计真实协议验收。

76a3d2c冻结源码`/var/lib/pvcomm/sources/v03-s7`逐级重验启动。三站120s全QC通过，P99=23.281709ms/max601.993079ms；正在十站。真实30站浏览器初轮等待选项超时，保留outputs/v03-prehour-e2e.log及失败上下文；改为先等待首次真实快照已显示、跟随按钮可用，再操作下拉框（不延长测试期限、不force点击），复验2 passed/1 skipped、22.2s。测量harness采用同一就绪边界。预检005真实30秒三时段完成、无errors，实际请求live9/replay2、API资源每阶段10样本，汇总明确acceptance=false（standalone且不足1h、无新实时功率），不冒充正式验收。

### 最终逐级与一小时实测、交付一致性修正

S7的3/10/20/30站全部QC通过，完整APDU分别2267/6654/12736/40855，解析率均100%；日志P99分别23.281709/3.845138/3.867107/3.788537ms，工具丢失均0、cleanup=[]。30站短运行含30秒Web预检006，不能把整个360s当无Web对照。76a3d2c的CI45618961711、29aa7e0的CI45619736287实际success，各五job成功。

最终冻结29aa7e0b17c094e13692166f2ddd7fcc62d01cb0，源码/var/lib/pvcomm/sources/v03-hour-s8。最终真实回归/var/lib/pvcomm/runs/v03_final_regression_s8：断联1 passed/138.17s；V0.1协议及清理3 passed/45.05s；V0.2 90s run/parse/Arrow/QC均退出0；科学与Arrow6 passed/133 deselected/9.21s。上述实验未并行运行命名空间任务。

一小时证据/var/lib/pvcomm/runs/v03_hour_s8/v03_30_normal_001，业务起点北京时间2026-11-25 21:22:08.701296625，实际3599.999549616s、completed/clean/cleanup=[]。run墙钟3637.690s（含初始化/停止），parse235.614s、独立QC223.299s、性能汇总0.632s，均退出0。333021完整APDU全部匹配；31路捕获及66个NetEm qdisc丢失0；P99=3.747593ms/max476.711026ms；90命令全部accepted_complete，物理108664样本、740次三馈线潮流/22200站点行全量重算误差0。Web三阶段真实完成，1197实时请求、238回放请求均200；9550新功率显示样本P95=1469ms/P99=1555ms/max2601ms，performance-report.web_measurement.passed=true。

备份outputs/v03_hour_s8_backup共394文件、1390074210字节，复制文件逐一与原件SHA256一致，原件保留；备份清单与冻结源码tar同目录。冻结源码tar SHA256=a24c40860d717bc242e13b8924367a25eda69ff4cb5034ac2dc88315594ad7e5。正式数据集尚未发布，不将不同输出根的同名开发run_id混合。

交付检查发现Health.stage及API/架构文档仍为V0.2，修正为V0.3-development并重新生成OpenAPI/TS、同步契约和UI测试；冻结运行源码未改。完整check退出0：129 passed/10 deselected，mypy81、Ruff/format/schema/前端质量/Vitest3/build通过，保留2条HTML缩进warning和817kB构建提示。最终一小时历史UI初两次综合测试触及默认30s总限：日志outputs/v03-final-real-e2e.log及-002.log，失败上下文分别复制outputs/v03-final-ui-failure-001/002。测试改为一次站点/IOA索引和每曲线聚合不匹配点断言，仍核对全部显示点，不改30s或断言语义；003实际2 passed/1 skipped、26.3s，其中真实数据操作23.4s、27183点核对。最后web-test再次退出0。该测试修正不改生产业务。最终详细报告与远程交付核验随后补齐，不进入V0.4。

### V0.3开发验收结论与资源估算

V0.3开发门禁通过，详见docs/reports/2026-11-25-v03-acceptance.md；README、计划、API/架构/数据字典及开发指南已同步。交付一致性修正提交f157172334b3962b79982984429d6b32beb7b5e6已推送，其CI45636936098实际success、五job成功；一小时运行/QC/性能仍明确归属29aa7e0，不冒称f157172实跑。PR #1→#2→#3仍OPEN、#3 draft，未合并。

三时段仿真单核CPU均值47.787/43.997/43.398%，RSS最大6.115/6.197/6.275GiB；Linux API CPU均值实时40.605/回放7.479%、RSS最大122.527/124.953MiB，Windows测量子树CPU均值32.533/10.215%、WorkingSet最大1.248/1.279GiB，共享页会重复计。三时段为顺序工程诊断，不能作随机因果结论。电气间隔各站P99最大1005.623ms、最长1027.296ms；求解周期P95=61.138ms/max113.787ms。母线0.999745–1.008135pu、线路17.596%/变压器33.961%最大；P/Q平衡误差最大1.221214e-5kW/1.178254e-5kvar，快照跨度1004.846ms/龄期1105.074ms最大，无无效或电气越限，反送作为工况保留。

保留数据1383250188B/h，其中PCAP129133060B、Parquet50170860B；按720×1.5约1.494TB（备份另计）。解析临时PDML观测为11275084964B，成功后既有流程清理，峰值空间另预留；没有将该单文件值冒充全流程磁盘峰值。720个同负载一小时单元串行连同实测初始化/解析/QC线性约819.45h；8实例90h未测，不承诺。现有C/E空闲不足正式数据与备份需求；现场校准、并发验证、正式参数冻结、720h批量、V0.4/训练均未开展。

### V0.3远程审核交付核验

验收文档提交8bc6489b6060ed1576710feb61702088a846bfc6已推送，git ls-remote与本地SHA一致；push CI45638703911及pull_request CI45638866871均实际success，Ubuntu/Windows Python、Arrow、Physical models、Frontend/browser五job全部成功。草稿PR #4：https://github.com/lxxkimwoaini/pvcomm-sim/pull/4 ，OPEN/isDraft=true，base=feat/v02-power-cosimulation，head=feat/v03-multisite。22:55北京时间复核#1→#2→#3→#4仍全OPEN，#3/#4 draft，未自动合并或改写历史；远程仍为lxxkimwoaini/pvcomm-sim、PUBLIC、默认main。

交付检查：29份Markdown的22个本地文件链接无断链，CLI实际版本0.3.0.dev0、30站配置校验退出0，diff检查通过，变更路径无data/outputs/PCAP/Parquet，非忽略项目文件未发现高风险凭据模式。本机8000/5173无监听；WSL实验namespace列表为空，宿主两个Docker网桥veth不属于本次资源，已保留。所有复制证据原件仍在，最终成功UI证据另存outputs/v03-final-ui-passed-003。此补记仅文档，不把8bc6489的CI结果混称为补记本身CI；最终分支CI以远程实际状态及交付回复为准。


## V0.4a 前置修复（2026-11-26，验证中）

基线5c406666e31e6edb3268e2be041ec9aa881ed26d，当前feat/v04-scenarios。已阅读AGENTS、V1.3、V0.3报告和ADR，新增分阶段计划2026-11-26-v04及ADR0011/0012。gh账号lxxkimwoaini，PUBLIC、main保留既有授权；PR #1 main←fix/v01-evidence-integrity，#2 ←feat/v01-acceptance，#3 ←feat/v02-power-cosimulation，#4 ←feat/v03-multisite，均OPEN，#3/#4 draft，未合并。

实现候选：实验完整性/质量与默认正常QC分离；真实ACT_CON与transmit结果分离、超时会话隔离；缺测/过期/品质无效按站排除且后续任务继续；旧气象契约冻结，新离线来源描述符校验日期/位置/产品/许可快照/哈希；逐值Arrow读回与原JSONL比对；保留原件的无损gzip及只读预算；M2品质与真实结果显示。包/Health/生成契约同步0.4开发状态，不声称完成场景库或标签。

初次普通总检148 passed/12 deselected，Ruff/mypy88/schema通过，前端Prettier失败，已修正当前Vue文件。WSL科学/数据全量155 passed/5 deselected、26.89s。新增来源完整性、全缺测、无效品质等回归后首次156项检查出现4失败：两项新fixture误读空调度、schema未重新生成、旧timeout fixture实际仍有有效线上ACK。已修正fixture并新增“有ACK但缺回调为证据不完整”负测试，未放宽业务门槛。最终静态/普通/Web检查及真实冻结实验结果将在后文追加。

新三站210s真实配置v0.4a-prerequisite.yaml将验证PV01维护缺测排除、PV02正常停机后合法拒绝、PV03和后续任务继续；独立协议fixture将实测不回复、超时隔离及其他站成功。当前均未执行，未标记通过。六类P0正式异常、故障核验、自动标签、配对/覆盖报告、场景表单队列仍未完成。原始历史输入与既有运行证据未删除，未生成720h或训练模型。

冻结候选前最终检查：完整`uv run --frozen python scripts/dev.py check`退出0，158 passed/13 deselected，mypy88文件，Ruff/format/schema/TS/ESLint/Prettier/vue-tsc/Vitest3/build通过；保留既有2条HTML缩进warning及817.61kB构建提示。WSL `pytest -q -m "not lab"`为165 passed/6 deselected、14.90s，含科学/Arrow。真实本机API的Playwright为2 passed/1 skipped、4.4s，跳过需实际运行数据的回放项，不能算实验验收。准备以本次候选提交创建原生Linux干净源码worktree，使用新持久输出执行lab；未与其他namespace运行并行。

冻结候选2f152d9已普通push，CI待实际查询。首轮v04a_s1的顶层data符号链接被git报告为未跟踪，manifest正确记录dirty；该运行完整保留但不用于验收。下轮改为实体data目录内的忽略输入链接，不修改原始输入或运行中的源码。等待期间独立复核修复：非有限遥测写null且quality_valid=false，防止allow_nan=False日志失败；被后续命令截短的执行窗不生成反馈缺失标签。对应45项回归及mypy88/Ruff通过。旧反馈缺失fixture含5s后续命令，应拆为完整10s无取代事务；新测试保留取代情况下的incomplete_or_ambiguous，不放宽期限。真实NASA新接口验证valid=true，原hash不变；V0.3备份只读storage-audit退出0，结果在outputs/v04a-v03-storage-audit.json。

首轮真实v04a_s1结束：2 failed/230.42s，三站运行completed但dirty不能验收；协议fixture发现c104浮点点值拒绝Python int(10)。应用接口现将有限非负目标规范为float，真实确认逻辑未改；回归断言原生float并保留真实故障测试。首轮原始PCAP/事件/失败日志保留，namespace已清理。实体data目录的干净4b23363源码已启动v04a_s2三站复验；不会把s1或尚未运行的类型修复算通过。2f152d9的push CI45741394907实际success、五job通过，不等同于后续提交CI。

v04a_s2实际2 failed/237.34s。三站clean/completed，真实缺测排除、1次否定确认及4次成功事务均满足，物理/解析/工具丢包/时间对齐通过；独立端点QC因PV01缺一个S回调失败。定位P1到达时间在server.stop结束后14.889ms，不能扩大QC尾部豁免。新增ADR0013：仅已授权V0.3计划维护先由主站真实STOPDT→OPEN_MUTED→disconnect→CLOSED，然后控制器才暂停RTU；物理持续，非计划故障不走该握手，0.1/0.2不变。161项普通测试、mypy88和Ruff通过，待新冻结实跑。第二项旧整数fixture失败仍由72e56a0修复，其独立v04a_s3/silent已实际1 passed/1 deselected/3.97s，确认超时3.000302849s、同会话隔离、其他站成功且clean，属于真实协议fixture而非正式A05标签验收。72e56a0 push CI45741724275五job成功。

当前代码真实浏览器回放既有30站小时证据：2 passed/1 skipped、25.6s，核对27183显示点，结果保存在outputs/v04a-real-ui-s1；无mock。资源日志原件2332442B，无损gzip149953B，解压和源SHA256均37d131a09865f69fda29b33e25450790be9acffbb3bf3174627a397990624f69，源复查相同且保留；归档只在outputs/v04a-verified-resource-archive。

v04a_s4冻结32f93f1前置真实2 passed/238.41s：三站clean/completed/cleanup=[]；PV01缺测排除、PV02实际否定确认、PV03及后续任务继续；实验完整性/数据质量通过、正常业务回归按预期false。2480/2480完整APDU、P99=3.722319ms/max5.012098ms、工具和12路qdisc丢失0；656物理样本通过。独立silent确认超时3.000395088s、同会话隔离、其他站成功。WSL科学/Arrow全量168 passed/6 deselected/32.48s。32f93f1 push CI45742721693五job实际success。

同冻结旧版回归实际1 failed/3 passed/193.29s：V0.1回环、独立端点及取消清理通过，V0.3断联物理连续和恢复最新状态通过，但严格独立端点QC失败。PV02/PV03的P1原始PCAP缺最终3条控制帧；P0和终端raw均有。tcpdump分别captured866/received875、captured783/received792，kernel drop0。失败目录/var/lib/pvcomm/runs/v04a_s4/legacy完整保留，未放宽门槛。ADR0014新增端点结束后有界统计确认的buffer排空，最终统计使用末组；旧即时抓包路径不变。真实ICMP缓冲尾包预检1 passed/3.56s（三次往返），目录/var/lib/pvcomm/runs/v04a_capture_preflight_001；该fixture不是正式IEC104实验。新冻结旧配置及V0.4a重验尚待执行，不能宣布V0.4a整体通过。

缓冲排空候选完整check退出0：162 passed/14 deselected、mypy90、Ruff/format/schema/TS、前端ESLint/Prettier/vue-tsc/Vitest3/build均通过；保留既有2条缩进warning与817.61kB包提示。该结果不替代新的真实兼容验收。

67ee2ef冻结v04a_s5旧回归实际1 failed/4 passed、67.67s。V0.3启动时暴露原有master.ready存在性与write_text内容发布竞态，读取空串报ValueError，未进入完整业务实验；manifest如实failed、保留证据。四路捕获排空均complete、captured=received、kernel drop0；真实尾包fixture及旧协议三项通过。改为同目录临时文件关闭后原子rename发布业务起点，新增阻塞半写入的并发回归（1 passed），不能用重试sleep吞掉无效值。67ee2ef push CI45744263481五job实际success。新冻结完整真实兼容重验仍待执行。

### V0.4a最终前置验收通过（限定范围）

冻结097a616bc19ad7204c34aaa4ff25f9cc011ae485，原生源码/var/lib/pvcomm/sources/v04a_s6，新持久证据/var/lib/pvcomm/runs/v04a_s6；全部manifest clean、completed、cleanup=[]。旧真实回归5 passed/188.19s：V0.3断联2109/2109完整APDU、P99=1.868130ms/max2.793382ms、9个accepted_complete；独立端点/回调对应、物理连续/恢复新状态、Arrow/潮流及四路缓冲排空全部通过。旧V0.1真实回环、独立端点、取消清理及ICMP尾包fixture也通过。未重跑1h/30min正式门禁，不把短兼容回归冒称整套旧版本重新验收。

同冻结V0.4a前置2 passed/237.40s：2528/2528完整APDU、100%、不完整0、P99=3.748478ms/max4.084588ms；P0/3份P1捕获和12路qdisc丢失0，独立回调/字节一致。4个accepted_complete+1 rejected，PV01 stale被排除，PV02真实否定确认，PV03及后续任务继续；657物理样本、来源/重算/Arrow/快照/平衡通过。run_integrity/data_quality=true，normal_regression=false保留真实正常回归结果。silent协议fixture确认超时3.000455484s、同会话隔离、另一站成功；fixture不当作正式A05或双端故障验收。

同冻结旧V0.2晴天90s CLI run→parse --parquet→默认严格QC均退出0；462/462完整APDU、工具丢失0、P99=11.492783ms/max12.092638ms、9次事务完整。WSL科学/Arrow全量170 passed/7 deselected/12.56s；Windows最新普通163 passed/14 deselected及Ruff/format/mypy91通过。完整前端/schema检查和真实小时M2回放结果见此前记录，本轮抓包/IPC修复未改前端。097a616 push CI45744568239五job实际success。最终namespace为空，旧失败PCAP与运行目录均保留。

README旧“V0.4尚未开展”及开发指南过期QC版本已修正，新增完整V0.4a报告docs/reports/2026-11-26-v04a-acceptance.md，并细化V0.4b正常场景计划。当前仅前置修复/兼容门禁通过：故障施加和标签质量仍not_evaluated，N01–N10、六类P0、配对/正式标签/UI任务队列及最终四报告未完成。下一任务按V0.4b计划实现配置驱动业务时序，先验证无调控正常背景，再逐类三站真实覆盖，不直接展开正式720h或训练。仓库仍https://github.com/lxxkimwoaini/pvcomm-sim、lxxkimwoaini、PUBLIC、默认main；PR #1–#4堆叠OPEN不合并。最新交付提交/推送及CI以实际远程核验为准。

### V0.4a远程交付核验

验收文档提交ac4ed18c852ad581637c6f569fe8a348f2cc2f0f已push，git ls-remote与本地一致；push CI45745825098及pull_request CI45745834313实际success，五job均成功。草稿PR #5：https://github.com/lxxkimwoaini/pvcomm-sim/pull/5 ，OPEN/isDraft=true，base=feat/v03-multisite，head=feat/v04-scenarios。PR #1→#2→#3→#4→#5正确堆叠，均未合并。源代码冻结和真实验收仍归属097a616，不将文档提交冒称实跑。交付检查25个本地Markdown链接无断链、无data/outputs/PCAP/Parquet/密钥路径进入提交，CLI实际0.4.0.dev0，工作区只有本次文档补记；最终分支CI以远程实际结果和交付回复为准。新增仅文档补记不会修改冻结实验或旧证据。


## V0.4b显式调度候选（2026-11-26）

继续feat/v04-scenarios，基线1599f928，工作区初始干净；AGENTS、V1.3相关契约、进度、V0.4a最终报告及ADR0011/0012已核对。PR #1→#2→#3→#4→#5仍OPEN，#3–#5 draft，未合并。工具Git2.52.0、gh2.102.0、uv0.11.13、Python3.11.15（Windows/WSL）、Node24.14.0、pnpm11.5.1可用，gh实际账号lxxkimwoaini；WSL以root运行授权隔离实验，namespace初始为空，Linux卷可用约907GiB（动态磁盘数值不是宿主真实空闲承诺）。

新增ADR0015和0.4-scenario.1组合契约，显式空调度、确定性时序、单站/子集分配/总召唤、新CLI入口和诊断侧完整计划；保留旧run、配置和QC门槛。正常维护的恢复召唤在独立维护任务内及时执行，避免被后续业务计划阻塞；旧路径不变。新增场景独立QC和N01–N10小规模开发模板，尚未把模板声明为真实覆盖或正式标签。

当前16项新增调度/因果/证据负测试通过，普通全量先前176 passed/14 deselected（随后新增3项单元及21项lab待最终总检），mypy98文件与Ruff通过。已实际以归档输入/pvlib核对日出日落候选：2024-06-23日出21:12:31 UTC，2024-06-24日落11:42:03 UTC，取120秒场景跨越该模型几何边界；小时背景不冒称秒级实测。下一步冻结clean候选、三站逐类两变体真实运行、原始证据保留、解析/Arrow/独立QC；任何失败停止后续批次。V0.4b当前未验收，六类异常/正式标签/配对/UI队列未开展，本次不生成720h或训练。

候选最终普通总检 `uv run --frozen python scripts/dev.py check` 实际退出0：179 passed/35 deselected，mypy98文件，Ruff/format/schema/前端生成类型、ESLint/Prettier/vue-tsc/Vitest3/build通过。保留既有2条HTML缩进warning和817.61kB包提示。真实21项正常场景尚未运行，普通通过不替代实际验收。

首轮冻结dfa09aa已push，push CI45748744637实际success；WSL科学/Arrow186 passed/28 deselected、11.62s。原生源码/var/lib/pvcomm/sources/v04b_s1，证据/var/lib/pvcomm/runs/v04b_s1。代码复核发现缓存先于available_at记录发布的并发因果窗口，故主动SIGINT终止首轮真实批次；pytest退出2、130.82s、no tests ran（没有完整通过用例），原始部分运行保留，不当作验收。修正为同一观测锁内先记录再发布缓存，写入失败不暴露未记录值；新增并发及I/O失败负测试，39项相关测试通过、mypy98通过。下一轮将冻结新提交完整重跑。

新冻结4ab3fc5在/var/lib/pvcomm/sources/v04b_s2顺序运行21项真实正常场景，证据/var/lib/pvcomm/runs/v04b_s2；截至本次记录N01两变体、N02夜间/日出及首个N03共5项已通过，后续仍在运行，不预报最终结论。4ab3fc5 push CI45749164612和PR CI45749169821均实际success。普通最终181 passed/35 deselected，Ruff/format/mypy98通过。

独立审查修正开发模板来源组：V1.3 §14.1要求同一原始气象日期跨场景共同分组，不能将模板N代码或种子当分区边界。新模板按NASA来源哈希+UTC日期产生source_group_id，18项相关回归通过；当前4ab3fc5冻结物理/协议实验不修改，原始模板ID保留，覆盖报告将另给共同来源映射。本阶段没有生成任何训练/验证/测试划分。此元数据修正不改变调度、随机种子、气象、电气或通信行为。

正常调度QC复核补齐严格P0/P1字节对应门禁：实验质量profile允许故障的跨端差异作为通信影响，但正常profile不得继承这一放行条件。新v04-normal-schedule-v2保留v1文件并记录运行代码和核验代码SHA/dirty；新增负测试证明“有效异常实验”不能自动通过正常通信核验。19项相关测试和mypy98通过；原始实验仍冻结4ab3fc5，完成后用新冻结QC重验，不篡改旧报告或PCAP。

真实UUID场景回放检查修正：浏览器测试按实际收到的1008遥测判定电力曲线能力，不再按run_id的v02_/v03_前缀跳过。使用N03-1已完成原件的逐文件SHA256一致副本（80文件、7,699,260B，原件保留），Playwright实际2 passed/1 skipped、5.5s，核对1380个显示点并执行辐照度/并网点电压、因果游标、移动布局和离线恢复；仅空态用例按真实数据模式跳过。证据outputs/v04b-real-ui-passed，复制清单outputs/v04b-ui-evidence/copy-hashes.json。单文件ESLint/Prettier/vue-tsc退出0。此前最终f48d114完整check实际182 passed/35 deselected、mypy98、前端Vitest3/build通过，保留既有警告。正常场景批次仍在运行，不能据此宣布V0.4b通过。

浏览器能力判定复核补充：API为未接入的测量也保留空series，故1008必须有非空实际接收点才启用物理曲线断言。修正后旧V0.1真实副本（50文件、530388B，逐文件哈希一致）Playwright 2 passed/1 skipped、4.5s，111点；UUID N03再验2 passed/1 skipped、5.3s，1380点并实际进入电气曲线分支。证据outputs/v04b-real-ui-v01-passed和v04b-real-ui-uuid-final；空态skip不计实测通过。e8ec399的push CI45752509472及PR CI45752519663实际success，不等同于本次补充提交的CI。

V0.4b第二轮18/21项真实用例已完成（N01–N09各两个变体），尚待N10及额外日落和最终v2重验；不预报最终验收。新增下一切片设计docs/plans/2026-11-26-v04c-single-faults.md，明确六类P0的候选强度、资源作用位置、读回/撤销、故障与数据质量分离及逐项真实门禁，尚未实施六类故障。修正数据字典将旧QC版本明确标为历史，当前版本不变。1465267的push CI45752877763及PR CI45752883505实际success。当前正常覆盖报告仍在整理，不作为已完成交付。

V0.4b真实批次已实际结束：21 passed/2907.66s、退出0；N01–N10各两个变体，另含N02日落。全部原始manifest clean/completed/cleanup=[]、v1 QC通过。开始使用独立f48d114源码对原件生成v2严格QC并顺序执行兼容回归；该重验尚在进行，不提前宣布V0.4b通过。

真实截图复核修正两项生产UI一致性：页眉阶段由后端health.stage提供，跟随最新时滑块及秒数同步实际快照游标；不改API、观测数据或仿真逻辑。新增浏览器断言验证页眉与游标，真实UUID回放2 passed/1 skipped、5.3s、1380点；旧V0.1回放2 passed/1 skipped、4.5s、111点，证据outputs/v04b-ui-stage-cursor-uuid和-v01。web-test与web-build均退出0、Vitest3通过；仅保留既有2条缩进warning及新构建817.73kB提示。首轮新增Tag换行warning已修正，最终无新增warning。

最新UI修正另对原30站/1小时真实证据备份执行回归：Playwright 2 passed/1 skipped、25.0s，核对27183个显示点，页眉/实际游标及电气曲线断言通过；证据outputs/v04b-ui-stage-cursor-30site。该结果是只读历史回放兼容性，不冒称重新运行30站1小时。251c881的push CI45754356008及PR CI45754360861实际success。

### V0.4b三站正常场景开发验收通过

真实批次21 passed/2907.66s，运行4ab3fc58485bb010acd0887c53a1c7270ec890ea clean，证据/var/lib/pvcomm/runs/v04b_s2。最终f48d114048d48650533e8aac7d40646144be724d源码逐项生成v04-normal-schedule-v2，21份全部通过：26132/26132完整APDU、解析100%、工具drop0、日志P99最大3.840095ms；14事务=12 accepted_complete+2 rejected，任务起点最大迟到1ms，8306物理样本通过。全部manifest completed/clean/cleanup=[]；旧normal_regression=false保留。21份保留127517958B、PCAP10479522B、Parquet4899784B，失败首轮和副本另计；4个原始日期共同来源组映射已写覆盖报告，没有执行数据集划分。

同f48d114最终真实回归顺序完成：旧协议/断联/取消/尾包5 passed/185.35s（V0.3 2131/2131 APDU、P99=2.720530ms）；V0.4a降级2 passed/235.15s（2527/2527、P99=3.813604ms），silent超时3.000418187s、同会话隔离、另一站accepted。旧V0.2 90s CLI run/parse/Arrow/默认QC均退出0，453/453、P99=5.696731ms。新CLI validate-scenario/run-scenario/parse/Arrow/schedule-QC均退出0，额外run_936bfe63db414081bfb028905b55b4be、1117/1117、P99=3.662255ms；默认唯一ID、新共同来源组、空调度无命令均验证。根/var/lib/pvcomm/runs/v04b_final包含全部日志/命令/耗时/证据，最终namespace实际为空；没有并行namespace实验。WSL科学/Arrow189 passed/28 deselected、11.79s，Windows完整182 passed/35 deselected及相关前端检查、三类真实UI回放通过（具体冻结归属见报告）。

报告docs/reports/2026-11-26-v04b-normal-coverage.md已补齐21个run_id、原/共同来源映射、SHA、CLI复现、旧版兼容、失败保留、存储及未完成项；README和阶段计划同步。V0.4b仅三站正常开发门禁通过；六类P0、配对/正式标签、场景表单/队列/标签页面、多站与30站复验仍待V0.4c–f；未进入720h或训练、未做现场高频校准。当前远程仍为https://github.com/lxxkimwoaini/pvcomm-sim，lxxkimwoaini、PUBLIC、默认main；PR #1→#2→#3→#4→#5均OPEN，#3–#5 draft，不合并。最后已验证的源代码交付为251c881，push CI45754356008及PR CI45754360861 success；本次验收文档的提交/推送/CI以随后实际远程核验为准，不把前一提交CI当作后一提交结果。

### V0.4b交付收尾核验

验收文档提交00914b1c8bc82008f5a43892a4118048fd1a8347已实际push，git ls-remote与本地一致。pull_request CI45756164645五job均success；push CI45756156864在本次补记时四job success、前端安装浏览器依赖仍in_progress，不冒称全部通过，最终状态另以实际查询为准。草稿PR #5已更新标题和正文为V0.4a/b实际实现及验收范围，仍OPEN/isDraft=true，base=feat/v03-multisite、head=feat/v04-scenarios；PR #1→#2→#3→#4→#5堆叠关系核对正确，全部未合并。仓库lxxkimwoaini/pvcomm-sim仍PUBLIC、默认main。

修正覆盖报告末尾多余空行，git diff --check退出0。只读文档核验实际退出0：21个run_id均在覆盖报告，指标文件SHA256=700d7d5ec36dcb0c14b72f498be2ff223a378f9d856a1ecf16e0d6196e11f45b匹配，22个本地Markdown链接有效；未重跑或修改真实运行。本次仅文档收尾，先前运行/核验/UI冻结归属不变。下一任务仍为V0.4c真实单故障机制，正式标签、配对及后续多站验收尚未完成。本次补记提交的push/CI最终结果在交付时独立核验。


## V0.4c A01候选实施（2026-11-26）

从feat/v04-scenarios的231671237c2978e7934b26b48a373962ec795f9d继续，初始工作区干净；该基线实际push CI45756607836和PR CI45756612798五job均success，最终核验记录outputs/v04b-delivery-2316712.json。重新核对AGENTS、V1.3故障/观测/标签/QC契约、进度、V0.4b报告及ADR0011/0015。PR #1→#2→#3→#4→#5仍正确堆叠且OPEN，#3–#5 draft，未合并。gh账号lxxkimwoaini、Git2.52.0、gh2.102.0、uv0.11.13、Windows/WSL Python3.11.15可用；隔离namespace初始为空，原生Linux可用约907GiB（不当作宿主空闲保证）。

新增ADR0016及A01首片计划；0.4-fault.1组合既有背景，显式站点/子集/区域/全局范围，保持5/20/60s候选。源/有效配置与逐操作事件仅在truth/fault，主站无故障计划。受限桥端口状态关闭转发，不停止RTU/物理；修改前后读回、部分施加失败与finally撤销保留。实际内核探针发现ip link bridge_slave命令成功但读回不变，bridge link命令才实际disabled；两份探针日志outputs/v04c-bridge-probe*.jsonl保留，均清理本次namespace，不冒称真实协议故障验收。

新增validate-fault-scenario/run-fault、独立A01机制QC及模板生成脚本，同步schema/数据字典/README。QC分别保留实验质量、施加事实、双端影响和恢复观测；正式label_quality仍not_evaluated，不放宽旧正常门禁。相关单元37 passed/0.57s；Windows完整check退出0、199 passed/40 deselected、mypy104及Ruff/schema/前端质量/Vitest3/build通过；随后新增生成脚本的全仓Ruff/format/mypy105退出0，生成配置与validate-fault-scenario实际退出0。保留既有两条HTML缩进warning和817.73kB构建提示。

当前A01尚未通过真实验收；下一步冻结clean代码，在/var/lib/pvcomm下新持久目录顺序执行三站低/中/高持续时间和站点/子集/区域/全局范围，首次失败停止批次并保留证据，再核验旧正常兼容。A02/A04/A05/A06/A08、配对、正式标签及完整V0.4c–f尚未完成；不生成720h或训练。

### A01首轮失败及背压证据修正

候选5c21fe48a6fd11679ebff75f93ec7f4f50284e97已push，CI45758875823及PR CI45758882136实际success。冻结源码/var/lib/pvcomm/sources/v04c_a01_s1，科学/Arrow206 passed/33 deselected、12.59s。首项真实single-low在/var/lib/pvcomm/runs/v04c_a01_s1/lab/test_real_a01_single_low_0/run_ed094257f9434adeb15b6e4705f7f815，1 failed/177.13s；失败即停，其余四项及兼容未执行。completed、clean、cleanup=[]，原独立质量和施加核验通过，但delivery_gap_observed断言失败，不能计A01通过。原PCAP/日志及qc/v04-a01-mechanism-v1报告保留。

实际disabled约20.031s至25.069s，P0作用期间无PV01测量，P1在初始1秒发送6条测量后背压停止；内区间要求P1持续发送导致漏判。v2改为经核验阻断期内真实P1/P0独占匹配并留帧索引，仍保留内区间无交付及旧质量门槛。新增真实SIGTERM恢复测试尚未执行；单元18 passed/0.30s、Ruff和mypy105实际退出0。下一步普通总检、新冻结六项A01及旧正常兼容；当前仍未验收。

修正后Windows完整check退出0：200 passed/41 deselected、4.11s；Ruff/format、mypy105、schema/生成类型、前端lint/typecheck/Vitest3/build通过，既有两条缩进warning及817.73kB构建提示保留，日志outputs/v04c-s2-check.log。随后为single-low真实用例接入validate→run-fault→parse --parquet→qc --profile fault全CLI链路，逐命令保存结果；六项lab收集、Ruff/format/mypy105实际通过，尚待冻结执行。

### A01第二轮及长中断质量失败

修正提交4bf0c7a2fd56738cbf1b1fa27346ea50023a70fb已push，push CI45760032224、PR CI45760041019实际success。新冻结/var/lib/pvcomm/sources/v04c_a01_s2；科学/Arrow207 passed/34 deselected、12.12s。真实批次/var/lib/pvcomm/runs/v04c_a01_s2：1 passed、1 failed/354.70s；失败后停止，子集/区域/全局/取消及兼容尚未执行。

single-low的run_4c42bb06e82c4195823854befcef2201实际通过完整CLI及机制断言：1867/1867完整APDU、四路0drop、P99=3.835006ms，disabled期间6条P1测量未及时交付，物理最大间隔1.004976s，未选站调控继续。恢复后首次遥测约2.444791s，可能为TCP排队的旧报文，不将其单独当作最新状态或完整业务恢复标签。

single-medium的run_a0cf52da16704be79783a0f1a10256fc真实completed/clean/cleanup=[]，独立质量失败于主站原始回调比P0多一条TESTFR_ACT（680443000000），逻辑38.642s，随后39.273s进入CLOSED_AWAIT_RECONNECT；不能计有效通过。原报告及所有证据保留，当前QC门槛未修改。实际核对c104 v2.2.1固定submodule m-unkel/lib60870@97c5f856e0cf78c938f549f8f9f83f6aba8a3cd5，writeToSocket在Socket_write之前调用rawMessageHandler；需要额外内核证据区分发送尝试与实际接口发送，不能仅凭该源码豁免失败。源码快照outputs/lib60870-97c5-{cs104_connection,socket_linux}.c及诊断脚本保留。

在已授权WSL隔离实验范围安装strace6.19及libunwind8（apt-get退出0，无既有包升级/删除）；同一冻结4bf0c7a启动独立诊断/var/lib/pvcomm/runs/v04c_a01_sendprobe_s3，记录真实sendto/sendmsg/connect/close/shutdown调用，只用于定位，不预先计验收。namespace实验顺序执行，S2结束后列表为空。下一步依据实际系统调用结果修复或明确保留限制；A01及V0.4c均未验收。

S3诊断退出1、194.041s，依旧只失败于原主站多余TESTFR回调；实际sendto6字节已被内核接受，随后同socket ENOTCONN及close，P0没有该报文。新增ADR0017，A01端点增加固定strace Oracle证据；v3独立核验哈希/清单/退出及窄范围TESTFR尝试核算，原正常结果和其他质量要求保留。-D PID/tracee探针实际一致，退出0。新单元36 passed/0.38s、Ruff及mypy108通过；最初测试揭示独立模块truth父目录创建问题及长行格式问题，已修正，未删除测试。新机制尚待普通总检和新冻结真实取消/各范围验收。

内核证据候选Windows完整check退出0：218 passed/41 deselected、4.54s，Ruff/format/mypy108/schema/生成类型和前端lint/typecheck/Vitest3/build通过；日志outputs/v04c-s4-check.log。既有两条HTML缩进warning和817.73kB提示保留。当前普通测试验证证据缺失、错误方向/类型/站点/时间、部分写入、复用证据、清单/哈希不符以及其他质量失败均拒绝；真实跟踪取消和各强度尚待冻结执行。

881dfa871bd946fe631caefc2d8158d7d28a1c49已push并冻结/var/lib/pvcomm/sources/v04c_a01_s4。科学/Arrow225 passed/34 deselected、13.68s。取消预检失败1项/41.16s，原件/var/lib/pvcomm/runs/v04c_a01_s4/cancel/test_a01_sigterm_restores_befo0/run_8a0f170280614687ac270848a593c4b8；五种A01及兼容均未执行。桥端口已恢复、namespace为空，但现有主站按InterruptedError取消退出1，跟踪已完整结束；采集器错误地把“跟踪完成”要求为所有worker退出0，导致cleanup_errors记录socket audit incomplete。修正为完整退出与退出码分别保存（trace_exit_code），取消不伪装0；正常完成QC继续要求0。新增回归，相关37 passed/0.37s、Ruff/mypy108通过，待新冻结取消重验。

取消修正后的普通回归实际219 passed/41 deselected、4.51s，Ruff/format/mypy108及diff检查退出0。此小改未变前端或schema入口，未重复声称真实协议通过；新冻结仍先取消预检，失败即停。

### A01 S5继续验收及核验代码追溯修复

当前运行冻结b240eac960699204468a479018da196650464c34，源码/var/lib/pvcomm/sources/v04c_a01_s5，证据/var/lib/pvcomm/runs/v04c_a01_s5。科学/Arrow226 passed/34 deselected、13.54s；取消预检1 passed/41.95s，CLI130、cancelled、cleanup=[]、先恢复再停止主站、工具drop0及namespace/路由恢复均实际通过。五项A01中single-low和single-medium已通过，其余仍在执行，不能提前计完整切片通过。该提交push CI45762889410及PR CI45762895826实际success。

发现fault QC从调用目录查询Git可能记录错误核验版本，修正v4为实际加载模块所在仓库，并拒绝dirty核验；原始run SHA不改，v3原报告保留。新增仓库外调用回归，相关38 passed/0.53s、Ruff/格式通过。A01 lab另增加既有audit_schedule对恢复后完整GI及未受影响站点业务的核验，不把首条恢复遥测视为最新状态。此改不修改正在运行的S5冻结树，最终须另行冻结核验并保存独立版本归属。

追溯修复Windows完整check实际退出0：220 passed/41 deselected、4.69s，Ruff/format、mypy109文件、schema/生成类型、前端lint/typecheck/Vitest3/build通过；日志outputs/v04c-s6-check.log。既有两条HTML缩进warning和817.73kB包提示保留；本次未修改前端。真实运行及兼容结果仍以S5批次结束后的记录为准。

### V0.4c A01三站单故障开发验收通过

S5冻结b240eac960699204468a479018da196650464c34的批次实际全部结束：五项A01 5 passed/887.92s、取消1 passed/41.95s、旧真实兼容5 passed/186.72s、正常N01/N09 2 passed/19 deselected/272.54s；六步命令退出0，精确参数/外层耗时在/var/lib/pvcomm/runs/v04c_a01_s5/commands.jsonl。再次核对namespace为空、无剩余lab_worker/端点strace，final-evidence.json保存各manifest/QC哈希与最终状态；取消不算completed样本。

五份原始运行completed/clean/cleanup=[]，支持单站5s/20s、双站5s、R1区域20s和全两区60s。P0共8190/8190完整APDU、解析100%、20路P0/P1工具drop0、日志P99最大21.352394ms；2436条物理样本通过适用pvlib/潮流/Arrow核验，作用期间最大更新间隔1.010740123s。共68条真实P1测量APDU未在撤销前交付，四个非全局用例的未选PV03调控均accepted_complete；无Oracle补齐M2。

最终核验冻结40f329901b5e320327859c586b92e4c714aee3fd，源码/var/lib/pvcomm/sources/v04c_a01_eval_s6；从运行证据目录调用，五份v04-a01-mechanism-v4和恢复后背景总召唤均通过，原运行SHA及v3报告不变。1140个非QC原文件哈希逐项前后一致，a01-metrics-v4.json SHA256=3c63a47e59b39423929ab00530d38c7b4a5411e39bafbcaa30efff854021a044。六条TESTFR写入尝试按ADR0017独立内核证据核算，strict_normal_comparison、其他质量门槛及全部旧失败保留，不将尝试当作已上线。九个恢复GI具有新请求、确认/数据/结束证据；首次撤销后观测仍可能为排队旧值，不直接生成恢复标签。

正常N01 run_a5845edc4ba54f3fac7d128bdbbfbd21与N09 run_7ed18692251546bb8d0e0e3d5739d0d6的v2严格QC均通过，分别1118/1118与1165/1165完整APDU、P99=3.777648/3.903548ms；N09保留真实合法拒绝及旧逐事务passed=false，没有强判为通信异常。五个A01成功run及QC保留54,476,283B（PCAP3,243,300B、Parquet1,506,454B、系统调用15,248,618B包含于其他项），失败/取消/兼容及源码另计。三站短运行不替代V0.3资源预算或正式并发测量。

完整证据、失败清单、限制和复现已写docs/reports/2026-11-26-v04c-a01-acceptance.md，README、总计划及单故障计划同步；新增下一切片docs/plans/2026-11-26-v04c-a02-delay.md，仅设计尚未实现。当前仅A01开发门禁通过：A02/A04/A05/A06/A08、配对/正式标签、工作台、多站与30站复验、40次/类正式覆盖仍未完成，label_quality=not_evaluated。没有生成720小时或训练模型。

40f3299已实际push，push CI45764024273及PR CI45764029573均completed/success。远程https://github.com/lxxkimwoaini/pvcomm-sim、lxxkimwoaini、PUBLIC、默认main不变；PR #1→#2→#3→#4→#5正确堆叠且OPEN，#3–#5 draft，未合并。后续验收文档交付SHA/push/CI另以实际远程查询和outputs/v04c-delivery.json为准，不借用前一提交CI。

## V0.4c A02开发启动

A01已验收，本轮仅兼容回归，不再重新设计。直接实施既有A02设计：LinkDelay/0.4-fault.2、真实单向qdisc change、完整内核读回、逐出口撤销/异步排空及独立P0/P1时延QC。未修改旧正常门禁、3秒业务确认期限或正式标签；M2不取得故障计划。ADR0018记录实施细节。

开发探针001在调度配置校验拒绝（未创建网络）；002实际读回不符失败；003在WSL内核6.6.87.2、iproute2-6.19.0上1 passed/2.54s，60条UDP全部交付、排队中撤销、0 qdisc drops及基线恢复一致。证据/var/lib/pvcomm/runs/v04c_a02_probe_003；不作为IEC104实验。Windows普通总检outputs/v04c-a02-check-001.log退出0：232 passed/49 deselected，Ruff/format、mypy113、schema/TS及前端检查通过。后续小增补将另作相关检查后冻结，顺序运行内核探针/取消/单站低档，再扩展强度和范围；当前A02尚未验收。

### A02 S1真实结果与采集边界修复

冻结bd02154f6722345d453c87f6ab10ec70e622c711已推送，push CI45769769747、PR CI45769779093均实际success。/var/lib/pvcomm/sources/v04c_a02_s1与/var/lib/pvcomm/runs/v04c_a02_s1保留源码/输入/命令/证据。科学/Arrow240 passed/14.41s、内核探针1 passed、取消1 passed/40.11s、单站低档1 passed/约185s（外层187.155s）。低档run_7da03d418c4b40ff98e433fa4bf6cb0a，1927/1927完整APDU、P99=18.182133ms，PV01正向基线/作用/恢复中位数7.292/207.047/7.461ms，目标及未选PV03事务均accepted_complete，未生成正式业务异常标签。

扩展首项反向1秒run_01d1138ea05f4dfe80e81eab3b1f21d7未通过v1机制QC，1 failed/188.31s后停止，其余四档及兼容未执行。原件保留：内核时延已生效、确认耗时1031.331686ms，但P1→P0基线/作用中位数3.193/3.2545ms，源端AF_PACKET时间位于自身出口qdisc之后，不能据该双端区间测得源端排队。未放宽门槛或回填计划值。

最小修复仅对A02选中链路插入无IP透明内部桥段，将该链路原双向NormalLink基线移至内部出口，P0/P1采集点及旧默认网络不变。新v2独立QC核验内部桥读回、配置端点映射和采集边界，旧v1报告保留。开发探针004两个方向2 passed/5.00s，60条/方向均交付、排队中撤销及恢复一致；不是IEC104验收。CLI生成器增量支持A02参数并拒绝混用/覆盖，相关集成12 passed；相关机制/旧A01配置单元33 passed，Ruff/mypy113通过。待新冻结重验取消、反向失败用例及六档位/范围；A02仍未验收。

### A02 S2三站开发验收通过

运行与v2独立核验共同冻结0b56d98201f43a2ad8d653344dd87624f1599740，源码/var/lib/pvcomm/sources/v04c_a02_s2，证据/var/lib/pvcomm/runs/v04c_a02_s2。科学/Arrow246 passed/17.00s；双向内核探针2 passed/4.41s；取消1 passed/39.28s；六项真实用例分别1 passed，实际外层耗时依次186.926/187.234/186.962/186.440/186.349/186.655s；旧V0.1独立端点真实兼容1 passed/22.58s。10条命令全部退出0，账本commands.jsonl保留精确参数及冻结SHA，未重复执行A01长时验收。

六份run均completed/clean/cleanup=[]，单站低/中/高、子集低、区域中、全局高及两个方向/历史日期实际通过。P0共10996/10996完整APDU、100%解析、24路P0/P1工具drop0、所有qdisc drops0、日志P99最大27.229112ms；物理持续更新最大间隔1.034928661s，pvlib/潮流/Arrow门槛通过。10个新恢复GI有完整请求/确认/数据/结束证据。高档单站1次、全局3次confirmation_timeout均有独立事务证据，其余11条accepted_complete；五个非全局未选PV03业务均正常，未延长期限或从Oracle补齐主站观测。label_quality仍not_evaluated，超时事实不冒充正式分层标签。

最终namespace列表为空、无lab_worker残留，各次宿主路由保持。机器汇总a02-acceptance-evidence.json的SHA256为de0452161bc542f85e2e71b3fdd96304f8939f314eed3374d1100b66b0b81eb3，含原件索引/哈希、命令、指标及存储统计，PCAP哈希复核一致。六份成功run及QC共73,242,671B，其中PCAP4,345,706B、Parquet1,851,802B、系统调用18,803,934B；旧失败、取消、探针及源码另计，不外推720小时资源预算。

完整报告docs/reports/2026-11-26-v04c-a02-acceptance.md，README和进度顶部同步。0b56d98已push；push CI45771257650、PR CI45771266265均completed/success且5 jobs全部通过。PR #1→#2→#3→#4→#5仍OPEN且未合并。后续仅文档提交的最终SHA/push/CI由独立交付记录核验，不套用0b56d98结果。下一切片直接A04，再A05/A06/A08及V0.4d；完整V0.4c/V0.4、正式40次/类覆盖、多站/30站复验仍未通过，未生成720小时或训练算法。

## A04/A05/A06/A08统一终端切片开始

基于69bb869继续，复用现有运行器、Oracle日志、独立PCAP及QC。已编写0.4-fault.3、终端状态控制/注册读回/取消撤销、周期及GI抑制、接受后1001/1006抑制、真实命令处理等待和维护恢复缺测机制，CLI生成器及schema同步。A08首片仅既有单站计划维护，其区域维护尚未实现。实施依据docs/plans/2026-11-26-v04c-terminal-execution.md、ADR0019，未重做A01/A02。

实际Windows检查退出0：新增17项单元通过；Ruff/format165文件、mypy118、普通pytest256 passed/62 deselected/7.05s，schema已实际生成。新增12项真实矩阵尚未执行，四类异常尚未验收；准备冻结后先逐类低档实跑，失败保留并停止依赖批次。标签及配对尚未实现，不将代码或CI视为真实验收。

S1冻结7972e24（/var/lib/pvcomm/sources/v04c_terminal_s1；证据/var/lib/pvcomm/runs/v04c_terminal_s1）科学/Arrow263 passed/55 deselected/17.05s。A04低档及A05低档原v1 QC命令退出0，外层196.384/196.656s；A06/A08低档仍在执行，尚不计四类完成。A04注册读回发现1004/1009/1010/1011等扩展日常测点未被抑制；原证据/v1报告保留，但该A04结果不满足完整语义，不计最终验收。修正从实际注册点集合推导A04/A08抑制范围，v2独立QC按完整恢复点表核对集合；A04另加入作用内真实GI以验证不能绕过。

同时修复部分注册失败的逐点撤销跟踪、A06撤销后下一电气周期真实1001/1006恢复反馈、实际读回后保持时长及取消截尾标记。新增部分失败和非法强度回归；Windows普通265 passed/62 deselected/7.13s，相关26 passed/0.32s、Ruff/mypy118均通过。新增真实取消门禁尚未执行。运行中的S1冻结保持不变，后续修正在新冻结进行真实矩阵验收。

冻结修正前追加实际检查：独立QC增加完整抑制集合、恢复、取消、跨事件及泄漏负测试，32项相关单元通过；普通pytest271 passed/63 deselected/6.83s，Ruff/format、mypy118、schema检查通过。A05低档及A06低档原v1真实QC退出0；A08仍在执行。下一冻结先真实取消，再顺序执行四类各三档，不套用S1结果为最终门禁。


## 终端S2实测与V0.4d并行开发

4d4fbf965fb8faa1576bb97c338b96d178711d0d已推送，push CI45781018930、PR CI45781025674均success。冻结/var/lib/pvcomm/sources/v04c_terminal_s2，持久证据/var/lib/pvcomm/runs/v04c_terminal_s2。科学/Arrow278 passed/56 deselected/16.19s（外层18.478s），真实A06取消通过44.934s；A04三档外层194.953/195.419/255.353s全部退出0，实际完整点表抑制及作用内GI通过；A05低/中档196.387/197.446s通过。S1四类低档原v1命令最终均退出0，但A04旧点表不足仍不计验收。

A05高档run_183fbabf9e39414e8359132f4af6613c失败，203.439s后矩阵停止，A06/A08三档及末尾兼容未执行。真实15秒处理等待、P0/P1字节一致、恢复GI及物理连续通过；PV03在40.074s重连，随后12条I帧未进入主站原始回调，business_callback_correspondence失败。未放宽门槛，原PCAP/回调/报告保留。最小修复改用c104公开延后正确认，释放协议读线程，物理与T1/3秒期限不改；补充有界待处理、取消及失败路径测试，见ADR0019增补。新冻结先复验该失败用例再继续剩余矩阵。

V0.4d独立代码已实际编写：配对严格共享条件、原运行器复用、正式分层标签schema/JSONL/Arrow、独立标签QC、CLI及22项合成单元负测试。实际只读探针核验已完成的S2原件，A04各档/A05低中标签与独立核验一致，A05低档故障实际施加而无业务违约，保留false；失败高档为invalid，不当正常负例。尚未写入正式冻结标签或执行真实配对。Windows普通293 passed/67 deselected/7.92s，Ruff/format176文件、mypy127和schema均通过；后续A05修正另测，不套用此结果为新代码最终检查。


92335bfb1cc97377d0ffab0246840218d3c05298已push，CI45785260313/45785269459均success。新冻结/var/lib/pvcomm/sources/v04c_terminal_s3科学测试退出0（外层20.231s），首项A05高档在实际确认路径遇到`ValueError: Information is read-only!`，64.204s退出1，run_a6b00973fe6f4bcf9dfd2660e82028fc及stderr/PCAP/失败manifest完整保留。原因是收到命令后c104信息对象只读，延后确认前重复给point.value赋相同值仍被库拒绝。最小修复保留原接收值，仅校验未被其他事务改变后调用协议栈延后确认；新增只读接收点单元fixture、实际拒绝/发送失败不伪造成功及故障无违约负标签回归。后续任务均未执行，不将科学测试或CI成功当真实验收。


### A04三站开发验收及首批冻结标签核验

A04三档共5699/5699完整APDU、12路drop0、日志P99最大13.578058ms、电气更新最大间隔1.015286366s，作用内GI/完整注册表/恢复GI及原实验质量均通过。完整报告docs/reports/2026-11-26-v04c-a04-acceptance.md，机器证据a04-acceptance-evidence.json SHA256=73abc8ce17dca87e5465384d35aa7a12e9bfe8c08ce6d1deb59a2b53703979a0。三次不代替40次/类或30站覆盖。

新冻结acde7d24bdb598d90e881efa9e9fc0e1275040b1，源码/var/lib/pvcomm/sources/v04c_terminal_s4，实际普通/科学测试退出0（外层20.058s），先复验A05高档，随后取消、低中档、A06/A08三档及兼容，再真实配对。该SHA push CI45786116178、PR CI45786128503均success。Windows294→298项普通回归通过（最终7.62s），Ruff/format176、mypy127和schema通过；28502c8曾有单元fixture只读属性继承的mypy失败，acde7d2已修正，未屏蔽检查。

另用S4冻结实际对S2三份A04和低/中/失败高档A05生成标签及独立标签QC，六份全部核验通过。共15 valid站点标签，失败高档3 invalid，不作为正常负例；A05低档的已施加故障没有业务违约，主标签false。全部来源哈希及Arrow逐值读回、外键/期限/在线因果通过，证据账本S4/retrospective-labels.jsonl。标签正确不改变失败高档的实验质量失败结论。

## 2026-11-26 终端矩阵及标签v2接续

S4冻结acde7d24bdb598d90e881efa9e9fc0e1275040b1，证据位于/var/lib/pvcomm/runs/v04c_terminal_s4。A05三档、A06三档、A08三档均实际退出0，A05待处理取消与旧独立端点兼容也退出0。每项运行均执行原解析、Arrow及独立终端机制QC。science日志为305 passed、61 deselected，17.81秒。详细度量和逐类报告随后从原件汇总。

P03真实配对已退出0，外层376.437813490秒。P04正常成员completed且QC/标签QC通过，改变成员受执行环境中断，清单仍为running，原件保留且不计成功。继续运行使用全新持久目录。协调进程随环境中断消失时，先检查进程、namespace与已有清单，避免重复启动活动实验。

标签v2新增通信影响诊断区间与完整恢复时间，A06实际作用起点引用feedback_hold，A05实际等待区间引用delay_started/finished。A01传输影响基于已有双端withheld证据。完整恢复由撤销后新GI的请求、确认、结束及1001/1002/1003/1006/1008/2001对象确认。v1产物及QC保留，v2另写目录。独立QC另行扫描原始事件及协议对象核验这些字段。

本地普通检查实际通过Ruff、mypy127文件、schema及308 passed、61 deselected（11.32秒）。新增标签专项26 passed（2.33秒）。开发中曾因地址变量插入错误导致16项单元失败，已修正并重新通过，未删改测试。当前v2尚待冻结源码对真实原件生成及独立核验。

S4终端汇总已生成terminal-acceptance-evidence.json，SHA256为8d016113aa07d81731482c69c653caec6f4eb019f12db9c5f13c077a49813959。A05/A06/A08逐类报告已写入docs/reports，三类完整APDU分别5735/5573/5413，36路工具drop均为0。6594465已推送，CI 45798743809及45798734520实际success。

6594465冻结源码已对27份原运行生成标签v2并通过独立QC v2，包括1份原失败A05高档，其标签维持invalid。为补齐删除整组证据的负测试，QC v3另增加主站发送、协议对象、有效接收和独立QC报告的必要引用覆盖，原QC v2报告保留。当前Ruff、mypy127源文件、schema通过，普通测试310 passed、61 deselected（11.26秒），标签专项28 passed（2.77秒）。QC v3待下一冻结只读核验原件。

## 2026-11-26 六类P0三站机制及V0.4d首批验收完成

A04/A05/A06/A08各三档共12次正常完成机制实验，完整APDU 22,420/22,420，48路抓包内核drop为0。A05、A06、A08验收报告已提交ea3a27c。该提交已普通推送，push/PR CI 45799607171、45799595971均实际success。当前分支feat/v04-scenarios，远程https://github.com/lxxkimwoaini/pvcomm-sim，所有者lxxkimwoaini，PUBLIC，默认main。PR #1→#2→#3→#4→#5堆叠均OPEN，#5维持draft且base为feat/v03-multisite。

四组配对真实退出0。P03冻结acde7d2，376.437813490秒。P04/P06/维护恢复失败冻结6594465，分别377.733384509、377.178959754、428.442750786秒，证据根/var/lib/pvcomm/runs/v04d_pairs_s1。八成员共15,021/15,021完整APDU，32路工具drop为0，日志P99最大19.663110ms。共享配置、来源组、唯一身份、业务逻辑起点及原正常/异常质量核验通过。正常成员均无业务违约，改变成员均为PV01业务违约。批次结束ip netns list为空，原测试核对宿主路由一致及cleanup_errors为空。

标签v2/QC v3完成34份去重运行核验，99 valid、0 uncertain、3 invalid，全部标签QC通过。包含原失败A05高档，其3站仍invalid且业务标签为空。中断P04正常成员只计独立标签核验，不计成功配对。冻结ea3a27c独立QC核验27份保留运行及新配对成员，CLI另在S2 A05低档实际label/qc退出0，重复输出均退出1。

最终机器证据/var/lib/pvcomm/runs/v04d_pairs_s1/paired-label-acceptance-evidence.json，SHA256 0231994175a81cff60a892a928bfa5254143e4222e3d8bc0d1a40e3c90f1243b。CLI证据SHA256 d3771ab6abb357e9ab0a2dfdb5be21ed633351f8a7e6a101f7ab4b39835ea556。详细报告为docs/reports/2026-11-26-v04d-paired-experiments.md及2026-11-26-v04d-label-quality.md，正常N01–N10覆盖继续引用既有v04b-normal-coverage报告。

当前小规模切片完成，剩余门禁包括A08区域/多站维护恢复、正式重复次数及30站复验、预冻结统计相似匹配、场景/任务/标签证据与配对回放工作台。A05延后拒绝仍受固定协议栈公共API限制，运行遇到该组合明确失败。数据保留、临时解析峰值与并发吞吐仍须在正式720小时生成前完成验证。没有开展720小时批量生成或算法训练。最终代码检查为Ruff/format182文件、mypy127、schema及310项普通测试通过，真实结果不由CI替代。


## V0.4接续：配对门禁与必要标签修正开发

依据用户附件执行docs/plans/2026-11-26-v04-completion.md。实际检查分支feat/v04-scenarios，基线7415d33；PR1→2→3→4→5仍OPEN且未合并。WSL Python3.11.15、tcpdump/TShark可用，namespace为空，数据盘剩余约906GiB。当前完整720小时1.5余量预算约1.494TB仍超出该空闲容量，不启动正式生成。

配对QC v2逐目标/非目标站核验并纳入passed；标签v3/QC v4区分迟到确认、反馈交付、执行旁证与任务覆盖；A05已知延后拒绝组合在配置阶段排除。新增负测试实际通过，专项77 passed/4.79s；全普通323 passed、61 deselected/14.28s，mypy127源文件通过。开发中一次新增测试块插入位置错误导致8项NameError，修正位置后通过；首次mypy变量名复用及Ruff长行/导入已修正，不屏蔽检查。当前原件重验尚未执行，源码提交冻结后执行。


5cff30b冻结原件复核退出0，四组配对、八成员、24站点新标签全部通过；账本SHA256=e1b085a6bdb5cf67cd9604dafccf494af476815124b4beb54d65dcc279a2bf9b，报告docs/reports/2026-11-26-v04-pair-label-review.md。5cff30b已push，CI45810953828/45810943785均success。当前进入独立正常重复运行的相似性校准；不使用这四组结果拟合阈值。


## 统计相似性实现与独立校准冻结准备

已实现config/similarity.py、pipeline/traffic.py、qc/similarity.py和原运行器上的六组正常重复校准入口。ADR0022及configs/v0.4-similarity-policy.json固定所有特征、窗口、距离、local/global分别校准和全特征门禁；新配对测试尚未开始，阈值尚未生成。已有配对QC改为单次逐行扫描包记录，只保留分析窗口；新统计使用8MiB缓存的临时磁盘排序及固定桶，不全量加载长运行包表。

专项9 passed/0.76s，含局部差异不能被全局稀释、校准/测试身份隔离、窗口边界、空流量、错误来源和固定裕量。首次Windows实际测试发现SQLite with事务块不关闭连接，7项临时文件清理失败；增加显式closing后通过，不放宽资源清理要求。普通测试/最终类型检查结果随后追加，校准需在下一冻结clean源码执行。

冻结前最终实际检查：Ruff通过、普通332 passed/61 deselected/14.74s、mypy135源文件通过、schema一致。当前代码将进行独立校准，尚不报告阈值、匹配率或新配对通过。
