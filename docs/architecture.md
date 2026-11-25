# 工程架构与实现边界

规范依赖方向见 V1.3 第 21.1 节。当前 V0.0 实际调用链：CLI / FastAPI → config.service → config.models；domain 仅依赖标准库，clock 和 logging 不拥有运行实例。网页通过本机代理访问 FastAPI。没有网络管理、运行任务或真值查询能力。

| 目录/文件 | 当前职责 | 禁止依赖或行为 |
|---|---|---|
| domain/ | 观测模式、运行状态、独立 QC 状态枚举 | c104、Web、数据库、电力框架 |
| config/ | 严格 YAML、引用及物理参数边界校验；不可变配置、规范化哈希 | 不创建运行、不施加故障 |
| clock.py | 宿主 UTC/单调零点及不确定度；左闭右开区间 | 不缩放实时协议、不代替设备/捕获时标 |
| logging.py | 显式日志实例与 JSON 格式 | 不修改全局 root logger，不推断实验成功 |
| cli.py | 参数、错误码、共享配置服务调用 | 不实现第二份业务逻辑 |
| web/ | health 和配置校验 API | 不执行 shell，不读取真值/标签 |
| frontend/ | 服务状态及 YAML 校验交互 | 无仿真曲线、mock 运行、场景故障表输出 |
| scripts/ | 薄开发命令、契约导出 | 无仿真/标签/网络业务逻辑 |

protocol、power、business、scenario、network、capture、storage、pipeline、qc、experiments 将按阶段建立。当前不生成这些空包；职责和后续测试见阶段计划。scenario 将是唯一运行控制入口，Web 与 CLI 共用。网络实验独立于 Web、仅管理本运行创建的隔离资源；命令经 c104 到达并被接受后才能进入电力模型。

本次没有正式数据 schema、manifest 或运行状态机；枚举不代表生命周期已实现。后续建立 observed/parsed 与 truth/labels 的后端隔离、M0–M3 白名单及 available_at 上界，Oracle 为独立诊断入口。仅存放合法类型并不等于已实现泄漏防护；对应验收随真实数据链路补齐。
