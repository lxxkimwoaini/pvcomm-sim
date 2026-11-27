# 实验室Linux服务器运行

用户于2026-11-27授权使用实验室服务器生成三站数据。SSH地址、用户名、私钥及known_hosts属于部署私有配置，不写入Git；私钥只留客户端。服务器数据放在独立/home分区，使用`$HOME/pvcomm-data`，不占容量较小的根分区。

现有本地生产队列继续保留，不在服务器重复运行相同task_id、日期时段和种子。源码`$HOME/pvcomm-sim`冻结在5f4cf2d；后续更新源码前须确认所有使用该工作目录的实验已结束。单次部署冒烟的任务、环境、日志分别保存在`$HOME/pvcomm-data/plans/server_smoke_s1`，真实原件在`$HOME/pvcomm-data/runs/server_smoke_s1`。该冒烟不作为新的正式独立测试数据。

## 已安装环境

Ubuntu22.04.5、Linux6.8.0-90-generic、24逻辑CPU、约64GB内存；部署时/home可用约311GB。Python3.11.15、uv0.13.10；项目依赖严格沿用uv.lock，c1042.2.1、pvlib0.16.1、pandapower3.5.6、pyarrow23.0.1。未部署前端，不运行模型训练。

远端PyPI文件CDN超时的原日志保存在`$HOME/pvcomm-sync.log`；从已有Linux环境传输uv下载缓存后，在服务器重新创建环境，未复制虚拟环境、改版本或重写锁文件：

```bash
cd "$HOME/pvcomm-sim"
UV_CACHE_DIR="$HOME/.cache/pvcomm-uv-transfer" "$HOME/.local/bin/uv" sync \
  --offline --frozen --group protocol --group data --group power --group experiments
```

旧系统tc5.15不接受seed。iproute2-6.15.0从kernel.org下载、在用户目录构建，仅安装tc到`$HOME/pvcomm-tools/bin`；保留归档、源码及构建日志。bison/flex及缺失依赖通过系统已配置Ubuntu软件源的`apt-get --download-only`取得，再由dpkg-deb解包到`$HOME/pvcomm-tools/root`，不修改系统包或sudo策略。原系统tcpdump4.99.1、ip、bridge、strace直接复用。新增NetEm已实际读回seed=7，未移除种子。

同源取得的tshark3.6.2实际解析未通过，原失败队列完整保留。最终从已有验证环境传输tshark4.6.4、专属动态加载器及依赖库至`$HOME/pvcomm-tools/tshark464-runtime`，49个二进制/库文件逐个SHA256核验；工具内的manifest.json记录来源、版本及哈希，许可证文件随包保留。`bin/tshark`包装器只对该子进程指定专属库路径，未替换服务器libc、动态加载器或系统配置。只使用内置IEC104解析，不加载缺少依赖的可选Falco插件。原PCAP在独立副本重处理后1746/1746完整APDU匹配、业务及独立标签QC通过，详见[部署报告](reports/2026-11-27-lab-server.md)。

`$HOME/pvcomm-tools/env.sh`只配置部署工具路径、动态库路径及WIRESHARK_DATA_DIR；不包含科研参数或凭据。NASA POWER历史输入`data/nasa-power-huixian-202406-month`已复制，weather/raw/source的SHA256与客户端一致。气象来源、许可及模型扰动的说明继承原配置，不把插值和模型云影称为秒级实测。

## 用户命名空间

此服务器允许非特权用户创建user/mount/network namespace。普通用户无sudo免密权限，也没有宿主有效capabilities。使用系统已允许的隔离能力，无需修改sysctl、安全策略或宿主路由：

```bash
cd "$HOME/pvcomm-sim"
. "$HOME/pvcomm-tools/env.sh"
sh "$HOME/pvcomm-tools/run_userns_lab.sh" .venv/bin/python -m pvcomm.cli --help
```

