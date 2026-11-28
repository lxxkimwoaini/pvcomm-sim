# V0.4c A01 三站单故障开发验收

2026-11-26，Asia/Shanghai。A01三站单故障开发验收通过：五种作用范围/持续时间、取消、旧版兼容及正常对照均已真实执行并通过。仅覆盖A01机制开发，不代表完整V0.4c、正式分层标签、40次/类或30站验收。

## 实现与证据边界

复用已验收的c104 2.2.1、独立P0/P1、pvlib/pandapower、空间模型云影、场景时钟、事务隔离、Arrow及M2白名单。新增0.4-fault.1组合既有正常背景与单个A01事件，支持站点子集、区域和全局范围。主站仅执行背景业务，不读取故障计划。配置、实际操作、系统调用及诊断报告均为Oracle，不作为M0/M1/M2/M3输入。

自有桥端口forwarding→disabled阻断真实转发，保持采集接口及RTU/物理模型运行；逐端口保存实际前后读回、请求/核验时刻、异常和撤销。站点/子集作用于接入端口，区域/全局作用于汇聚端口。它是隔离仿真实验链路不可用，不是现场硬件断线，也不使用STOPDT伪装非计划中断。操作只能由配置解析本次拥有的资源，Web未新增任意系统命令入口。

机制核验、实验质量、实际通信影响、旧正常回归及正式标签状态分别报告。全部完整APDU仍须≥99.5%解析、工具drop0、日志P99≤100ms、各端回调/抓包对应及物理/Arrow通过。正常默认QC未放宽；故障施加本身不生成service_violation标签。

## 冻结及复核归属

- 运行冻结：`b240eac960699204468a479018da196650464c34`，源码`/var/lib/pvcomm/sources/v04c_a01_s5`。
- 持久证据：`/var/lib/pvcomm/runs/v04c_a01_s5`；每个run独立目录，旧失败不删除、不回写。
- 首次机制QC：同运行冻结的`v04-a01-mechanism-v3`，五份均通过。
- 最终独立核验：`40f329901b5e320327859c586b92e4c714aee3fd`，源码`/var/lib/pvcomm/sources/v04c_a01_eval_s6`，新增`v04-a01-mechanism-v4`，五份均通过。运行与核验均clean。

v4仅修正核验版本来源：从实际加载模块的仓库查询Git，不依赖调用目录，并拒绝dirty核验。此次在运行证据目录调用冻结模块，实际记录40f3299；保留原run SHA及v3报告。另用既有audit_schedule复核背景命令和恢复后总召唤，原pytest的五项计数归属b240eac，不冒称运行了后续新增的lab断言。新增断言与实际独立复核调用同一核验函数。

逐文件核对五个run共1,140个非QC文件，新增诊断前后SHA256一致。汇总`a01-metrics-v4.json`的SHA256为`3c63a47e59b39423929ab00530d38c7b4a5411e39bafbcaa30efff854021a044`；`audit-v4-commands.jsonl`保存逐run核验版本、报告哈希、脚本哈希、耗时、恢复后GI请求及原文件核验数量。所有原始产物留在Git忽略的持久运行目录。

## 五项真实运行

三站、两个通信区域：PV01/PV02属R1，PV03属R2，仍保留三个独立电气馈线。历史背景为已归档NASA POWER的2024-06-23/24 04:00 UTC，seed104/105；N03云影为显式灰色光学模型，未冒称秒级实测或现场校准。物理/设备/网络参数是开发仿真假设。

|用例|run_id|持续候选/实际阻断 s|seed|P0完整APDU|日志P99 ms|撤销前未交付测量APDU|
|---|---|---:|---:|---:|---:|---|
|single-low|`run_918c6b75e13a4af6bcb12d528eccc768`|5 / 5.029999|104|1879|21.352394|PV01:6|
|single-medium|`run_5cd92c6d77a14d5d80cddaaa02d0d375`|20 / 20.071065|105|1680|16.409814|PV01:6|
|subset-low|`run_08abd87da63246f29e45fd52b9349fa8`|5 / 5.040555|104|1883|17.130562|PV01:6，PV02:9|
|region-medium|`run_8ba7b0c83a7442e289d71477e2f4051d`|20 / 20.063150|105|1642|12.797207|PV01:6，PV02:10|
|global-high|`run_562f3069bd28479d8768f3ee152e0327`|60 / 60.013347|104|1106|13.074821|PV01:6，PV02:10，PV03:9|

各目录为`lab/test_real_a01_<用例下划线>_0/<run_id>`。五项pytest实际5 passed、1 deselected，887.92s，退出0；外层含启动收尾890.226472s。配置逻辑运行长度为四项150s及高强度180s，合计780s，不将初始化/后处理算作业务逻辑运行时间。

