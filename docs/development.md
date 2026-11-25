# 开发与复现

## 已验证环境与依赖

宿主：Windows 11 x64、PowerShell 7.6.9。uv 0.11.13 管理 CPython 3.11.15；Node 24.14.0、pnpm 11.5.1。Python 3.13.5 系统环境未改动。本项目以 .python-version 和 pyproject.toml 限定 Python 3.11；.node-version 与 package.json 指定已验证 Node 24。

`uv sync --frozen` 安装轻量基础、dev、web 组；`uv sync --frozen --group protocol --group power` 另外安装 c104 2.2.1、pvlib 0.16.1、pandapower 3.5.6。后者在 Windows 3.11 已导入并完成 Client 构造、地外辐照计算和单母线潮流探针；不是协议或电气科研验收。pandapower 未装 numba，以 numba=False 的 CPU 路径做探针，不承诺性能。uv.lock 由 uv 解析，pnpm-lock.yaml 由 pnpm 解析。

WSL2 Ubuntu 26.04 可启动，内核 6.6.87.2；默认 root、系统 Python 3.14.4、Docker daemon 29.5.2、ip/tc/make 已存在。没有 uv、tcpdump、TShark/dumpcap。Windows 原生没有 Docker/make/抓包工具。当前不创建 namespace、不修改宿主网络、不启动容器。WSL 的正式实验工具/隔离权限需在 V0.1 专项准备，不能将本机 Python 探针当作 Linux 兼容结果。

2026-11-25 已在该 Ubuntu 实例安装 tcpdump 4.99.6、TShark/Dumpcap 4.6.4、libpcap 开发包、build-essential 和 pkg-config；`apt-get update` 遇到 Docker 官方源一次 TLS reset，但 Ubuntu 官方源安装成功，Docker 源不是本次依赖。使用 Astral 安装器得到 uv 0.13.10（安装器未接受预期的 0.11.13 环境变量）；项目 Python 3.11.15 已由 uv 管理。Linux 项目环境放在仓库 `.venv-wsl/`，不覆盖 Windows `.venv`，该目录已加入 `.gitignore`。版本差异记录为环境事实，CI 仍固定 uv 0.11.13。

## 命令

首次安装见 README。下表 make 和脚本目标等价，Windows 无 make 时使用 `uv run python scripts/dev.py <target>`。

| 目标 | 内容 |
|---|---|
| bootstrap | 显示 uv/Node/pnpm 版本，frozen 同步 Python/前端；不提权 |
| lint | Ruff lint + format check |
| format | 显式 Ruff 修复/格式化；会写文件 |
| typecheck | strict mypy 检查 src/scripts/tests |
| test | pytest 普通测试（非特权） |
| schemas | Python schema 导出差异 + TS 类型差异（只检查） |
| web-test | ESLint、Prettier check、vue-tsc、Vitest |
| web-build | 类型检查及 Vite 生产构建 |
| check | lint/typecheck/test/schemas/web-test/web-build 串行失败即停 |
| test-lab | 仅授权 Linux 且 `PVCOMM_ALLOW_LAB=1`；使用当前解释器执行 `pytest -m lab`，否则退出 3 |
| dev-web | 本机前端；后端独立终端启动 |

`pnpm --dir frontend format` 显式写入前端格式。可选 pre-commit 配置为本地检查 hook，未自动安装到用户 Git。CI 仅使用检查模式，不会自动改代码。

V0.1 实验命令必须在授权的 WSL Linux 环境中执行：

```sh
PVCOMM_ALLOW_LAB=1 .venv-wsl/bin/python -m pytest -m lab
.venv-wsl/bin/python -m pvcomm.cli run --isolated --config configs/v0.1-lab-smoke.yaml --output-root outputs/new_isolated_run
.venv-wsl/bin/python -m pvcomm.cli parse --run-dir outputs/new_isolated_run/v01_smoke_001
```

`test-lab` 只负责真实协议集成测试；普通 `make test` 明确使用 `-m "not lab and not data"`。共享 checkout 中不可直接以默认 `uv run` 选择 WSL 环境；使用 `.venv-wsl/bin/python`，避免触碰 Windows `.venv`。

运行目录保留配置源文、有效配置、manifest、PCAP、tcpdump stderr/统计和两端应用证据。`observed/` 仅存主站记录，RTU APDU、终端事件和设备状态存 `truth/`；旧版回环记录中的混合文件不应用于研究特征。解析写新的版本目录，成功后原子提交；重跑同版本不会覆盖。取消 CLI 返回 130，保留 manifest 和部分证据。`--isolated` 的宿主路由与 namespace 清理由 lab 测试前后比对。

## 浏览器验证

```sh
pnpm --dir frontend exec playwright install chromium
pnpm --dir frontend test:e2e
```

使用真实后端与 Vite 服务在 127.0.0.1 的 8000/5173 端口测试；占用端口会失败，不复用不明服务。用例检查服务状态、合法配置、非法配置、修改后清空旧结果、移动端无横向溢出。截图输出 frontend/test-results（Git 忽略）。Vitest 的 synthetic HTTP 响应仅是明确标注的单元 fixture，不计运行数据。

## CI 与 Git

普通 CI 对 Ubuntu/Windows 执行 frozen 安装、Ruff/mypy/pytest/schema；前端 job 检查锁文件、lint/格式/类型/单测/build、生成类型和 Chromium e2e。没有特权、NetEm、PCAP 或正式数据 QC。完整 CI 状态见 progress，不能由本地结果推断远程通过。

main 初始提交后保持稳定，新任务使用 feat/fix/chore/docs 分支。每个验收切片更新进度、审查 diff 后 Conventional Commit，在授权私有 origin 推送；不自动合并未审核 PR。不要提交 data/outputs、PCAP、Parquet、凭据、模型权重；新增 fixture 必须记录来源/许可/测试用途。

## 可选 Arrow 数据契约与真实 UI 验收

Windows：`uv sync --frozen --group data`；`uv run --frozen python scripts/dev.py data-test`。WSL 共享目录必须显式选择环境：`UV_PROJECT_ENVIRONMENT=.venv-wsl uv sync --frozen --group protocol --group data`，之后继续使用 `.venv-wsl/bin/python`。已实际安装 pyarrow 23.0.1，不能将基础安装没有该组当作数据测试通过。普通 CI 增加独立 Arrow job。

PowerShell 设置 PVCOMM_RUN_ROOT 与 PVCOMM_E2E_REAL_ROOT 为同一个真实验收运行根，再执行 `pnpm --dir frontend test:e2e`，覆盖真实数据选择、回放、桌面/移动视口及浏览器离线与恢复。无该环境变量时，CI 明确跳过真实运行用例，另执行空运行与配置用例；不使用 Mock 冒充真实协议验收。截图与测试轨迹只保存在已忽略的 frontend/test-results。
