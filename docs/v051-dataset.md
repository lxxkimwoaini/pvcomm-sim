# V0.5.1 多尺度开发数据集

本版按用户2026-11-27要求修正来源分层、短事件覆盖和严格事件评价。W10=10秒/1秒，W30=30秒/1秒，W60=60秒/5秒，三尺度共享split/assignment.json。旧V0.5目录和配置保留。新划分仅用于开发评估，正式论文测试需要未参与开发决策的来源。

在干净冻结源码和锁定data/experiments环境执行以下命令。目标目录必须不存在，脚本不会创建实验网络或生成新通信。

```bash
uv sync --frozen --group data --group experiments
uv run --frozen --group data --group experiments python scripts/build_v051_dataset.py \
  --root /var/lib/pvcomm/runs --root /mnt/e/project/pv/outputs \
  --dataset-dir /var/lib/pvcomm/datasets/NEW_UNIQUE_V051 \
  --config configs/v0.5.1-dataset.json \
  --previous-dataset /var/lib/pvcomm/datasets/v05_prerelease_s3
```

WSL已验证数据环境可用`/var/lib/pvcomm/venvs/v05-data/bin/python`替代uv，并将PYTHONPATH设为冻结源src。inventory→split只执行一次，再对各尺度构建、QC和固定基线。分步CLI的features、qc-dataset和benchmark均须指定`--scale W10`、W30或W60。benchmark的`--mode M0/M1/M2`投影同一M2表，`--mode M2 --ablation without-rule-proxies`及without-peer执行预登记消融。拒绝覆盖已有产物；失败产物保留，新版本另建目录。

构建完成后可追加校验附录，不改原release.json及训练表。该命令核验旧版全部发布哈希，并按run/站/端点逐行比较9036个W60特征、标签和规则值，再复制指南和字典至publication目录。若来源目录后续增加运行，W60集合自然变化，不能宣称与本次旧控制完全一致；此附录只适用于本轮相同106原件的对照。

```bash
uv run --frozen --group data python scripts/finalize_v051_release.py \
  --dataset-dir /var/lib/pvcomm/datasets/v051_prerelease_s1 \
  --previous-dataset /var/lib/pvcomm/datasets/v05_prerelease_s3
```

## 表与训练读取

根目录runs.jsonl包含合格和排除记录及原件路径/SHA。split/source_profile.json包含每运行/来源的真实事件和三尺度覆盖，stratification.json保存固定目标、枚举结果和组分区。各尺度下features-M2保存features、labels、index、rules四表，后面三表不能拼入X。特征白名单仍为M0的13项、M1累计22项、M2累计39项，字段单位和缺失语义继承[data-dictionary-v05.md](data-dictionary-v05.md)，本版时间与事件修正见[data-dictionary-v051.md](data-dictionary-v051.md)。

六次十站记录保留在每尺度表中，必须通过管理侧runs.jsonl过滤，仅作共享来源补充诊断。训练、验证、主测试使用三站记录。以下只读取已冻结训练分区，不修改分组。

```python
import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

root = Path("/var/lib/pvcomm/datasets/v051_prerelease_s1")
folder = root / "W10" / "features-M2"
runs = {
    r["run_id"]: r
    for line in (root / "runs.jsonl").read_text().splitlines()
    if (r := json.loads(line))["eligible"]
}
columns = json.loads((folder / "whitelist.json").read_text())["cumulative_modes"]["M2"]
features = pq.read_table(folder / "features.parquet").to_pylist()
labels = pq.read_table(folder / "labels.parquet").to_pylist()
index = pq.read_table(folder / "index.parquet").to_pylist()
members = [
    i
    for i, row in enumerate(index)
    if row["partition"] == "train"
    and runs[row["run_id"]]["site_count"] == 3
    and labels[i]["label_status"] == "valid"
]
X_train = np.array([[features[i][c] for c in columns] for i in members], dtype=float)
y_train = np.array([labels[i]["label_at_end"] for i in members], dtype=np.int8)
print(X_train.shape, np.bincount(y_train))
# 仅训练拟合填补/标准化，验证选阈值，测试只评价。元数据不进入X。
```

## 评价及使用限制

event_coverage.json保留全部合并站点事件及finding原始证据，区分完整历史不足、无实际采样端点、端点尚不可在线辨识、真正可评估。t-1ns业务标签与在线起点不混用。一段10秒合并告警最多归属一个事件，重复段另报；连续告警允许归属首个相交事件，不能复用以检出后继事件，正常前缀仍纳入窗口误报。误告警段全部端点须为正常，正常暴露量为每个正常端点前最多step的非重叠槽，不把缺口计满。该槽口径是采样暴露量，不能当作精确连续正常真值时长。

事件delay_s自在线可辨识起点计算，delay_from_violation_s另计真实违约起点。history_wait_s记录完整历史等待，sampling_wait_s记录首个合法端点等待，detection_after_first_endpoint_s记录后续检测等待。漏检与run_end_censored完全独立；事务被覆盖但未证明恢复的情况仍保留各finding。没有point adjustment。

双类验证最大F1并取最高并列阈值。单类正常验证固定使用>=比较、误报<=1%的最低离散阈值，允许nextafter(max,+inf)，不再让大量并列分数直接全部告警。全阳性验证缺少正常校准依据时阻止发布。模型容量和7/17/27种子不变，所有预处理仅训练拟合，IF/EWMA仅正常训练。十站诊断沿用三站校准阈值，不反馈至模型选择。

without-rule-proxies预先删除power_age_budget_ratio、confirmation_wait_s、execution_wait_s、fresh_point_coverage、known_maintenance_active、maintenance_recovery_remaining_s、power_age_s、available_age_s、command_age_s。without-peer另行删除peer_fresh_power_coverage、peer_mean_power_kw、peer_mean_power_age_s。剩余值、缺失掩码及协议语义仍可能间接关联业务约束，该消融不能证明已排除所有规则代理。

规则标签与规则检测高度一致不构成创新。当前小来源、短事件和业务背景不均衡均需披露。research_feasibility.json为固定描述统计，不进行IID显著性推断，也不据测试结果挑窗口。所有结果以本地acceptance.json、ACCEPTANCE.md及逐种子报告为准；未完成新数据运行不能记为通过。