P0共8,190/8,190已完整捕获APDU严格解析匹配，解析100%，不完整字节0；20路P0/P1抓包工具drop均0。日志对齐P99最大21.352394ms，单次最大误差28.064060ms。全部manifest completed、clean、cleanup_errors=[]；物理/潮流/输入及Arrow门禁通过，2,436条pvlib设备物理样本，作用期间各站物理最大更新间隔1.010740123s，未用停止物理模型制造通信中断。

withheld计数为真实完整测量APDU，不是独立PCAP帧数；同一TCP帧可能承载多条APDU。依据完整方向/IP/端口/字节内容逐条独占关联，68条在实际已核验阻断区间从P1发送且未在撤销前交付。受影响站内区间P0及主站有效有功测量均0；TCP背压后无新发送仍保留已发送证据，不把单纯静默当作阻断证明。

## 发送尝试、恢复及业务证据

五次运行分别有0/1/0/2/3条TESTFR写入尝试未在P0接口出现，共6条。c104固定版本的raw发送回调发生在非阻塞写入前，不能等同于接口发送或交付。每条都有实际strace的精确字节、sendto返回6、100ms内回调、相同fd的ENOTCONN及成功close，并位于目标站已核验阻断区间。其分类严格为`kernel_accepted_TESTFR_attempt_not_observed_on_interface`，不是发送成功、业务成功或异常标签。

v3/v4保留strict_normal_comparison；正常QC仍不接受多余回调。其他帧、接收回调、部分写入、错误方向/站点/时段、歧义或缺证据不能使用该规则。三次长中断的原严格回调比较为false，实际独立内核证据在显式A01质量profile核算；这不是提高解析率或降低采集/对齐门槛。详[ADR0017](../decisions/0017-v04-prewrite-testfr-evidence.md)。

四个非全局用例在中断期间向未选PV03实际下发35kW，均accepted_complete；确认耗时188.515432～237.940038ms，设定值及功率反馈具有独占事务证据。全局用例没有未受影响站，不捏造旁路成功结果。

撤销后首次有效有功观测分别为：single-low PV01 3.259628s；single-medium PV01 0.989927s；subset PV01/PV02 3.242420/1.481266s；region PV01/PV02 0.560222/1.940883s；global PV01/PV02/PV03 2.841791/6.884146/10.614125s。它们可能包含TCP排队旧值，不能单独作为最新状态或正式恢复标签。

另存的`qc/a01-background-schedule.json`逐项核对恢复后新请求、同流同CA的ACT_CON、COT20测量（含1001/1006）及ACT_TERM，五份均通过；九个目标站GI请求均晚于实际撤销19.902327～19.941600s，按各自请求至结果的半开窗口核验，未复用启动召唤或未来响应。背景任务最大开始迟到1ms。这证明撤销后真实总召唤业务完成，不对协议未携带的设备采样时刻作额外精度承诺。

五次M2实际API测试在故障结束前查询因果快照，受影响站仍显示故障前最后收到值，未从Oracle补齐；响应不包含故障配置、A01、注入ID或socket_audit。当前只验证既有工作台API兼容和隔离，没有实现故障时间轴/配对/标签页面。

## 取消、兼容及普通检查

同冻结取消预检实际1 passed/41.95s，run_id为`run_8c62379c052640a899ae551b47f6dd10`，目录`cancel/test_a01_sigterm_restores_befo0/<run_id>`：故障生效后SIGTERM，CLI退出130，manifest cancelled、cleanup_errors=[]，端口disabled→forwarding读回完成后才记录master_stopped。四路抓包退出0/drop0，namespace及宿主路由恢复到原状态。取消部分证据保留，不进入completed质量计数。

运行冻结WSL科学/Arrow实际226 passed/34 deselected、13.54s。核验修复Windows完整`uv run --frozen python scripts/dev.py check`实际退出0：220 passed/41 deselected、4.69s，Ruff/format、mypy109文件、schema/生成TS、ESLint/Prettier/vue-tsc、Vitest3/build均通过。保留既有两条HTML缩进warning和817.73kB构建提示。未执行的lab/data/power不混入Windows普通通过数量。

旧版真实回归已实际5 passed/186.72s、退出0，包括V0.3物理持续/恢复新状态、V0.1三站协议、独立进程端点/路由与namespace清理、SIGTERM部分证据保留，以及缓冲抓包尾包不丢失。证据在`legacy/`，`commands.jsonl`保留外层188.964523s及精确参数。旧V0.3恢复用例另存prerequisite-qc.json且全部通过；其他测试按其真实协议/抓包/清理断言计数，不冒称每项均执行完整QC或重跑原30分钟/一小时验收。

