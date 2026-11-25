# V0.3 多站点电力—通信联合仿真开发验收

日期：2026-11-25（Asia/Shanghai）。结论：**V0.3 开发门禁通过**，包版本0.3.0.dev0。完成3→10→20→30站逐级实跑，以及30站、3通信区域、3600秒实时联合仿真和三时段Web资源测量。没有生成720小时正式数据集、V0.4异常库或训练检测模型；本结论不代表辉县现场参数校准或正式研究数据发布。

依据：[V1.3](../分布式光伏电力—通信联合仿真数据集构建方案_V1.3.md)第6、10、13、15、17、21节，[计划](../plans/2026-11-25-v03.md)及ADR0006–0010。保留真实c104、独立P0/P1、pvlib、pandapower、Arrow、事务证据独占及observed/truth隔离；没有重建原项目。

## 1. 冻结、来源与证据位置

- 基线：e4e14b91e3412f5f1e43dd9a13ee5fc2b5d98eeb，分支feat/v03-multisite。
- 逐级冻结：76a3d2c2b6e0293b54ea506e359f3ae1c0278123，源码`/var/lib/pvcomm/sources/v03-s7`；证据根`/var/lib/pvcomm/runs/v03_scale_s7`。
- 最终回归及一小时冻结：29aa7e0b17c094e13692166f2ddd7fcc62d01cb0，源码`/var/lib/pvcomm/sources/v03-hour-s8`，code_dirty=false。76a3d2c→29aa7e0的src差异实际检查为空；后者增加浏览器就绪等待及测试/文档。
- 一小时唯一验收证据键：`v03_hour_s8/v03_30_normal_001`，持久路径`/var/lib/pvcomm/runs/v03_hour_s8/v03_30_normal_001`。开发配置内run_id可能在其他预检根复用，不能把不同输出根混合为同一运行或正式数据集。所有重跑使用全新根，未覆盖旧数据。
- 业务起点：北京时间2026-11-25 21:22:08.701296625，UTC `start_utc_ns=1795612928701296625`；实际业务区间3599.999549616秒，速率1×；含初始化/停止的CLI墙钟3637.690秒。
- 协议配置SHA256：`d3f1c98d52f377cbe404a4246487f8c94cce67abadc310f7f13d3ba99a1527e2`；完整电力配置SHA256：`60ce8dee2efbaa955f3cc363b5436e3fa88a971df8b872462d8f41ed732c3e4c`。
- NASA POWER Hourly v2.10.5，MERRA2/SYN1DEG/POWER，2024-06整月、35.4N/113.8E、330.63m；源时间起点2024-06-23T04:00:00Z。规范化SHA256：`72a8d6275ef89029b6b34e73d0cc68708ee749eef69dcf59abe65d0fe13346bd`。原始响应、版本、来源/政策、规范化输入随运行truth/weather保存；小时历史卫星/再分析，不是现场秒级实测。
- P0原始PCAP SHA256：`f56926cf044e4d2be2b379d8e4a7ffdafcc68e57a77dc2c60fd8d5facbcab622`；各P1哈希及工具统计见manifest.captures。
- 完整本地备份：`E:/project/pv/outputs/v03_hour_s8_backup`，394文件、1,390,074,210字节，复制件逐一与原件SHA256一致；原件保留。`backup-manifest.json`记录所有文件哈希；其中frozen-source.tar为新生成的Git源码归档，其SHA256为`a24c40860d717bc242e13b8924367a25eda69ff4cb5034ac2dc88315594ad7e5`，不是从原目录复制的文件。
- 后验报告：运行内`qc/v03-power-v4/report.json`；父目录`performance-report.json`、`final-metrics.json`、`postprocess-timing.jsonl`；Web客户端证据原在`outputs/v03_hour_s8_web`，备份副本在`web-client/`。原manifest的qc_status仍pending，独立QC报告passed=true；按既有契约不回写原始manifest。

