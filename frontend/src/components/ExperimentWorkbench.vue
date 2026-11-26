<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  ElAlert,
  ElButton,
  ElInput,
  ElInputNumber,
  ElOption,
  ElSelect,
  ElTag,
} from 'element-plus'
import {
  getExperimentCatalog,
  getExperimentLabels,
  getExperimentEvidence,
  getExperimentInventory,
  experimentFileUrl,
  getExperimentQuality,
  getExperiments,
  submitExperiment,
  type ExperimentList,
  type ExperimentQuality,
  type ExperimentRequest,
  type ExperimentView,
  type LabelPage,
  type EvidenceView,
  type ExportInventory,
  type TemplateCatalog,
} from '../api/client'
import LabelTimeline from './LabelTimeline.vue'

const catalog = ref<TemplateCatalog | null>(null)
const listing = ref<ExperimentList | null>(null)
const quality = ref<ExperimentQuality | null>(null)
const labels = ref<LabelPage | null>(null)
const evidence = ref<EvidenceView | null>(null)
const inventory = ref<ExportInventory | null>(null)
const exportRun = ref('')
const evidenceOffset = ref(0)
const selected = ref('')
const selectedRun = ref('')
const offset = ref(0)
const labelOffset = ref(0)
const error = ref('')
const sending = ref(false)
const refreshing = ref(false)
const submitted = ref('')
const template = ref('N09')
const siteCount = ref<3 | 10 | 30>(3)
const variant = ref(0)
const seed = ref(900000)
const strength = ref(20)
const scope = ref<'station' | 'region'>('station')
const stations = ref('PV01')
const region = ref<'R1' | 'R2' | 'R3'>('R1')
const direction = ref<'master_to_rtu' | 'rtu_to_master'>('master_to_rtu')
const isFault = computed(() => catalog.value?.fault.includes(template.value))
const isPair = computed(() => catalog.value?.pairs.includes(template.value))
const levels = computed(() => catalog.value?.strengths[template.value] ?? [])
const current = computed(() =>
  listing.value?.experiments.find((e) => e.experiment_id === selected.value),
)
const captureHref = computed(() =>
  evidence.value?.capture
    ? experimentFileUrl(
        selected.value,
        selectedRun.value,
        String(evidence.value.capture.file_key),
      )
    : null,
)
const exportFiles = computed(
  () =>
    inventory.value?.files.map((file) => ({
      key: String(file.file_key),
      label: String(file.path),
      sha: String(file.sha256),
      size: file.size_bytes,
      href: experimentFileUrl(
        selected.value,
        exportRun.value,
        String(file.file_key),
      ),
    })) ?? [],
)
const exportEnd = computed(
  () =>
    !inventory.value || inventory.value.offset + 50 >= inventory.value.total,
)
const exportPage = (step: number) =>
  inspectExport(
    exportRun.value,
    Math.max(0, (inventory.value?.offset ?? 0) + step),
  )
const pretty = (value: unknown) => JSON.stringify(value, null, 2)
const requestLabel = (experiment: ExperimentView) =>
  `${experiment.request.template} · ${experiment.request.site_count} 站`
let timer: ReturnType<typeof setInterval> | undefined
let alive = true

watch(template, () => {
  strength.value = levels.value[0] ?? 20
  if (isPair.value) siteCount.value = 3
})

async function refresh() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    const data = await getExperiments(offset.value)
    if (alive) {
      listing.value = data
      error.value = ''
    }
  } catch (cause) {
    if (alive) error.value = String(cause)
  } finally {
    refreshing.value = false
  }
}

async function submit() {
  sending.value = true
  error.value = ''
  submitted.value = ''
  const request: ExperimentRequest = {
    template: template.value,
    seed: seed.value,
    site_count: siteCount.value,
    variant: variant.value,
    direction: 'master_to_rtu',
    scope: 'station',
    station_ids: ['PV01'],
    region_id: 'R1',
  }
  if (isFault.value) {
    request.strength = strength.value
    request.scope = scope.value
    if (scope.value === 'station') {
      request.station_ids = stations.value.split(',').map((s) => s.trim())
    } else request.region_id = region.value
    if (template.value === 'A02') request.direction = direction.value
  }
  try {
    const result = await submitExperiment(request)
    selected.value = result.experiment_id
    submitted.value = `${result.experiment_id} 已排队，等待独立实验执行器。`
    offset.value = 0
    await refresh()
  } catch (cause) {
    error.value = String(cause)
  } finally {
    sending.value = false
  }
}

