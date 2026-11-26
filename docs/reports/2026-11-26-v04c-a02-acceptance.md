# V0.4c A02三站单向时延开发验收报告

日期：2026-11-26（Asia/Shanghai）。结论：**A02三站开发切片通过；完整V0.4c/V0.4尚未验收。**

## 实现与冻结范围

运行及独立QC共同冻结于`0b56d98201f43a2ad8d653344dd87624f1599740`，源码`/var/lib/pvcomm/sources/v04c_a02_s2`，持久证据`/var/lib/pvcomm/runs/v04c_a02_s2`。所有六次运行均completed、code_dirty=false、cleanup_errors=[]。只新增A02，不重做已验收的A01。PR #1～#5仍堆叠且未合并。

复用既有故障配置外层、Oracle事件日志、隔离运行器及QC入口。新增0.4-fault.2/LinkDelay、方向和200/1000/3000ms附加时延；0.4-fault.1 A01继续有效。实际tc qdisc change、完整options/handle/seed/limit读回、部分失败撤销、正常撤销与异步队列排空均有代码及负测试。仅选定链路增加自有透明内部段，将原双向基线移入P0/P1之间，不重复叠加；其他正常/A01默认路径保留。原因和边界见[ADR0018](../decisions/0018-a02-owned-netem-delay.md)。

三站配置含两通信区域（PV01/PV02属R1，PV03属R2）和三条电气馈线。使用已归档NASA POWER 2024-06-23/24小时背景及显式灰色光学层模型云影，未声称秒级实测或现场高频校准。每次160秒、故障起点20秒、持续30秒；作用期27秒发起目标站调控，未选PV03在24秒调控，90秒执行新的恢复GI。业务确认期限仍3秒，功率反馈期限仍按既有逐事务规则。

## 六项真实实验

下表为目标方向完整APDU可交付时延中位数（ms），含TCP有序交付等待。它不是逐IP包传播时延，也不是从配置强度反填的值。

|用例/附加时延|方向|run_id|目标站基线 → 作用 → 恢复（ms）|P0完整APDU|日志P99 ms|
|---|---|---|---|---:|---:|
|single-low/200ms|主站→终端|`run_e8796a15930647f2b7ac88db91404a39`|PV01 7.416 → 207.173 → 6.817|1927|27.229112|
|single-medium/1000ms|终端→主站|`run_55772a2f424346f783afdd30b02bf3b7`|PV01 11.037 → 1011.065 → 11.044|1904|16.808340|
|single-high/3000ms|主站→终端|`run_8dcb2657f86b4b9ca63b7da926453eb9`|PV01 7.572 → 3006.854 → 7.822|1904|12.105954|
|subset-low/200ms|终端→主站|`run_9109a0304f9344f382b37cc6695166b3`|PV01 11.111 → 211.197 → 11.026；PV02 43.203 → 244.891 → 44.154|1745|13.883072|
|region-medium/1000ms|主站→终端|`run_7ae8780d892c4e169d1290bf643cb00c`|PV01 10.758 → 1010.588 → 10.271；PV02 37.695 → 1040.987 → 38.868|1766|12.862863|
|global-high/3000ms|终端→主站|`run_6fd2a95ac14f41b4b1f0d9529663ed43`|PV01 3.218 → 3002.909 → 3.245；PV02 2.894 → 3003.324 → 3.095；PV03 3.216 → 3003.050 → 3.097|1750|13.439043|

每个用例均独立运行、逐条双端唯一关联及独立QC。重复内容歧义、未匹配、边缘在途帧单列，缺失不填零；原始PCAP及时间戳不修改。实际施加区间来自操作读回，所有出口最终恢复原参数，qdisc drops均为0。机制诊断阈值在S1前写入A02计划，未为通过试验调整。

## 质量、业务及恢复

- P0完整APDU **10996/10996**，完整解析率100%；24路P0/P1工具报告丢失均0，各端回调及双端字节对应通过。原≥99.5%、工具drop0及日志P99≤100ms门槛不变，实测日志P99最大27.229112ms。
- 电气持续运行，作用期最大更新间隔1.034928661s；气象来源/输入哈希、pvlib重算、物理边界、潮流及Arrow质量门禁通过。
- 10个恢复GI均有新的主站请求、确认、召唤数据和结束证据；参数撤销、队列排空及GI完成分别核验，不将首条排队遥测视为恢复。
- 单站高档出现1次confirmation_timeout，全局高档出现3次confirmation_timeout，逐事务证据均有效；其余11条调控accepted_complete。五个非全局用例的未选PV03调控均accepted_complete。没有凭transmit(False)推断拒绝类别，也未延长确认期限。
- 低/中档中故障已施加但命令仍正常完成，如实保留。原normal_regression结果继续保留；它不是新场景调度的正式标签。独立实验质量不要求预期的全部业务成功，严格正常门禁未改。
- label_quality=not_evaluated：当前给机制、数据质量、传输事实和已有事务结果，未生成正式service_violation标签。正式因果分层标签、独立标签QC及P03/P04/P06仍属于V0.4d。M2/Oracle边界和available_at契约保留，未修改主站观测投影。

