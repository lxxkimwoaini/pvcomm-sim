# 实验室服务器部署与真实三站验证

2026-11-27，固定三站，源码5f4cf2d9648d51f956f8aba73d7fb6262cc00d8c，code_dirty=false。未修改c104、电力、标签、特征、QC门槛或前端。服务器SSH私有配置与密钥不写入仓库。

## 部署与权限

实测Ubuntu22.04.5 LTS、Linux6.8.0-90-generic、24逻辑CPU、约64GB内存；数据位于独立/home分区，正式小时片段启动前可用311,159,259,136字节。uv0.13.10及Python3.11.15在用户目录安装，uv.lock保持原件；protocol/data/power/experiments依赖离线frozen同步实际退出0，科学和协议库真实导入成功。缓存从已有Linux环境转运，未复制venv。

普通用户没有宿主有效capabilities，sudo非交互检查实际需要密码。系统允许user namespace；使用保留当前UID的unshare及私有mount/network，不修改宿主sysctl、网络路由、安全机制或系统包。NetEm旧tc不支持seed，用户目录构建tc6.15.0后内核读回seed=7成功。tshark最终使用独立加载器运行已验证4.6.4，49个文件逐一SHA验证通过。NASA POWER weather/raw/source三个输入SHA与本地完全一致。

`scripts/run_userns_lab.sh`是薄部署包装器，服务器实际运行副本SHA256为`352b557c365c70a8e56d64bb0aacf1833300cffd019df67900956fa871a3cf10`。映射当前UID避免tcpdump尝试改变到未映射系统账户时的setgroups失败；保留原tcpdump安全行为，没有编译关闭降权或改变宿主权限。

## 真实冒烟及保留的失败

沿用N06，三站、两个有功限发/解除任务。真实run_id为`run_a290f9daeac7455f84855df12b87160b`；业务配置180秒，独立质量记录179.999586991秒，全流程191.078706697秒。原件在服务器`$HOME/pvcomm-data/runs/server_smoke_s1/three_site_N06/attempt_1/`。

首轮tshark3.6.2仅产生329条解析APDU，独立完整原始APDU为1746，严格对账只匹配145条（8.3047%），真实QC失败。四路抓包工具丢包0、物理计算及真实回调对应均通过。原队列completed_with_failures、原parsed/labels/QC和全部PCAP均完整保留，不将其改成成功。

更换用户目录解析工具至tshark4.6.4，在`$HOME/pvcomm-data/reprocessing/server_smoke_s1_tshark464/<同一run_id>`创建原输入副本，64个输入文件SHA逐一相等，仅重新生成派生解析、Arrow、标签及QC。实际5.438956954秒、退出0，不重跑仿真、不产生第二次独立实验，不改原PCAP时间戳或原失败清单。新report.json明确关联原路径和相同run_id。

|检查|实际结果|
|---|---|
|P0完整APDU匹配|1746/1746，100%，incomplete_bytes=0|
|P0包数/解析对象数|3489/1565|
|四路P0/P1工具丢包|全部0，全部drain_complete=1|
|日志时间对齐|P99=3.784621ms，最大6.787857ms|
|正常业务及电力QC|通过|
|独立标签QC|通过，3 valid、0 invalid、0 uncertain|
|业务违约|无|
|原运行资源清理|cleanup_errors=[]|
|宿主ip netns list|空；生产时命名资源仍只存在于私有mount namespace|

这是服务器部署兼容验证，未重复六类机制验收、规模测试或独立test实验，也不把冒烟计入新的正式测试覆盖。

## 实际服务器生产

新一小时正常片段于2026-11-27 14:59:36 Asia/Shanghai启动，PID1755669，后台session独立于SSH。来源2024-06-13T04:00:00Z、三站、3602秒含2秒预热，既有N10及日常总召唤/限发/解除/合法维护。来源角色train、完整有效配置及哈希在启动前冻结；与本地2024-06-12的24小时队列没有重复任务，与正式test日期隔离。

计划`$HOME/pvcomm-data/plans/v10_server_normal_hour_s1`，输出`$HOME/pvcomm-data/runs/v10_server_normal_hour_s1`。batch.json SHA256=`03cb11b5ab50d15f54bab10173c11086551bd59919df688db383b4eba0860e9e`。15:00检查为running，0/1完成、0失败；不能提前报告一小时数据通过或已入库。首个正常小时通过后再据实际成本安排后续服务器分段，不启动大规模队列。

本地原驱动1151保留，未停止或迁移活跃队列。15:00仍为20/21正式测试完成、0失败，六类故障已各3次有效机制运行，最后一个正常业务在运行；后续24小时由原冻结驱动接续。真实阳性、事件数量和数据集交付仍以原标签及新快照统计为准，不把机制有效次数直接当作业务阳性次数。当前推荐WSL快照仍为pvcomm_v1.0_s2，107次是该快照规模，不是实时生产总量。

本轮不运行模型训练、不评估正式test。部署、续跑及不可重复启动约束见[操作指南](../lab-server.md)。大数据和密钥未推送，代码文档检查及Git/CI结果记录于progress。
