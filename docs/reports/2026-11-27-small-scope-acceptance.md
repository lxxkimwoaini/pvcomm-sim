# 当前3站开发范围交付验收记录

日期：2026-11-27（Asia/Shanghai）。依据用户授权和ADR0030，默认3站、最多10站；暂停新增30站、剩余228项及40次/类批量推进。本报告只验收当前范围保护和兼容回归。

## 实现与验证范围

`config/workbench.py`统一新任务范围为3/10站；提交时校验矩阵实际规模，30站请求返回422且不创建目录。消费者将历史超范围请求记为`deferred_scope`并继续小任务，保留原请求、矩阵、队列和数据。catalog、Vue、OpenAPI及生成TypeScript同步；历史30站配置读取和独立CLI复现能力保留。

相关Python测试31 passed（2.81s）；Ruff及format253文件、strict mypy174源文件（Linux平台）、schema/生成类型检查通过。前端lint/format/typecheck、7项Vitest和build通过，仅有原RunWorkbench两条ESLint warning及既有chunk大小提示。Playwright范围及M2隔离验证1 passed（3.2s），输出`outputs/v04_small_scope_web_s1`。没有重复执行无关机制验收。

## 唯一三站真实回归

冻结SHA：`eb2c68cc4a43a5b289f3bcc561757866494ca8f3`，源码`/var/lib/pvcomm/sources/v04_small_scope_s1`。在该目录执行：

```bash
PVCOMM_ALLOW_LAB=1 PYTHONPATH=src \
  /mnt/e/project/pv/.venv-wsl/bin/python -m pytest \
  tests/lab/test_experiment_request_runtime.py -m lab \
  --basetemp /var/lib/pvcomm/runs/v04_small_scope_s1 -q
```

实际结果：**1 passed in 151.29s，退出0**。上述目录已含证据，再复现须使用新的唯一basetemp，禁止复用或覆盖。

- 实验：`experiment_d973c8ef2211407788df4a9198af5d4b`。
- 运行：`run_cc6f483787ce48918ac68537d4a121ba`，N09、3站。
- 原件：`/var/lib/pvcomm/runs/v04_small_scope_s1/test_real_submitted_request_an0/experiments/experiment_d973c8ef2211407788df4a9198af5d4b/queue/task_1/attempt_1/run_cc6f483787ce48918ac68537d4a121ba`。
- 驱动、执行日志和GNU time：`/var/lib/pvcomm/runs/v04_small_scope_s1_plan/`。
- API→授权消费者→真实c104及电力模型→解析/Arrow→正常业务QC→分层标签及独立标签QC全部通过。
- 完整APDU解析1188/1188（100%），四路P0/P1捕获工具丢包均0；日志对齐P99为3.647101ms，最大3.880648ms。
- `v04-normal-schedule-v2`及`v04-label-qc-v4`通过，3 valid、0 invalid、0 uncertain，无正常业务违约。
- manifest为completed、code_dirty=false、cleanup_errors=[]；二次消费不重跑，M2不读取管理或真值数据。

## 交付边界

eb2c68c已推送，Actions push45923625862及PR45923631032均success；CI与上述真实实验分别核验，互不替代。未合并任何PR。

已有N01～N10、六类P0三站机制、A08分组维护、四组原始配对及十站六类证据直接复用。固定12组配对复验有9组有效业务对照，统计相似匹配仍为0；不改阈值、不删除未匹配或失败样本。当前切片通过不代表原完整V0.4通过，V0.5未启动。下一步只处理3站流程中明确的科研缺口；多站机制确需验证时最多10站，不再用暂停的大规模覆盖阻塞当前交付。