## 实际检查与清理

|检查|实际结果|
|---|---|
|Windows Ruff/format、mypy、schema检查|退出0，113个源文件类型检查通过|
|Windows普通pytest|239 passed，50 deselected，6.62s|
|WSL科学/Arrow/普通pytest|246 passed，43 deselected，17.00s|
|双向有队列NetEm变更探针|2 passed，7 deselected，4.41s；每方向60条UDP全部交付、0 qdisc drops|
|A02真实SIGTERM|1 passed，39.28s；CLI130、cancelled、先撤销再停止端点、恢复截尾、采集drop0、清理正常|
|六项真实IEC104故障用例|六项分别1 passed；逐命令退出0和精确耗时见commands.jsonl|
|旧V0.1独立端点真实兼容|1 passed，22.58s|
|冻结代码远程CI|push 45771257650与PR 45771266265均completed/success，5 jobs通过|

本次网络操作仅使用本次拥有的隔离namespace/veth/bridge；每次真实运行核对前后宿主路由和namespace。最终namespace列表为空，无剩余lab_worker。取消不计入completed实验。旧模块仅做兼容回归，没有重复长时A01验收。

## 证据与保留成本

机器汇总`/var/lib/pvcomm/runs/v04c_a02_s2/a02-acceptance-evidence.json`，SHA256 `de0452161bc542f85e2e71b3fdd96304f8939f314eed3374d1100b66b0b81eb3`。其中保存每份manifest/故障日志/有效配置/QC哈希和完整命令账本，PCAP哈希再次核验一致。原始配置、气象来源/哈希、物理状态、P0/P1、解析、Parquet、系统调用及失败证据均保留，未提交Git。

六份成功运行及QC总计73,242,671B，其中PCAP 4,345,706B、Parquet 1,851,802B、系统调用证据18,803,934B。取消、S1失败/早期探针及源码另计。不据三站短运行外推720小时吞吐或取代V0.3存储预算。

## 失败证据及修复

- 探针001：在配置阶段发现同站业务调度窗重叠，未创建网络；将恢复GI移至90秒且运行160秒，保留60秒既有观察窗。
- 探针002：tc change未显式保留limit/seed，内核读回不符，拒绝通过；修正为显式保留，注明重设seed会重新初始化随机序列，不承诺随机流连续。003（单向）及004（双向）探针随后通过。
- S1/bd02154：取消及正向低档通过；`run_01d1138ea05f4dfe80e81eab3b1f21d7`反向1秒用例失败后停批次。其确认耗时1031.331686ms，而P1→P0基线/作用仅3.193/3.2545ms，源端AF_PACKET时间已位于源出口排队之后。原1905/1905解析和v1质量通过不替代时延证据。原件位于`/var/lib/pvcomm/runs/v04c_a02_s1`，未回写通过。
- S2/0b56d98：将选定原基线移入透明内部段，v2独立检查桥/端点映射及采集边界。首先重验原反向失败，再完成整个矩阵。v1原报告保留，未增加A01 TESTFR豁免或放宽对齐门槛。

## 复现及后续边界

先按既有开发说明准备授权隔离Linux环境、Python 3.11和归档历史输入；使用干净冻结代码和全新持久输出根，避免pytest basetemp覆盖证据。

```sh
uv run python scripts/make_fault_scenario.py --fault-type A02 --scope station --stations PV01 --delay-ms 200 --direction master_to_rtu --output outputs/new-a02.yaml
uv run pvcomm validate-fault-scenario --config outputs/new-a02.yaml
PYTHONPATH=src .venv-wsl/bin/python -m pvcomm.cli run-fault --config outputs/new-a02.yaml --output-root /var/lib/pvcomm/runs/NEW_A02_ROOT
PYTHONPATH=src .venv-wsl/bin/python -m pvcomm.cli parse --run-dir <实际返回目录> --parquet
PYTHONPATH=src .venv-wsl/bin/python -m pvcomm.cli qc --profile fault --run-dir <实际返回目录>
```

完整复验入口为tests/lab/test_a02_runtime.py（PVCOMM_ALLOW_LAB=1），先内核/取消，再单站，再子集/区域/全局，失败即停，实际S2命令均保存于账本。GitHub普通CI不执行特权实验。

**尚未完成**：A04/A05/A06/A08机制、V0.4d配对/正式分层标签及标签QC、后续工作台增量、多站和30站场景复验、40次/类正式覆盖。下一切片直接实施A04，沿用现有流程，不重列参数候选，不重复优化已验收的A01。本次未生成720小时数据或训练检测模型。
