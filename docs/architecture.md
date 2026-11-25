# 工程架构与实现边界

当前版本0.2.0.dev0，规范依赖方向见V1.3第21.1节。保留V0.0配置入口和V0.1真实协议链路；V0.2采用独立power模块。阶段状态以progress及验收报告为准。

| 路径 | 实际职责及边界 |
|---|---|
| domain、clock、logging | 领域枚举、UTC/单调/逻辑时间映射、结构化日志；不依赖协议或模型框架 |
| config | 0.0/0.1/0.2严格配置、有限YAML、引用/单位边界、规范化哈希；不创建实验资源 |
| scenario、network、capture | 唯一运行编排、受限namespace/veth生命周期、独立P0/P1、tcpdump readiness/统计；仅清理本次资源 |
| protocol | 实际c104主站/RTU、点表、总召唤、命令/确认；主站不读电气IPC或真值 |
| weather、power | 版本化历史输入、pvlib、RTU拥有的逆变器状态、三站pandapower；指令只经真实协议回调接受后改变目标 |
| storage | 配置/manifest/事件、原六表Arrow及独立Oracle电气/潮流Arrow；拒绝覆盖、保留哈希 |
| pipeline、qc | TShark解析、独立TCP/APDU复核、一对一业务证据、物理重算；后验QC可读Oracle，不作为主站输入 |
| cli | 配置校验、运行、解析和QC；label/split/features/benchmark明确未实现 |
| web、frontend | 本机配置服务、M2只读API、真实遥测曲线和因果回放；不启动特权实验，不读取truth/P1/QC/注入 |
| scripts | 薄开发/契约命令及带来源/政策快照的历史输入下载 |

RTU独立进程持有自身物理状态，电气步长1s。scenario控制器读取单写者追加完整JSONL快照，每5s及接受命令时补算潮流；反馈带求解时标并检验新鲜度。真实协议始终1×。原始双端事件另行持久保存，不将IPC当作可观测证据。

observed只含主站收到/发送的合法信息；truth保存RTU事件、气象、物理状态与IPC。M2接口以白名单及available_at截断，Oracle请求拒绝。增加的辐照/可用功率测点是同一IEC链路的遥测，不能冒充独立M3或可信真值。新增契约不改原六表，旧冻结证据由对应SHA源码核验。

没有正式标签/划分/特征管线或检测算法；不创建experiments空壳。30站、复杂异常库、正式参数冻结和数据发布属于后续阶段。