async function inspect(experiment: ExperimentView) {
  selected.value = experiment.experiment_id
  selectedRun.value = ''
  quality.value = null
  labels.value = null
  evidence.value = null
  inventory.value = null
  error.value = ''
  try {
    const value = await getExperimentQuality(experiment.experiment_id)
    if (alive && selected.value === experiment.experiment_id)
      quality.value = value
  } catch (cause) {
    error.value = String(cause)
  }
}

async function inspectLabels(runKey: string, nextOffset = 0) {
  const identity = selected.value
  selectedRun.value = runKey
  labelOffset.value = nextOffset
  labels.value = null
  evidence.value = null
  evidenceOffset.value = 0
  try {
    const value = await getExperimentLabels(identity, runKey, nextOffset)
    if (
      alive &&
      selected.value === identity &&
      selectedRun.value === runKey &&
      labelOffset.value === nextOffset
    ) {
      labels.value = value
    }
  } catch (cause) {
    error.value = String(cause)
  }
}

async function inspectEvidence(link: Record<string, unknown>) {
  evidence.value = null
  const identity = selected.value
  const run = selectedRun.value
  try {
    const value = await getExperimentEvidence(
      identity,
      run,
      Number(link.label_ordinal),
      String(link.evidence_key),
    )
    if (alive && selected.value === identity && selectedRun.value === run)
      evidence.value = value
  } catch (cause) {
    error.value = String(cause)
  }
}

async function inspectExport(runKey: string, offset = 0) {
  inventory.value = null
  exportRun.value = runKey
  const identity = selected.value
  try {
    const value = await getExperimentInventory(identity, runKey, offset)
    if (alive && selected.value === identity && exportRun.value === runKey)
      inventory.value = value
  } catch (cause) {
    error.value = String(cause)
  }
}

const evidenceCaption = (link: Record<string, unknown>) => {
  const reference = link.reference as Record<string, unknown>
  return `${String(reference.path)} · ${String(reference.record_id ?? '文件证据')}`
}

async function page(next: number) {
  offset.value = next
  await refresh()
}

onMounted(async () => {
  try {
    catalog.value = await getExperimentCatalog()
    await refresh()
    if (alive) timer = setInterval(refresh, 5000)
  } catch (cause) {
    if (alive) error.value = String(cause)
  }
})
onUnmounted(() => {
  alive = false
  clearInterval(timer)
})
</script>

