<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElAlert, ElButton, ElInput, ElTag } from 'element-plus'
import { getHealth, validateConfig, type ValidationResult } from './api/client'
import RunWorkbench from './components/RunWorkbench.vue'

const serviceStatus = ref('尚未检查')
const serviceError = ref('')
const yamlText = ref('')
const pending = ref(false)
const result = ref<ValidationResult | null>(null)
const validationError = ref('')
const page = ref<'config' | 'runs'>('config')

async function refreshHealth() {
  serviceError.value = ''
  serviceStatus.value = '检查中'
  try {
    const health = await getHealth()
    serviceStatus.value = `${health.stage} · 服务可用`
  } catch (error) {
    serviceStatus.value = '服务不可用'
    serviceError.value =
      error instanceof Error ? error.message : '无法连接本地服务'
  }
}

async function validate() {
  pending.value = true
  result.value = null
  validationError.value = ''
  try {
    result.value = await validateConfig(yamlText.value)
  } catch (error) {
    validationError.value = error instanceof Error ? error.message : '校验失败'
  } finally {
    pending.value = false
  }
}

function clearResult() {
  result.value = null
  validationError.value = ''
}

onMounted(refreshHealth)
</script>

<template>
  <div class="workbench">
    <header class="topbar">
      <a class="brand" href="/" aria-label="PVComm 首页">
        <span class="brand-mark" aria-hidden="true">PV</span>
        <span>PVComm <small>联合仿真实验平台</small></span>
      </a>
      <ElTag type="info" effect="plain">V0.2 开发 · 联合仿真</ElTag>
    </header>

    <div class="workspace">
      <aside class="sidebar">
        <p class="nav-label">实验准备</p>
        <button class="nav-current" @click="page = 'config'">配置校验</button>
        <button class="nav-current" @click="page = 'runs'">运行与回放</button>
        <p class="phase-note">
          只读监测真实 IEC 104 实验。运行由 CLI 独立管理。
        </p>
        <div class="mode-note">
          主实验规划<br /><strong>M2 业务感知</strong>
        </div>
      </aside>

      <main v-if="page === 'config'">
        <div class="page-heading">
          <div>
            <p class="eyebrow">PROJECT FOUNDATION</p>
            <h1>工程配置校验</h1>
            <p>检查参数、站点引用与时间约束，为后续实验准备一致的配置。</p>
          </div>
          <ElButton @click="refreshHealth">刷新服务状态</ElButton>
        </div>

        <section class="status-strip" aria-label="工程状态">
          <div>
            <span>本地 API</span
            ><strong role="status">{{ serviceStatus }}</strong>
          </div>
          <div><span>运行编号</span><strong>无活动运行</strong></div>
          <div><span>数据来源</span><strong>用户提交的配置文本</strong></div>
          <div><span>协议时钟</span><strong>未启动</strong></div>
        </section>
        <ElAlert
          v-if="serviceError"
          :title="serviceError"
          type="warning"
          :closable="false"
        />
        <ElAlert
          title="此页面仅校验0.0工程配置；0.1/0.2实验由CLI运行，“运行与回放”展示实际主站观测。"
          type="info"
          :closable="false"
          show-icon
        />

        <section class="editor-panel">
          <div class="panel-heading">
            <div>
              <h2>YAML 配置</h2>
              <p>工程契约 0.0 · 仿真假设</p>
            </div>
            <ElTag type="info">仅校验</ElTag>
          </div>
          <label for="yaml-config">粘贴 configs/foundation.yaml 的内容</label>
          <ElInput
            id="yaml-config"
            v-model="yamlText"
            type="textarea"
            :rows="15"
            placeholder="schema_version: '0.0'"
            :disabled="pending"
            @input="clearResult"
          />
          <div class="editor-actions">
            <span>CLI 与网页使用同一校验服务；不会连接任何设备。</span>
            <ElButton
              type="primary"
              :loading="pending"
              :disabled="!yamlText.trim()"
              @click="validate"
            >
              校验配置
            </ElButton>
          </div>
          <ElAlert
            v-if="validationError"
            :title="validationError"
            type="error"
            :closable="false"
            show-icon
          />
          <div v-if="result" class="validation-result" role="status">
            <strong>配置校验通过</strong>
            <p>
              站点数 {{ result.station_count }} · schema
              {{ result.schema_version }}
            </p>
            <p>规范化配置 SHA256</p>
            <code>{{ result.config_sha256 }}</code>
            <p>此结果不代表协议运行或数据质量检查通过。</p>
          </div>
        </section>
        <footer>
          PVComm · 业务约束下的分布式光伏通信异常检测 · 本地科研工作台
        </footer>
      </main>
      <main v-else><RunWorkbench /></main>
    </div>
  </div>
</template>
