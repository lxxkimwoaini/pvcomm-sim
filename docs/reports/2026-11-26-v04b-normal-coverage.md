# V0.4b 正常场景开发覆盖与验收记录

2026-11-26，Asia/Shanghai。V0.4b配置化正常场景及三站小规模开发验收通过，依据如下实际运行、独立核验和兼容结果；不代表完整V0.4、正式数据集、40次/类异常覆盖或现场校准。

## 实现与证据边界

新增`0.4-scenario.1`外层配置及必填`business_schedule`，复用已验收的V03Config、区域网络、真实c104、pvlib/pandapower、电气时钟、独立P0/P1和Arrow。`setpoint`、`dispatch`、`interrogation`分别表示显式设定值、站点子集分配和运行中总召唤；空数组没有远方调控。旧`run`及旧配置继续使用原有协议回归时序。

任务时刻由种子和schedule_id独立展开，配置拒绝无效引用、容量越界、同站事务窗口重叠和不完整观察窗。运行ID默认为不含场景类别的UUID，已有目录失败关闭。调控调用真实主站接口；只有终端实际接受回调可以改变远方目标。维护使用已验收的STOPDT、关闭屏障及恢复召唤，物理模型持续运行。

原始场景、有效配置及未来展开计划位于`truth/scenario/`；manifest记录三个哈希及Oracle范围。主站仅在任务到期发布实际意图及协议事实。M2投影白名单和available_at截断不变；同一IEC104链路上的可用功率/辐照观测不作为独立可信M3来源。

新`qc --profile schedule`核验计划成员、真实命令/确认/反馈的独占关联、已接收且新鲜的分配输入、合法拒绝的设备条件，以及本轮总召唤请求至结果的半开窗口。`v04-normal-schedule-v2`另外保留正常实验的严格P0/P1字节对应门禁，不继承故障实验允许跨端差异的条件。旧normal门禁及结果保留；新正常场景门禁不生成正式异常标签，fault_verification与label_quality仍为not_evaluated。

## 冻结与失败保留

21项正常场景实际运行代码为`4ab3fc58485bb010acd0887c53a1c7270ec890ea`，源码`/var/lib/pvcomm/sources/v04b_s2`，证据根`/var/lib/pvcomm/runs/v04b_s2`。最终独立核验代码为`f48d114048d48650533e8aac7d40646144be724d`，源码`/var/lib/pvcomm/sources/v04b_final`。两者分别记录，不将后续核验提交冒称原始运行提交。其间变化仅为来源组元数据生成和严格正常QC，不改变上述21项的物理、协议或调度行为。后续浏览器测试提交不改运行引擎。

首轮`dfa09aa6697d366d16e5b75682bb08a375f886d1`发现主站缓存先于available_at证据发布的并发窗口，主动终止；pytest退出2、130.82s、no tests ran。`/var/lib/pvcomm/runs/v04b_s1`中的部分PCAP和事件保留，不算有效覆盖。修复为同一观测锁内先写证据再发布缓存；I/O失败不发布新值，并增加并发和写入失败负测试。

原v1核验文件和源场景不覆盖，v2另建目录。每份manifest、PCAP哈希、抓包统计、协议JSONL/Arrow、电气状态、潮流及独立QC保留在各run目录；pytest的current符号链接去重后统计，不能重复计算运行次数。

## 正常成立条件

| 场景 | 本轮验证内容 | 证据要求 |
|---|---|---|
|N01|不同历史日期的日间平稳运行|有效遥测和会话，实际功率非零，无隐式旧调控|
|N02|夜间、日出、日落|真实历史小时输入和pvlib太阳几何；夜间零功率，晨昏跨越模型功率边界|
|N03|两个空间云影参数/背景|三个站点按空间传播产生时差，光学层及可用功率变化|
|N04|两次独立单站限发|仅PV01真实发送/接受/反馈，PV02/PV03目标保持|
|N05|区域内子集和分批调控|容量/主站已观测可用功率两种分配；仅PV01/PV02，PV03未选|
|N06|两个限发后解除变体|两次独占事务，释放后功率恢复，受资源/容量/斜率限制|
|N07|两个阴天历史背景下资源不足|合法目标被接受，实际功率符合可用功率，不强求不可达目标|
|N08|两个运行中总召唤变体|本轮请求、ACT_CON、ACT_TERM及COT20测量；不复用启动总召唤|
|N09|两个正常设备停机时的合法拒绝|真实否定ACT_CON、终端拒绝和原目标保持；不是transmit(false)推断|
|N10|两个授权维护与恢复变体|真实停传/关闭/恢复/重召唤、物理连续及新遥测|

