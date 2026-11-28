<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElButton, ElOption, ElSelect, ElSlider, ElTag } from 'element-plus'
import { init, use, type EChartsType } from 'echarts/core'
import { LineChart, BarChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import {
  getPairs,
  getPairReplay,
  type PairList,
  type PairReplay,
} from '../api/client'
import { linePoints, reportOf, scalarRows } from '../pairReplay'

use([
  LineChart,
  BarChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
])
const listing = ref<PairList | null>(null)
const replay = ref<PairReplay | null>(null)
const key = ref('')
const station = ref('PV01')
const ioa = ref(1001)
const cursor = ref(0)
const offset = ref(0)
const loading = ref(false)
const error = ref('')
const scope = ref('global')
const direction = ref('up')
const distribution = ref('packet_length')
const curve = ref<HTMLDivElement | null>(null)
const histogram = ref<HTMLDivElement | null>(null)
const report = computed(() => reportOf(replay.value))
const current = computed(() =>
  listing.value?.pairs.find((p) => p.pair_key === key.value),
)
const scopes = computed(() => Object.keys(report.value?.scopes ?? {}))
const rows = computed(() =>
  scalarRows(replay.value, scope.value, direction.value),
)
const prettyNumber = (value: number | undefined) =>
  value === undefined
    ? '未知'
    : value.toLocaleString('zh-CN', { maximumFractionDigits: 6 })
const resultText = (value: boolean | null) =>
  value === null ? '未核验' : value ? '通过' : '未通过'
let charts: EChartsType[] = []
let resize: ResizeObserver | undefined
let generation = 0
let alive = true

async function refresh() {
  try {
    listing.value = await getPairs(offset.value)
  } catch (cause) {
    error.value = String(cause)
  }
}
async function load(reset = false) {
  if (!current.value) return
  const token = ++generation
  if (reset) {
    station.value = current.value.station_ids[0] ?? 'PV01'
    cursor.value = current.value.duration_s
  }
  loading.value = true
  replay.value = null
  error.value = ''
  try {
    const value = await getPairReplay(
      key.value,
      station.value,
      ioa.value,
      cursor.value,
    )
    if (alive && generation === token) {
      replay.value = value
      if (!scopes.value.includes(scope.value))
        scope.value = scopes.value[0] ?? 'global'
    }
  } catch (cause) {
    if (alive && generation === token) error.value = String(cause)
  } finally {
    if (generation === token) loading.value = false
  }
}
async function page(step: number) {
  generation++
  key.value = ''
  replay.value = null
  offset.value = Math.max(0, offset.value + step)
  await refresh()
}
async function draw() {
  await nextTick()
  if (!alive || !curve.value || !histogram.value) return
  if (!charts.length) charts = [init(curve.value), init(histogram.value)]
  charts[0]!.setOption(
    {
      tooltip: { trigger: 'axis' },
      legend: { top: 0 },
      grid: { left: 64, right: 20, top: 40, bottom: 45 },
      xAxis: {
        type: 'value',
        name: '场景秒',
        min: 0,
        max: current.value?.duration_s ?? 1,
      },
      yAxis: { type: 'value', name: replay.value?.unit ?? '', scale: true },
      series: (replay.value?.members ?? []).map((member) => ({
        name: member.member === 'normal' ? '正常成员' : '改变成员',
        type: 'line',
        showSymbol: false,
        connectNulls: false,
        data: linePoints(member, cursor.value),
      })),
    },
    { notMerge: true },
  )
  const normal =
    report.value?.summaries.normal?.summaries[scope.value]?.[direction.value]
      ?.distributions[distribution.value]
  const changed =
    report.value?.summaries.changed?.summaries[scope.value]?.[direction.value]
      ?.distributions[distribution.value]
  charts[1]!.setOption(
    {
      tooltip: { trigger: 'axis' },
      legend: { top: 0 },
      grid: { left: 64, right: 20, top: 40, bottom: 70 },
      xAxis: {
        type: 'category',
        axisLabel: { rotate: 30 },
        data:
          normal?.upper_bounds.map((v) => (v === null ? '溢出档' : `≤${v}`)) ??
          [],
      },
      yAxis: { type: 'value', name: '频数' },
      series: [
        { name: '正常成员', type: 'bar', data: normal?.counts ?? [] },
        { name: '改变成员', type: 'bar', data: changed?.counts ?? [] },
      ],
    },
    { notMerge: true },
  )
}
watch([replay, scope, direction, distribution], draw)
onMounted(async () => {
  resize = new ResizeObserver(() => charts.forEach((c) => c.resize()))
  if (curve.value) resize.observe(curve.value)
  if (histogram.value) resize.observe(histogram.value)
  await refresh()
})
onUnmounted(() => {
  alive = false
  generation++
  resize?.disconnect()
  charts.forEach((c) => c.dispose())
})
</script>

<template>
  <section class="editor-panel pair-replay" data-testid="pair-replay">
    <h2>正常—异常配对回放</h2>
    <p>
      Oracle
      事后诊断。曲线仅使用各成员在游标前实际收到的遥测；质量与匹配结论为全运行核验。
    </p>
    <p v-if="error" role="alert">{{ error }}</p>
    <div class="pair-controls">
      <label>
        <span>配对记录</span>
        <ElSelect v-model="key" aria-label="配对记录" @change="load(true)">
          <ElOption
            v-for="pair in listing?.pairs"
            :key="pair.pair_key"
            :value="pair.pair_key"
            :label="`${pair.pair_type} · ${pair.pair_id} · ${pair.status}`"
          />
        </ElSelect>
      </label>
      <label>
        <span>回放站点</span>
        <ElSelect v-model="station" aria-label="配对回放站点" @change="load()">
          <ElOption
            v-for="s in current?.station_ids"
            :key="s"
            :value="s"
            :label="s"
          />
        </ElSelect>
      </label>
      <label>
        <span>遥测指标</span>
        <ElSelect v-model="ioa" aria-label="配对遥测指标" @change="load()">
          <ElOption :value="1001" label="实际有功遥测" /><ElOption
            :value="1005"
            label="可用功率遥测"
          />
          <ElOption :value="1006" label="终端已接受目标遥测" /><ElOption
            :value="1004"
            label="辐照度遥测"
          />
          <ElOption :value="1003" label="并网点电压遥测" />
        </ElSelect>
      </label>
    </div>
    <ElButton :disabled="offset === 0" @click="page(-20)">上一页配对</ElButton>
    <ElButton
      :disabled="offset + 20 >= (listing?.total ?? 0)"
      @click="page(20)"
    >
      下一页配对
    </ElButton>
    <ElButton @click="refresh">刷新配对列表</ElButton>
    <p v-if="listing?.total === 0">
      尚无配对原件。可提交配对任务，或由服务端配置只读历史批次根目录。
    </p>
    <div v-if="current" data-testid="pair-results">
      <p>
        数据质量
        <ElTag>{{ resultText(current.data_quality_passed) }}</ElTag> 业务对照
        <ElTag>{{ resultText(current.business_contrast) }}</ElTag> 统计匹配
        <ElTag>{{ resultText(current.statistical_match) }}</ElTag>
      </p>
      <label>
        <span>场景时间游标（秒）：{{ cursor }} / {{ current.duration_s }}</span>
        <ElSlider
          v-model="cursor"
          :max="current.duration_s"
          :step="1"
          :disabled="loading"
          aria-label="配对场景时间游标"
          @change="load()"
        />
      </label>
      <p v-if="loading">正在查询原始观测索引…</p>
      <p
        v-for="member in replay?.members"
        :key="member.run_id"
        class="member-state"
      >
        {{ member.member === 'normal' ? '正常成员' : '改变成员' }} ·
        {{ member.run_id }} · 最新值
        {{ member.latest_value === null ? '未知/缺失' : member.latest_value }}
        {{ replay?.unit }} · 状态 {{ member.status }}
      </p>
    </div>
    <div ref="curve" class="pair-chart" role="img" aria-label="配对遥测曲线" />
    <p v-if="replay?.traffic_unavailable_reason">
      {{ replay.traffic_unavailable_reason }}
    </p>
    <div v-if="report" class="pair-controls">
      <label>
        <span>固定统计范围</span>
        <ElSelect v-model="scope" aria-label="配对统计范围">
          <ElOption
            v-for="s in scopes"
            :key="s"
            :value="s"
            :label="s === 'global' ? '全局' : s"
          />
        </ElSelect>
      </label>
      <label>
        <span>流量方向</span>
        <ElSelect v-model="direction" aria-label="配对流量方向">
          <ElOption value="up" label="上行" />
          <ElOption value="down" label="下行" />
        </ElSelect>
      </label>
      <label>
        <span>统计分布</span>
        <ElSelect v-model="distribution" aria-label="配对统计分布">
          <ElOption value="packet_length" label="包长（字节）" /><ElOption
            value="iat_s"
            label="包间隔（秒）"
          /><ElOption value="packet_rate_s" label="每秒包数" />
        </ElSelect>
      </label>
    </div>
    <div v-if="report" class="pair-table" data-testid="pair-traffic-table">
      <p>
        固定窗口
        {{ report.summaries.normal?.window_s.join('～') }}
        秒。未匹配样本保留；此处不重新计算或调整阈值。
      </p>
      <table>
        <thead>
          <tr>
            <th>统计项</th>
            <th>正常成员</th>
            <th>改变成员</th>
            <th>固定距离</th>
            <th>匹配</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.feature">
            <td>{{ row.feature }}</td>
            <td>{{ prettyNumber(row.normal) }}</td>
            <td>{{ prettyNumber(row.changed) }}</td>
            <td>{{ prettyNumber(row.distance) }}</td>
            <td>
              {{
                row.unmatched === undefined
                  ? '未核验'
                  : row.unmatched
                    ? '未匹配'
                    : '匹配'
              }}
            </td>
          </tr>
        </tbody>
      </table>
      <p>
        策略 {{ report.policy_sha256 }} · 校准 {{ report.calibration_sha256 }}
      </p>
    </div>
    <div
      ref="histogram"
      class="pair-chart"
      role="img"
      aria-label="配对流量分布"
    />
    <p v-if="replay">{{ replay.limitations }}</p>
  </section>
</template>

<style scoped>
.pair-replay {
  overflow-wrap: anywhere;
}
.pair-controls {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px;
  margin: 16px 0;
}
.pair-controls label {
  display: grid;
  gap: 8px;
  min-width: 0;
}
.pair-chart {
  width: 100%;
  min-width: 0;
  height: 320px;
}
.pair-table {
  overflow: auto;
  margin: 16px 0;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
td,
th {
  padding: 8px;
  border-bottom: 1px solid #e4e9f0;
  text-align: left;
  white-space: nowrap;
}
.member-state {
  font-size: 13px;
}
</style>
