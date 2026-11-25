# ADR0008：规模运行的抓包缓冲及持久化

2026-11-25。e27c44b冻结三站120s全部QC通过。相同代码十站120s completed且物理/业务通过，但P0 dropped_by_kernel=54，独立协议QC失败；不推进20站，原目录outputs/v03_scale_s1/v03_10_normal_001保留。

待验证优化：V0.3不启用tcpdump immediate模式，保留内核包时间戳及-U，16MiB缓冲不改；抓包先写Linux原生临时目录，退出后复制到唯一运行目录，核对长度及SHA256后才删除本次spool。manifest保留active spool路径，可在异常时定位；复制失败保留原spool，不宣称原始证据已持久化。运行完成门禁仍要求所有抓包成功保存、0工具丢包、≥99.5%完整APDU和日志对齐P99≤100ms。V0.1/V0.2默认capture路径/选项不变。

这针对实际WSL drvfs并发写入环境及突发逐包抓取，不将丢包归因写成已证明的系统结论。下一轮须实测判断优化是否足够；不通过加大容差或丢弃失败包通过验收。


追加实测：a2187a6十站120s（outputs/v03_scale_s2/v03_10_normal_001）原生spool后全部QC通过，11路dropped=0、6830完整APDU。二十站120s同代码全部21路dropped=0、物理及业务通过，但日志映射P99=164.241582ms、最大310.274496ms，违反原100ms门槛，保留失败且不进入30站。下一对照只将运行产物目录改为WSL持久Linux路径 `/var/lib/pvcomm/runs/v03_scale_s3`，保留代码/配置/来源，检验逐条flush跨drvfs的影响；该路径非临时目录，报告需写明实际存储环境及备份。