运行环境：Intel Core Ultra7 265K；WSL2 Ubuntu/Linux6.6.87.2、glibc2.43、20逻辑CPU、MemTotal 24,327,336KiB、6GiB swap；Python3.11.15、c1042.2.1、pvlib0.16.1、pandapower3.5.6、pyarrow23.0.1、tcpdump4.99.6、TShark4.6.4、iproute2-6.19.0。Windows客户端Node24.14.0、pnpm11.5.1。运行写WSL原生ext4，项目Windows与WSL虚拟环境分开。

最终交付另有f157172334b3962b79982984429d6b32beb7b5e6：仅把Health.stage历史字符串修正为V0.3-development、同步生成契约/文档并优化E2E核验开销。该修正后的完整check和真实浏览器复验均通过。**没有把f157172冒称为一小时运行SHA**；它未修改物理、通信、调度、网络、抓包、时间或QC实现，冻结29aa7e0及其真实证据未改。

## 2. 实现及前置回归

| 工作 | 实际交付与验证 |
|---|---|
| 统一场景时钟 | clock.py及scenario屏障分离宿主映射与场景起点；初始化保持逻辑t=0，业务/气象/电气使用同一场景起点，协议始终真实1×；相同配置不因启动耗时改变背景条件，未承诺OS调度下逐纳秒采样一致 |
| 物理与通信解耦 | RTU独立物理tick，GI/周期通信读取最近状态；真实接收/接受回调后才改变远程目标。通信失联、设备enabled/connected/faulted及电气calculation_valid/limit独立；真实120s断联回归验证20s暂停内至少15个物理样本、气象变化、恢复最新遥测及全QC |
| N站与区域网络 | config/multisite.py、network/regional.py、通用worker/runner/feeder；严格配置支持1–120站、1–12区域/馈线，不宣称已验收120站。三区真实汇聚链路+30条独立接入链路，地址/CA/点表/设备配置化；旧0.1/0.2不改 |
| 正常链路 | 每区域每向3±1ms；接入每向7±2/37±7/97±19ms，合计名义10/40/100ms、抖动3/8/20ms；双向种子冻结，0配置随机丢包。66个内核NetEm qdisc初末delay/jitter/seed读回，均0drop；不改宿主根网络 |
| 电气与空间模型 | 三条独立于通信区域映射的合成20/10kV、2.5MVA馈线，线段0.2km、R0.4Ω/km、X0.3Ω/km、0.3kA，节点50kW+10kvar；总PV5040kW。站点容量/倾角/kVA/P/Q斜率/2、5、10秒遥测及相位/死区有意异质，参数均为开发仿真假设 |
| 空间气象 | 位置东x/北y；平流方向30°、速度10m/s、光学厚度1.5、半宽200m、横风尺度1500m，位置决定传播时差及横风幅度。支持每站来源/哈希独立的背景覆盖；本轮共用同一历史背景叠加空间模型，不声称实测秒级云影 |
| 正常业务 | 容量/新鲜M2可用功率分配、区域任务、批次5站、跨站并行、同站IOA锁、任务隔离；死区及状态变化真实上送。资源不足独立保留，不用Oracle补主站观测、不把正确认当功率到位 |
| 物理QC | 电压、线路/变压器热限、馈线P/Q平衡、反向潮流、P/Q斜率、能力圆、快照新鲜度/跨度、全量pvlib/pandapower重算；无效计算、电网越限、通信异常分开，不生成通信标签 |
| 增量查询 | IPC至多128KiB尾读；observed按新增字节摄入临时SQLite；通信统计累计、联合时间/ID分页≤100；每条测量原始窗口≤600、多站显示≤120点。原始证据不降采样，M2按available_at，不读truth/P1补缺口 |
| Web | 配置双拓扑、区域覆盖率/聚合与分站、空间遥测、实际状态、调控/短缺、协议统计、独立资源诊断、历史回放/分页、桌面/移动；浏览器关闭不停止采集 |

兼容性：Arrow六表版本0.1-protocol-evidence及Oracle0.2-power-evidence.2保留。`pandapower_three_site_v02`生产者标识、QC项`three_station_business_loop`保留历史名称，实际成员数由配置决定，不代表仍固定三站。revision2新增真实遥测IOA1009/1010/1011；旧revision1及0.1/0.2冻结配置保留。当前独立QC为v03-power-v4、内部v01-evidence-v4；旧报告不覆写。

