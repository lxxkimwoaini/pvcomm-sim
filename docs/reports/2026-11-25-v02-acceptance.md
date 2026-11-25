# V0.2 三站电力—通信开发验收

日期：2026-11-25（Asia/Shanghai）。结论：**V0.2三站开发验收通过**，版本0.2.0.dev0。不是正式科研数据集发布或现场模型校准；未推进V0.3。依据V1.3第4/5/6/10/11/15/17/21节及本阶段计划。

## 审查及修复

PR #1（main←fix/v01-evidence-integrity）与#2（fix/v01-evidence-integrity←feat/v01-acceptance）按堆叠顺序审核，代码/测试/冻结V0.1证据见[审查报告](2026-11-25-pr01-pr02-review.md)。均保持OPEN，未合并、变基、改写历史或代用户批准。当前功能分支feat/v02-power-cosimulation在#2之上开发。

c5c9464修复重复action的本地task_id及同值证据复用。命令、接受、确认、设定反馈和功率反馈按同站同点的串行左闭右开区间及会话/方向/CA/IOA/type/value核验，证据只消费一次，歧义失败。反馈不得早于终端接受或位于下一任务/期限端点。18项命令回归含同值重复、证据缺失、错站/任务/点/值、跨TCP会话、精确截止点、物理资源不足。旧V0.1正常1800s及两对照按新版QC重新核验3/3通过，不声称重新跑过30min。

README、API能力、CLI、包版本及架构/数据字典已同步。固定available_kw仍用于V0.1回归，V0.2物理状态由独立power模块产生。label/split/features/benchmark仍明确未实现，Web不提交特权任务。

## 模型与来源

历史NASA POWER Hourly v2.10.5，MERRA2/SYN1DEG/POWER，35.4N/113.8E背景网格，2024-06整月UTC；历史卫星/再分析而非现场实测。规范化输入SHA256 `72a8d6275ef89029b6b34e73d0cc68708ee749eef69dcf59abe65d0fe13346bd`。保留原始响应、下载日期、原始及规范化哈希、来源/引用/政策快照，使用依据为NASA full/open Earth science data政策而非自行指定CC许可。输入与快照只在Git忽略的data及运行truth/weather中。详见[ADR0005](../decisions/0005-v02-physical-model.md)。

pvlib0.16.1：太阳位置、isotropic POA、SAPM温度、PVWatts DC/AC及显式损耗；设备30/100/300kW、kVA能力圆、P上下行/Q斜率、运行/开关/故障状态；pandapower3.5.6：20/0.4kV三站径向配电网、5s及命令触发求解。设备参数、馈线/负荷均为配置化工程仿真假设，未冻结论文正式参数。无随机电气量或手工报文。

主站只通过真实c104发送指令，RTU回调接受后修改自身目标；RTU物理模型1s推进，scenario控制器只读RTU快照算潮流。协议1×，不加速历史时间。新增电流/辐照/可用功率测点经真实IEC传输后进入M2，不从truth补齐。P0/P1及六张0.1 Arrow表保留；新增Oracle契约0.2-power-evidence.2，独立目录parsed/parquet-v02-power-v2。

晴朗/多云是历史辐照特征候选，不是现场云量标签；小时边界保留真实小时均值阶跃。快速云影是历史背景上的显式灰色消光层平流（Beer–Lambert，tau=1.5、半宽200m、10m/s），不是实测秒级云影，未实现散射/云边增强或校准微物理。Oahu虽已可下载，但原站再分发限制与聚合页许可冲突，未接入或再分发。此简化层验证快速物理响应，不代替正式高频观测校准。

## 真实运行

四类各90s配置，全部completed、code_dirty=false、cleanup_errors=[]；每运行9条任务，三站各限发、重复同值限发、解除。运行前依赖/端点初始化的墙钟时间另计，实际业务区间约89.997–89.999s，独立QC按配置容差核验。

| 工况 | P0 包/APDU/对象 | 对齐P99 ms | 电气行/三站潮流次数 | 独立QC |
|---|---|---|---|---|
| 历史晴朗 | 725 / 363 / 987 | 50.007106 | 431 / 20 | v02-power-v1: pass |
| 历史多云候选 | 754 / 378 / 987 | 50.608219 | 431 / 20 | v02-power-v1: pass |
| 历史小时边界 | 718 / 363 / 987 | 45.546582 | 431 / 19 | v02-power-v1: pass |
| 历史背景+模型云影 | 726 / 363 / 987 | 61.827776 | 431 / 20 | v02-power-v5: pass |

每次完整APDU解析率100%，四路tcpdump内核丢包0；双端APDU与回调逐字节对应，确认/反馈在期限内。物理边界、P斜率及资源约束例外、全部潮流收敛、模型全量重算、Arrow读回/源及产物哈希通过。云影最终pvlib和pandapower重算最大绝对误差均0；保留1e-6物理/天气数值容差和1µs源时间映射容差。最小透过率0.223137，GHI由1002.67降至约223.73 W/m²；三站可用功率区间约5.765–24.154、19.216–80.514、57.647–241.541kW，均由历史背景+已声明光学变换及pvlib计算。

