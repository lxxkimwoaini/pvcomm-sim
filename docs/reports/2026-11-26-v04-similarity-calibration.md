# V0.4 独立流量校准及首四组回顾性匹配

日期：2026-11-26。此报告不代表V0.4整体验收通过。

冻结代码3ef4f83df5222f1c100db55e071f650ed1d6cf83；源码`/var/lib/pvcomm/sources/v04d_similarity_s1`，持久输出`/var/lib/pvcomm/runs/v04d_similarity_s1`。规则、窗口、特征、距离、阈值生成公式及全部满足策略在运行前按ADR0022冻结，没有根据下述匹配结果修改。

实际执行`PVCOMM_ALLOW_LAB=1 PYTHONPATH=src /mnt/e/project/pv/.venv-wsl/bin/python scripts/calibrate_v04_similarity.py --policy configs/v0.4-similarity-policy.json --output /var/lib/pvcomm/runs/v04d_similarity_s1/calibration`，退出0。N01、N09、N10各两种独立背景、每组两次真实联合仿真，共12次；每次先通过原正常QC，再生成正式标签并通过独立核验，所有站均valid/false。校准manifest六组completed，阈值文件只在全部通过后产生。

| 冻结产物 | SHA256 |
|---|---|
| calibration/thresholds.json | 276c634a7c9664208e0a9228a5778f87c8d1a78482021f85623b97b31c15e96d |
| calibration/calibration-records.jsonl | f77af451bd5e4cff54941130feaf508c4ab06ee6b1a7ea280ee3b18278167209 |

阈值作为小型开发配置归档于`configs/v0.4-similarity-calibration-dev.json`，实际60项，其中global.up.iat_p50_s达到上限1，该项在当前校准中不具备筛选能力，其他59项仍按具体阈值检查。12次运行共36个valid/false站点标签，invalid/uncertain/违约均0，48路工具抓包drop合计0。原PCAP、解析、物理输出、标签和QC仍留在上述持久目录，未提交Git。它仅来自六组独立正常重复，含预声明工程裕量，不是现场校准或统计置信保证。

使用同一冻结代码的`evaluate_v04_similarity.py`，以已有四组pair目录、该policy和thresholds参数执行，退出0；只增加新版本匹配报告，不重跑或修改原实验。总账为`retrospective.jsonl`，各pair内增加`qc/v04-traffic-similarity-v1/report.json`。

| 配对 | 回顾性尝试 | 数据有效 | 逐站业务对照成立 | 统计匹配 |
|---|---:|---:|---:|---:|
| P03 | 1 | 1 | 1 | 0 |
| P04 | 1 | 1 | 1 | 0 |
| P06 | 1 | 1 | 1 | 0 |
| 维护恢复失败P03_recovery | 1 | 1 | 1 | 0 |

P03未通过目标站上行包长分布；P04在目标站上行包数/字节/间隔以及部分全局上行与下行间隔特征上未通过；P06在目标站部分上下行IAT和下行速率分布上未通过；维护恢复失败在目标站和全局多项包数、字节、间隔、速率和突发特征上未通过。精确距离和各项阈值见原报告。没有将约35%差异或其他未匹配样本宣称为相似，也没有将其标为无效实验或删除。

后续按预声明的12组新独立配对（四类型各三个种子及历史背景）报告尝试/有效/对照/匹配四种计数；目前仅冻结重复矩阵生成器，尚未获得新重复实验结果。没有调整阈值以追求匹配率，没有开展V0.5、720小时生成或训练。
