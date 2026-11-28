# V0.4配对及标签审查修正原件复核

2026-11-26（Asia/Shanghai）。冻结代码5cff30ba9381e97e9e06da37dbcde81dbdbf342a，源码/var/lib/pvcomm/sources/v04d_review_s1。未重跑实验；原始PCAP、日志、标签v1/v2及历史QC全部保留。

已有四组P03、P04、P06及P03_recovery在原目录新增标签v3、独立标签QC v4及配对QC v2。八成员、24站点标签均valid，无uncertain/invalid；四组data_quality_passed、contrast_observed及passed均true。每个目标站正常成员有效无违约、改变成员出现预期业务违约，非目标站两成员均有效无违约。无效/不确定/null、正常违约、改变成员无预期违约、错误目标及控制站违约的负测试通过。

迟到肯定/否定确认、反馈交付/物理执行、后续同点事务覆盖的修正及版本字段见ADR0021。新版独立QC验证这些区间和恢复证据，不能由CI或注入代替。A05固定版本的Point.transmit公开签名只有cause，无延后负确认参数；已知延后拒绝配置提前排除，运行时未预期实际拒绝继续失败。

实际命令为PYTHONPATH=src /mnt/e/project/pv/.venv-wsl/bin/python scripts/recheck_v04_pairs.py，四个--pair-dir沿用既有验收报告路径，输出/var/lib/pvcomm/runs/v04d_pairs_s1/review-pairs-v2-label-v3.jsonl，退出0，SHA256=e1b085a6bdb5cf67cd9604dafccf494af476815124b4beb54d65dcc279a2bf9b。

本地相关77项回归通过，普通323 passed/61 deselected，Ruff/format185文件、mypy128源文件和schema通过。5cff30b已普通推送，CI45810953828、45810943785实际success。首次代理连接失败、第二次直连reset均保留工具记录，使用单次HTTP/1.1直连后成功，未修改持久Git配置。PR1–5仍OPEN，未合并。

本复核确认共享与业务对照，不代表统计相似匹配、多站/区域恢复、40次覆盖或整体V0.4验收。
