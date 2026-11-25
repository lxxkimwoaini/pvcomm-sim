# PVComm 开发进度

日期：2026-11-25（Asia/Shanghai）。当前阶段：**V0.0 工程基础验收通过**，V0.1 尚未开始。初始只有 AGENTS.md 和 V1.2/V1.3 方案；无 Git 仓库、代码或既有实验。本文件只记录实际执行，不将计划标为完成。

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

下一任务为 V0.1 Linux 实验前置准备：确认现有 WSL Ubuntu 是负责人授权的隔离环境，在其内准备 Python 3.11/uv/c104、tcpdump/TShark，并核验 namespace/veth/NetEm 权限及清理范围；随后单 RTU 联通，再扩到 3 站和 30 min 双端抓包验收。当前工具缺失且未执行任何特权网络操作，故没有进入 V0.1 正式运行。无需修改科研范围、参数、标签或划分规则。

本次用户授权安装已完成：WSL Ubuntu 安装 tcpdump 4.99.6、TShark/Dumpcap 4.6.4、libpcap 开发包、build-essential、pkg-config；uv 0.13.10；uv 管理 CPython 3.11.15。项目环境目标为 `.venv-wsl/`，尚未把 IEC 104 进程接入网络，也未创建 namespace/veth、修改 tc 或执行抓包实验。

安装后复核（2026-11-25，WSL Ubuntu）：`tcpdump -D` 列出 13 个接口，`dumpcap -D` 列出同一组接口；`tshark -G protocols` 识别 `iec60870_104`，字段查询可见 IEC 60870 解析器；`ip`/`tc`/`make` 可用。`.venv-wsl/bin/python` 成功导入 c104、pvlib 0.16.1、pandapower 3.5.6 和 pydantic 2.14.4。`.venv-wsl/bin/python -m pytest -q -m "not lab"`：49 passed in 5.78s；`.venv-wsl/bin/python -m pvcomm.cli validate-config --config configs/foundation.yaml`：`valid=true`，配置 SHA256 为 `15e441b1026a506b8856d27f6fa8e022304deabf6c80752d56638a3c81e1089e`。上述仅证明工具和 Python 依赖可用，不构成 V0.1 协议闭环验收。

远程核验：提交 `bec54cfec8618f06246d239a40cd6f5a5d41d02d` 已推送到 `origin/main`；GitHub Actions CI run `45544659528` 对该 SHA 实际 conclusion=success。

## 尚未实现/测量

IEC 104 会话与命令闭环、P0/P1 PCAP、三级解析、30 min 正常数据、物理模型、QC 指标、标签/划分/特征与真实运行 Web。未进行现场验证。V0.1 依赖隔离 Linux 工具与权限检查，不能以本阶段探针替代。

## GitHub

仓库：https://github.com/lxxkimwoaini/pvcomm-sim 。所有者 lxxkimwoaini，visibility=PRIVATE，defaultBranchRef=main，viewerPermission=ADMIN。`gh repo create pvcomm-sim --private --source=. --remote=origin --push` 实际退出 0。

- 首次提交：`25bf8c4848d110f771af14cff20090fbefb377ea`，`chore: initialize PVComm research foundation`。
- `git remote -v`：fetch/push 均为 `https://github.com/lxxkimwoaini/pvcomm-sim.git`。
- `git ls-remote --heads origin main` 与首次本地 HEAD 均为上述 SHA；推送后 `git status -sb` 为 `main...origin/main`，工作区干净。
- GitHub CI run `45539355121`：https://github.com/lxxkimwoaini/pvcomm-sim/actions/runs/45539355121 ，实际 conclusion=success；Python (ubuntu-latest)、Python (windows-latest)、Frontend and browser 三个 job 全部 success。CI 检查的代码 SHA 为上述首次提交；本验收记录随后以仅文档提交入库，避免自引用尚未产生的本提交 SHA。
- `uv run --frozen python scripts/dev.py test-lab` 实际退出 3，提示实验 harness 尚未实现；不是通过或已执行特权测试。