包装脚本保留真实UID，`--keep-caps`保留命名空间内能力；私有挂载传播后，为本次实验单独挂载/run，使ip netns的挂载不进入宿主目录。所有子进程在同一私有网络中，虚拟站点无外部默认路由。实验内正常执行既有网络创建、参数读回、P0/P1抓包及清理。它不授予宿主root能力，也不把任意命令接口暴露给Web。

尝试映射为namespace root会使系统tcpdump的默认降权触发`Operation not permitted`；最终采用映射当前UID的原生unshare选项，保持tcpdump原安全行为，不修改程序或宿主setgroups限制。

## 任务执行与续跑

所有任务仍调用既有`pvcomm.scenario.batch.run_batch`。部署目录的薄driver.py只读取BatchConfig、调用run_batch并根据真实完成状态退出；没有另建任务系统。每次启动前检查对应driver PID和summary.json。完成任务不得重跑；仅原驱动退出后使用同一源码、清单和输出续跑，SQLite锁及原件哈希校验继续生效。

部署冒烟的同等启动命令如下（首次执行已在本轮完成；保留原失败状态，不消耗新尝试；不要在活跃时重复执行）：

```bash
cd "$HOME/pvcomm-sim"
. "$HOME/pvcomm-tools/env.sh"
sh "$HOME/pvcomm-tools/run_userns_lab.sh" .venv/bin/python -u \
  "$HOME/pvcomm-data/plans/server_smoke_s1/driver.py" \
  "$HOME/pvcomm-data/plans/server_smoke_s1/batch.json" \
  "$HOME/pvcomm-data/runs/server_smoke_s1"
```

后台运行使用nohup或已有tmux，日志写入计划目录，关闭SSH不停止实验。状态读取`runs/<batch>/summary.json`；原始PCAP、主站观测、物理真值、解析、Arrow、标签和QC均在对应run_id下。大文件及服务器私有配置不推送GitHub。独立服务器上的UTC与monotonic映射分别记录，跨宿主运行不按墙钟强行拼成连续片段。

当前24小时本地队列不能直接在服务器重新启动。不能复制运行中的SQLite锁假设获得跨服务器互斥。新增正常数据逐片段完成现有parse→Arrow→业务/标签QC后入库，168小时未完成前如实保留缺口。

本轮服务器正式补充一个新日期的一小时正常片段，已在2026-11-27 14:59:36 Asia/Shanghai启动，PID1755669；来源2024-06-13T04:00:00Z，不与本地2024-06-12的24小时计划重复，也不碰27/28/29日正式test。沿用既有N10、总召唤、限发/解除和合法维护，3602秒中预热2秒；固定三站、最多两次尝试。计划及来源角色在启动前冻结为train，不看模型效果。原106次来源日期中不含13日，已实际核对；同日后续片段仍须同组划分。

计划目录`$HOME/pvcomm-data/plans/v10_server_normal_hour_s1`包含batch.json、protocol.json、driver.py、launch.json、driver.log；输出为`$HOME/pvcomm-data/runs/v10_server_normal_hour_s1`。协议中的frozen_not_started为冻结时状态，运行状态以launch及输出summary为准。检查该PID已退出后，使用相同冻结源码和下列命令续跑：

```bash
cd "$HOME/pvcomm-sim"
. "$HOME/pvcomm-tools/env.sh"
sh "$HOME/pvcomm-tools/run_userns_lab.sh" .venv/bin/python -u \
  "$HOME/pvcomm-data/plans/v10_server_normal_hour_s1/driver.py" \
  "$HOME/pvcomm-data/plans/v10_server_normal_hour_s1/batch.json" \
  "$HOME/pvcomm-data/runs/v10_server_normal_hour_s1"
```

本片段尚未完成或验收时不计入有效正常时长。它没有自动加入WSL快照，也未安排更多服务器长时任务。入库时需将其原件及哈希收录到新的数据快照，不能将服务器绝对路径误作本地可读路径。
