# V0.1 最小协议原型验收报告

日期：2026-11-25（Asia/Shanghai）。结论：**V0.1 首周开发门禁通过**，可以开展 V0.2 电力闭环准备；不是正式科研数据集发布或现场验证。

依据 AGENTS.md 与 V1.3 第5/6/10/11/15/17/21节。V0.0此前已通过，本报告补齐独立端点30分钟、维护/中断对照、独立QC和只读Web。

## 冻结环境与证据

- 协议运行SHA：`0847def76bab3082f20e7535cb1b9f01c5589f42`；核验/Web代码SHA：`60cdbae2c9ef533166952547f16ca950a47f2d76`。两者均已推送私有 origin。
- Windows11 + WSL2 Ubuntu26.04，Linux6.6.87.2，Python3.11.15，c1042.2.1，tcpdump4.99.6，TShark4.6.4，pyarrow23.0.1；uv.lock/pnpm-lock版本化。不是Docker性能或生产网络测试。
- 本地证据根：`outputs/v01_frozen_acceptance_001/`。机器验收汇总 `acceptance-v01.json`、冻结代码 `source.tar/source.sha/source-provenance.json`、配对审计 `pair-audit.json`、截图 `web-evidence/`。原始数据和这些运行产物均未提交Git。
- 快照91个文件逐一与Git blob一致，三份运行配置源文与快照一致。归档SHA256：`0daf969a550c75c511ee86a310fce2ec9a3b85b7515e20de1dc7bcfb761425d7`。
- 正常运行 code_dirty=false；两个对照原始 manifest code_dirty=true，保留原记录，不改称clean。旁证只证明保留的冻结内容一致。
- 一主站+三个RTU独立进程/namespace，三条内部veth，每站独立地址、TCP2404，无默认路由。P0在主站，三份P1在RTU；四路capture实际结束码0、内核丢包0，清理错误为空。

## 实际运行指标

| run_id | 业务区间实测s | P0包 | 完整APDU | 对象 | 完整解析率 | 映射P99 ms | QC |
|---|---:|---:|---:|---:|---:|---:|---|
| v01_acceptance_30m_001 | 1799.997722 | 11831 | 6123 | 10848 | 100% | 4.325865 | 通过 |
| v01_case_001 | 89.998307 | 620 | 351 | 578 | 100% | 6.045663 | 通过 |
| v01_case_002 | 89.998603 | 611 | 349 | 574 | 100% | 3.987475 | 通过 |

正常配置为1800s、实时1×，业务区间单调计时约1799.998s（记录/调度边界约2.3ms），未缩放协议时间；主进程UTC起止为2026-11-25 07:10:04.774835至07:40:09.136326（北京时间15:10至15:40），包含资源启动和关闭。P0 SHA256为 `49b344c21733659e111bbb62709bf71861f4fa8f90c9c59fc66150afc43deeb2`。

正常6123个完整APDU均与独立重组结果一致，无截断/缺字节/多余解析；三站各2041个APDU与P1逐方向字节相同，终端raw回调一致。主站业务回调匹配6120个，结束后另保留3条S帧。维护尾部另4帧（2I/2S）、中断尾部另2帧；均保留在完整解析和双端核验中。覆盖边界见ADR0003，不宣称主站关闭过程全回调覆盖。

正常日志映射P99=4.325865ms≤100ms，**最大208.659407ms**，未删去极端点，也未把P99结论写成所有误差均≤100ms。抓包工具的零丢包不等于生产网络零丢包或所有未来实验可靠。

## 逐门禁证据

