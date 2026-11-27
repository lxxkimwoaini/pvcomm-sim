# V1.0正式数据读取与生产续跑

本轮固定三站。正式测试来源为此前未使用的2024-06-27、28、29日，不参与训练、阈值、特征或方法选择。本轮不运行benchmark。旧版106次运行中100次三站用于开发训练/验证，6次十站保持历史索引，不进入本版主训练表。旧validation保留，旧train和已被分析的test合并为train；三个尺度共用固定来源划分，禁止重新随机分配窗口。

当前生产源码冻结58aa8e5，目录`/var/lib/pvcomm/sources/v10_production_s2`；任务配置及协议位于`/var/lib/pvcomm/runs/v10_production_s2_plan`，真实运行在`/var/lib/pvcomm/runs/v10_production_s2`。s1因数据根符号链接被Git识别为未跟踪文件，在生成仿真前被干净门禁拒绝，失败日志保留。s2保留真实忽略目录及其内部气象链接，code_dirty=false。

仅在没有原执行进程时续跑同一冻结任务；原进程仍在运行时不要重复启动。既有SQLite锁拒绝并行消费，已完成任务不重跑，失败至多两次尝试。授权隔离WSL中执行：

```bash
cd /var/lib/pvcomm/sources/v10_production_s2
export PYTHONPATH="$PWD/src"
export PVCOMM_ALLOW_LAB=1
/mnt/e/project/pv/.venv-wsl/bin/python -u scripts/run_v10_production.py \
  --plan /var/lib/pvcomm/runs/v10_production_s2_plan \
  --output /var/lib/pvcomm/runs/v10_production_s2
```

每个完成片段已经过现有运行器的解析、协议/物理Arrow导出、业务或机制QC、正式标签和独立标签QC；summary.json记录真实状态。运行中和失败片段只保留证据，不进入训练，不作正常负例。需要新增发布快照时，在干净、包含build_v10_dataset.py的处理源码目录执行；已有目标拒绝覆盖，用新唯一名称保留历次交付：

```bash
cd /var/lib/pvcomm/sources/v10_export_s1
export PYTHONPATH="$PWD/src"
/var/lib/pvcomm/venvs/v05-data/bin/python scripts/build_v10_dataset.py \
  --plan /var/lib/pvcomm/runs/v10_production_s2_plan \
  --production /var/lib/pvcomm/runs/v10_production_s2 \
  --dataset-dir /var/lib/pvcomm/datasets/pvcomm_v1.0
```

后续目录使用`pvcomm_v1.0_s2`等新名称。构建复用V0.5.1的W10/W30/W60特征与13项独立QC，不重算划分最优解、不训练模型。继承的schema/purpose字符串用于兼容，正式发布版本、固定划分方法和来源优先以production_protocol.json及split/stratification.json为准。只有QC全部通过才写release.json；失败快照保留，不能作为已验收版本。

首批实际快照`pvcomm_v1.0`已完成，处理源码d0c5ab2、code_dirty=false，276.824006秒；101次三站运行，train/validation/test为76/24/1次，来源组7/1/1。该快照只包含构建开始时已合格的新增A01，后续完成的运行要使用新目录增量导出。三尺度各13项QC通过，44个发布文件SHA逐项验证通过；三尺度M2训练表实际读取为43287×39、38727×39、6423×39。它是可读取的首批数据，不代表六类新测试或24/168小时生产已完成。后续快照和覆盖以[实际交付报告](reports/2026-11-27-v10-first-delivery.md)为准。

原始PCAP、协议事件、主站观测、事件标签、QC及校验和通过raw_structured_index.json关联到持久原件；不复制大PCAP，也不改时间戳。每尺度features-M2目录含features/labels/index/rules四个Parquet表。M0/M1/M2列名单在whitelist.json；index、标签、规则及来源元数据不得拼入X。空值保留缺测语义，仅训练集拟合填补。详细字段继承data-dictionary-v05.md和data-dictionary-v051.md。

```python
import json
from pathlib import Path

import pyarrow.parquet as pq

root = Path("/var/lib/pvcomm/datasets/pvcomm_v1.0")
folder = root / "W10/features-M2"
columns = json.loads((folder / "whitelist.json").read_text())["cumulative_modes"]["M2"]
features = pq.read_table(folder / "features.parquet").select(columns)
index = pq.read_table(folder / "index.parquet")
labels = pq.read_table(folder / "labels.parquet")
train = [p == "train" for p in index["partition"].to_pylist()]
X_train = features.filter(train)
y_train = labels.filter(train).select(["label_at_end"])
print(X_train.shape, y_train.shape)
# 正式test保持封存；本轮不选阈值、不评估模型。
```

统计中的独立阳性运行不等于站点展开事件或重叠窗口。故障已施加但未违约的合格样本保留；数据卡列出实际每类阳性与尚未满足覆盖。正常时长以合格无违约正常任务的预热后时长累计，不将站点小时乘三，不将异常任务中的正常窗口充作168小时正常建设时长。48个半小时是独立初始化片段；气象源时间和真实采集时间分别保留，不声称连续一周或现场实测。

24小时先于168小时扩充。当前计划不自动追加剩余144小时；只有实际完成且通过门禁才入库。所有大数据、PCAP和任务产物仅在本地WSL，代码和轻量文档进入Git。