正常N01/N09对照实际2 passed/19 deselected、272.54s，退出0，外层274.643493s。两份v04-normal-schedule-v2均通过严格正常P0/P1对应和适用物理/Arrow检查，四路抓包各0drop：

|对照|run_id|P0完整APDU|日志P99 ms|业务结果|
|---|---|---:|---:|---|
|N01-0|`run_a5845edc4ba54f3fac7d128bdbbfbd21`|1118|3.777648|无调控，未触发旧固定命令|
|N09-0|`run_7ed18692251546bb8d0e0e3d5739d0d6`|1165|3.903548|合法拒绝：真实否定确认、终端拒绝及设备条件证据齐备|

N09逐事务旧成功检查passed=false、outcome=rejected，原值保留；正常场景核验依据合法拒绝条件通过，没有把transmit(false)或正常拒绝当故障标签。目录位于`normal/test_real_normal_scenario_<场景>_0/<run_id>`。批次结束后再次检查namespace为空，/proc中无剩余lab_worker及携带该端点命令的strace。`final-evidence.json`记录六步命令全部退出0、各run的manifest/QC哈希、正常/兼容结果和清理状态；不覆盖运行原件。

## 存储及失败保留

五个成功run及v3/v4/背景诊断共保留54,476,283B，其中PCAP3,243,300B、Parquet1,506,454B、其他49,726,529B；系统调用原件15,248,618B已包含在其他项。取消、旧失败、兼容、父目录日志、源码快照及任何备份另计。三站短实验及strace开销不能替代V0.3的一小时性能测量，也不据此重估30站720小时容量。V0.3的1.383GB/h、720小时加余量约1.494TB及并发/解析峰值未验证限制继续有效。

保留以下失败，后续成功不覆盖其状态：

|批次/冻结|真实结果及原因|证据根|
|---|---|---|
|S1 / 5c21fe4|single-low 1 failed/177.13s；背压后不再发送，原v1内区间断言漏判；质量/操作已通过但未计验收|`/var/lib/pvcomm/runs/v04c_a01_s1`|
|S2 / 4bf0c7a|1 passed、1 failed/354.70s；20s多一条未在P0出现的TESTFR回调；原v2失败保留|`/var/lib/pvcomm/runs/v04c_a01_s2`|
|S3 / 4bf0c7a|诊断仍失败/194.041s；系统调用证实写入后ENOTCONN/close；仅用于定位，不补造manifest|`/var/lib/pvcomm/runs/v04c_a01_sendprobe_s3`|
|S4 / 881dfa8|取消1 failed/41.16s；桥恢复且跟踪完整，但worker退出1被误判跟踪不完整；后续未执行|`/var/lib/pvcomm/runs/v04c_a01_s4`|

S5修正后取消不把worker退出1伪装0，完整退出与trace_exit_code分列；completed质量仍要求每条退出记录为0。S1背压修正和负测试见[ADR0016](../decisions/0016-v04-owned-link-interruption.md)。不修改/删除原PCAP、源计划、旧QC或失败清单。

## 复现及后续门禁

在clean冻结源码、已授权隔离Linux、已归档NASA缓存和已验证protocol/power/data环境下，选择全新持久根顺序运行：

```sh
PYTHONPATH=src PVCOMM_ALLOW_LAB=1 /mnt/e/project/pv/.venv-wsl/bin/python -m pytest \
  tests/lab/test_a01_runtime.py -vv -x --durations=10 \
  --basetemp /var/lib/pvcomm/runs/NEW_A01_BATCH/lab
```

A01另需strace，仅跟踪本次自有端点的固定socket调用。single-low用例实际覆盖`validate-fault-scenario → run-fault → parse --parquet → qc --profile fault`全CLI，逐条保存命令、退出码、stdout/stderr及耗时。其余四项用同应用服务接口执行，不复制业务逻辑。已有run/解析/QC目录拒绝覆盖；恢复或核验结果不能由计划时刻推定。

当前仅A01开发切片；A02设计见[单向时延计划](../plans/2026-11-26-v04c-a02-delay.md)，尚未实施。A04/A05/A06/A08、P03/P04/P06、正式标签及标签独立核验、场景工作台、多站/30站复验和正式每类40次覆盖均未完成。label_quality仍not_evaluated，旧normal_regression=false如实保留；本轮没有重新执行原V0.1 30分钟、V0.2 90秒或V0.3 30站一小时门禁，没有生成720小时、划分正式数据集或训练模型。

修复40f3299已实际push，push CI45764024273及PR CI45764029573均completed/success。远程lxxkimwoaini/pvcomm-sim仍PUBLIC（用户既有授权）、默认main；PR #1→#2→#3→#4→#5仍OPEN，#3–#5 draft，正确堆叠且未合并。最终文档提交的SHA、推送及CI在交付时另行核验，不用源码提交CI冒称后续提交结果。