- `outputs/v02_frozen_001/v02_clear_001`：P0 SHA256 `f19ceafab9e41e4319aa7aa4902b50a8b2e1ce53fbb1f61775e6cd5606da052c`；运行代码 `a4b7039ef2b9b657b67d7e408e6b8713ce045474`。
- `outputs/v02_frozen_001/v02_cloudy_001`：P0 SHA256 `e3b2de453a99e92e4235f1ec439f097836f254389e7362e4d8ac95b699888e0c`；运行代码 `a4b7039ef2b9b657b67d7e408e6b8713ce045474`。
- `outputs/v02_frozen_001/v02_irradiance_transition_001`：P0 SHA256 `0c1effd1ec875ed6c36dea40745bbca5126ea70dcf5565fdd023225b992c525a`；运行代码 `a4b7039ef2b9b657b67d7e408e6b8713ce045474`。
- `outputs/v02_cloud_frozen_003/v02_cloud_shadow_001`：P0 SHA256 `3f25c957b683a775dee6ff374518b5f7090984bda9aeeb97d1b9bc637e485c77`；运行代码 `76e73ad92a8bec3f3c09df477ac7dbf00ca23f2d`。

历史三场景冻结源码outputs/v02_frozen_001/source.tar的SHA256为38e8ae14db516cea3d8d2b5e1af7731f73bea7754d6b9e2010a80306e180b2d7；云影003运行源码archive为6c4408f205ce8719a8ffbb9f168b591008e0017f86a08e66ef9866a11d3c86aa。最终v5核验代码da7b5262c24e0ae5c991692b5cc0f8f23fd69f21，qc-source.tar SHA256 ea478fbf7c8b70f22efa91bbd57890644fdee91989dea8d139c6c2128d15d602；独立复核结果与核验SHA另存outputs/v02_cloud_frozen_003/qc-evaluator.json。运行SHA与后验QC SHA分别记录，不混称同一冻结版本。旧v1字段不包含新增透过率，不用新schema覆写旧运行。

## 测试及失败留痕

- `uv run --frozen python scripts/dev.py check`退出0：94 passed/9 deselected，Ruff、strict mypy60文件、schema/OpenAPI/TS一致性、ESLint/Prettier/vue-tsc、Vitest3及生产构建通过。
- data-test退出0：4 passed/99 deselected；power-test退出0：2 passed/101 deselected，含独立PVWatts公式和潮流符号/电压响应。合成边界仅单元fixture。
- WSL真实V0.1 lab回归3 passed/97 deselected，51.03s，保留outputs/v02_protocol_regression_001，含取消/清理/宿主路由断言。这是本阶段早期runtime集成后的实际执行，不当作最终SHA再次运行。
- 晴朗和云影真实数据模式Playwright分别2 passed/1 skipped；跳过项为空运行态。接口所有测量曲线与原始主站接收逐值核对、纳秒因果游标、站点/指标选择、桌面/390px及离线恢复通过。截图分别保留outputs/v02_frozen_001/web-evidence和outputs/v02_cloud_frozen_003/web-evidence。截图视觉检查无横向页面溢出。
- outputs/v02_qc_negative_001为明确负测试副本：故意将PV01一条功率改为31kW，独立QC拒绝electrical_bounds及power_arrow源完整性；原始运行未改动。该副本绝不能作为仿真结果。
- 初轮IPC在drvfs替换路径出现ENOENT，运行failed并保留；追加JSONL及完整行读取回归后修复。Arrow曾用错pa.bool，修正bool_并实际读回。浏览器环境配置/隐藏输入点击失败按trace修正，未force点击或加长sleep。
- 云影001/v2与002/v3的物理重算曾失败，约3.2e-5偏差源于ISO时间微秒舍入；临时v4放宽至1e-4不用于最终验收。最终v5使用保留的单调纳秒映射重算，恢复原1e-6并通过，失败和临时报告均保留。
- CI b463e68/45577492912因遗漏配置失败，a4b7039/45577551377补齐后成功；76e73ad/45581377368因Ruff长行失败，da7b526/45582627395五job全部success。7410fe7/45579829682与44adf12/45580435928也实际success。CI无特权网络，不冒充lab验收。
- Vite约810kB JS仍有>500kB构建提示；未以关闭提示或放宽检查隐藏。没有30站资源测试。

## 复现及交付边界

按README安装frozen依赖及下载来源，授权WSL使用项目解释器，并选择全新输出根：

```sh
.venv-wsl/bin/python -m pvcomm.cli validate-power-config --config configs/v0.2-cloud_shadow.yaml
.venv-wsl/bin/python -m pvcomm.cli run --isolated --config configs/v0.2-cloud_shadow.yaml --output-root outputs/new_unique_v02
.venv-wsl/bin/python -m pvcomm.cli parse --parquet --run-dir outputs/new_unique_v02/v02_cloud_shadow_001
.venv-wsl/bin/python -m pvcomm.cli qc --run-dir outputs/new_unique_v02/v02_cloud_shadow_001
```

其他工况替换clear/cloudy/irradiance_transition配置及对应run_id。当前版本QC为v02-power-v5；历史三场景用对应archive源码复核。普通check、data-test、power-test与真实lab相互区分；运行manifest的qc_status保留pending，独立QC报告是验收依据，避免改写原始清单。

本轮完成V0.2三站开发门禁；没有正式气象高频观测/设备参数现场校准、M3独立可信来源、正式标签/划分/特征/基线或数据发布。Web仍是实际可操作的只读监测/回放，特权运行由CLI控制。未生成30站数据或训练模型。后续由负责人决定正式参数/高频来源校准及下一阶段范围，PR依赖仍须依#1→#2→本分支顺序审核，未经授权不合并。
