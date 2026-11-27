# V0.5 数据集生产与训练复现

依据2026-11-27科研范围调整，默认3站、最多10站。下列命令只处理已保留原件；不会创建网络或启动仿真。使用干净冻结源码；目标目录必须不存在，禁止复用已存在目录。

```bash
uv sync --frozen --group data --group experiments --group power
# 历史运行若缺当前版标签，可在干净冻结代码下仅补充新版本派生产物。
uv run --frozen --group data --group power python scripts/prepare_v05_labels.py \
  --root /var/lib/pvcomm/runs --root /mnt/e/project/pv/outputs \
  --report /var/lib/pvcomm/datasets/new_unique_preparation.json

uv run --frozen --group data --group experiments python scripts/build_v05_dataset.py \
  --root /var/lib/pvcomm/runs --root /mnt/e/project/pv/outputs \
  --dataset-dir /var/lib/pvcomm/datasets/new_unique_release \
  --config configs/v0.5-dataset.json
```

分步入口（用新的数据集目录，不与上述完整命令重复执行）：

```bash
uv run --frozen --group data pvcomm dataset-inventory --root /var/lib/pvcomm/runs \
  --root /mnt/e/project/pv/outputs --dataset-dir /var/lib/pvcomm/datasets/new_unique_release
uv run --frozen pvcomm split --dataset-dir /var/lib/pvcomm/datasets/new_unique_release \
  --config configs/v0.5-dataset.json
uv run --frozen --group data pvcomm features \
  --dataset-dir /var/lib/pvcomm/datasets/new_unique_release --mode M2
uv run --frozen --group data pvcomm qc-dataset \
  --dataset-dir /var/lib/pvcomm/datasets/new_unique_release
uv run --frozen --group data --group experiments pvcomm benchmark \
  --dataset-dir /var/lib/pvcomm/datasets/new_unique_release --mode M0
# 同一特征表继续分别使用--mode M1和--mode M2；不重新扫描原件。
```

现有WSL环境可用`/var/lib/pvcomm/venvs/v05-data/bin/python`替换uv，设置`PYTHONPATH`指向所选干净冻结源的src。完整脚本自动执行独立QC，训练入口验证其通过且哈希仍一致；三个模式都投影同一已审计M2样本表。任何阶段失败保留证据，使用新唯一发布目录，不删原件重跑。

## 产物与数据字典

| 路径 | 含义/权限 |
|---|---|
| runs.jsonl / inventory.json | 有效及排除清单、实际代码/配置、PCAP/协议/业务/天气/标签/QC路径与SHA；仅数据管理，不作模型特征 |
| split/assignment.json | 冻结运行、来源、日期、pair传递分组及train/validation/test；先于窗口生成 |
| split/policy.json / audit.json | 参数快照、哈希与零交叉核验 |
| features-M2/features.parquet | sample_id与M0/M1/M2累计白名单数值；M0/M1通过投影使用同一批样本 |
| features-M2/labels.parquet | 独立Oracle标签，禁止加入X |
| features-M2/index.parquet | sample_id对应运行/站点/真实UTC半开窗口/分区/组及最晚输入时刻；禁止加入X |
| features-M2/rules.parquet | 独立主站业务规则结果，仅作基线，不加入学习特征 |
| features-M2/whitelist.json / quality.json | 白名单、版本、样本数量及校验和 |
| dataset_statistics.json / leakage_report.json | 真实类型/分区/样本分布和独立标签映射/因果/来源零交叉审计 |
| benchmark-M0、M1、M2/report.json | 规则、IF、RF、EWMA指标、阈值、三种子、训练拟合参数、逐事件漏检和时延 |
| benchmark-*/predictions.parquet | 原始测试分数和预测；不做point-adjustment |

模型输入由`FEATURES[mode]`显式选择，绝不将整个Parquet表直接当作X。sample_id是不透明关联键，不是特征。run_id、station_id、场景、日期、故障配置、Oracle/P1、最终标签/规则判断均不在白名单。天气值、可用功率等M2量仅来源于IEC104链路已收到的观测，不是独立可信气象；M3未实现。

M0含双向包/字节、载荷、包长均值/标准差、包间隔均值/P95、速率、重传、10ms突发比例及活跃时长；只读P0。M1增加I/S/U、否定确认、命令、GI/时钟同步、测量对象和品质统计。M2增加已见功率/可用功率/电压、接收年龄/覆盖、最后已知调控目标与等待时长、已知合法维护窗口、同游标其他站新鲜观测覆盖及均值。单位分别为包、byte、秒、kW、kV及无量纲比例；具体字段固定于whitelist.json。

1008为可用功率，1006为已接受设定值回送，两者不可互换。缺测/无效品质保持null，零功率保留0；陈旧样本保持实际接收年龄，跨站均值排除陈旧和无效观测。预处理在训练拟合中位数、缺失指示和标准化，IF/EWMA只用正常训练样本，RF使用监督训练样本，参数存入报告。

`label_at_end`为t-1ns业务违约状态；`label_any`及`anomaly_overlap_ratio`为[t-60s,t)内违约并集，`observable_at_end`独立表示当时是否已达在线可辨识时刻。`finding_ids`定位原始逐站事件/证据，恢复、被后续事务覆盖、未恢复截尾保留。无效/不确定运行不能作为正常负例；有效施加但未违约的实验可作为合法正常边界。整个故障运行不会统一标异常。

本轮是模拟预发布可用性验证，不是现场效果或算法创新结论。数据集统计分别报告实际独立运行/分段时长、fault与业务事件/窗口，不能把窗口数冒充独立实验数或把分段时长称为连续记录。训练/验证/测试组数及缺少的机制如实披露；同组重叠窗口不随机拆分。168小时、24小时及建议5～10次补充根据当前实际缺口另行推进，不影响当前数据使用。