| 门禁 | 核验及结果 |
|---|---|
| 真实协议 | c104/lib60870产生真实TCP/IEC104；没有手工报文代替实验 |
| 三站业务 | 1001/1002/1003/1006/2001遥测；总召唤COT6/7/10；每站限发及解除各一次，合计6命令 |
| 命令闭环 | P0命令对象→终端接受事件→主站确认→设定值与实际有功反馈；正常确认实测0.904340～5.254200ms，均≤3000ms |
| 数据与物理边界 | 2700个状态记录满足容量和可用功率边界；固定可用功率斜率测试，无气象/潮流验收主张 |
| 抓包/QC | 完整解析率≥99.5%、工具丢包0、P99≤100ms均满足；4份原始哈希、配置哈希、事件唯一性、因果性、完整时长和清理通过 |
| 版本化导出 | 六张Parquet表，int64纳秒/单位/观测scope、必需字段、源和产物哈希、行数/schema含metadata读回核验；保留三级JSONL和PCAP，拒绝覆盖 |
| 维护/中断对照 | 同站点/种子104/时间计划/网络，仅run_id和计划标志不同；PV02在40s停止20s并恢复总召唤和遥测；两者通信受影响，计划维护未记业务违约，非计划关闭记违约；仅首周开发审计 |
| 只读M2 Web | 实际主站数据、三站拓扑、曲线、事件与P0报文；后端按available_at截断、纳秒字符串/BigInt；Oracle422，truth接口404；实际正常运行API再次比对通过 |
| 浏览器 | 真实后端/保留c104证据，选择、回放、最新数据、390px、离线恢复均通过；正常运行列表和最新72/90/126kW及回放因果游标再次实际核对 |
| 资源清理 | lab测试前后路由/namespace比对，取消保留证据；长运行结束后ip netns list为空、无lab_worker残留，Web测试后8000/5173无监听 |

## 实际验证命令和版本管理

以下实际退出0：`uv run --frozen python scripts/dev.py check`（普通Python64 passed/6 deselected、Ruff/mypy46文件、schema与TS契约、前端检查、Vitest3项、build）；`scripts/dev.py data-test`（3 passed/67 deselected）；授权WSL `PVCOMM_ALLOW_LAB=1 .venv-wsl/bin/python -m pytest -m lab --basetemp outputs/v01_lab_acceptance_002`（3 passed/67 deselected，51.51s）。

真实根Playwright：2 passed/1 skipped，跳过的为空态用例；普通CI：2 passed/1 skipped，跳过的是真实证据用例。两种模式互补，普通CI不生成PCAP或执行特权lab。截图已留本地。

长运行实际命令为冻结快照的 `run --isolated --config configs/v0.1-acceptance-30m.yaml`；完成后用核验版本执行 `parse --parquet --run-dir <正常运行目录>` 和 `qc --run-dir <正常运行目录>` 均退出0。重跑必须使用新的输出根；同版本派生目录拒绝覆盖。完整复现命令见README与development。

私有仓库 `lxxkimwoaini/pvcomm-sim`，默认main，origin地址已核对；功能分支 `feat/v01-acceptance`。PR #2：https://github.com/lxxkimwoaini/pvcomm-sim/pull/2 ，base=fix/v01-evidence-integrity，依赖PR #1，均未自动合并。

代码SHA60cdbae的push CI `45569491739` 和PR CI `45569549685`实际success，各含Ubuntu/Windows Python、Arrow data contracts、Frontend and browser四个success job。本报告属于其后文档提交，不能把此前CI说成测试了尚未产生的文档SHA。

## 保留的局限与下一阶段

固定可用功率模型只用于协议联调；未生成论文正式样本，未接入真实天气或pvlib/pandapower联合模型。80/100/140kW与R01/R02是已有开发测试配置，未替代方案正式物理参数或实现区域链路，详见ADR0003。V0.2需要核实物理参数、气象来源许可与版本、斜率/逆变器约束和潮流收敛，再通过协议闭环验收。

本次未实现正式label/split/features/benchmark、40次/类异常覆盖、30站压力或正式划分；这些命令明确退出3。维护/中断标签、共享组仅是首周证据，不作为V0.4/V0.5验收。Vite产物JS约808kB，存在>500kB构建提示，后续按页面拆分并在V0.3测量资源。没有付费服务、现场控制、外部校准或生产部署结论。
