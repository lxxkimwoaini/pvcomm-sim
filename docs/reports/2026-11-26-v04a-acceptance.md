# V0.4a 前置修复验证记录

2026-11-26（Asia/Shanghai）。当前为前置修复切片，**不是V0.4场景库、六类P0、配对或标签验收报告**。V0.4a前置修复门禁已通过，最终冻结及完整兼容结果见“最终完整验收”一节。故障施加和正式标签机制留给后续切片，当前明确not_evaluated。

## 范围与冻结

从已验收V0.3的5c406666e31e6edb3268e2be041ec9aa881ed26d建立feat/v04-scenarios。计划见`docs/plans/2026-11-26-v04.md`，设计依据ADR0011–0013。未合并PR #1–#4，保留原有堆叠关系及main。

首轮通过前置用例的真实源码冻结为`32f93f1f02878939d79c082b20a1e847bea339a8`，原生Linux目录`/var/lib/pvcomm/sources/v04a_s4`，Git clean。WSL使用已验证的Python3.11.15、c1042.2.1、pvlib0.16.1、pandapower3.5.6、tcpdump4.99.6、TShark4.6.4。外部输入通过被忽略的实体data目录访问原有NASA缓存，不重写原件。

证据根`/var/lib/pvcomm/runs/v04a_s4`；前置用例位于`prerequisites/`，日志为`prerequisites.log`。用例标识沿用配置中的v04a_03_degradation_001，复验由不同持久根隔离，不能把同名开发用例当作正式数据集全局唯一run_id。V0.4b起批量/配对编排须分配全局唯一run_id，pair_id/source_group_id另存。

## 已实现

- 默认正常QC继续要求完整成功业务；新增显式experiment质量报告，独立报告运行完整性、数据质量、正常回归及业务事实。故障施加和标签质量当前明确not_evaluated。
- 从真实raw ACT_CON取得正/否确认，保留栈返回事实；区分超时、失联、不可用和会话隔离。同站串行及独占证据继续保留，超时后同会话不重用。
- 可用功率缺测/陈旧/品质无效时按站排除，显式记录未分配量，其他站及后续任务继续；不读取Oracle补观测。正常资源零值与未知分开。
- 截止前不判超时；无效品质不能完成反馈；有线上ACK但缺应用回调及被后续任务截短的执行窗保留不确定。非有限遥测写null并保留原始报文。
- 已授权维护采用真实STOPDT及关闭回调屏障再暂停RTU；物理继续。非计划故障不走正常维护握手。旧0.1/0.2路径不变，QC未放宽。
- 新离线气象来源契约支持声明日期、位置、产品版本和来源/许可快照；旧配置不改。当前仅NASA小时适配，不声称接入新高频数据或M3。
- 保留等级、只读预算、保留原件的无损归档；协议与物理Arrow逐值对原JSONL投影核验，新物理Parquet使用zstd，原表版本保留。

## 三站真实联合仿真（s4前置复验）

运行210秒，PV01授权维护40–60秒；PV02正常本地停机45–75秒；第50、130秒执行两次基于实际收到可用功率的区域任务。使用三个独立RTU、真实区域网络、主站P0和三份独立P1。

| 检查 | 实际结果 |
|---|---|
| 运行与清理 | completed、code_dirty=false、cleanup_errors=[] |
| 第一次分配 | PV01因stale排除，PV02实际否定确认，PV03接受 |
| 第二次任务 | 三站均accepted_complete |
| 事务证据 | 四次完整接受，一次rejected；否定确认有实际raw、P0及终端决策证据 |
| 完整APDU | 2480/2480，100%；不完整字节0，未匹配0 |
| 工具丢包 | P0和三份P1均0 |
| 日志对齐 | 2480样本，P99=3.722319ms，最大5.012098ms |
| 双端/回调 | 各站字节对应、各自终端回调对应；未使用控制尾帧豁免 |
| 物理与来源 | 656电气样本；来源、pvlib/潮流重算、P/Q/斜率、快照及馈线平衡检查通过 |
| 适用网络检查 | 12路qdisc读回，drops0 |
| 实验质量 | run_integrity=true、data_quality=true |
| 旧正常门禁 | normal_regression=false，合法拒绝及缺测并未被篡改为全部成功 |

四次正确认耗时22.638074、89.835043、224.704504、227.627310ms。数据质量通过只支持本次前置用例，不证明P0故障施加或正式标签有效。后续A02等需独立区分预期网络影响与采集丢失，不能直接豁免normal_network_parameters整个检查。

同一冻结代码的真实回环协议fixture使用自建终端ResponseState.NONE，确认超时3.000395088秒；positive_confirmation=null、stack_result=false；随后同会话命令session_quarantined，另一站accepted。原始TCP/IEC104与PCAP实际产生。此fixture仅检验真实接口，**不替代独立P0/P1的正式A05场景**。两用例合计`2 passed in 238.41s`。

