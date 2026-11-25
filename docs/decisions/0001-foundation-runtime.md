# ADR 0001：分阶段契约与已验证工具链

日期：2026-11-25；状态：V0.0 工程实现采用，不修改科研方案。

采用 uv 管理 Python 3.11.15，Node 24.14.0 / pnpm 11.5.1；三种候选科学依赖在 Windows 3.11 安装和最小计算后才写入 protocol/power 可选组。默认基础检查不依赖科学库或管理员。Windows 用于工程及 UI；真实隔离网络实验仍要求 Linux 专项验证。

完整方案 schema 1.0 含尚未实现的气象、网络与场景运行，当前使用明确的 foundation_validation 0.0 校验契约，范围只到时间参数、站点和模板引用。它不是运行入口或正式数据格式；未来需另建运行 schema，禁止静默把 0.0 解释成 1.0。当前无需冻结正式参数，不修改方案的容量、标签、窗口、划分与 QC。

配置服务为 Web/CLI 唯一来源，Pydantic frozen + forbid unknown；JSON schema/OpenAPI 与 TS 类型自动生成，CI 查差异。前端仅校验用户输入，不创建无数据的监测面板。未实现命令统一非零退出 3。

不提前写 Dockerfile/compose 或空模块：当前没有可部署的实验运行器或 Linux 依赖验证。后续 V0.1 根据隔离资源和清理契约引入，镜像摘要及权限另记 ADR。此选择仅调整文件落地时间，不改变 monorepo 架构。

初始安装发现 ESLint 9 已退出支持；核对 typescript-eslint/eslint-plugin-vue 的 peer 范围支持 10 后，采用 ESLint 10.15.0。当前 Starlette TestClient 已弃用 httpx，按其实际导入契约使用 httpx2 2.16.2，清除弃用警告而不屏蔽检查。pnpm 仅许可 Vite 所需的 esbuild 原生构建，不全局放开依赖安装脚本。CI Actions 通过 GitHub API 核验 tag 后固定实际 commit SHA。