N03为历史小时背景上的灰色光学层云影模型，不是真实秒级观测，未完成现场高频校准。日期、位置、设备、网络与响应参数均为开发仿真假设，不能称为辉县实际参数。每类两个变体及N02额外日落只提供功能覆盖，不代表正式统计覆盖或多站规模验收。

## 来源组与划分限制

本轮原始NASA POWER来源哈希为`72a8d6275ef89029b6b34e73d0cc68708ee749eef69dcf59abe65d0fe13346bd`，历史小时背景经已有许可/来源归档接口读取。运行冻结版本早期模板组ID含场景代码，不能用其直接划分数据。后续模板按`weather_<来源哈希前12位>_<UTC原始日期>`共同分组；本报告实测清单另给旧ID至共同组映射，原件保持不变。

本阶段没有执行训练/验证/测试划分。相同原始日期、重复高频模板或共享轨迹的连通分组仍须在V0.5统一核验；仅字符串相等不是完整防泄漏证明。

## 复现

在具备已授权隔离Linux、已验证科学/协议/Arrow环境和原NASA缓存的干净代码树中，选择全新持久根：

```sh
PYTHONPATH=src PVCOMM_ALLOW_LAB=1 /mnt/e/project/pv/.venv-wsl/bin/python -m pytest \
  tests/lab/test_normal_scenarios.py -vv -x --durations=30 \
  --basetemp /var/lib/pvcomm/runs/NEW_NORMAL_BATCH/lab

PYTHONPATH=src /mnt/e/project/pv/.venv-wsl/bin/python -m pvcomm.cli \
  run-scenario --config configs/v0.4b-steady.yaml \
  --output-root /var/lib/pvcomm/runs/NEW_CLI_SCENARIO
```

新运行返回实际run_dir后，再执行`parse --parquet --run-dir <实际目录>`及`qc --profile schedule --run-dir <实际目录>`。重放原场景使用原运行的`truth/scenario/source.yaml`，不能只使用根目录编译后的`scenario.yaml`，否则将遗漏显式业务计划。已有同版解析/QC目录拒绝覆盖；旧证据重验用新的核验版本目录。

## 浏览器与普通检查

Windows冻结f48d114的完整`uv run --frozen python scripts/dev.py check`退出0：182 passed/35 deselected，mypy98源文件、Ruff/格式、schema/生成TS、ESLint/Prettier/vue-tsc、Vitest3和build通过。保留既有两条HTML缩进warning及817.61kB构建提示；lab/data/power未执行项不算普通通过。

真实N03-1运行的Windows副本80文件、7,699,260B，逐文件SHA256与原件一致，原件保留；副本位于`outputs/v04b-ui-evidence/run_949729ea3a094c45aeb77ec63d4e4bc3`。最终Playwright实际2 passed/1 skipped、5.3s，核对1,380个显示点并操作辐照度、并网点电压、因果游标、移动布局和离线恢复。旧V0.1真实副本50文件、530,388B，同样逐文件核验；2 passed/1 skipped、4.5s，111点。空态用例按真实模式跳过，不算真实覆盖；证据分别在`outputs/v04b-real-ui-uuid-final`和`outputs/v04b-real-ui-v01-passed`。

浏览器用实际1008非空遥测识别电力能力，不按UUID/旧版本名称猜测。该验证只覆盖现有M2只读工作台兼容性，不代表场景表单、任务队列、配对回放或标签证据页面已完成。

真实截图复核又发现页眉静态V0.3及跟随最新后滑块仍为0秒两个一致性问题，提交`251c881f2e359ed53e28cbb20acb7fb1f983d573`改为health.stage和实际快照游标，新增真实浏览器断言。修正后UUID回放2 passed/1 skipped、5.3s、1,380点；旧V0.1回放2 passed/1 skipped、4.5s、111点。最终证据在`outputs/v04b-ui-stage-cursor-uuid`与`outputs/v04b-ui-stage-cursor-v01`，此前截图仍保留。web-test/web-build均退出0，Vitest3通过，保留既有两条缩进warning和817.73kB构建提示；不将该前端提交冒称原始运行或QC冻结代码。

同一UI修正对既有30站/1小时真实备份回放复验2 passed/1 skipped、25.0s，核对27,183个显示点及实际游标、电气曲线；证据`outputs/v04b-ui-stage-cursor-30site`。这是历史回放兼容性，不是重新运行30站1小时，也不是新的三时段资源测量。

## 21项真实运行及v2严格复核

批次pytest实际退出0，21 passed in 2907.66s（48分27秒）；逻辑业务时长合计2640s。最终v2对全部21份原始证据复核均退出0、passed=true，运行SHA为4ab3fc5，核验SHA为f48d114，二者均clean。所有manifest completed、cleanup_errors=[]，严格正常跨端对应全部通过。