## 普通、浏览器及存储验证

完整`uv run --frozen python scripts/dev.py check`退出0：161 passed/13 deselected、mypy88源文件、Ruff/格式、schema/TS、ESLint/Prettier/vue-tsc、Vitest3及build通过。既有两条HTML缩进warning与817.61kB构建提示保留。未把lab的deselected计为通过。

实际本机API与浏览器回放V0.3小时原件：2 passed/1 skipped、25.6s，核对27183显示点、历史游标及缺测边界；跳过空态是实际数据模式预期。证据另存`outputs/v04a-real-ui-s1`。该结果验证当前M2兼容，不是V0.4任务队列或场景表单验收。

新气象描述符读取原有NASA原件/政策快照验证valid=true，哈希仍为72a8d6275ef89029b6b34e73d0cc68708ee749eef69dcf59abe65d0fe13346bd。其他日期/坐标/产品版本及来源损坏使用明确的合成单元fixture验证，不冒充新真实历史来源。

只读清单覆盖V0.3备份395文件、1,390,158,812B（含辅助证据），不代替原运行1,383,250,188B口径。720h×1.5预算1,493,910,203,040B，完整备份另计。已知单PDML11,275,084,964B仅临时空间下界；全流程峰值、并发吞吐尚未测量。

实际资源日志2,332,442B压缩至149,953B，解压SHA256与源均为37d131a09865f69fda29b33e25450790be9acffbb3bf3174627a397990624f69，原件复查相同并保留。新归档在`outputs/v04a-verified-resource-archive`。没有删除旧PCAP、JSONL、Parquet、来源或备份。

## 失败记录

- v04a_s1，2 failed/230.42s：顶层data符号链接被Git识别为未跟踪，manifest如实dirty；另有c104原生浮点点值拒绝Python int。保留全部证据。改实体data目录、应用边界转float后分别复验。
- v04a_s2，2 failed/237.34s：业务及物理通过，PV01缺一个P1接收S回调；帧在stop结束后约14.889ms到达，独立质量正确失败。未扩大QC尾部窗口，按ADR0013修复正常维护流程；另一失败仍为旧冻结整数接口，不能当作已运行新修复。
- v04a_s3/silent，1 passed/1 deselected、3.97s：72e56a0下超时3.000302849秒、隔离/其他站成功。最终s4同时复验两项，未用s3替代s4。

## 边界及后续

V0.4b–f仍未验收：N01–N10完整覆盖、六类P0多强度多范围复验、P03/P04/P06、正式分层标签/schema与质量、任务队列/场景表单/诊断时间轴/配对回放及四类最终报告仍待实施。M3保持未实现；默认M2没有注入表、P1、Oracle或未来信息。`label`仍返回3，不能把实验质量报告当正式标签。

不生成720h、不训练模型，不改变正式参数/划分/标签定义，不合并未授权PR。正式生成前仍须落实约1.494TB主存储、额外备份、解析临时峰值和并发验证。

## 兼容回归历史失败与修复

冻结32f93f1的旧版回归为1 failed/3 passed、193.29s；V0.1真实回环、独立端点及取消清理三项通过。V0.3断联物理继续和恢复最新状态断言通过，严格独立协议QC失败：PV02/PV03的P1原始PCAP各缺3条最终S/STOPDT帧，P0与各终端raw均存在。tcpdump captured/received分别866/875、783/792，kernel dropped均0。证据在`/var/lib/pvcomm/runs/v04a_s4/legacy`，不修写原始文件或放宽QC。

按ADR0014修复有界缓冲排空；真实ICMP尾包预检1 passed/3.56s（三次），仅是捕获回归fixture。当时新冻结代码的V0.3/V0.4a和旧V0.2真实兼容验收待执行，未标为通过；最终s6结果见下文。当前WSL科学/Arrow全量实际168 passed/6 deselected、32.48s。

冻结67ee2ef的v04a_s5旧回归1 failed/4 passed、67.67s：启动阶段读取半写入的master.ready为空，manifest failed，未冒称已完成120秒运行。四路抓包均drain_complete=1、captured=received、kernel dropped0。采用临时文件原子发布起点，新增半写入并发回归通过；Ruff/format/mypy91及普通163 passed/14 deselected。该实际新问题与原P1尾帧缺失分开记录，当时仍待完整新冻结实跑；后续结果见下文。


## 最终完整验收

最终冻结提交为`097a616bc19ad7204c34aaa4ff25f9cc011ae485`，源码`/var/lib/pvcomm/sources/v04a_s6`，实验根`/var/lib/pvcomm/runs/v04a_s6`。全部新运行manifest均code_dirty=false；未修改冻结树、原始PCAP、旧QC或失败证据。以下结果由该提交重新执行，不将s4/s5结果混作最终结果。

