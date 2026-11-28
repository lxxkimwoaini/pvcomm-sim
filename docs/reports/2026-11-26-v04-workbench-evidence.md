# V0.4 只读证据、分层时间轴与原件导出切片

本切片基于现有受控Oracle工作台，新增`web/diagnostic_index.py`、原记录/导出API、`LabelTimeline.vue`及对应页面操作。保留M2路由、原仿真运行器和全部原始证据，没有重跑已验收故障。标签分页和JSONL定位使用最多四个可清理的SQLite偏移索引；单行4MiB、SQLite每索引2MiB缓存，分页按序号范围及身份索引读取，避免反复扫描JSONL。源变化、跨运行和身份重复明确拒绝。

HTTP不接受文件路径。证据键只能来自实际标签引用，完整来源SHA核验后才返回原记录；协议对象保留原始PCAP帧号及过滤式。终态运行导出提供分页文件清单和SHA，下载按已有文件键流式发送；捕获文件另与原manifest SHA对账。符号链接、未知文件键及运行未终止时拒绝导出。不删除原件，不在实验目录写索引/压缩副本，不向M2接口加入诊断字段。

图形投影分别展示故障作用、实际通信影响和业务违约；标明可判定时刻、证据恢复及未知/截尾。合法拒绝等valid/false结果显示为正常结果点，不因end=null画成未恢复异常。正常、违约、未知显示语义由明确合成单测验证，非实验数据。

实际验证：完整Windows普通回归413 passed、66 lab deselected（19.59s）；随后仅补充缓存跨运行拒绝，相关API/索引回归30 passed（1.15s）。`uv run --frozen python scripts/dev.py check`退出0：普通默认依赖405 passed、74 lab/data/power deselected（16.96s），Ruff、strict mypy166源文件、schema/生成TS、前端类型、Prettier、Vitest5项及build通过。ESLint仅保留旧RunWorkbench两条缩进warning，无本切片新增warning；Vite约834.25kB主JS体积提示保留。

浏览器使用真实已执行N09原件：`experiment_00d4a995900246faaf5377a0c1744611`、`run_120a668b863d4f1da4193150df79b12a`，运行源仍为clean冻结36b8e72。本次是当前开发工作树的只读API/UI验证，不声称新增仿真实验或并发吞吐测量。`PVCOMM_EXPERIMENT_ROOT`为`\\wsl.localhost\Ubuntu\var\lib\pvcomm\runs\v04d_workbench_s2\experiments\test_real_submitted_request_an0\experiments`，`PVCOMM_E2E_EXPERIMENT_ID`指定上述身份；运行`pnpm --dir frontend exec playwright test tests/e2e/experiments.spec.ts --output ../outputs/v04_web_evidence_s5`，**2 passed（8.5s）**。实际核验P0对象`primary-512-5:0`、原始帧512及PCAP SHA`12bb0e617699211180297c499cbba3c010d8c0cebffc8d87312c33e808ec2288`；下载PCAP真实magic及SHA响应、manifest.run_id、M2默认隔离、390px无溢出均通过。s5截图已保留；先前s4截图已人工查看。

首轮s1失败于390px长运行编号撑宽（483px），增加实际换行后s2通过；扩展到真实协议对象时s3发现263字符UNC路径触发WinError3，改显式Windows文件系统命名空间后s4/s5通过。所有失败输出目录保留；新增深路径回归验证此修复，不修改系统注册表或关闭安全机制。

完整配对曲线/统计交互、旧批次工作台检索、规模及每类40次有效覆盖仍未完成；不宣称完整V0.4工作台或阶段验收通过。
