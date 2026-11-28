# ADR0014：有界排空独立抓包缓冲后停止

2026-11-26。冻结32f93f1的V0.4a前置实验通过，但V0.3断联兼容回归在独立端点QC失败。PV02/PV03的最终S/STOPDT帧存在于P0及终端原始回调，P1原始PCAP没有这些帧。P1最终tcpdump统计分别为captured866/received875和captured783/received792，kernel dropped均0。不是独立解析器漏帧，也不是应用回调缺失。失败目录`/var/lib/pvcomm/runs/v04a_s4/legacy`永久保留。

Linux缓冲捕获在SIGINT退出时仍可能有未交付到tcpdump的包；`-U`只控制已经交付的输出刷新。停止业务端点后，V0.3隔离Ethernet/any捕获先用SIGUSR1请求实际统计，等待完整统计响应；存在received-captured-dropped积压时继续核验，最长5秒。达到排空条件才SIGINT；超时仍关闭进程并保留PCAP、统计和原生spool复制证据，报告清理失败。最终统计必须取最后一组，不能被新增实时统计误导为首组。

这不是增加固定尾部sleep，也不扩大业务窗口或豁免尾帧；原有完整性、独立字节对应、丢包和对齐门槛不变。核验仅用于已停止全部自建流量生产者的隔离捕获，不能泛用于Linux loopback（received可能重复计数）或外部持续流量。V0.1/V0.2默认即时捕获保持原行为。新增真实namespace内短ICMP fixture，立即停止前发送最终往返包，三次核验缓冲排空及原始PCAP包数；ICMP不当作正式协议实验数据。

随后必须冻结新提交重跑失败V0.3、V0.4a前置及旧V0.2兼容验收，不能以单元测试替代真实独立抓包。