完整APDU合计26,132个，全部匹配，最小解析率100%，工具丢包0，日志对齐P99最大3.840095ms。14次调控事务中12次accepted_complete、2次rejected；任务起点最大迟到1ms。8,306条物理样本通过适用来源、pvlib/潮流重算、P/Q/斜率、快照和平衡核验。旧normal_regression在21份场景中均为false，因为显式计划不等于旧固定三站命令模板；该结果保留，与新计划验收分列。

下表各目录均位于`/var/lib/pvcomm/runs/v04b_s2/lab/test_real_normal_scenario_<场景>_<变体>/<run_id>`；单次原始NASA日期对应共同来源组。

|场景变体|run_id|气象起点UTC|seed|完整APDU|日志P99 ms|命令/结果|
|---|---|---|---:|---:|---:|---|
|N01-0|`run_82df051050ce41bda0fa5bcbfc795991`|2024-06-23T04:00:00Z|104|1120|3.660135|0 / 无调控|
|N01-1|`run_396eed88f29c427fad4712aae39d0b88`|2024-06-24T04:00:00Z|105|1117|3.832151|0 / 无调控|
|N02-0|`run_236b3bd420f34a7c9d87466d55d35b9a`|2024-06-23T16:00:00Z|104|1118|3.654063|0 / 无调控|
|N02-1|`run_56edf184bc3946d6a330e6c52f90e769`|2024-06-23T21:11:45Z|105|1130|3.634084|0 / 无调控|
|N02-2|`run_8e088237f2e74feea1bfc3f6d1b6307f`|2024-06-24T11:41:00Z|106|1120|3.691935|0 / 无调控|
|N03-0|`run_25f6c678d358408ea8ab467b48887bc9`|2024-06-23T04:00:00Z|104|1770|3.740163|0 / 无调控|
|N03-1|`run_949729ea3a094c45aeb77ec63d4e4bc3`|2024-06-24T04:00:00Z|105|1724|3.669093|0 / 无调控|
|N04-0|`run_ab8c56a97c7740309815e72a5511f1e4`|2024-06-23T04:00:00Z|104|1175|3.747907|1 / accepted_complete|
|N04-1|`run_dffdc02755744a83bf17ce2e802a438e`|2024-06-24T04:00:00Z|105|1143|3.686707|1 / accepted_complete|
|N05-0|`run_097bad8efb1a4898b5a7f0e8bed15e18`|2024-06-23T04:00:00Z|104|1230|3.840095|2 / accepted_complete, accepted_complete|
|N05-1|`run_9b96cf1c8c654d9781494bf76b5254e9`|2024-06-24T04:00:00Z|105|1199|3.729135|2 / accepted_complete, accepted_complete|
|N06-0|`run_6c500a66b0cd458cb55011d90f283923`|2024-06-23T04:00:00Z|104|1750|3.806429|2 / accepted_complete, accepted_complete|
|N06-1|`run_ccbe11e668c443f49863708cc50205f4`|2024-06-24T04:00:00Z|105|1709|3.767853|2 / accepted_complete, accepted_complete|
|N07-0|`run_09338eee09f44188a8ec4c906baf30e1`|2024-06-04T04:00:00Z|104|1121|3.766220|1 / accepted_complete|
|N07-1|`run_80bfc4110b5946b3a1e08ab0d31485cd`|2024-06-05T04:00:00Z|105|1121|3.741375|1 / accepted_complete|
|N08-0|`run_f2e702958e6d47cdbb483ffea83eae72`|2024-06-23T04:00:00Z|104|1127|3.769429|0 / 无调控|
|N08-1|`run_a64e69d4dde54e74ae44017a0b478459`|2024-06-24T04:00:00Z|105|1129|3.787322|0 / 无调控|
|N09-0|`run_586f3cd02d8248668e7d239ec3df73d1`|2024-06-23T04:00:00Z|104|1164|3.818012|1 / rejected|
|N09-1|`run_41a63fc850a545fcaa9ca26271159e22`|2024-06-24T04:00:00Z|105|1155|3.695475|1 / rejected|
|N10-0|`run_ab0e4ca67e09465db341e7def07d1ee6`|2024-06-23T04:00:00Z|104|1004|3.686569|0 / 无调控|
|N10-1|`run_7b13b9f1563c438f8c75b325b4bce551`|2024-06-24T04:00:00Z|105|1006|3.729616|0 / 无调控|

### 共同来源映射

每行旧source_group_id为`normal_development_<场景>_<变体>`，例如N02-1为`normal_development_N02_1`。按下表映射至共同组，不改写任何原件；同组不能跨分区。

