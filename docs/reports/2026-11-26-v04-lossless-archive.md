# V0.4 小运行完整归档核验

冻结源码87f50ed907f72ccc28d6e58b7dac204cee1e0090，原件为8519edb真实N09运行 `run_b2facb3453bd43809d155693bca51516`。冻结目录WSL普通回归：375 passed、65 deselected（24.28s），退出0。

实际命令在`/var/lib/pvcomm/sources/v04d_resources_s1`执行：

```sh
PYTHONPATH=src /usr/bin/time -v -o /var/lib/pvcomm/runs/v04d_archive_s2.resources \
 /mnt/e/project/pv/.venv-wsl/bin/python scripts/profile_v04_archive.py \
 --run-dir /var/lib/pvcomm/runs/v04d_batch_s2/experiments/test_real_finite_batch_and_ver0/queue/task_1/attempt_1/run_b2facb3453bd43809d155693bca51516 \
 --output /var/lib/pvcomm/runs/v04d_archive_s2
```

退出0：全部文件逐一压缩并解压哈希核验，原始目录归档前后文件集合及SHA256完全一致。实际源字节5,677,670，gzip载荷619,332，逐文件清单及载荷目录655,359字节（不含随后写入的总报告）。内部归档时间0.169291s，整个命令墙钟0.53s、最大RSS34,820KiB。所有原始PCAP、Parquet、配置、来源、标签、QC、日志保留。

首个命令把任务目录误写为normal而非实际task_1，读取manifest时退出1；未创建归档目录、未修改原件，错误资源记录`v04d_archive_s1.resources`保留。修正路径后使用全新s2输出及资源记录。

此测量仅覆盖三站短运行，且当时有独立配对队列负载；不是30站留存预算、解析临时峰值、并发吞吐或720小时容量验收。1秒资源采样仍只能提供峰值下界，不能将采样最大值描述为严格内存/临时空间上界。