<template>
  <div class="experiment-workbench">
    <div class="page-heading">
      <div>
        <p class="eyebrow">ORACLE · RESEARCH DIAGNOSTICS</p>
        <h1>场景与实验队列</h1>
        <p>科研诊断入口：场景、注入及事后标签不属于 M2 在线观测。</p>
      </div>
      <ElButton :loading="refreshing" @click="refresh">刷新队列</ElButton>
    </div>
    <ElAlert
      title="队列由独立授权 Linux 执行器运行；关闭浏览器不会停止实验。完成状态与质量通过分别判断。"
      type="info"
      :closable="false"
      show-icon
    />
    <ElAlert v-if="error" :title="error" type="error" :closable="false" />
    <ElAlert
      v-if="catalog && !catalog.submission_enabled"
      title="服务端未配置实验队列目录，提交功能未启用。"
      type="warning"
      :closable="false"
    />
    <section class="editor-panel">
      <h2>创建开发验收实验</h2>
      <p>复用现有场景和固定强度。秒级云影为模型扰动，未做现场高频校准。</p>
      <form class="experiment-form" @submit.prevent="submit">
        <label>
          <span>场景模板</span>
          <ElSelect v-model="template" aria-label="场景模板">
            <ElOption
              v-for="code in catalog?.normal"
              :key="code"
              :value="code"
              :label="code"
            />
            <ElOption
              v-for="code in catalog?.fault"
              :key="code"
              :value="code"
              :label="code"
            />
            <ElOption
              v-for="code in catalog?.pairs"
              :key="code"
              :value="code"
              :label="code"
              :disabled="!catalog?.pair_submission_enabled"
            />
          </ElSelect>
        </label>
        <label>
          <span>站点数量</span>
          <ElSelect
            v-model="siteCount"
            aria-label="站点数量"
            :disabled="isPair"
          >
            <ElOption
              v-for="count in catalog?.site_counts"
              :key="count"
              :value="count"
              :label="String(count)"
            />
          </ElSelect>
        </label>
        <label>
          <span>历史背景变体</span>
          <ElInputNumber
            v-model="variant"
            aria-label="历史背景变体"
            :min="0"
            :max="2"
            :step="1"
            :precision="0"
          />
        </label>
        <label>
          <span>随机种子</span>
          <ElInputNumber
            v-model="seed"
            aria-label="随机种子"
            :min="0"
            :max="2147483647"
            :step="1"
            :precision="0"
          />
        </label>
        <template v-if="isFault">
          <label>
            <span>既定强度（A02 毫秒，A04/A08 周期，其余秒）</span>
            <ElSelect v-model="strength" aria-label="异常强度">
              <ElOption
                v-for="level in levels"
                :key="level"
                :value="level"
                :label="String(level)"
              />
            </ElSelect>
          </label>
          <label>
            <span>作用范围</span>
            <ElSelect v-model="scope" aria-label="作用范围">
              <ElOption value="station" label="站点或站点组" /><ElOption
                value="region"
                label="通信区域"
              />
            </ElSelect>
          </label>
          <label v-if="scope === 'station'">
            <span>站点编号（英文逗号分隔）</span>
            <ElInput v-model="stations" aria-label="目标站点" />
          </label>
          <label v-else>
            <span>通信区域</span>
            <ElSelect v-model="region" aria-label="通信区域">
              <ElOption
                v-for="r in siteCount === 3 ? ['R1', 'R2'] : ['R1', 'R2', 'R3']"
                :key="r"
                :value="r"
                :label="r"
              />
            </ElSelect>
          </label>
          <label v-if="template === 'A02'">
            <span>实际时延方向</span>
            <ElSelect v-model="direction" aria-label="时延方向">
              <ElOption value="master_to_rtu" label="主站 → 终端" /><ElOption
                value="rtu_to_master"
                label="终端 → 主站"
              />
            </ElSelect>
          </label>
        </template>
        <ElButton
          native-type="submit"
          type="primary"
          :loading="sending"
          :disabled="!catalog?.submission_enabled"
        >
          <span>提交到实验队列</span>
        </ElButton>
      </form>
      <p v-if="submitted" role="status">{{ submitted }}</p>
    </section>
    <section class="editor-panel">
      <h2>任务状态与独立运行覆盖</h2>
      <p>{{ listing?.total ?? 0 }} 项请求 · 每页 20 项 · 5 秒刷新</p>
      <p v-if="listing?.total === 0">尚无已提交实验。</p>
      <div
        v-for="experiment in listing?.experiments"
        :key="experiment.experiment_id"
        class="experiment-row"
      >
        <ElButton link type="primary" @click="inspect(experiment)">
          <span>{{ experiment.experiment_id }}</span>
        </ElButton>
        <ElTag>{{ experiment.status }}</ElTag>
        <span>{{ requestLabel(experiment) }}</span>
        <p v-if="experiment.error">{{ experiment.error }}</p>
      </div>
      <div class="editor-actions">
        <ElButton
          :disabled="offset === 0"
          @click="page(Math.max(0, offset - 20))"
        >
          <span>上一页</span>
        </ElButton>
        <ElButton
          :disabled="offset + 20 >= (listing?.total ?? 0)"
          @click="page(offset + 20)"
        >
          <span>下一页</span>
        </ElButton>
      </div>
      <div v-if="current">
        <h3>{{ current.experiment_id }}</h3>
        <p>
          当前状态：{{
            current.status
          }}。站点、标签和窗口数量不计作独立运行次数。
        </p>
        <pre class="diagnostic-json">{{
          pretty(current.queue ?? { status: 'queued' })
        }}</pre>
      </div>
    </section>
    <section v-if="quality" class="editor-panel">
      <h2>实际质量报告与分层标签</h2>
      <p v-if="quality.runs.length === 0">
        尚无运行原件；提交成功不代表实验已执行。
      </p>
      <article
        v-for="run in quality.runs"
        :key="run.run_key"
        class="experiment-row"
      >
        <h3>{{ run.run_id }} · {{ run.status }}</h3>
        <pre class="diagnostic-json">{{ pretty(run.reports) }}</pre>
        <ElButton @click="inspectLabels(run.run_key)">
          <span>查询标签时间与原始证据引用</span>
        </ElButton>
        <ElButton @click="inspectExport(run.run_key)">
          <span>生成只读导出清单</span>
        </ElButton>
      </article>
      <template v-if="labels">
        <h3>分层标签、业务期限、恢复时间及证据</h3>
        <p>
          纳秒以字符串保留；null 与未恢复截尾不作为正常负例。此处为事后诊断。
        </p>
        <LabelTimeline :labels="labels.labels" />
        <pre class="diagnostic-json" data-testid="diagnostic-labels">{{
          pretty(labels.labels)
        }}</pre>
        <ElButton
          :disabled="labelOffset === 0"
          @click="inspectLabels(selectedRun, Math.max(0, labelOffset - 10))"
        >
          <span>上一页标签</span>
        </ElButton>
        <ElButton
          :disabled="labelOffset + 10 >= labels.total"
          @click="inspectLabels(selectedRun, labelOffset + 10)"
        >
          <span>下一页标签</span>
        </ElButton>
        <h3>证据原记录定位</h3>
        <p>按标签引用的完整 SHA 校验来源；此查询为 Oracle 事后诊断。</p>
        <div
          v-for="link in labels.evidence_links?.slice(
            evidenceOffset,
            evidenceOffset + 20,
          )"
          :key="`${link.label_ordinal}-${link.evidence_key}`"
          class="evidence-link"
        >
          <ElButton link type="primary" @click="inspectEvidence(link)">
            <span>{{ evidenceCaption(link) }}</span>
          </ElButton>
        </div>
        <ElButton
          :disabled="evidenceOffset === 0"
          @click="evidenceOffset = Math.max(0, evidenceOffset - 20)"
        >
          上一页证据
        </ElButton>
        <ElButton
          :disabled="
            evidenceOffset + 20 >= (labels.evidence_links?.length ?? 0)
          "
          @click="evidenceOffset += 20"
        >
          下一页证据
        </ElButton>
        <div v-if="evidence" data-testid="evidence-record">
          <p>
            来源 SHA 核验：{{
              evidence.source_hash_verified ? '通过' : '未通过'
            }}
          </p>
          <pre class="diagnostic-json">{{ pretty(evidence) }}</pre>
          <a v-if="captureHref" :href="captureHref" download>下载 P0 PCAP</a>
        </div>
      </template>
      <div v-if="inventory" data-testid="export-inventory">
        <h3>运行原件导出 · {{ inventory.run_id }}</h3>
        <p>
          代码 {{ inventory.code_version }} · 原件保留 · 逐文件下载，SHA
          可核对；失败原件不作为有效样本。
        </p>
        <div class="diagnostic-json">
          <div v-for="file in exportFiles" :key="file.key" class="export-file">
            <a :href="file.href" download>{{ file.label }}</a>
            <small>{{ file.size }} B · SHA256 {{ file.sha }}</small>
          </div>
        </div>
        <ElButton :disabled="inventory.offset === 0" @click="exportPage(-50)">
          <span>上一页导出文件</span>
        </ElButton>
        <ElButton :disabled="exportEnd" @click="exportPage(50)">
          <span>下一页导出文件</span>
        </ElButton>
      </div>
    </section>
  </div>
</template>

<style scoped>
.experiment-workbench {
  overflow-wrap: anywhere;
}
.experiment-form {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px;
  margin: 20px 0;
}
.experiment-form label {
  display: grid;
  gap: 8px;
}
.experiment-form .el-input-number,
.experiment-form .el-select {
  width: 100%;
}
.experiment-row {
  border-bottom: 1px solid #e4e9f0;
  padding: 14px 0;
  overflow-wrap: anywhere;
}
.experiment-row .el-tag {
  margin: 0 12px;
}
.diagnostic-json {
  max-height: 420px;
  overflow: auto;
  background: #f6f8fb;
  padding: 12px;
  font-size: 12px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.evidence-link :deep(.el-button) {
  white-space: normal;
  height: auto;
  text-align: left;
}
.export-file {
  display: grid;
  gap: 4px;
  margin-bottom: 12px;
}
</style>
