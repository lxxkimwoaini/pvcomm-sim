<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ labels: Record<string, unknown>[] }>()
type Interval = {
  key: string
  title: string
  start: bigint
  end: bigint | null
  observable: bigint | null
  recovery: bigint | null
  layer: string
}
const stamp = (value: unknown): bigint | null =>
  typeof value === 'string' && /^\d+$/.test(value) ? BigInt(value) : null
const object = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
const intervals = computed(() => {
  const result: Interval[] = []
  const add = (
    key: string,
    title: string,
    layer: string,
    start: unknown,
    end: unknown,
    observable?: unknown,
    recovery?: unknown,
  ) => {
    const parsed = stamp(start)
    if (parsed !== null)
      result.push({
        key,
        title,
        layer,
        start: parsed,
        end: stamp(end),
        observable: stamp(observable),
        recovery: stamp(recovery),
      })
  }
  props.labels.forEach((label, i) => {
    const station = String(label.station_id ?? '未知站点')
    add(
      `${i}-fault`,
      `${station} · 故障作用`,
      'fault',
      label.fault_start_utc_ns,
      label.fault_end_utc_ns,
    )
    add(
      `${i}-impact`,
      `${station} · 通信影响`,
      'impact',
      label.communication_impact_start_utc_ns,
      label.communication_impact_end_utc_ns,
    )
    if (Array.isArray(label.findings))
      label.findings.forEach((value, j) => {
        const finding = object(value)
        const normal =
          finding.service_violation === false &&
          finding.label_status === 'valid'
        const resultTime = finding.recovery_utc_ns ?? finding.start_utc_ns
        add(
          `${i}-${j}`,
          `${station} · ${String(finding.outcome)}${normal ? '（正常业务结果）' : ''}`,
          finding.service_violation === true
            ? 'violation'
            : normal
              ? 'result'
              : 'unknown',
          normal ? resultTime : finding.start_utc_ns,
          normal ? resultTime : finding.end_utc_ns,
          normal ? null : finding.observable_at_utc_ns,
          normal ? null : finding.recovery_utc_ns,
        )
      })
  })
  return result
})
const bounds = computed(() => {
  const times = intervals.value.flatMap((r) =>
    [r.start, r.end, r.observable, r.recovery].filter(
      (v): v is bigint => v !== null,
    ),
  )
  const first = times.reduce((a, b) => (a < b ? a : b), times[0] ?? 0n)
  const last = times.reduce((a, b) => (a > b ? a : b), first + 1000000000n)
  return { first, last }
})
const x = (value: bigint) =>
  5 +
  Number(
    ((value - bounds.value.first) * 990n) /
      (bounds.value.last - bounds.value.first),
  )
const relative = (value: bigint) =>
  (Number(value - bounds.value.first) / 1e9).toFixed(3)
const intervalText = (row: Interval) =>
  row.layer === 'result'
    ? `${relative(row.start)} s · 正常业务结果`
    : `${relative(row.start)}–${row.end === null ? '未知 / 截尾' : relative(row.end)} s`
const utc = (value: bigint) => new Date(Number(value / 1000000n)).toISOString()
</script>

<template>
  <section class="label-timeline" data-testid="label-timeline">
    <h3>分层诊断时间轴</h3>
    <p>
      本页标签的 UTC
      时间；区间左闭右开。圆点为在线可判定时刻，菱形为有证据的业务恢复。虚线表示结束未知，不代表已经恢复。
    </p>
    <p v-if="intervals.length">起点 {{ utc(bounds.first) }} · 图上为相对秒</p>
    <p v-else>本页无可定位的作用区间或业务事件，未推断异常。</p>
    <div
      v-for="row in intervals.slice(0, 120)"
      :key="row.key"
      class="timeline-row"
    >
      <span>{{ row.title }}</span>
      <svg viewBox="0 0 1000 32" role="img" :aria-label="row.title">
        <line x1="5" x2="995" y1="16" y2="16" class="baseline" />
        <line
          :x1="x(row.start)"
          :x2="row.end === null ? 995 : x(row.end)"
          y1="16"
          y2="16"
          :class="[row.layer, { censored: row.end === null }]"
          class="interval"
        />
        <circle
          v-if="row.layer === 'result'"
          :cx="x(row.start)"
          cy="16"
          r="5"
          fill="#7594ac"
        />
        <circle
          v-if="row.observable !== null"
          :cx="x(row.observable)"
          cy="16"
          r="5"
          fill="#182b49"
        />
        <path
          v-if="row.recovery !== null"
          :transform="`translate(${x(row.recovery)},16)`"
          d="M 0 -7 L 7 0 L 0 7 L -7 0 Z"
          fill="#157658"
        />
      </svg>
      <small>{{ intervalText(row) }}</small>
    </div>
    <p v-if="intervals.length > 120">
      本页共
      {{ intervals.length }} 个区间，图示前120个；完整原件可导出，未修改标签。
    </p>
  </section>
</template>

<style scoped>
.label-timeline {
  margin: 18px 0;
}
.timeline-row {
  display: grid;
  grid-template-columns: minmax(150px, 1fr) minmax(160px, 3fr) minmax(
      100px,
      1fr
    );
  gap: 12px;
  align-items: center;
  overflow-wrap: anywhere;
}
svg {
  width: 100%;
  min-height: 32px;
}
.baseline {
  stroke: #e4e9f0;
  stroke-width: 1;
}
.interval {
  stroke-width: 7;
}
.fault {
  stroke: #8d66a9;
}
.impact {
  stroke: #d29b3d;
}
.violation {
  stroke: #c74a51;
}
.result {
  stroke: #7594ac;
}
.unknown {
  stroke: #8a8d94;
}
.censored {
  stroke-dasharray: 8 6;
}
@media (max-width: 650px) {
  .timeline-row {
    grid-template-columns: 1fr;
    gap: 0;
    margin-bottom: 14px;
  }
}
</style>
