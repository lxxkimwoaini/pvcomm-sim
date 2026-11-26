# V0.4c A04日常遥测上送缺失开发验收

日期：2026-11-26（Asia/Shanghai）。结论：**三站A04机制及首版分层标签核验通过；不是完整V0.4或正式40次覆盖验收。**

运行与机制QC冻结`4d4fbf965fb8faa1576bb97c338b96d178711d0d`，源码`/var/lib/pvcomm/sources/v04c_terminal_s2`，证据`/var/lib/pvcomm/runs/v04c_terminal_s2`。标签生成与独立核验另冻结`acde7d24bdb598d90e881efa9e9fc0e1275040b1`。旧原始证据和机制报告不改写，两种代码归属分别记录。

## 实际行为

复用统一TerminalFault、Oracle日志、原隔离运行器、真实c104注册点读回及恢复。A04抑制完整日常测量集合1001/1002/1003/1004/1005/1008/1009/1010/1011/2001，保留1006已接受设定值与3001命令。它与A06接受调控后专门抑制1001/1006的语义不同。周期、事件以及作用内额外总召唤均不能绕过抑制。主站不读取故障状态，设备物理模型继续运行。

三站周期分别2/5/10秒；低档PV01缺2周期，中档R1两站缺5周期，高档全两区三站缺12周期。各站保持自己的周期数，外层持续时间是最大目标站保持时间。NASA POWER 2024-06-23/24小时背景与模型云影来源不变，不称秒级实测。

|用例|run_id|P0完整APDU|日志P99 ms|实际外层耗时s|
|---|---|---:|---:|---:|
|单站2周期|`run_09086884c91f48959b61762a3bfc75cd`|1997/1997|13.578058|194.953462268|
|区域5周期|`run_f0cb265b1a23438bbf9fe35da29e74dc`|1876/1876|11.692306|195.418728112|
|全局12周期|`run_eedf6320569e4faf9fe13a170ddc04e5`|1826/1826|13.082629|255.352583140|

三次均completed、clean、cleanup_errors=[]。P0共5699/5699完整APDU、解析100%，12路P0/P1工具drop0，无未解释双端字节损失；注册集合、实际抑制、恢复及完整新GI均通过独立`v04-terminal-mechanism-v2`。作用期电气更新最大间隔1.015286366秒，适用pvlib、潮流、物理边界及Arrow检查通过。低/中档未选PV03业务accepted_complete。门槛未放宽。

## 标签及失败保留

`v04-label-v1`按主站实际有效接收间隔和期限计算，不以missing_cycles直接填业务标签。三次独立`v04-label-qc-v1`均通过，9个站点标签valid、0 uncertain/invalid；违约站点分别PV01、PV01/PV02、PV01/PV02/PV03。标签源哈希包含原PCAP、主站/终端/物理记录及原机制QC；Arrow逐值读回、证据外键、因果可判定点和维护规则核验通过。M2后端不增加Oracle字段；M3仍无独立来源。

S1原型仅抑制最小点集，遗漏1004/1009/1010/1011等日常点，旧v1 QC未发现该完整性缺陷。S1原PCAP及报告保留，明确不计A04完整语义验收。S2改为从真实注册点表推导全集，并增加完整集合、泄漏、无恢复、取消、错事件等负测试及作用内真实GI，未删除旧失败或修改原件。

## 复现与证据

入口为`tests/lab/test_terminal_fault_runtime.py::test_real_terminal_fault[A04-low|medium|high]`，要求授权Linux隔离环境、归档天气、冻结clean源码及全新持久basetemp。命令和耗时在S2的commands.jsonl。CLI可用`scripts/make_fault_scenario.py --fault-type A04 --scope station --stations PV01 --strength 2 --output <新文件>`，随后run-fault、parse --parquet、qc --profile fault、label及qc --profile labels。重复输出拒绝覆盖。

机器证据`/var/lib/pvcomm/runs/v04c_terminal_s2/a04-acceptance-evidence.json`，SHA256 `73abc8ce17dca87e5465384d35aa7a12e9bfe8c08ce6d1deb59a2b53703979a0`。生成时三份成功运行含标签/QC共36,984,943B，其中PCAP2,238,740B；源码、S1及其他失败另计，不外推正式720小时成本。原始产物不提交Git。

尚未完成：多站/30站复验、每类40次正式覆盖、统计相似配对筛选及完整V0.4工作台。A01/A02保持既有验收，后续只做必要兼容。