最终冻结回归根`/var/lib/pvcomm/runs/v03_final_regression_s8`：断联1 passed/138.17s；旧协议/隔离/取消清理3 passed/45.05s；V0.2晴朗90秒run、parse --parquet、qc均退出0；科学及Arrow6 passed/133 deselected/9.21s。并行同值、错站/跨任务、拒绝/超时/缺反馈、半开期限、证据独占有单元/契约回归；本轮90条真实命令均为成功，**未声称一小时里实测了真实拒绝或超时故障**。V0.2其余历史场景的既有验收仍由原冻结证据支持，本次只新增晴朗兼容实跑。

## 3. 逐级真实规模门禁

均运行76a3d2c，完成后才进入下一级；全部completed/clean、各N+1路工具丢包0、双端及回调对应、模型/Arrow/QC通过、cleanup_errors=[]。

|站点|配置秒数|完整APDU|完整匹配率|日志P99 ms|日志最大 ms|
|---|---:|---:|---:|---:|---:|
|3|120|2267|100%|23.281709|601.993079|
|10|120|6654|100%|3.845138|32.776870|
|20|120|12736|100%|3.867107|157.505305|
|30|360|40855|100%|3.788537|46.771531|

|站点|CPU均值 %单核|RSS最大 GiB|P0+P1 MiB|Parquet MiB|各站电气间隔P99最大 ms|求解周期P95 ms|UTC映射绝对偏差最大 ms|
|---|---:|---:|---:|---:|---:|---:|---:|
|3|12.60|0.831|0.871|0.323|1008.926|67.508|0.051265|
|10|30.92|2.178|2.542|0.887|1007.194|73.996|0.010179|
|20|57.25|4.097|4.884|1.789|1011.965|69.081|0.040436|
|30|61.74|6.044|15.623|6.405|1005.768|65.768|0.055650|

30站短运行含30秒Web预检006，表中资源是整段短运行，不能归为严格无Web对照。该预检实时显示P95=1623ms不充当最终一小时结果。日志门槛是P99≤100ms，最大值照实保留，未改成逐帧100ms或删掉长尾。

## 4. 一小时协议、事务及物理结果

|检查|实测|门禁/结论|
|---|---|---|
|完整APDU|333021/333021，100%；不完整字节0，未匹配0|≥99.5%，通过|
|工具与链路|31路tcpdump内核丢失0；66 qdisc drops0|原型工具零丢失，通过；不等同于全网永不丢包|
|日志对齐|333021样本，P99=3.747593ms，最大476.711026ms|P99≤100ms，通过；独立逐字节/站点/方向FIFO关联|
|协议对象/包|647282包、295571对象|保留握手/ACK/重传；双端及c104回调核对通过|
|调控|90/90 accepted_complete，证据独占|接受、正确认、设定反馈和功率反馈均按站点/IOA/任务窗核验|
|确认|均值106.642ms、P95=225.808ms、最大331.846ms|全部≤3000ms|
|命令到实际功率反馈|P95=6331.700ms、最大9195.756ms|逐条在配置10–60s动态执行期限内，不用统一最大值替代逐条QC|
|区域资源短缺|area_2请求5040kW、分配2998.886678kW、短缺2041.113322kW|合法正常资源不足；其他两任务分配2268/5040kW，短缺0|
|正常本地操作|PV01第310s停、330s恢复；两次实际IOA2001收到，延迟86.435/79.278ms|不修改远程已接受目标、connected或faulted|
|物理计算|108664样本；740次三馈线周期、22200站点潮流行|全收敛，pvlib/pandapower全量重算最大绝对误差0|
|电压/热限|母线0.999745–1.008135pu；线路最大17.595896%；变压器最大33.960910%|开发边界[0.9,1.1]pu/≤100%，无越限|
|功率平衡|最大绝对P误差1.221214e-5kW、Q误差1.178254e-5kvar|均≤0.001，计算有效|
|反向潮流|2220个馈线诊断均记录反送|作为真实电气工况，未标成通信异常|
|快照时序|跨度最大1004.846ms；solved−injection最大1105.074ms|跨度≤1500ms、龄期≤3000ms，通过|
|P/Q及设备状态|容量/能力圆、P上下行/Q斜率、资源/断开约束独立QC通过|设备故障、电气无效和通信失联不混用|
|时钟映射|3512资源样本，绝对偏差P99=0.027468ms、最大0.108611ms；有符号范围[0.005907,0.108611]ms|是宿主UTC/单调映射诊断，不替代PCAP日志对齐|
|清理|manifest.cleanup_errors=[]|本次进程/namespace/veth/NetEm正常释放，原始证据保留|