|共同source_group_id|原始日期|实际运行数|成员（由场景变体唯一定位旧ID）|
|---|---|---:|---|
|`weather_72a8d6275ef8_20240623`|2024-06-23|10|N01-0, N02-0, N02-1, N03-0, N04-0, N05-0, N06-0, N08-0, N09-0, N10-0|
|`weather_72a8d6275ef8_20240624`|2024-06-24|9|N01-1, N02-2, N03-1, N04-1, N05-1, N06-1, N08-1, N09-1, N10-1|
|`weather_72a8d6275ef8_20240604`|2024-06-04|1|N07-0|
|`weather_72a8d6275ef8_20240605`|2024-06-05|1|N07-1|

### 数据保留与普通科学检查

21份运行在v2复核后合计127,517,958B，其中PCAP 10,479,522B、Parquet 4,899,784B；这是本批开发证据实测，不替代30站资源门禁或正式720小时预算。原v1与新v2并存，失败首轮及Windows校验副本另计。汇总源文件保存在`/var/lib/pvcomm/runs/v04b_final/coverage-metrics.json`及本机忽略文件`outputs/v04b-final-coverage-metrics.json`，SHA256为`700d7d5ec36dcb0c14b72f498be2ff223a378f9d856a1ecf16e0d6196e11f45b`。

f48d114的WSL `PYTHONPATH=src ... -m pytest -q -m "not lab"`实际189 passed/28 deselected、11.79s，退出0，含科学模型与Arrow；28项lab没有计入此通过数。独立QC命令与耗时保存在`/var/lib/pvcomm/runs/v04b_final/commands.jsonl`及各qc-NN.log。

## f48d114冻结兼容回归

持久证据根`/var/lib/pvcomm/runs/v04b_final`。各namespace实验顺序执行，保留全部原始证据；测试日志的pytest时长与外层命令墙钟分别记录，不混用。

|检查|实际结果|证据|
|---|---|---|
|旧V0.3断联物理连续与恢复、V0.1真实回环/独立端点、取消清理、抓包尾包|5 passed、185.35s，退出0|legacy.log及legacy/|
|旧V0.3严格物理/协议QC|2,131/2,131完整APDU、P99=2.720530ms，passed=true|legacy/test_physics_continues_without0/v03_03_disconnect_001/prerequisite-qc.json|
|V0.4a缺测降级与真实silent接口|2 passed、235.15s，退出0|degradation.log及degradation/|
|V0.4a三站独立实验质量|2,527/2,527完整APDU、P99=3.813604ms，passed=true|degradation/test_planned_missing_availabil0/v04a_03_degradation_001/qc/v04-experiment-quality-v1/report.json|
|自建silent真实协议fixture|确认超时3.000418187s，同会话重发被隔离，另一站accepted，clean|degradation/test_real_silent_terminal_time0/protocol-results.json|

silent仅为接口回归fixture，不是正式A05施加或标签验收；V0.4a三站合法拒绝仍保留旧normal_regression=false。上述旧版短回归不冒称重新执行V0.1的30分钟或V0.3的30站一小时正式门禁。

旧V0.2晴天90s CLI `run --isolated`、`parse --parquet`、默认严格`qc`均实际退出0，墙钟分别102.824/3.120/8.382s；证据`v02/v02_clear_001`。453/453完整APDU、P99=5.696731ms、manifest clean/completed/cleanup=[]。

最终代码另通过真实CLI执行`validate-scenario`→`run-scenario`→`parse --parquet`→`qc --profile schedule`，全部退出0；墙钟分别0.436/135.647/3.459/9.602s。运行`scenario/run_936bfe63db414081bfb028905b55b4be`，f48d114 clean/completed/cleanup=[]，1,117/1,117完整APDU、P99=3.662255ms，source_group_id=`weather_72a8d6275ef8_20240623`，显式空调度没有调控。它是21项批次之外的CLI烟测，不重复计入21项覆盖或上述批次存储统计。

最后实际检查Linux namespace列表为空，运行器断言恢复到初始资源状态。已有证据、冻结树及失败记录均保留；PCAP、来源输入、运行产物和本机副本没有进入Git。

## 结论及尚未完成范围

本切片完成显式业务调度、N01–N10三站正常边界覆盖、严格正常计划QC、来源组修正、因果观测发布修复和既有M2工作台兼容。运行和核验分别冻结，所有结果以实际命令退出和持久证据为准；普通CI不能替代上述特权实验。

完整V0.4尚未验收：六类P0真实单故障、P03/P04/P06配对、正式分层标签与证据核验、场景表单/任务队列/配对回放/标签查询以及多站/30站复验未完成。本批每类两个变体不代替正式独立事件覆盖或每类五次人工Wireshark抽检；没有执行正式数据集划分、720小时生成、异常检测训练或现场高频校准。下一切片按`docs/plans/2026-11-26-v04c-single-faults.md`逐项实现机制及实际施加证据，仍保留原标签定义和质量门槛。
