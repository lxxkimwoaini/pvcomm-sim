# V0.4 配对回放工作台验收

2026-11-26（Asia/Shanghai）。本切片完成Oracle配对遥测曲线及固定流量统计交互，V0.4整体仍未通过。后端为`web/pair_replay.py`，前端为`PairReplayPanel.vue`，契约同步OpenAPI和自动生成TypeScript。

仅枚举服务端配置根及已有实验请求内的真实配对；保留失败/未匹配记录。遥测按每个成员自己的scene_clock映射相同场景秒，严格available_at<游标；600条索引读取/240点显示预算，未知与陈旧不补零，不读取truth。固定统计窗口结束前不显示事后流量报告；结束后复核pair/PCAP来源SHA。质量/业务对照/统计匹配为全运行事后诊断，入口明确Oracle，不进入M2。

真实保留批次`/var/lib/pvcomm/runs/v04d_pair_repeats_s1`发现12组；浏览器实际验证trial_06的P06：`pair_21a5dce4c0a24a8597533aff67f115db`，正常成员`run_46eb4575cbd14219ac7615d6f6ba99bc`、改变成员`run_1a99d76ec402497aa0677ce1d620befa`。数据质量及业务对照通过，统计未匹配，保留原结论。

实际PowerShell复现（项目根）：

```powershell
$env:PVCOMM_PAIR_REPLAY_ROOT='\\wsl.localhost\Ubuntu\var\lib\pvcomm\runs\v04d_pair_repeats_s1'
pnpm --dir frontend exec playwright test tests/e2e/pair-replay.spec.ts --trace on --output ../outputs/NEW_PAIR_REPLAY_E2E
```

真实E2E结果1 passed（测试7.5秒、整体10.6秒），输出`outputs/v04_pair_replay_web_s3`，保留trace及390px截图，已实际查看。验证服务端因果截断、10秒游标隐藏事后统计、默认M2没有配对面板/无法通过配对key读取运行、两曲线canvas、目标站流量数值对应原固定报告、游标往返及窄屏无横向溢出。该测试回放原件，没有重新运行协议或用合成fixture替代真实验收。

先前s1默认30秒和s2诊断90秒均失败，证据保留。trace确认组合框readonly输入被Element Plus占位层拦截鼠标；接口可实际发现12组。改为标准ArrowDown键盘交互后在默认30秒内通过，没有force点击或sleep。最初目录匹配怀疑经直接1.35秒列表调用排除，未改扫描规则。

普通检查实际结果：Ruff/format248文件、strict mypy173源文件（含Linux平台）、schema/生成类型校验通过；完整普通Python448 passed、66 lab deselected（22.04秒）；前端7项单测、lint/format/typecheck/build通过。ESLint仅既有RunWorkbench两条warning，构建保留既有大chunk提示。补充长记录读取及边界测试结果在进度中追加。

限制：浏览器真实验收代表上述P06，不等于所有12组逐项UI验收；只读接口不修复历史失败原件，不承诺全平台常量内存。三十站异常门禁、40次/类有效覆盖及有限并发仍须真实通过，不能由此切片或CI替代。
