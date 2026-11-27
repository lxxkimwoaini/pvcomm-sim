"""Evidence-derived development release statistics and bounded feasibility diagnostics."""

from __future__ import annotations

import bisect
import importlib
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from pvcomm.pipeline.dataset import read_json, write_json
from pvcomm.storage.parquet import iter_jsonl


def release_report(root: Path, previous: Path) -> dict[str, Any]:
    pq = importlib.import_module("pyarrow.parquet")
    metrics = importlib.import_module("sklearn.metrics")
    records = {r["run_id"]: r for r in iter_jsonl([root / "runs.jsonl"]) if r["eligible"]}
    profiles = read_json(root / "split/source_profile.json")
    scales: dict[str, Any] = {}
    feasibility: dict[str, Any] = {}
    for scale in ("W10", "W30", "W60"):
        path = root / scale
        frames = {
            name: pq.read_table(path / f"features-M2/{name}.parquet").to_pylist()
            for name in ("features", "labels", "index")
        }
        indices, features, labels = frames["index"], frames["features"], frames["labels"]
        lookup = {r["sample_id"]: i for i, r in enumerate(indices)}
        grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
        contexts: dict[str, list[int]] = defaultdict(list)
        for i, r in enumerate(indices):
            if records[r["run_id"]]["site_count"] != 3:
                continue
            grouped[r["run_id"], r["station_id"]].append(i)
            if labels[i]["label_at_end"] == 0:
                contexts[str(records[r["run_id"]].get("normal_scene"))].append(i)
        backgrounds = {
            context: dict(
                windows=len(members),
                runs=len({indices[i]["run_id"] for i in members}),
                groups=len({indices[i]["group_id"] for i in members}),
                packet_rate_median=statistics.median(features[i]["packet_rate"] for i in members),
                packet_rate_mean=statistics.mean(features[i]["packet_rate"] for i in members),
                retransmission_mean=statistics.mean(
                    features[i]["retransmission_fraction"] for i in members
                ),
            )
            for context, members in sorted(contexts.items())
        }
        pre = []
        for event in profiles["events"]:
            if records[event["run_id"]]["site_count"] != 3:
                continue
            members = grouped[event["run_id"], event["station_id"]]
            times = [indices[i]["window_end_utc_ns"] for i in members]
            offset = bisect.bisect_right(times, event["start_utc_ns"]) - 1
            if offset < 0 or event["start_utc_ns"] - times[offset] > 10 * 10**9:
                pre.append(dict(event_id=event["event_id"], status="no_full_normal_history"))
                continue
            i = members[offset]
            if labels[i]["label_at_end"] != 0:
                pre.append(dict(event_id=event["event_id"], status="already_in_other_violation"))
                continue
            import json

            config = json.loads(
                (Path(records[event["run_id"]]["path"]) / "power_config.json").read_bytes()
            )
            station = next(
                s for s in config["protocol"]["stations"] if s["station_id"] == event["station_id"]
            )
            age = features[i]["power_age_s"]
            pre.append(
                dict(
                    event_id=event["event_id"],
                    run_id=event["run_id"],
                    status="normal_precursor_available",
                    precursor_s=(event["start_utc_ns"] - times[offset]) / 1e9,
                    retransmission_present=features[i]["retransmission_fraction"] > 0,
                    power_age_exceeds_period=age is not None
                    and age > station["telemetry_period_s"],
                    power_missing=features[i]["power_kw"] is None,
                )
            )
        incomplete = {}
        baseline_summary = {}
        for name in ("M0", "M1", "M2", "M2-without-rule-proxies", "M2-without-peer"):
            report = read_json(path / f"benchmark-{name}/report.json")
            baseline_summary[name] = report["summaries"]
            predictions = pq.read_table(path / f"benchmark-{name}/predictions.parquet").to_pylist()
            subgroups: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
            for p in predictions:
                i = lookup[p["sample_id"]]
                if records[indices[i]["run_id"]]["site_count"] != 3:
                    continue
                group = (
                    "incomplete_or_stale"
                    if features[i]["fresh_point_coverage"] < 1
                    else "fresh_complete"
                )
                subgroups[p["algorithm"], p["seed"], group].append(p)
            incomplete[name] = [
                dict(
                    algorithm=algorithm,
                    seed=seed,
                    subgroup=group,
                    windows=len(ps),
                    runs=len({indices[lookup[p["sample_id"]]]["run_id"] for p in ps}),
                    source_groups=len({indices[lookup[p["sample_id"]]]["group_id"] for p in ps}),
                    positives=sum(labels[lookup[p["sample_id"]]]["label_at_end"] for p in ps),
                    f1=float(
                        metrics.f1_score(
                            [labels[lookup[p["sample_id"]]]["label_at_end"] for p in ps],
                            [p["predicted"] for p in ps],
                            zero_division=0,
                        )
                    ),
                )
                for (algorithm, seed, group), ps in sorted(subgroups.items())
            ]
        scales[scale] = dict(
            statistics=read_json(path / "dataset_statistics.json"),
            events=read_json(path / "event_coverage.json")["by_partition"],
            qc=read_json(path / "leakage_report.json"),
            evidence_qc={
                k: v
                for k, v in read_json(path / "feature_evidence_qc.json").items()
                if k != "failures"
            },
            baselines=baseline_summary,
        )
        feasibility[scale] = dict(
            normal_backgrounds=backgrounds,
            pre_violation=pre,
            pre_violation_status=dict(Counter(p["status"] for p in pre)),
            incomplete_observation_diagnostics=incomplete,
            limitations=[
                "retrospective diagnostics, not early-warning ground truth or feature selection",
                "overlapping windows and shared sources prohibit IID significance claims",
                "peer ablation and context contrasts use fixed splits and thresholds",
                "M2 measurements use the same IEC104 channel, no independent weather truth",
            ],
        )
    result = dict(
        version="0.5.1",
        evaluation_use="development_only",
        dataset_dir=str(root),
        previous_dataset=str(previous),
        eligible_runs=len(records),
        source_groups=read_json(root / "split/audit.json")["independent_groups"],
        prior_statistics=read_json(previous / "dataset_statistics.json"),
        scales=scales,
        simulation_added_s=0,
        supplementation_decision="existing sources provide normal and positive endpoints in all "
        "partitions/scales; retain limited W60 event support; no extra simulation in this release",
    )
    write_json(root / "research_feasibility.json", feasibility)
    write_json(root / "acceptance.json", result)
    lines = [
        "# V0.5.1 数据集质量修正验收",
        "",
        "日期为2026-11-27，评估用途为开发阶段。",
        "",
        f"本地新目录 `{root}`，有效运行数为{len(records)}。",
        "来源组和各尺度统计如下，未新增仿真。旧数据、阈值、划分、结果和原件保留。",
        "",
        "## 事件和样本覆盖",
        "",
        "各分区先按来源分组再采样。以下只计"
        f"{sum(r['site_count'] == 3 for r in records.values())}次三站运行；"
        f"{sum(r['site_count'] == 10 for r in records.values())}次十站另作共享来源诊断。",
        "",
        "| 尺度 | 分区 | 窗口 | 阳性 | 原始站点事件 | 可评估事件 | 可评估独立运行 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for scale, values in scales.items():
        for part, stats in values["statistics"]["primary_by_partition"].items():
            es = values["events"][part]
            lines.append(
                f"| {scale} | {part} | {stats['windows']} | {stats['positives']} | "
                f"{es['raw_events']} | {es['evaluable_events']} | {es['evaluable_event_runs']} |"
            )
    lines.extend(
        [
            "",
            "原版train/validation/test阳性为6/0/159，原始站点事件3/0/80；旧范围判断称1/0/31可评估，"
            "其中测试5段实际无端点。原版含十站，不能把其计数变化全部归于评价修正。"
            "新版将实际端点、在线可辨识、漏检及真实结束截尾分开。W60"
            "保留60秒/5秒，W10和W30均1秒步长，重叠率分别90%、96.67%、91.67%。",
            "",
            "## 固定基线与消融",
            "",
            "以下为实际三种子均值，确定性规则/EWMA一次。AP为非插值Average Precision，种子SD"
            "见JSON，不代表独立来源不确定性。所有阈值只用验证集，十站不参与拟合。",
            "",
            "| 尺度 | 特征 | 模型 | Precision | Recall | F1 | AP | 正常FPR | 误告警/站点小时 |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for scale, values in scales.items():
        for mode, summary in values["baselines"].items():
            for algorithm, stats in summary.items():
                if not stats or (algorithm == "business_rule" and mode != "M2"):
                    continue
                row = [
                    f"{stats[k]['mean']:.4f}" if k in stats else "NA"
                    for k in (
                        "precision",
                        "recall",
                        "f1",
                        "pr_auc_average_precision",
                        "normal_false_positive_rate",
                        "false_alerts_per_station_hour",
                    )
                ]
                lines.append(f"| {scale} | {mode} | {algorithm} | " + " | ".join(row) + " |")
    lines.extend(
        [
            "",
            "## 严格事件评价",
            "",
            "| 尺度 | 特征 | 模型 | 检出 | 可评估 | 原始 | 事件Recall | 在线起点平均延迟s |",
            "|---|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for scale, values in scales.items():
        for mode, summary in values["baselines"].items():
            for algorithm, stats in summary.items():
                if not stats or (algorithm == "business_rule" and mode != "M2"):
                    continue
                row = [
                    f"{stats[k]['mean']:.4f}" if k in stats else "NA"
                    for k in (
                        "detected_events",
                        "event_count",
                        "raw_event_count",
                        "event_recall",
                        "detection_delay_mean_s",
                    )
                ]
                lines.append(f"| {scale} | {mode} | {algorithm} | " + " | ".join(row) + " |")
    lines.extend(
        [
            "",
            "## 质量和研究限制",
            "",
            "三个尺度均须独立QC通过，逐表和原件SHA、共享划分、实际采样格点、标签映射和P0/主站"
            "固定抽样39项特征独立重算见各尺度QC文件。APDU重组可用时间逐帧引用核对，"
            "未采到真实重组的情况不能冒充实测重组覆盖，另有合成边界负测试。",
            "",
            "W60测试只有一个可评估独立事件，验证仅两个，因此不支持稳定六类性能结论。"
            "新划分改善训练及验证事件可用性，但不会创造独立来源。六次十站与训练共享天气日期"
            "和部分站点身份，只作规模诊断，不能称未见站点泛化。",
            "",
            "业务规则标签与规则高度同构，保留规则作为基线。without-rule-proxies删除9项"
            "规则相关及年龄代理，without-peer删除3项跨站特征，名单在报告中冻结。测试指标"
            "不能用来选择窗口、阈值、划分或模型。research_feasibility.json保留前兆可用性、"
            "正常背景描述和不完整观测分层；小来源及相关窗口不支持显著性、稳定跨站增益或"
            "早期预警有效性的确认。正式论文测试须增加从未参与开发决策的独立来源。",
            "",
            "NASA POWER历史小时背景加灰光学云影模型，非现场秒级实测；仿真假设和标签"
            "来源不变。未启动24/168小时、30站或任何机制复验。",
            "",
            "复现入口为scripts/build_v051_dataset.py，分步和读取见docs/v051-dataset.md。"
            "全部数据/PCAP/原始分数只保存在本地，代码、契约、测试和文档进入Git。",
            "",
        ]
    )
    (root / "ACCEPTANCE.md").write_text("\n".join(lines), encoding="utf-8")
    (root / "DATA_CARD.md").write_text(
        "# PVComm V0.5.1 开发评估数据\n\n" + "\n".join(lines[2:6]) + "\n\n"
        "来源清单runs.jsonl及split/source_profile.json，冻结划分split/assignment.json。"
        "W10/W30/W60下features-M2为白名单输入；labels/index/rules不进入X。"
        "三站训练须过滤site_count=3，十站独立诊断。完整限制和指标见ACCEPTANCE.md。"
        "旧版及所有失败留存，正式测试仍需未参与开发的来源。\n",
        encoding="utf-8",
    )
    return result