电气更新含命令触发推进及停止尾部：各站间隔P99最大1005.623ms、最长1027.296ms。三馈线一次工作器求解周期（含关联快照读取等）均值56.560ms、P95=61.138ms、P99=67.798ms、最大113.787ms；不是纯pandapower内核微基准。初始化/停止样本保留，未删掉边界样本制造周期结果。

原六表Arrow行数：packets647282、iec104_apdu333021、iec104_objects295571、master_events295702、terminal_events54232、electrical_state108694；独立电气/潮流Arrow另存。行数、单位、非空、元数据、源与产物哈希均通过，Oracle不进入M2。

## 5. 三时段Web及资源验收

使用真实WSL API、Windows Vite与Chromium：场景[0,1200)无Web/API/浏览器；[1200,2400)真实页面1秒轮询；[2400,3600)每5次循环实际点击历史游标。live实际就绪1206.964703s，replay开始2400.517703s，completed标记3601.336703s；启动空档和结束截图时间均记录，不当有效展示。HTTP/页面错误0。

|时段|仿真CPU均值 %单核|仿真CPU P95 %单核|RSS均值 GiB|RSS最大 GiB|UTC映射偏差P99 ms|
|---|---:|---:|---:|---:|---:|
|off|47.79|106.61|6.063|6.115|0.034724|
|live|44.00|101.29|6.156|6.197|0.035694|
|replay|43.40|98.01|6.236|6.275|0.022230|

|时段|请求数|请求跨度 s|端到端P95 ms|服务端P95 ms|API CPU均值 %单核|API RSS最大 MiB|Windows树CPU均值 %单核|Windows树WorkingSet最大 GiB|
|---|---:|---:|---:|---:|---:|---:|---:|---:|
|live|1197|1196.419|533.740|469.475|40.61|122.53|32.53|1.248|
|replay|238|1198.673|592.357|521.343|7.48|124.95|10.21|1.279|

实时1197/回放238请求全部200，覆盖跨度均超过相应1200s时段的90%。实时累计负载3,915,651,222字节，回放753,510,273字节；API端到端最大2438.030/628.487ms，服务端最大2368.542/587.039ms，保留冷启动长尾。浏览器JS堆最大实时51,466,512字节、回放48,230,924字节。

**新功率显示延迟9550样本：均值871.138ms、P95=1469ms、P99=1555ms、最大2601ms，P95≤2000ms通过。** 按每站初始快照之后新显示的最近功率相对主站available_at计时，DOM后两次animation frame近似绘制；回放旧样本年龄不混入实时延迟。不是传感器端到端、设备采样年龄或硬件屏幕扫描延迟。

仿真物理磁盘写累计三个时段末324882432/643371008/961339392字节，物理读累计95555584字节；API live读0→151552、写0→1314816字节，replay读151552→151552、写1314816→2609152字节。它们是/proc进程IO计数，缓存命中不会变成物理读；不当作逻辑读取字节。完整统计、进程范围及样本数见performance-report.json。

CPU100%表示一个逻辑核，不是20核整机100%。Linux仿真RSS合计和Windows进程树WorkingSet均可能重复计算共享页；Windows WSL包装进程内存不代表整个VM，API在Linux单列。三时段顺序固定、首段包含调控任务，属于同一运行工程开销诊断，不能据均值差异声称开启Web降低CPU或作随机因果比较。全程工具丢失0及小幅时钟偏差支持本轮采集独立性，不能替代所有硬件和负载的普遍保证。