|验证|实际结果及证据|
|---|---|
|旧版真实回归|`legacy.log`：5 passed /188.19s，包括V0.3断联、V0.1真实回环、独立端点、取消清理和缓冲尾包|
|V0.3断联严格QC|120s业务；2109/2109完整APDU、100%、不完整0；日志P99=1.868130ms/max2.793382ms；9次事务均accepted_complete；物理连续、恢复最新状态及独立端点/Arrow/潮流检查通过|
|V0.3捕获排空|P0/P1 captured=received分别2511/853/865/793，全部drain_complete=1，kernel drop0；completed/cleanup=[]|
|V0.4a真实前置|`prerequisites.log`：2 passed /237.40s；210s三站模型和真实超时协议fixture|
|V0.4a独立协议|2528/2528完整APDU、100%、不完整0；日志P99=3.748478ms/max4.084588ms，四路capture和12路qdisc丢失0；双端回调/字节对应通过|
|V0.4a业务/物理|PV01 stale排除、PV02真实否定确认、PV03接受；后续三站接受；4 accepted_complete+1 rejected；657物理样本及Arrow/重算/快照/平衡通过|
|V0.4a质量分离|run_integrity=true、data_quality=true、normal_regression=false；拒绝/缺测事实保留，未放宽原正常门禁|
|实际确认耗时|4次正确认23.864911/74.703258/231.802931/194.619488ms，均在原3s期限内|
|silent协议fixture|实际超时3.000455484s，同会话session_quarantined、其他站accepted；无伪造否定确认。fixture_only=true，不冒充正式A05或双端异常库|
|V0.2兼容|`v02-run.log`、`v02-parse.log`、`v02-qc.log`：90s运行→parse --parquet→默认严格QC均退出0；462/462完整APDU、100%、工具drop0，日志P99=11.492783ms/max12.092638ms，9次完整业务闭环|

V0.3报告在`legacy/test_physics_continues_without0/v03_03_disconnect_001/prerequisite-qc.json`；V0.4a报告在`prerequisites/test_planned_missing_availabil0/v04a_03_degradation_001/qc/v04-experiment-quality-v1/report.json`；V0.2报告在`v02/v02_clear_001/qc/v02-power-v7/report.json`。最终namespace列表为空；所有运行的cleanup_errors=[]。

复现需要授权WSL Linux、原有许可/哈希输入、可选protocol/power/data组及独立新输出根。测试入口如下；真实实验按顺序执行，不与其他namespace负载并行：

```sh
PYTHONPATH=src PVCOMM_ALLOW_LAB=1 /mnt/e/project/pv/.venv-wsl/bin/python -m pytest tests/lab/test_v03_prerequisites.py tests/lab/test_v01_runtime.py tests/lab/test_isolated_network.py tests/lab/test_capture_drain.py -q --basetemp /var/lib/pvcomm/runs/NEW_UNIQUE_ROOT/legacy
PYTHONPATH=src PVCOMM_ALLOW_LAB=1 /mnt/e/project/pv/.venv-wsl/bin/python -m pytest tests/lab/test_v04a_degradation.py -q --basetemp /var/lib/pvcomm/runs/NEW_UNIQUE_ROOT/prerequisites
PYTHONPATH=src /mnt/e/project/pv/.venv-wsl/bin/python -m pvcomm.cli run --isolated --config configs/v0.2-clear.yaml --output-root /var/lib/pvcomm/runs/NEW_UNIQUE_ROOT/v02
PYTHONPATH=src /mnt/e/project/pv/.venv-wsl/bin/python -m pvcomm.cli parse --run-dir /var/lib/pvcomm/runs/NEW_UNIQUE_ROOT/v02/v02_clear_001 --parquet
PYTHONPATH=src /mnt/e/project/pv/.venv-wsl/bin/python -m pvcomm.cli qc --run-dir /var/lib/pvcomm/runs/NEW_UNIQUE_ROOT/v02/v02_clear_001
```

Windows当前普通检查163 passed/14 deselected，Ruff/format/mypy91通过；之前完整check含schema/TS及前端质量/Vitest3/build通过，最新两次生产修改仅涉及抓包及启动IPC。097a616 push CI45744568239实际success、五job成功；普通CI不运行特权实验。未重跑V0.3一小时或30分钟V0.1门禁，本次为旧配置短兼容回归，原完整验收证据继续保留。

V0.4a通过仅表示本文范围的前置修复和兼容门禁通过，不代表V0.4整体完成。N01–N10后续切片设计已细化到`docs/plans/2026-11-26-v04b-normal-scenes.md`；六类P0、P03/P04/P06、正式标签、任务队列和四份V0.4最终报告尚未完成。

最终冻结097a616的WSL科学/Arrow全量为170 passed/7 deselected、12.56s（`science-data-regression.log`）；该结果包含全部非lab测试及可选科学/Arrow依赖，不与Windows163项相加成独立用例总数。
