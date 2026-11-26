# V0.4 受控工作台首切片真实验证

冻结代码36b8e721bf3c344fa31d0ad87b9e509cc7c83de3，源`/var/lib/pvcomm/sources/v04d_workbench_s1`。Windows普通396 passed、66 deselected（20.21s），WSL普通396 passed、66 deselected（25.15s），退出0。Ruff、strict mypy161源文件、schema/生成TS、类型检查及构建通过。ESLint仅保留旧RunWorkbench两条缩进warning，Vite仍有>500kB体积提示。

真实命令：`PVCOMM_ALLOW_LAB=1 PYTHONPATH=src /mnt/e/project/pv/.venv-wsl/bin/python -m pytest -q tests/lab/test_experiment_request_runtime.py --basetemp=/var/lib/pvcomm/runs/v04d_workbench_s2/experiments`。父目录在首次执行前创建，输出根未复用。**1 passed in 139.96s**，退出0。首个s1命令因basetemp父目录缺失在pytest setup退出1，未启动网络；其日志保留，不计真实实验尝试。

实际由POST提交N09、seed902001，产生`experiment_00d4a995900246faaf5377a0c1744611`。单独调用授权消费者，复用run_batch执行`run_120a668b863d4f1da4193150df79b12a`。真实c104及pvlib/pandapower运行、P0/P1采集、自动解析、Arrow、正常业务QC、分层标签及独立标签QC全部通过；3个valid标签、无业务违约，合法拒绝未标异常。manifest记录code_dirty=false、cleanup_errors=[]，四路工具丢包0。

已完成任务再次消费返回空列表，没有重跑协议。HTTP轮询只读summary，不含artifacts清单；M2运行根独立且返回空集，未由Oracle根填充观测。普通负测试覆盖跨源写入、未启用、未知参数、非法档位/目标/规模、矩阵哈希变化、双消费者锁、完成不重跑及纳秒精度。

原件及浏览器引用在`/var/lib/pvcomm/runs/v04d_workbench_s2/experiments/test_real_submitted_request_an0`。浏览器API使用此真实实验根，`PVCOMM_E2E_EXPERIMENT_ID`指定上述ID；`pnpm --dir frontend exec playwright test tests/e2e/experiments.spec.ts --output ../outputs/v04_web_real_s1`实际**2 passed（4.0s）**，核对启用态、M2默认隔离、移动端无横向溢出及页面标签与真实API完全相等。截图使用唯一测试输出路径。未启用态另为1 passed、1实际原件用例skipped（3.6s）。

当时有独立配对队列同时运行，本次不作为并发吞吐或资源隔离性能验收。所有仿真仅在各自独立本地网络，未连接外部设备。

本切片交付场景表单/受限提交、独立执行器、紧凑队列及覆盖状态、实际QC摘要、标签/证据引用查询。完整图形时间轴、配对曲线/统计交互、协议/PCAP定位和导出尚待后续切片，不冒称完整V0.4工作台。六类故障的已有机制不因此重复验收。

远程已核验SHA与上述代码相同，GitHub Actions push45832152566、PR45832162862均实际success。PR #1～#5仍OPEN，堆叠关系不变，没有合并。
