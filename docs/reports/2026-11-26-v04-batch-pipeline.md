# V0.4 有限队列真实验证

冻结代码8519edb8858b57b02cda000db4476dd1f95cced0，源目录`/var/lib/pvcomm/sources/v04d_batch_s2`。普通科学环境回归361 passed、65 deselected（19.49s）。真实命令：`PVCOMM_ALLOW_LAB=1 PYTHONPATH=src /mnt/e/project/pv/.venv-wsl/bin/python -m pytest -q tests/lab/test_batch_runtime.py --basetemp=/var/lib/pvcomm/runs/v04d_batch_s2/experiments`，退出0，**1 passed in 350.84s**。

队列清单位于`/var/lib/pvcomm/runs/v04d_batch_s2/experiments/test_real_finite_batch_and_ver0/queue/manifest.json`。同一运行器串行完成N09合法拒绝与区域A08，实际自动解析、Arrow导出、场景/机制QC、标签生成及独立标签QC，不使用合成执行器。

| 任务 | 实际run_id | 队列耗时 | 留存字节 | 索引文件数 | 业务违约站 |
|---|---|---:|---:|---:|---|
| N09 | run_b2facb3453bd43809d155693bca51516 | 139.555s | 5,685,768 | 85 | 无 |
| 区域A08 | run_83b2d0ebfbfc4f12a3771efb5ccce793 | 210.820s | 10,891,913 | 247 | PV01、PV02 |

最终status=completed，2个任务、2次尝试、2个不同run_id、1次有效正常运行和1次有效A08运行。全部8路工具丢包为0，两个运行code_dirty=false、cleanup_errors为空，结束后无实验namespace残留。正常合法拒绝未标业务异常。两个运行的uncertain标签均0。

测试在完成后以相同冻结配置调用resume，逐产物核对路径、大小及SHA256，验证任务尝试列表及独立覆盖计数不变；没有重新调用c104。崩溃/取消和有限重试由普通合成记账测试验证，不能将该部分称为已实际中断真实网络后的恢复实验。

前一冻结de1f849的WSL普通测试5 failed、356 passed，失败发生在/tmp的12GB临时文件系统容量门禁，守卫因此没有启动真实队列。修正仅限记账测试磁盘fixture；生产空间门禁不变，失败日志保留于v04d_batch_s1。

本次容量预算为181,551,588字节，实际两任务证据16,577,681字节；这是三站短实验留存，不能替代V0.3的30站每小时预算。临时峰值、压缩及并发吞吐未实测，最大并发仍为1。完整40次/类和30站异常验收未完成。

后续固定12组配对矩阵在同一冻结源码执行，输出根`/var/lib/pvcomm/runs/v04d_pair_repeats_s1`，尚未取得完整实验结果；其评估阈值与校准来源保持不变。