## 6. 存储、时间及720小时估算

一小时运行目录保留1,383,250,188字节（1.383GB，1.288GiB），其中P0+P1 PCAP129,133,060字节（123.151MiB）、Parquet50,170,860字节（47.847MiB）、解析JSONL/字段等650,076,464字节、observed230,469,301字节、truth318,994,070字节，其余为QC/清单/资源等。父目录console、Web辅助/客户端、源码归档和备份自身另计；备份完整包1,390,074,210字节。未提交Git。

按V1.3实测×720×1.5，约**1,493,910,203,040字节=1.494TB（1.359TiB）**，完整备份另预留同量级，不将备份当1.5余量的一部分。观察到单个解析临时PDML为11,275,084,964字节，成功解析后原有流程清理；临时峰值空间另预留，不把它乘720当最终保留体积。没有连续采样整个解析阶段的磁盘峰值，不能把该单文件值冒称整机峰值。

实测run3637.690s、parse235.614s、QC223.299s、汇总0.632s，退出均0。如果720个同负载一小时单元完全串行运行及后处理，线性约819.45h（34.14天），不含失败重跑、备份和不同长短场景的初始化差异；仅实时业务本身需720h。8实例理想90h只是方案中的理论值，**本轮未测并发吞吐**，不能承诺；单实例仿真RSS合计最大6.275GiB，也不能简单当去重内存推算8实例容量。

交付时Windows实测C盘空闲146,015,014,912字节、E盘555,231,293,440字节；WSL ext4逻辑容量约1007GiB不等于VHD宿主可用空间。现有磁盘不足以容纳720h估算及完整备份，正式批量前需要落实存储和并发验证，未擅自采购或运行批量任务。

当前快照实时每秒约3.3MB，虽然P95达标，仍有序列化/浏览器资源开销；后续可评估按需指标或增量传输。817kB Vite构建提示与2条HTML缩进warning保留，不降低限值来消除告警。光学云影、高频校准、现场参数及容量估算外推均有明确适用边界。

## 7. 失败与修复证据

所有实验失败原始目录保留；不把重新解释、旧报告或预检充当新的实跑。

- 前置001物理连续通过，但停止期间缺一个S回调导致QC失败；002/003真实重验通过，详progress/ADR0006。
- S1十站P0工具丢54包：改原生Linux PCAP spool、哈希核对归档；S2十站通过。S2二十站P99164.241582ms失败；同代码原生持久目录S3 P9914.146801ms通过，未放宽100ms。
- S5带抖动三站TCP重组因果核验失败：独立重组器补入前序缺口填补帧及available_at依赖，v4另目录重验通过；保留v3失败、严格packet_index/协议字段及分母，详ADR0009。
- S6二十站日志P99860.012185ms、PV15缺S回调：核对c1042.2.1源码，发现Init.ALL同步等待GI/clock阻塞raw回调队列及stop先停调度的尾部问题。仅V0.3改真实显式GI/clock和STOPDT→状态回调→disconnect→stop，保留原三站路径；S7逐级全部通过，未任意延长sleep或豁免尾帧，详ADR0010。
- 30站早期UI下拉/忙时回放/首快照就绪失败记录保留，真实复验通过。测量003因辅助目录冲突失败，004/005/006均为短预检，不替代本小时。
- 最终一小时历史UI综合测试001/002触及30s总时限，保存`outputs/v03-final-real-e2e.log`、`-002.log`及`v03-final-ui-failure-001/002`。测试端重复扫描与逐点断言记录改为一次索引和每曲线集中断言全部不匹配点，所有点仍检查，不改30s期限。003为2 passed/1 skipped，26.3s，真实数据操作23.4s，27183点对账、回放、指标、移动及离线恢复通过。

