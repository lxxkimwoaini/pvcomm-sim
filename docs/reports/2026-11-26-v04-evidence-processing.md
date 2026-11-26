# V0.4 证据处理与保留运行复核

冻结代码761568f，源目录`/var/lib/pvcomm/sources/v04d_processing_s1`。WSL普通测试实际373 passed、65 deselected（22.46s），Windows373 passed、65 deselected（18.03s）。Ruff、mypy及schema检查通过。未改标签v3判据或Arrow契约。

在同一保留真实运行`run_f340077c75674a6da68ebd0b81e10ab9`上分别执行：`PYTHONPATH=src /mnt/e/project/pv/.venv-wsl/bin/python scripts/profile_v04_evidence.py --run-dir /var/lib/pvcomm/runs/v04d_maintenance_s2/experiments/test_grouped_maintenance_real_1/run_f340077c75674a6da68ebd0b81e10ab9 --mode <list或indexed> --output /var/lib/pvcomm/runs/v04d_processing_s1/<同名模式>`。两次退出0。原始记录、标签及QC未重写；新报告与/usr/bin/time记录在外部目录。

| 实际单次测量 | 列表路径 | 索引路径 |
|---|---:|---:|
| 标签及独立审计耗时 | 0.729s | 1.358s |
| 同阶段Python追踪分配峰值 | 15,693,891B | 4,126,284B |
| 临时索引大小 | 0 | 3,608,576B |
| 全脚本进程最大RSS | 157,184KiB | 144,432KiB |
| 旧Parquet逐行QC耗时 | 1.500s | 1.564s |

两路径生成的完整标签及全部证据均与保留标签相等，独立审计和六表Parquet QC通过，索引退出清理成功。规范化标签SHA256均为`d623e24702aa97a39e3e897893ce8db469b7d212dcd988bdcda649e4ad183f97`。

这是三站短实验的两次单次测量，运行时另有配对实验在执行，不是隔离性能benchmark。Python追踪分配不包含原生分配；全脚本RSS包含Arrow导入及QC。索引减少该样本的Python分配，但增加处理时间，不宣称全流程吞吐改善、全流程常量内存或30站性能已验证。

导出和核验采用4096行批次，原始JSONL及Parquet不再整体读入；原始文件转换前后哈希一致才提交新目录。10,003行合成契约测试在禁止完整文本/二进制/Arrow读取的条件下完成往返核验，最后一批内容篡改且重算manifest仍被检出。四类业务结果与新鲜度缺口的完整标签、来源哈希及独立审计在列表/索引路径相同。重复键、跨运行输入和求值异常均测试本次临时目录清理。

剩余限制：QC报告JSON、单站序列和电力扩展表仍有驻留消费者；解析临时峰值、真实批次资源和有限并发吞吐还需实测。新增资源采样器只观察现有队列及其子进程，1秒采样峰值明确是观测下界；不以它放宽原抓包、解析、日志或业务门槛。
