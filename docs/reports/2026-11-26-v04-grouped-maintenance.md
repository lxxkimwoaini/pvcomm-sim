# V0.4 A08 分组维护恢复验收

冻结运行代码：bade029f97a1d1fd899cfd82d4cfb0dad4a0890a。源目录 `/var/lib/pvcomm/sources/v04d_maintenance_s2`，持久证据 `/var/lib/pvcomm/runs/v04d_maintenance_s2`。未覆盖第一轮失败目录 `v04d_maintenance_s1`。

实际执行：`PVCOMM_ALLOW_LAB=1 PYTHONPATH=src /mnt/e/project/pv/.venv-wsl/bin/python -m pytest -q tests/lab/test_maintenance_runtime.py --basetemp=/var/lib/pvcomm/runs/v04d_maintenance_s2/experiments`，退出0，**3 passed in 642.53s**。日志为该根目录的 `lab.log`。

| 用例 | 运行编号 | 维护站 | 故障目标 | 业务违约站 |
|---|---|---|---|---|
| 区域全部恢复失败 | run_bf5ce60de3204f94add75b0b1ab92671 | PV01、PV02（R1） | PV01、PV02 | PV01、PV02 |
| 区域部分恢复失败 | run_f340077c75674a6da68ebd0b81e10ab9 | PV01、PV02（R1） | PV01 | PV01 |
| 跨区域站点组 | run_4941a450792147da8ff4d80383b2c524 | PV01、PV03 | PV01、PV03 | PV01、PV03 |

三项均通过 `v04-terminal-mechanism-v3`、`v04-label-qc-v4` 及 `v04-grouped-recovery-acceptance-v1`。9个站点标签全部valid，invalid/uncertain均0。12份独立P0/P1捕获的工具报告丢包均0，运行completed、code_dirty=false、cleanup_errors为空。测试逐次检查宿主路由不变、本次namespace清理、未维护站在维护期间实际调控完成。

授权计划在主站事先可见；维护期间物理连续性每站20个样本，最大采样间隔1.028秒。连接、协议和业务恢复分别由真实OPEN、GI和有效1001/1006证据判断。固定业务恢复期限15秒保持不变。混合用例PV01在维护结束后0.154秒连接恢复、1.023秒协议恢复，46.087秒业务恢复，产生业务违约；PV02分别为0.317、1.099、1.102秒，期限内恢复且不标违约。合法维护授权未被当作正常标签的充分条件。

第一轮实际3 failed（650.32秒）保留：两个用例末尾断言误要求非故障站具备不适用的故障恢复记录；另一个混合用例存在真实连接启动顺序缺陷，机制门禁失败。bade029以端点恢复监听就绪握手修复启动顺序，未延长15秒期限；本报告仅以新三次实测判定通过。详见ADR0023及进度历史。

本切片证明三站、区域/站点组及混合恢复机制有效，不代表10/30站覆盖、每类40次独立运行、批量吞吐或完整V0.4验收通过。
