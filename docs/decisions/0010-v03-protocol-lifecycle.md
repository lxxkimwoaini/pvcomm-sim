# ADR0010：多站点协议初始化与有序停止

2026-11-25。带NetEm的6e42aa9三站原证据重验和十站新运行通过，但二十站`/var/lib/pvcomm/runs/v03_scale_s6/v03_20_normal_001`失败：完整APDU及物理QC通过，日志映射P99=860.012185ms/max2128.690613ms；PV15缺少一个入站S帧回调。失败报告与PCAP保留，不进入30站，不排除初始化数据或放宽100ms门槛。

逐APDU检查最大误差集中在场景屏障之前。实际核对c104 v2.2.1官方`src/remote/Connection.cpp`：INIT_ALL将等待总召唤/时钟确认的任务放入Client调度队列，raw回调也使用该队列；多连接时初始化任务阻塞实际回调交付。`src/Client.cpp`的stop先停止调度线程、随后断开连接。来源为Fraunhofer-FIT-DIEN/iec104-python的v2.2.1标签，通过GitHub API只读取得，本地快照在outputs/c104-2.2.1-{Connection,Client}.cpp。

V0.3采用Init.NONE（仍由真实协议栈STARTDT建立传输），在调用线程显式逐站总召唤和时钟同步，仍等待实际确认，完成后再进入场景屏障。回调调度线程保持处理原始报文；不回写接收时间，不修改QC分母。V0.1/V0.2默认Init.ALL及既有行为保留。

V0.3正常结束先请求各连接STOPDT，等待真实OPEN_MUTED状态回调，再显式disconnect，等待CLOSED状态回调，最后停止Client。条件变量等待实际回调，3s沿用业务确认期限；没有任意延长sleep或新增尾帧豁免。失败仍停止客户端并将错误交由运行器记录，不能声称正常结束。此为协议生命周期实现修正，不改变正式业务参数、标签或观测权限。必须在新冻结源码、新输出根重新逐级验证；单元替身只验证顺序，不替代真实PCAP。
