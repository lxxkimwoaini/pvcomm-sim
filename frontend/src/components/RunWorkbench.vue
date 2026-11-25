<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  ElAlert,
  ElButton,
  ElOption,
  ElSelect,
  ElSlider,
  ElTag,
} from 'element-plus'
import {
  init,
  use,
  type EChartsCoreOption,
  type EChartsType,
} from 'echarts/core'
import { LineChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import {
  getRuns,
  getSnapshot,
  getEvents,
  getResources,
  type EventsPage,
  type ResourceView,
  type RunList,
  type RunSnapshot,
} from '../api/client'

use([
  LineChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
])
const runs = ref<NonNullable<RunList['runs']>>([])
const key = ref('')
const snapshot = ref<RunSnapshot | null>(null)
const error = ref('')
const busy = ref(false)
const offsetSeconds = ref(0)
const live = ref(true)
const selectedRegion = ref('all')
const eventPage = ref<EventsPage | null>(null)
const resources = ref<ResourceView | null>(null)
const visibleStations = computed(
  () =>
    snapshot.value?.stations.filter(
      (s) =>
        selectedRegion.value === 'all' || s.region_id === selectedRegion.value,
    ) ?? [],
)
const visibleEvents = computed(
  () => eventPage.value?.events ?? snapshot.value?.events ?? [],
)
const feeders = computed(() => [
  ...new Set(
    snapshot.value?.stations
      .map((s) => s.feeder_id)
      .filter((s): s is string => !!s) ?? [],
  ),
])
const feederWidth = computed(() =>
  Math.max(
    1160,
    160 +
      Math.max(0, ...feeders.value.map((id) => feederSites(id).length)) * 100,
  ),
)
const measurement = (station: RunSnapshot['stations'][number], ioa: number) =>
  station.measurements?.find((m) => m.ioa === ioa)?.points.at(-1)?.value
const feederSites = (id: string) =>
  snapshot.value?.stations.filter((s) => s.feeder_id === id) ?? []
const space = (value: number | null | undefined, axis: 'x_m' | 'y_m') => {
  const values = snapshot.value?.stations.map((s) => s[axis] ?? 0) ?? [0]
  const low = Math.min(...values),
    high = Math.max(...values)
  return (
    45 +
    (((value ?? low) - low) / Math.max(1, high - low)) *
      (axis === 'x_m' ? 900 : 260)
  )
}
const chartNode = ref<HTMLDivElement>()
const physicalNode = ref<HTMLDivElement>()
const physicalStation = ref('PV01')
const physicalMetric = ref('power')
let physicalChart: EChartsType | undefined
let chart: EChartsType | undefined
let timer: ReturnType<typeof setInterval> | undefined
let generation = 0
let resize: ResizeObserver | undefined
const durationSeconds = computed(() =>
  snapshot.value
    ? Number(
        (BigInt(snapshot.value.last_available_at) -
          BigInt(snapshot.value.first_available_at)) /
          1000000000n,
      )
    : 0,
)
const date = (ns: string) =>
  new Date(Number(BigInt(ns) / 1000000n)).toLocaleTimeString('zh-CN', {
    hour12: false,
  })
function eventContent(event: RunSnapshot['events'][number]): string {
  if (event.state) return event.state
  if (event.action) return event.action
  return event.ioa ? `IOA ${event.ioa} = ${event.value}` : '—'
}
function draw() {
  if (!chartNode.value || !snapshot.value) return
  if (chart && chart.getDom() !== chartNode.value) {
    chart.dispose()
    chart = undefined
  }
  chart ??= init(chartNode.value)
  resize?.observe(chartNode.value)
  if (physicalNode.value) resize?.observe(physicalNode.value)
  const option: EChartsCoreOption = {
    animation: false,
    tooltip: { trigger: 'axis' },
    legend: { top: 0, type: 'scroll' },
    grid: { left: 55, right: 20, top: 40, bottom: 35 },
    xAxis: { type: 'value', name: 's' },
    yAxis: { type: 'value', name: 'kW', min: 0 },
    series: visibleStations.value.map((station) => {
      const points: (number | null)[][] = []
      let previous = -1
      for (const point of station.power) {
        if (previous >= 0 && point.gap_before)
          points.push([
            (previous + station.telemetry_period_s * 1000) / 1000,
            null,
          ])
        points.push([point.sim_elapsed_ms / 1000, point.value])
        previous = point.sim_elapsed_ms
      }
      return {
        name: station.station_id,
        type: 'line',
        showSymbol: false,
        connectNulls: false,
        data: points,
      }
    }),
  }
  chart.setOption(option, true)
  drawPhysical()
}
function drawPhysical() {
  if (!physicalNode.value || !snapshot.value) return
  if (physicalChart && physicalChart.getDom() !== physicalNode.value) {
    physicalChart.dispose()
    physicalChart = undefined
  }
  physicalChart ??= init(physicalNode.value)
  const station =
    snapshot.value.stations.find(
      (s) => s.station_id === physicalStation.value,
    ) ?? snapshot.value.stations[0]
  if (!station) return
  const selected =
    physicalMetric.value === 'power'
      ? [1001, 1006, 1008]
      : physicalMetric.value === 'irradiance'
        ? [1005]
        : [1003]
  const names: Record<number, string> = {
    1001: '实际有功遥测',
    1006: '已接受调控目标',
    1008: '可用功率遥测',
    1005: '辐照度遥测',
    1003: '并网点线电压',
  }
  const measurements = (station.measurements ?? []).filter((m) =>
    selected.includes(m.ioa),
  )
  physicalChart.setOption(
    {
      animation: false,
      tooltip: { trigger: 'axis' },
      legend: { top: 0 },
      grid: { left: 60, right: 25, top: 45, bottom: 35 },
      xAxis: { type: 'value', name: 's' },
      yAxis: { type: 'value', name: measurements[0]?.unit ?? '', scale: true },
      series: measurements.map((m) => {
        const data: (number | null)[][] = []
        let previous = -1
        for (const p of m.points) {
          if (previous >= 0 && p.gap_before)
            data.push([
              (previous + station.telemetry_period_s * 1000) / 1000,
              null,
            ])
          data.push([p.sim_elapsed_ms / 1000, p.value])
          previous = p.sim_elapsed_ms
        }
        return {
          name: names[m.ioa],
          type: 'line',
          showSymbol: false,
          connectNulls: false,
          step: m.ioa === 1006 ? 'end' : false,
          data,
        }
      }),
    },
    true,
  )
}
async function loadSnapshot(replay = false) {
  if (!key.value || (busy.value && !replay)) return
  const version = ++generation
  busy.value = true
  error.value = ''
  try {
    const cursor =
      replay && snapshot.value
        ? (
            BigInt(snapshot.value.first_available_at) +
            BigInt(offsetSeconds.value) * 1000000000n
          ).toString()
        : undefined
    const result = await getSnapshot(key.value, cursor)
    if (version !== generation) return
    snapshot.value = result
    eventPage.value = null
    await nextTick()
    draw()
  } catch (cause) {
    if (version === generation)
      error.value = cause instanceof Error ? cause.message : '读取失败'
  } finally {
    if (version === generation) busy.value = false
  }
}
async function olderEvents() {
  const oldest = visibleEvents.value[0]
  if (!oldest || !snapshot.value) return
  live.value = false
  try {
    eventPage.value = await getEvents(
      key.value,
      snapshot.value.cursor_utc_ns,
      oldest.available_at,
      oldest.event_id,
    )
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '分页读取失败'
  }
}
async function loadResources() {
  try {
    resources.value = await getResources(key.value)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '资源读取失败'
  }
}
async function refreshRuns() {
  error.value = ''
  try {
    runs.value = (await getRuns()).runs ?? []
    if (!runs.value.some((run) => run.run_key === key.value))
      key.value = runs.value[0]?.run_key ?? ''
    await loadSnapshot()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '读取失败'
  }
}
function changeRun() {
  generation++
  busy.value = false
  snapshot.value = null
  eventPage.value = null
  resources.value = null
  selectedRegion.value = 'all'
  offsetSeconds.value = 0
  live.value = true
  void loadSnapshot()
}
function replay() {
  live.value = false
  void loadSnapshot(true)
}
function follow() {
  live.value = true
  void loadSnapshot()
}
onMounted(async () => {
  await refreshRuns()
  timer = setInterval(() => {
    if (live.value) void loadSnapshot()
  }, 1000)
  resize = new ResizeObserver(() => {
    chart?.resize()
    physicalChart?.resize()
  })
  if (chartNode.value) resize.observe(chartNode.value)
  if (physicalNode.value) resize.observe(physicalNode.value)
})
onBeforeUnmount(() => {
  generation++
  if (timer) clearInterval(timer)
  resize?.disconnect()
  chart?.dispose()
  physicalChart?.dispose()
})
</script>

<template>
  <div class="page-heading">
    <div>
      <p class="eyebrow">PROTOCOL OBSERVATORY</p>
      <h1>运行与回放</h1>
      <p>主站可观测数据 · M2 · 真实 c104 / TCP / IEC 104</p>
    </div>
    <ElButton @click="refreshRuns">刷新运行列表</ElButton>
  </div>
  <ElAlert v-if="error" :title="error" type="error" :closable="false" />
  <section class="editor-panel run-controls">
    <label for="run-selector">选择运行</label>
    <ElSelect
      id="run-selector"
      v-model="key"
      placeholder="暂无独立端点运行"
      :disabled="!runs.length"
      @change="changeRun"
    >
      <ElOption
        v-for="run in runs"
        :key="run.run_key"
        :value="run.run_key"
        :label="`${run.run_key} · ${run.status}`"
      />
    </ElSelect>
    <ElTag>M2 只读</ElTag
    ><ElButton :disabled="!key || busy" @click="follow">跟随最新数据</ElButton>
    <p v-if="!runs.length">
      未发现真实运行。请在服务端配置运行输出根，并通过 CLI 启动授权实验。
    </p>
  </section>
  <template v-if="snapshot">
    <ElAlert :title="snapshot.limitations" type="info" :closable="false" />
    <section class="status-strip" aria-label="运行状态">
      <div><span>数据来源</span><strong>真实协议仿真</strong></div>
      <div><span>观测模式</span><strong>M2 主站</strong></div>
      <div>
        <span>游标 UTC 对应本地时间</span
        ><strong>{{ date(snapshot.cursor_utc_ns) }}</strong>
      </div>
      <div>
        <span>显示方式</span
        ><strong>{{ live ? '最新观测' : '因果回放' }}</strong>
      </div>
    </section>
    <section class="editor-panel">
      <h2>回放游标</h2>
      <ElSlider
        v-model="offsetSeconds"
        :max="Math.max(1, durationSeconds)"
        :step="1"
        aria-label="回放秒数"
        @change="replay"
      />
      <p>起点后 {{ offsetSeconds }} 秒；仅显示游标时刻已到达主站的信息。</p>
    </section>
    <section class="editor-panel">
      <h2>区域通信拓扑</h2>
      <div class="station-grid">
        <article
          v-for="region in snapshot.regions ?? []"
          :key="region.region_id"
          class="station-card"
        >
          <h3>{{ region.region_id }} · 汇聚链路</h3>
          <p>连接 {{ region.open_connections }} / {{ region.stations }}</p>
          <strong
            >{{ region.observed_power_kw?.toFixed(2) ?? '未知' }} kW</strong
          >
          <p>
            有效功率 {{ region.valid_power_stations }} 站 · 容量覆盖
            {{ (region.capacity_coverage_fraction * 100).toFixed(1) }}%
          </p>
        </article>
      </div>
      <div class="run-controls">
        <ElSelect v-model="selectedRegion" aria-label="通信区域" @change="draw">
          <ElOption label="全部区域" value="all" />
          <ElOption
            v-for="region in snapshot.regions ?? []"
            :key="region.region_id"
            :label="region.region_id"
            :value="region.region_id"
          />
        </ElSelect>
      </div>
      <div class="topology-master">IEC 104 主站</div>
      <div class="station-grid topology-cards">
        <article
          v-for="station in visibleStations"
          :key="station.station_id"
          class="station-card"
        >
          <span class="link-label"
            >主站 → {{ station.region_id }} 汇聚 → 站点接入</span
          >
          <h3>{{ station.station_id }} · {{ station.region_id }}</h3>
          <p>CA {{ station.common_address }} · {{ station.capacity_kw }} kW</p>
          <ElTag
            :type="station.connection_state === 'OPEN' ? 'success' : 'info'"
          >
            {{ station.connection_state ?? '未知' }}
          </ElTag>
          <p class="power-reading">
            {{ station.latest_power_kw?.toFixed(2) ?? '未知' }}
            <small>kW</small>
          </p>
          <ElTag v-if="!station.power_fresh" type="warning">
            功率未知或已过期
          </ElTag>
          <p>
            {{
              station.power_available_at
                ? `最近接收 ${date(station.power_available_at)}`
                : '尚无功率观测'
            }}
          </p>
        </article>
      </div>
    </section>
    <section v-if="feeders.length" class="editor-panel">
      <h2>电气馈线拓扑</h2>
      <p>
        配置定义的合成工程馈线，与通信区域独立；线路数值为主站实际收到的同链路遥测。
      </p>
      <div class="table-scroll">
        <svg
          class="feeder-svg"
          :viewBox="`0 0 ${feederWidth} ${feeders.length * 120}`"
          role="img"
          aria-label="配置驱动电气馈线"
        >
          <g
            v-for="(feeder, row) in feeders"
            :key="feeder"
            :transform="`translate(0,${row * 120})`"
          >
            <rect
              x="5"
              y="25"
              width="90"
              height="40"
              fill="#edf5fc"
              stroke="#86acc6"
            />
            <text x="50" y="50" text-anchor="middle">{{ feeder }}</text>
            <g
              v-for="(site, col) in feederSites(feeder)"
              :key="site.station_id"
            >
              <line
                :x1="95 + col * 100"
                y1="45"
                :x2="135 + col * 100"
                y2="45"
                :stroke="
                  measurement(site, 1009) === undefined ? '#b8c2cb' : '#4282a6'
                "
              />
              <rect
                :x="135 + col * 100"
                y="25"
                width="60"
                height="40"
                fill="#f5fafc"
                stroke="#86acc6"
              />
              <text :x="165 + col * 100" y="49" text-anchor="middle">
                {{ site.station_id }}
              </text>
              <text :x="165 + col * 100" y="82" text-anchor="middle">
                {{ measurement(site, 1009)?.toFixed(1) ?? '未知' }}%
              </text>
              <text :x="165 + col * 100" y="101" text-anchor="middle">
                {{ measurement(site, 1003)?.toFixed(3) ?? '未知' }} kV
              </text>
            </g>
          </g>
        </svg>
      </div>
    </section>
    <section
      v-if="snapshot.stations.some((s) => s.x_m != null)"
      class="editor-panel"
    >
      <h2>空间气象与站点响应</h2>
      <p>
        局部东/北坐标（m），颜色来自已收到的辐照度；灰色表示未知。秒级扰动为物理模型，背景为历史小时产品。
      </p>
      <svg viewBox="0 0 1000 350" role="img" aria-label="站点辐照度空间分布">
        <g
          v-for="site in snapshot.stations"
          :key="site.station_id"
          :transform="`translate(${space(site.x_m, 'x_m')},${space(site.y_m, 'y_m')})`"
        >
          <circle
            r="12"
            :fill="
              measurement(site, 1005) === undefined
                ? '#b8c2cb'
                : `hsl(40 85% ${75 - Math.min(1200, measurement(site, 1005) ?? 0) / 40}%)`
            "
          />
          <text y="26" text-anchor="middle" font-size="11">
            {{ site.station_id }} ·
            {{ site.latest_power_kw?.toFixed(1) ?? '未知' }}kW
          </text>
          <title>
            {{ site.station_id }} ({{ site.x_m }}, {{ site.y_m }})m ·
            {{ measurement(site, 1005)?.toFixed(1) ?? '未知' }} W/m²
          </title>
        </g>
      </svg>
    </section>
    <section class="editor-panel">
      <h2>区域调控与协议统计</h2>
      <p>
        发送回调
        {{ snapshot.protocol_statistics?.master_send_callbacks ?? 0 }} 次 /
        接收回调
        {{ snapshot.protocol_statistics?.master_receive_callbacks ?? 0 }} 次；{{
          snapshot.protocol_statistics?.master_receive_bytes ?? 0
        }}
        B 接收。回调次数不等同于独立解析后的 APDU 数。
      </p>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>任务</th>
              <th>区域</th>
              <th>分配 / 请求 kW</th>
              <th>资源缺口 kW</th>
              <th>确认 / 站点</th>
              <th>传输失败</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="task in snapshot.dispatches ?? []"
              :key="task.dispatch_id"
            >
              <td>{{ task.dispatch_id }}</td>
              <td>{{ task.regions.join(', ') }}</td>
              <td>
                {{ task.allocated_kw.toFixed(1) }} /
                {{ task.requested_kw.toFixed(1) }}
              </td>
              <td>{{ task.unallocated_kw.toFixed(1) }}</td>
              <td>{{ task.positive_confirmations }} / {{ task.stations }}</td>
              <td>{{ task.failed_transmissions }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p>确认是主站收到的协议响应；不据此冒充电气执行完成。</p>
      <ElButton @click="loadResources">读取实验资源诊断</ElButton>
      <p v-if="resources">
        独立基础设施诊断（最新时刻，不随 M2 游标回放）：CPU
        {{
          resources.cpu_percent_one_core?.toFixed(1) ?? '未知'
        }}%（单核100%）；RSS
        {{
          resources.rss_bytes == null
            ? '未知'
            : (resources.rss_bytes / 1048576).toFixed(1)
        }}
        MiB；时钟映射偏差
        {{
          resources.utc_mapping_drift_ms?.toFixed(3) ?? '未知'
        }}ms。范围为仿真/抓包进程，不含浏览器与API。
      </p>
    </section>
    <section class="editor-panel">
      <h2>主站接收有功曲线</h2>
      <p>
        横轴：逻辑时间 s。最多读取最近 600 个观测；多站场景按 min/max
        降采样至120点，保留首尾及缺口；仅影响展示。
      </p>
      <div
        ref="chartNode"
        class="power-chart"
        role="img"
        aria-label="多站主站功率遥测曲线"
      />
    </section>
    <section class="editor-panel">
      <h2>光伏电气过程</h2>
      <p>
        数据均为同一 IEC 104
        链路上已到达主站的遥测；缺测保持未知，不从电气真值补齐。
      </p>
      <div class="run-controls">
        <ElSelect
          v-model="physicalStation"
          aria-label="电气曲线站点"
          @change="drawPhysical"
        >
          <ElOption
            v-for="station in snapshot.stations"
            :key="station.station_id"
            :label="station.station_id"
            :value="station.station_id"
          />
        </ElSelect>
        <ElSelect
          v-model="physicalMetric"
          aria-label="电气曲线指标"
          @change="drawPhysical"
        >
          <ElOption label="实际 / 可用 / 调控目标 (kW)" value="power" />
          <ElOption label="辐照度 (W/m²)" value="irradiance" />
          <ElOption label="并网点线电压 (kV)" value="voltage" />
        </ElSelect>
      </div>
      <div
        ref="physicalNode"
        class="power-chart"
        role="img"
        aria-label="光伏电气遥测曲线"
      />
      <div class="station-grid">
        <article
          v-for="station in snapshot.stations"
          :key="station.station_id"
          class="station-card"
        >
          <h3>{{ station.station_id }} · 已接收电气量</h3>
          <p v-for="m in station.measurements ?? []" :key="m.ioa">
            {{ m.name }}：{{ m.points.at(-1)?.value.toFixed(3) ?? '未知' }}
            {{ m.unit }}
          </p>
        </article>
      </div>
    </section>
    <section class="editor-panel">
      <h2>主站业务事件</h2>
      <p>每页最多100条主站事件，按 available_at 截断并以时间/ID联合分页。</p>
      <ElButton :disabled="!visibleEvents.length" @click="olderEvents">
        较早100条
      </ElButton>
      <ElButton @click="eventPage = null">当前游标最近100条</ElButton>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>接收时间</th>
              <th>站点</th>
              <th>事件</th>
              <th>内容</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="event in [...visibleEvents].reverse()"
              :key="event.event_id"
            >
              <td>{{ date(event.available_at) }}</td>
              <td>{{ event.station_id ?? '—' }}</td>
              <td>{{ event.event_type }}</td>
              <td>
                {{ eventContent(event) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
    <section class="editor-panel">
      <h2>P0 IEC 104 报文</h2>
      <p v-if="snapshot.protocol_status === 'not_yet_parsed'">
        尚未解析 PCAP。实时事件仍来自实际主站；运行完成后执行 parse 可查看报文。
      </p>
      <div v-else class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>抓包可用时间</th>
              <th>帧</th>
              <th>类型</th>
              <th>COT</th>
              <th>CA</th>
              <th>Tx / Rx</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="apdu in [...snapshot.protocol].reverse()"
              :key="apdu.apdu_id"
            >
              <td>{{ date(apdu.available_at) }}</td>
              <td>{{ apdu.frame_type }}</td>
              <td>{{ apdu.type_id ?? '—' }}</td>
              <td>{{ apdu.cot ?? '—' }}</td>
              <td>{{ apdu.ca ?? '—' }}</td>
              <td>{{ apdu.send_seq ?? '—' }} / {{ apdu.recv_seq ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </template>
</template>

<style scoped>
.run-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 14px;
}
.run-controls .el-select {
  width: min(340px, 100%);
}
.topology-master {
  margin: 12px auto 0;
  padding: 16px;
  text-align: center;
  border: 1px solid #b6cce0;
  border-radius: 8px;
  background: #edf5fc;
  max-width: 260px;
  font-weight: 600;
}
.station-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(230px, 100%), 1fr));
  gap: 20px;
}
.feeder-svg {
  width: 100%;
  min-width: 900px;
  font-size: 12px;
}
.station-card {
  min-width: 0;
  text-align: center;
  padding: 18px 10px;
  border: 1px solid #dce5ed;
  border-radius: 8px;
}
.link-label {
  display: block;
  color: #60758a;
  border-top: 2px solid #adc8dd;
  padding-top: 14px;
}
.power-reading {
  font-size: 26px;
  font-weight: 650;
  color: #185d86;
}
.power-reading small {
  font-size: 13px;
}
.power-chart {
  width: 100%;
  height: 300px;
  min-width: 0;
  overflow: hidden;
}
.table-scroll {
  overflow-x: auto;
  max-height: 370px;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  white-space: nowrap;
}
td,
th {
  text-align: left;
  padding: 10px 14px;
  border-bottom: 1px solid #e5ebf1;
}
th {
  background: #f5f8fb;
  position: sticky;
  top: 0;
}
@media (max-width: 760px) {
  .station-grid {
    grid-template-columns: 1fr;
  }
}
</style>