普通完整check实际129 passed/10 deselected，Ruff/格式、mypy81文件、schema/TS、前端质量、Vitest3、build均退出0；后续只修改E2E索引/断言组织，再次web-test退出0。科学/Arrow及真实实验分别报告，不把普通CI中跳过的lab算通过。真实数据模式跳过空态1项是预期，不把skipped算passed。

## 8. 复现与只读查看

选择全新Linux源码worktree和全新输出根；不能覆盖本报告目录。WSL使用已验证`.venv-wsl`，若换机器先按README安装protocol/power/data组及Linux抓包工具。下载或还原有来源/哈希的历史输入，冻结源码需能从自身data路径访问该目录。

```bash
# 冻结源码工作目录；下列命令实际入口已验证。
PYTHONPATH=src /mnt/e/project/pv/.venv-wsl/bin/python -m pvcomm.cli validate-power-config --config configs/v0.3-30-1h.yaml
PYTHONPATH=src /mnt/e/project/pv/.venv-wsl/bin/python -m pvcomm.cli run --isolated --config configs/v0.3-30-1h.yaml --output-root /var/lib/pvcomm/runs/YOUR_UNIQUE_V03_ROOT
PYTHONPATH=src /mnt/e/project/pv/.venv-wsl/bin/python -m pvcomm.cli parse --parquet --run-dir /var/lib/pvcomm/runs/YOUR_UNIQUE_V03_ROOT/v03_30_normal_001
PYTHONPATH=src /mnt/e/project/pv/.venv-wsl/bin/python -m pvcomm.cli qc --run-dir /var/lib/pvcomm/runs/YOUR_UNIQUE_V03_ROOT/v03_30_normal_001
```

Windows仓库根在CLI启动后立即执行真实Web测量（运行中勿改生产源码）：

```powershell
node frontend/scripts/measure-v03.mjs --source-linux /var/lib/pvcomm/sources/YOUR_FROZEN_SOURCE --root-linux /var/lib/pvcomm/runs/YOUR_UNIQUE_V03_ROOT --run-name v03_30_normal_001 --output outputs/YOUR_UNIQUE_WEB_EVIDENCE
```

测量脚本拒绝dirty正式客户端，默认1200秒×3，非standalone。结束后运行`scripts/report_v03_performance.py --run-dir <运行> --web-dir <Web证据> --output <新JSON>`。性能报告是基础设施/Oracle诊断，不作为M2模型输入。复跑同配置期望物理来源/配置一致及门禁满足，不承诺PCAP字节或OS调度纳秒一致。

已有备份只读回放：PowerShell设置`$env:PVCOMM_RUN_ROOT='E:/project/pv/outputs/v03_hour_s8_backup'`，启动README中的本机API及前端，选择运行。M2不因结束后连接关闭而以Oracle填补缺失。完整机器契约见[API](../api.md)和[数据字典](../data_dictionary.md)。

## 9. Git交付及后续边界

远程https://github.com/lxxkimwoaini/pvcomm-sim，所有者lxxkimwoaini、PUBLIC（既有用户授权）、默认main。PR #1 main←fix/v01-evidence-integrity；#2 fix/v01-evidence-integrity←feat/v01-acceptance；#3 feat/v01-acceptance←feat/v02-power-cosimulation，均OPEN、#3 draft；正确顺序#1→#2→#3，未合并、改写历史或代用户批准。V0.3继续在#3上方堆叠。

实际CI：76a3d2c/45618961711、29aa7e0/45619736287、f157172/45636936098均success，五job通过。最终验收文档提交/推送及草稿PR信息在progress追加；不把先前CI算作新文档提交CI。代码、配置、契约和报告入Git，PCAP、Parquet、外部历史输入、运行产物和本地备份不入Git。

后续仍需现场参数/高频物理模型校准和正式数据配置冻结；高频许可调研见[报告](2026-11-25-high-frequency-weather-review.md)：Tateno BSRN候选元数据与条款明确，但第三方转递受限，本次未下载矩阵/集成或分发；Oahu门户与原站许可冲突未解决，不接入。正式720h需要实际存储与并发压测。V0.4异常库、正式标签/分组划分、特征与算法训练均未开展，不自动进入下一阶段。
