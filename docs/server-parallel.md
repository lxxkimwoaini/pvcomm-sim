# 三站正常数据并行生产

应用冻结5f4cf2d，不对活动`$HOME/pvcomm-sim`执行pull或编辑。独立薄driver部署于`$HOME/pvcomm-tools/server-parallel-s1/server_parallel.py`；脚本SHA和应用SHA、clean、48项原清单及6份BatchConfig哈希在protocol.json冻结，续跑前检查。生产数据不进Git。

计划：`$HOME/pvcomm-data/plans/v10_server_parallel_20240614_s1`；统一产物：`$HOME/pvcomm-data/runs/v10_server_parallel_20240614_s1`。trial_a/trial_b为两个1802秒真实验证，余项worker_1～4分别12/12/11/11项。每项只有1800秒有效历史，48项累计24小时，不能当成连续24小时。所有任务同属6月14日train来源组，27～29日正式test隔离。

`parallel-index.json`记录实际PID、时刻、原清单哈希、任务、输出、完成/失败/重试、QC和全局资源；每Worker的原manifest.json、summary.json、.execution-lock.sqlite分别保留。两路须100%完整APDU、四路工具drop0、正常及独立标签QC通过、invalid/uncertain/违约均0、清理无错误及全部产物SHA一致才启动四路。原一小时任务须先完成并通过同样核验；失败则不启动新仿真。

启动前以及每次调用原execute_job前，计算全部未完成任务的剩余尝试留存预算，加并发解析历史下界和20GiB安全余量；MemAvailable至少16GiB。资源不足的driver等待，不调用仿真函数，活动正常任务继续完成。四路预算不足时维持两路。原run_batch的固定并发1及串行循环不改，原业务/标签门禁不放宽。各Worker独立包装器、user/mount/net namespace、TMPDIR、日志、BatchResources采样和SQLite执行锁。主站观测、Oracle和标签边界不变。

在服务器查询（不会启动任务）：

```bash
python3 -m json.tool "$HOME/pvcomm-data/plans/v10_server_parallel_20240614_s1/parallel-index.json"
python3 -m json.tool "$HOME/pvcomm-data/runs/v10_server_parallel_20240614_s1/worker_1/summary.json"
```

SSH断开后nohup协调进程和独立session的Worker继续。协调进程仍在运行时不要重复启动。若协调进程退出，先检查索引PID、/proc起点和实际命令。2026-11-27实际发现原资源采样的/proc/io权限错误会退出协调器；四个独立Worker持续正常生产，不应因此重启它们。现使用外置恢复入口，保持冻结脚本、应用、清单和输出，只在新协调进程内替换诊断采样；它会重新纳入已归属PID1、起点身份匹配的Worker。缺测逐字段记录，未知IO总量为null，旧资源缺口不补造。服务器已部署的恢复脚本SHA为deaaa9c5deac8297366abbeb765e6d98e9559d0416eab259306e76cb4dab048a。

```bash
cd "$HOME/pvcomm-sim"
. "$HOME/pvcomm-tools/env.sh"
nohup .venv/bin/python -u \
  "$HOME/pvcomm-tools/server-parallel-recovery-s1/resume_server_parallel.py" \
  --plan "$HOME/pvcomm-data/plans/v10_server_parallel_20240614_s1" \
  >> "$HOME/pvcomm-data/plans/v10_server_parallel_20240614_s1/coordinator-recovery.log" \
  2>&1 < /dev/null &
```

恢复会保留存活Worker，不重跑completed任务；仅重新调度已退出driver。原run_batch重新校验已有文件SHA及矩阵，未完成真实运行从新attempt开始，既有失败/中断均消耗原max_attempts=2。进程锁阻止同一协调器/Worker的重复执行；不共享或合并不同Worker清单。资源等待不创建额外attempt，原始及失败证据始终保留。

各Worker可独立恢复，但须先确认对应driver退出，保持原TMPDIR及包装器：

```bash
cd "$HOME/pvcomm-sim"
. "$HOME/pvcomm-tools/env.sh"
export TMPDIR="$HOME/pvcomm-data/runs/v10_server_parallel_20240614_s1/worker_1-tmp"
nohup sh "$HOME/pvcomm-tools/run_userns_lab.sh" .venv/bin/python -u \
  "$HOME/pvcomm-tools/server-parallel-s1/server_parallel.py" worker \
  --plan "$HOME/pvcomm-data/plans/v10_server_parallel_20240614_s1" --worker worker_1 \
  >> "$HOME/pvcomm-data/plans/v10_server_parallel_20240614_s1/worker_1.log" 2>&1 < /dev/null &
```

通常优先恢复协调进程，避免手工调度与协调器竞争；独立命令仍由driver锁和原SQLite锁保护。单独恢复后应依据Worker原summary查询真实状态，不手改parallel-index或manifest。最终独立audit重新核验整个批次所有产物SHA及QC报告；资源采样的CPU/RSS及临时峰值为离散采样下界，不称作精确系统峰值。
