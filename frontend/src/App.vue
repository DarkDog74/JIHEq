<script setup lang="ts">
import * as echarts from 'echarts'
import { animate, stagger } from 'motion'
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import type {
  AiNarration,
  AiStreamEvent,
  AuditDetail,
  AuditItem,
  AuditProgressEvent,
  AuditSummary,
  AuthPreset,
  Job,
  ModelConfig,
  ModelConfigForm,
  ModelTestResult,
  SourcePreview,
} from './types'

type View = 'home' | 'running' | 'results' | 'settings'
type LiveAiStatus = 'pending' | 'streaming' | 'completed' | 'failed' | 'skipped' | 'program'
type LiveItemKind = 'pending' | 'ai' | 'program'
type LiveAiItem = {
  itemId: string
  itemName: string
  current?: number
  total?: number
  kind: LiveItemKind
  hasAiEvent: boolean
  visible: boolean
  status: LiveAiStatus
  targetText: string
  displayText: string
  cached: boolean
  narration?: AiNarration
  reasonCode?: string
  errorType?: string
  errorMessage?: string
  finalizedAt?: number
}

const statusLabels: Record<string, string> = {
  passed: '通过', failed: '不通过', not_applicable: '不适用',
}
const auditGroupLabels: Record<string, string> = {
  BEAN: '备案信息表',
  DJ: '定级报告',
  FHX: '符合性评测表',
  FX: '风险评估报告',
  STB: '三同步报告',
  ZIC: '资产清单',
  ZM: '证明材料',
}
const view = ref<View>('home')
const sourcePath = ref('')
const preview = ref<SourcePreview | null>(null)
const recentSystems = ref<Record<string, string>[]>([])
const validating = ref(false)
// 从用户自己的电脑上传文件夹。
// 浏览器把选中目录下的每个文件连同 webkitRelativePath 逐个传上来，
// 服务器按原目录结构还原，再交给原有的校验/稽核流程。
const folderInput = ref<HTMLInputElement | null>(null)
const uploading = ref(false)
const uploadDone = ref(0)
const uploadTotal = ref(0)
const UPLOAD_CONCURRENCY = 3
const starting = ref(false)
const error = ref('')
const job = ref<Job | null>(null)
const summary = ref<AuditSummary | null>(null)
const search = ref('')
const statusFilter = ref('')
const groupFilter = ref('')
const selectedItem = ref<AuditItem | null>(null)
const selectedDetail = ref<AuditDetail | null>(null)
const detailLoading = ref(false)
const auditItemCount = ref(0)
const modelForm = ref<ModelConfigForm>({
  base_url: 'https://open.bigmodel.cn/api/paas/v4',
  api_key: '',
  model: 'glm-5v-turbo',
  timeout_seconds: 120,
  // 请求形态：默认值等于平台一直以来的行为，改这些是为了适配非标准网关
  auth_header: 'Authorization',
  auth_scheme: 'Bearer',
  chat_path: '/chat/completions',
  stream: false,
  max_tokens: 1024,
  extra_body: '',
  extra_headers: '',
})
// 各厂商思考参数的写法差别很大，给几个现成的直接填，
// 用户不用去翻文档，也不用为换模型改代码
const extraBodyExamples = [
  { key: 'qwen', label: 'Qwen / 阿里云百炼', value: '{\n  "enable_thinking": true,\n  "thinking_budget": 4000\n}' },
  { key: 'glm', label: '智谱 GLM-5.3（不支持关闭思考）', value: '{\n  "thinking": {"type": "enabled"},\n  "reasoning_effort": "low"\n}' },
  { key: 'deepseek', label: 'DeepSeek', value: '{\n  "thinking": {"type": "enabled"},\n  "reasoning_effort": "high"\n}' },
  { key: 'off', label: '清空（不附加任何参数）', value: '' },
]
const modelPresets = ref<AuthPreset[]>([])
const modelPresetKey = ref('')
// 后端按配置拼出的地址也返回了，但这里按输入实时算，改一个字就能看到结果
const endpointPreview = computed(() => {
  const base = (modelForm.value.base_url || '').trim().replace(/\/+$/, '')
  const path = (modelForm.value.chat_path || '').trim()
  if (!base) return ''
  if (!path) return base
  return base + (path.startsWith('/') ? path : '/' + path)
})
const isDefaultRequestShape = computed(() =>
  modelForm.value.auth_header === 'Authorization'
  && modelForm.value.auth_scheme === 'Bearer'
  && modelForm.value.chat_path === '/chat/completions'
  && !modelForm.value.stream
)

function applyPreset(key: string) {
  modelPresetKey.value = key
  const preset = modelPresets.value.find(item => item.key === key)
  if (!preset) return
  modelForm.value.auth_header = preset.auth_header
  modelForm.value.auth_scheme = preset.auth_scheme
  modelForm.value.chat_path = preset.chat_path
  modelForm.value.stream = preset.stream
}

function onPresetChange(event: Event) {
  applyPreset((event.target as HTMLSelectElement).value)
}

function onExtraExampleChange(event: Event) {
  const select = event.target as HTMLSelectElement
  const example = extraBodyExamples.find(item => item.key === select.value)
  if (example) modelForm.value.extra_body = example.value
  select.value = ''
}
const modelSuggestions = ref(['glm-5v-turbo', 'glm-5.3-flash'])
const modelApiKeySet = ref(false)
const modelApiKeyMasked = ref('')
const showKey = ref(false)
const savingModel = ref(false)
const testingModel = ref(false)
const settingsError = ref('')
const settingsSaved = ref(false)
const modelTest = ref<ModelTestResult | null>(null)
const chartEl = ref<HTMLElement | null>(null)
const liveAiItems = ref<Record<string, LiveAiItem>>({})
const liveAiOrder = ref<string[]>([])
const activeAiItemId = ref('')
const reducedMotion = ref(false)
const streamEl = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null
let events: EventSource | null = null
let pollTimer: number | null = null
let typingTimer: number | null = null
let switchingTimer: number | null = null
let resultTransitionTimer: number | null = null
let streamFallbackTimer: number | null = null
let motionPreference: MediaQueryList | null = null
let connectedEventJobId = ''
let pendingResultSystemId = ''
let sseEndReceived = false
let streamDisconnected = false
let streamFallbackReady = false
let loadingPendingResult = false
let userWantsStreamAutoScroll = true
const seenEventIds = new Set<string>()
const seenAiChunks = new Map<string, Set<string>>()
const aiEventTypes = new Set(['ai_started', 'ai_delta', 'ai_completed', 'ai_failed'])
const auditEventTypes = new Set(['audit_started', 'audit_complete'])
const segmenter = typeof Intl !== 'undefined' && 'Segmenter' in Intl
  ? new Intl.Segmenter('zh-CN', { granularity: 'grapheme' })
  : null

const terminalStatuses = new Set(['completed', 'completed_with_errors', 'failed', 'cancelled'])
const completedJobStatuses = new Set(['completed', 'completed_with_errors'])
const terminalAiStatuses = new Set<LiveAiStatus>(['completed', 'failed', 'skipped', 'program'])
const narrationTotal = computed(() => Math.max(
  auditItemCount.value,
  liveAiOrder.value.length,
  ...liveAiOrder.value.map(itemId => Number(liveAiItems.value[itemId]?.total) || 0),
))
const narrationCompleted = computed(() => liveAiOrder.value.filter(itemId => {
  const state = liveAiItems.value[itemId]
  return Boolean(
    state?.visible
    && terminalAiStatuses.has(state.status)
    && state.displayText === state.targetText,
  )
}).length)
const progressPercent = computed(() => narrationTotal.value
  ? Math.round(narrationCompleted.value / narrationTotal.value * 100)
  : 0)
const materialStageDone = computed(() => liveAiOrder.value.length > 0
  || ['auditing', 'narrating', 'reporting', 'completed'].includes(job.value?.stage || ''))
const auditStageDone = computed(() => ['narrating', 'reporting', 'completed'].includes(job.value?.stage || ''))
const auditStageActive = computed(() => ['auditing', 'narrating'].includes(job.value?.stage || ''))
const reportStageDone = computed(() => completedJobStatuses.has(job.value?.status || ''))
const reportStageActive = computed(() => job.value?.stage === 'reporting' && !reportStageDone.value)
const runningSystemName = computed(() => {
  if (preview.value?.system_name) return preview.value.system_name
  const source = job.value?.source_path?.replace(/\\/g, '/').replace(/\/$/, '')
  return source?.split('/').pop() || '单系统安全稽核'
})
const groupedPreview = computed(() => {
  if (preview.value?.category_statuses?.length) return preview.value.category_statuses
  return Object.entries(preview.value?.category_counts || {}).map(([category_name, amount]) => ({
    category_code: '', category_name, count: amount, missing: false,
  }))
})
const groups = computed(() => [...new Set((summary.value?.audit.items || []).map(item => item.item_id.split('-')[0]))]
  .sort()
  .map(code => ({ code, label: auditGroupLabels[code] || code })))
const statuses = computed(() => [...new Set((summary.value?.audit.items || []).map(item => item.status))].sort())
const filteredItems = computed(() => {
  const query = search.value.trim().toLowerCase()
  return (summary.value?.audit.items || []).filter(item =>
    (!statusFilter.value || item.status === statusFilter.value) &&
    (!groupFilter.value || item.item_id.startsWith(`${groupFilter.value}-`)) &&
    (!query || JSON.stringify(item).toLowerCase().includes(query)),
  )
})
const count = (status: string) => summary.value?.audit.counts[status] || 0
const notApplicableCount = computed(() => count('not_applicable'))
const passRate = computed(() => {
  const applicable = count('passed') + count('failed')
  return applicable ? Math.round(count('passed') / applicable * 100) : 0
})
const currentAiItem = computed(() => liveAiItems.value[activeAiItemId.value] || null)

// 部署在反向代理的子路径下时所有请求都要带前缀，前缀来自 vite.config.ts 的
// base（打包时写死进产物）。去掉尾部斜杠方便拼接。
const API_BASE = import.meta.env.BASE_URL.replace(/\/$/, '')

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  // 上传文件时 body 是 File/Blob，必须让浏览器自己决定 Content-Type，
  // 硬设成 application/json 会把二进制内容发坏。
  const isBinary = init?.body instanceof FormData || init?.body instanceof Blob
  const headers = isBinary
    ? init?.headers
    : { 'Content-Type': 'application/json', ...(init?.headers as Record<string, string> | undefined) }
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: `请求失败：${response.status}` }))
    throw new Error(payload.detail || '请求失败')
  }
  return response.json()
}

async function loadRecent() {
  recentSystems.value = await api<Record<string, string>[]>('/api/systems').catch(() => [])
}

async function loadHealth() {
  const health = await api<{ audit_item_count: number }>('/api/health').catch(() => null)
  auditItemCount.value = health?.audit_item_count || 0
}

async function enterSettings() {
  settingsError.value = ''
  settingsSaved.value = false
  modelTest.value = null
  try {
    const config = await api<ModelConfig>('/api/model-config')
    modelForm.value = {
      base_url: config.base_url,
      api_key: '',
      model: config.model,
      timeout_seconds: config.timeout_seconds,
      auth_header: config.auth_header ?? 'Authorization',
      auth_scheme: config.auth_scheme ?? 'Bearer',
      chat_path: config.chat_path ?? '/chat/completions',
      stream: config.stream ?? false,
      max_tokens: config.max_tokens ?? 1024,
      extra_body: config.extra_body ?? '',
      extra_headers: config.extra_headers ?? '',
    }
    modelPresets.value = config.auth_presets?.length ? config.auth_presets : []
    modelPresetKey.value = ''
    modelSuggestions.value = config.suggested_models?.length
      ? config.suggested_models
      : modelSuggestions.value
    modelApiKeySet.value = config.api_key_set
    modelApiKeyMasked.value = config.api_key_masked
    modelForm.value.model = config.model
    modelSuggestions.value = config.suggested_models?.length
      ? config.suggested_models
      : modelSuggestions.value
  } catch (caught) {
    settingsError.value = caught instanceof Error ? caught.message : String(caught)
  } finally {
    // Do not expose editable fields until the saved configuration has loaded;
    // otherwise a late GET response can overwrite text the user just entered.
    view.value = 'settings'
  }
}

async function saveModelConfig() {
  savingModel.value = true
  settingsError.value = ''
  settingsSaved.value = false
  try {
    const config = await api<ModelConfig>('/api/model-config', {
      method: 'PUT', body: JSON.stringify(modelForm.value),
    })
    modelApiKeySet.value = config.api_key_set
    modelApiKeyMasked.value = config.api_key_masked
    modelForm.value.api_key = ''
    settingsSaved.value = true
    await loadHealth()
  } catch (caught) {
    settingsError.value = caught instanceof Error ? caught.message : String(caught)
  } finally {
    savingModel.value = false
  }
}

async function runModelTest() {
  if (testingModel.value) return
  testingModel.value = true
  settingsError.value = ''
  modelTest.value = null
  try {
    modelTest.value = await api<ModelTestResult>('/api/model-config/test', {
      method: 'POST', body: JSON.stringify(modelForm.value),
    })
  } catch (caught) {
    settingsError.value = caught instanceof Error ? caught.message : String(caught)
  } finally {
    testingModel.value = false
  }
}

function clearAiTimers() {
  if (typingTimer !== null) window.clearTimeout(typingTimer)
  if (switchingTimer !== null) window.clearTimeout(switchingTimer)
  typingTimer = null
  switchingTimer = null
}

function clearResultGateTimers() {
  if (resultTransitionTimer !== null) window.clearTimeout(resultTransitionTimer)
  if (streamFallbackTimer !== null) window.clearTimeout(streamFallbackTimer)
  resultTransitionTimer = null
  streamFallbackTimer = null
}

function resetResultGate() {
  clearResultGateTimers()
  pendingResultSystemId = ''
  sseEndReceived = false
  streamDisconnected = false
  streamFallbackReady = false
  loadingPendingResult = false
}

function resetAiStreamState() {
  clearAiTimers()
  liveAiItems.value = {}
  liveAiOrder.value = []
  activeAiItemId.value = ''
  userWantsStreamAutoScroll = true
  seenEventIds.clear()
  seenAiChunks.clear()
}

function readLiveItemMetadata(payload: AiStreamEvent | AuditProgressEvent, state: LiveAiItem) {
  if (payload.item_name) state.itemName = payload.item_name
  if (payload.current !== undefined) state.current = payload.current
  if (payload.total !== undefined) state.total = payload.total
}

function ensureAiItem(payload: AiStreamEvent | AuditProgressEvent): LiveAiItem {
  const itemId = payload.item_id || '未知稽核项'
  let state = liveAiItems.value[itemId]
  if (!state) {
    state = {
      itemId,
      itemName: payload.item_name || itemId,
      current: payload.current,
      total: payload.total,
      kind: 'pending',
      hasAiEvent: false,
      visible: !activeAiItemId.value,
      status: 'pending',
      targetText: '',
      displayText: '',
      cached: false,
    }
    liveAiItems.value[itemId] = state
    liveAiOrder.value.push(itemId)
    if (!activeAiItemId.value) activeAiItemId.value = itemId
    autoFollowStream()
  }
  readLiveItemMetadata(payload, state)
  return state
}

function handleStreamScroll() {
  const element = streamEl.value
  if (!element) return
  const distanceFromBottom = element.scrollHeight - element.scrollTop - element.clientHeight
  userWantsStreamAutoScroll = distanceFromBottom <= 80
}

function autoFollowStream() {
  if (!userWantsStreamAutoScroll) return
  void nextTick(() => {
    const element = streamEl.value
    if (element) element.scrollTop = element.scrollHeight
  })
}

function nextGrapheme(text: string): string {
  if (!text) return ''
  if (segmenter) {
    const iterator = segmenter.segment(text)[Symbol.iterator]()
    const next = iterator.next()
    if (!next.done) return next.value.segment
  }
  return Array.from(text)[0] || ''
}

function maybeAdvanceAiItem() {
  const state = currentAiItem.value
  if (!state || (!terminalAiStatuses.has(state.status) && !streamFallbackReady)) return
  if (state.displayText !== state.targetText) return
  const index = liveAiOrder.value.indexOf(state.itemId)
  const nextId = liveAiOrder.value[index + 1]
  if (!nextId) {
    maybeFinishPendingResult()
    return
  }
  const minimumVisible = state.status === 'failed' ? 500 : state.status === 'skipped' ? 320 : 120
  const elapsed = Date.now() - (state.finalizedAt || 0)
  if (elapsed < minimumVisible) {
    if (switchingTimer !== null) window.clearTimeout(switchingTimer)
    switchingTimer = window.setTimeout(() => {
      switchingTimer = null
      maybeAdvanceAiItem()
    }, minimumVisible - elapsed)
    return
  }
  activeAiItemId.value = nextId
  const nextState = liveAiItems.value[nextId]
  if (nextState) nextState.visible = true
  autoFollowStream()
  void nextTick().then(scheduleAiTyping)
}

function scheduleAiTyping() {
  if (typingTimer !== null) return
  const state = currentAiItem.value
  if (!state) return
  if (!state.targetText.startsWith(state.displayText)) state.displayText = ''
  if (reducedMotion.value) {
    state.displayText = state.targetText
    autoFollowStream()
    maybeAdvanceAiItem()
    return
  }
  const next = nextGrapheme(state.targetText.slice(state.displayText.length))
  if (!next) {
    maybeAdvanceAiItem()
    return
  }
  // Cached narrations arrive as one large chunk. Replay them at the same
  // readable pace so repeated audits still visibly stream instead of flashing.
  const delay = 22
  typingTimer = window.setTimeout(() => {
    typingTimer = null
    const active = currentAiItem.value
    if (active?.itemId === state.itemId) {
      active.displayText += next
      autoFollowStream()
    }
    scheduleAiTyping()
  }, delay)
}

function aiDisplayQueueDrained(): boolean {
  if (!liveAiOrder.value.length) return true
  const lastId = liveAiOrder.value[liveAiOrder.value.length - 1]
  if (activeAiItemId.value !== lastId) return false
  return liveAiOrder.value.every(itemId => {
    const state = liveAiItems.value[itemId]
    return Boolean(
      state
      && state.displayText === state.targetText
      && (terminalAiStatuses.has(state.status) || streamFallbackReady),
    )
  })
}

async function enterPendingResults(systemId: string) {
  if (loadingPendingResult || !systemId) return
  loadingPendingResult = true
  events?.close()
  localStorage.removeItem('audit-current-job')
  try {
    await loadSummary(systemId)
    pendingResultSystemId = ''
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : String(caught)
    loadingPendingResult = false
    resultTransitionTimer = window.setTimeout(() => {
      resultTransitionTimer = null
      maybeFinishPendingResult()
    }, 1500)
    return
  }
  loadingPendingResult = false
}

function maybeFinishPendingResult() {
  if (
    !pendingResultSystemId
    || loadingPendingResult
    || (!sseEndReceived && !streamFallbackReady)
    || !aiDisplayQueueDrained()
  ) return
  if (resultTransitionTimer !== null) return
  resultTransitionTimer = window.setTimeout(() => {
    resultTransitionTimer = null
    if (
      pendingResultSystemId
      && (sseEndReceived || streamFallbackReady)
      && aiDisplayQueueDrained()
    ) void enterPendingResults(pendingResultSystemId)
  }, 160)
}

function scheduleStreamFallback() {
  if (!pendingResultSystemId || sseEndReceived || streamFallbackReady || streamFallbackTimer !== null) return
  streamFallbackTimer = window.setTimeout(() => {
    streamFallbackTimer = null
    if (!sseEndReceived && streamDisconnected) {
      streamFallbackReady = true
      scheduleAiTyping()
      maybeAdvanceAiItem()
      maybeFinishPendingResult()
    }
  }, 1500)
}

function noteStreamActivity() {
  if (streamFallbackTimer !== null) window.clearTimeout(streamFallbackTimer)
  streamFallbackTimer = null
  if (streamDisconnected || streamFallbackReady) {
    streamDisconnected = false
    streamFallbackReady = false
    if (resultTransitionTimer !== null) window.clearTimeout(resultTransitionTimer)
    resultTransitionTimer = null
  }
}

function markSseEnded() {
  sseEndReceived = true
  streamDisconnected = false
  streamFallbackReady = false
  if (streamFallbackTimer !== null) window.clearTimeout(streamFallbackTimer)
  streamFallbackTimer = null
  events?.close()
  maybeFinishPendingResult()
}

function markCompletedJob(systemId: string) {
  pendingResultSystemId = systemId
  if (streamDisconnected) scheduleStreamFallback()
  maybeFinishPendingResult()
}

function handleAiEvent(payload: AiStreamEvent): boolean {
  if (payload.chunk_seq !== undefined) {
    const chunks = seenAiChunks.get(payload.item_id) || new Set<string>()
    const sequence = String(payload.chunk_seq)
    if (chunks.has(sequence)) return false
    chunks.add(sequence)
    seenAiChunks.set(payload.item_id, chunks)
  }
  const state = ensureAiItem(payload)
  if (state.kind === 'program') {
    state.targetText = ''
    state.displayText = ''
  }
  state.kind = 'ai'
  state.hasAiEvent = true
  if (switchingTimer !== null) {
    window.clearTimeout(switchingTimer)
    switchingTimer = null
  }
  if (payload.type === 'ai_started') {
    state.status = 'streaming'
  } else if (payload.type === 'ai_delta') {
    state.status = 'streaming'
    if (typeof payload.delta === 'string') state.targetText += payload.delta
  } else if (payload.type === 'ai_completed') {
    state.status = 'completed'
    state.narration = payload.narration
    state.cached = Boolean(payload.narration?.cached)
    if (typeof payload.narration?.text === 'string') state.targetText = payload.narration.text
    state.finalizedAt = Date.now()
  } else if (payload.narration?.status === 'skipped') {
    state.status = 'skipped'
    state.narration = payload.narration
    state.errorType = undefined
    state.errorMessage = payload.narration.text || payload.message || 'AI未配置，已按现有逻辑完成'
    state.finalizedAt = Date.now()
  } else {
    state.status = 'failed'
    state.narration = payload.narration
    state.errorType = payload.error_type || payload.narration?.error_type
    state.errorMessage = payload.message || payload.narration?.error_message || payload.narration?.message || '未知原因'
    // Failed partial output is not shown, so it must not delay the following item.
    if (activeAiItemId.value === state.itemId && typingTimer !== null) {
      window.clearTimeout(typingTimer)
      typingTimer = null
    }
    state.displayText = state.targetText
    state.finalizedAt = Date.now()
  }
  scheduleAiTyping()
  autoFollowStream()
  return true
}

function programReason(payload: AuditProgressEvent): string {
  if (payload.message?.trim()) return payload.message.trim()
  return {
    missing_document: '缺少目标文档，已按现有程序逻辑完成。',
    missing_target_section: '缺少目标章节，已按现有程序逻辑完成。',
    multiple_documents: '存在多文档冲突，已按现有程序逻辑完成。',
    extraction_error: '字段提取异常，已按现有程序逻辑完成。',
    execution_error: '程序执行异常，已按现有程序逻辑完成。',
    blocked_by_parse_error: '材料解析异常，已按现有程序逻辑完成。',
  }[payload.reason_code || ''] || '该项未调用 AI，已按现有程序逻辑完成。'
}

function handleAuditEvent(payload: AuditProgressEvent) {
  if (payload.type !== 'audit_complete') return
  const state = ensureAiItem(payload)
  const narration = payload.ai_narration
  if (narration?.status === 'completed') {
    state.kind = 'ai'
    state.hasAiEvent = true
    state.status = 'completed'
    state.narration = narration
    state.cached = Boolean(narration.cached)
    state.targetText = narration.text || '模型未返回可显示的说明。'
    state.displayText = ''
    state.finalizedAt = Date.now()
  } else if (narration?.status === 'skipped') {
    state.kind = 'ai'
    state.hasAiEvent = true
    state.status = 'skipped'
    state.narration = narration
    state.errorMessage = narration.text || narration.error_message || 'LLM 未启用或未配置'
    state.targetText = ''
    state.displayText = ''
    state.finalizedAt = Date.now()
  } else if (narration?.status === 'failed') {
    state.kind = 'ai'
    state.hasAiEvent = true
    state.status = 'failed'
    state.narration = narration
    state.errorType = narration.error_code || narration.error_type
    state.errorMessage = narration.error_message || narration.message || narration.text || '未知原因'
    state.targetText = ''
    state.displayText = ''
    state.finalizedAt = Date.now()
  } else if (!state.hasAiEvent) {
    // Compatibility fallback for historical jobs created before LLM-gated
    // completion records were introduced.
    state.kind = 'program'
    state.status = 'program'
    state.reasonCode = payload.reason_code
    state.targetText = programReason(payload)
    state.displayText = ''
    state.finalizedAt = Date.now()
  }
  scheduleAiTyping()
  autoFollowStream()
}

function streamItemBody(state: LiveAiItem): string {
  if (state.kind === 'program') return `程序结果：${state.displayText}`
  if (state.status === 'skipped') return state.errorMessage || 'AI未配置，已按现有逻辑完成'
  if (state.status === 'failed') return `AI说明生成失败：${state.errorMessage || '未知原因'}`
  if (state.displayText) return state.displayText
  if (state.status === 'completed') return '模型未返回可显示的说明。'
  return ''
}

function showStreamCursor(state: LiveAiItem): boolean {
  return state.itemId === activeAiItemId.value
    && state.kind === 'ai'
    && !reducedMotion.value
    && (state.status === 'streaming' || state.displayText !== state.targetText)
}

function receiveEvent(event: MessageEvent, fallbackType = '') {
  try {
    const parsed = JSON.parse(String(event.data))
    if (!parsed || typeof parsed !== 'object') return
    const payload = parsed as Record<string, unknown>
    noteStreamActivity()
    if (!payload.type && fallbackType) payload.type = fallbackType
    const eventIdValue = event.lastEventId || payload.event_id || payload.id || ''
    const eventId = String(eventIdValue)
    if (eventId && seenEventIds.has(eventId)) return
    if (eventId) seenEventIds.add(eventId)
    if (typeof payload.type === 'string' && aiEventTypes.has(payload.type)) {
      const aiPayload = payload as unknown as AiStreamEvent
      if (!handleAiEvent(aiPayload)) return
      return
    }
    if (typeof payload.type === 'string' && auditEventTypes.has(payload.type)) {
      handleAuditEvent(payload as unknown as AuditProgressEvent)
    }
  } catch { /* heartbeat or incomplete line */ }
}

async function validateSource() {
  error.value = ''
  preview.value = null
  if (!sourcePath.value.trim()) { error.value = '请输入一个业务系统文件夹的绝对路径'; return }
  validating.value = true
  try {
    preview.value = await api<SourcePreview>('/api/systems/validate', {
      method: 'POST', body: JSON.stringify({ source_path: sourcePath.value.trim() }),
    })
    sourcePath.value = preview.value.source_path
    await nextTick()
    animate('.preview-enter', { opacity: [0, 1], y: [16, 0] }, { duration: .45, delay: stagger(.035) })
  } catch (caught) { error.value = caught instanceof Error ? caught.message : String(caught) }
  finally { validating.value = false }
}

function pickLocalFolder() {
  error.value = ''
  folderInput.value?.click()
}

async function onFolderPicked(event: Event) {
  const input = event.target as HTMLInputElement
  const picked = Array.from(input.files || [])
  // 清空 value，否则再次选同一个文件夹不会触发 change
  input.value = ''
  if (!picked.length) return

  type PickedFile = File & { webkitRelativePath?: string }
  const files = picked as PickedFile[]
  // webkitRelativePath 形如 "某系统/材料/文件.pdf"，第一段就是用户选中的文件夹名
  const rootName = (files[0].webkitRelativePath || files[0].name).split('/')[0]

  uploading.value = true
  uploadDone.value = 0
  uploadTotal.value = files.length
  error.value = ''
  try {
    // 并发上传，队列取空即止；顺序无所谓，服务端按相对路径各自落盘。
    // 文件走原始请求体（不走 multipart），目录名和相对路径放查询参数。
    const queue = [...files]
    const worker = async () => {
      while (queue.length) {
        const file = queue.shift()!
        const query = new URLSearchParams({
          folder: rootName,
          relative_path: file.webkitRelativePath || file.name,
        })
        await api<{ ok: boolean }>(`/api/systems/upload?${query}`, { method: 'POST', body: file })
        uploadDone.value += 1
      }
    }
    await Promise.all(Array.from({ length: UPLOAD_CONCURRENCY }, worker))

    const done = await api<{ source_path: string }>('/api/systems/upload/finish', {
      method: 'POST', body: JSON.stringify({ folder: rootName }),
    })
    sourcePath.value = done.source_path
    await validateSource()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : String(caught)
  } finally { uploading.value = false }
}

async function startJob() {
  if (!preview.value) return
  starting.value = true; error.value = ''
  try {
    job.value = await api<Job>('/api/jobs', {
      method: 'POST', body: JSON.stringify({ source_path: preview.value.source_path }),
    })
    localStorage.setItem('audit-current-job', job.value.id)
    view.value = 'running'
    connectEvents(job.value.id)
    startPolling(job.value.id)
  } catch (caught) { error.value = caught instanceof Error ? caught.message : String(caught) }
  finally { starting.value = false }
}

function connectEvents(jobId: string) {
  events?.close()
  if (connectedEventJobId !== jobId) {
    resetAiStreamState()
    resetResultGate()
    connectedEventJobId = jobId
  }
  events = new EventSource(`${API_BASE}/api/jobs/${jobId}/events`)
  events.onmessage = event => receiveEvent(event)
  for (const eventType of aiEventTypes) {
    events.addEventListener(eventType, event => receiveEvent(event as MessageEvent, eventType))
  }
  for (const eventType of auditEventTypes) {
    events.addEventListener(eventType, event => receiveEvent(event as MessageEvent, eventType))
  }
  events.addEventListener('end', markSseEnded)
  events.onerror = () => {
    streamDisconnected = true
    if (pendingResultSystemId) scheduleStreamFallback()
    if (job.value && ['failed', 'cancelled'].includes(job.value.status)) events?.close()
  }
}

function startPolling(jobId: string) {
  if (pollTimer) window.clearInterval(pollTimer)
  const poll = async () => {
    try {
      job.value = await api<Job>(`/api/jobs/${jobId}`)
      if (terminalStatuses.has(job.value.status)) {
        if (pollTimer) window.clearInterval(pollTimer)
        pollTimer = null
        if (completedJobStatuses.has(job.value.status)) markCompletedJob(job.value.system_id)
        else {
          events?.close()
          localStorage.removeItem('audit-current-job')
        }
      }
    } catch { /* transient polling failure */ }
  }
  void poll(); pollTimer = window.setInterval(poll, 850)
}

async function resumeJob(jobId: string) {
  try {
    const existing = await api<Job>(`/api/jobs/${jobId}`)
    job.value = existing
    if (terminalStatuses.has(existing.status)) {
      localStorage.removeItem('audit-current-job')
      if (existing.status.startsWith('completed')) await loadSummary(existing.system_id)
      return
    }
    view.value = 'running'; connectEvents(jobId); startPolling(jobId)
  } catch { localStorage.removeItem('audit-current-job') }
}

async function cancelJob() {
  if (!job.value) return
  job.value = await api<Job>(`/api/jobs/${job.value.id}/cancel`, { method: 'POST' })
  events?.close(); if (pollTimer) window.clearInterval(pollTimer); pollTimer = null
}

async function loadSummary(systemId: string) {
  summary.value = await api<AuditSummary>(`/api/systems/${systemId}/summary`)
  view.value = 'results'
  await nextTick(); renderChart()
  animate('.metric-card', { opacity: [0, 1], y: [18, 0] }, { duration: .5, delay: stagger(.06) })
}

function renderChart() {
  if (!chartEl.value || !summary.value) return
  chart?.dispose(); chart = echarts.init(chartEl.value)
  const data = [
    { value: count('passed'), name: '通过', itemStyle: { color: '#18a873' } },
    { value: count('failed'), name: '不通过', itemStyle: { color: '#e84f63' } },
    { value: notApplicableCount.value, name: '不适用', itemStyle: { color: '#e1a12d' } },
  ]
  chart.setOption({
    backgroundColor: 'transparent', tooltip: { trigger: 'item', backgroundColor: '#ffffff', borderColor: '#dce5f0', textStyle: { color: '#223047' }, extraCssText: 'box-shadow:0 12px 30px rgba(30,64,110,.14);border-radius:10px;' },
    series: [{ type: 'pie', radius: ['66%', '84%'], center: ['50%', '48%'], startAngle: 110, label: { show: false },
      emphasis: { scaleSize: 8 }, itemStyle: { borderColor: '#ffffff', borderWidth: 5, borderRadius: 8 }, data }],
    graphic: [{ type: 'text', left: 'center', top: '39%', style: { text: `${passRate.value}%`, fill: '#1b2a41', fontSize: 34, fontWeight: 700 } },
      { type: 'text', left: 'center', top: '57%', style: { text: '总体通过率', fill: '#718096', fontSize: 12 } }],
  })
}

async function openDetail(item: AuditItem) {
  selectedItem.value = item; selectedDetail.value = null; detailLoading.value = true
  try { selectedDetail.value = await api<AuditDetail>(`/api/systems/${summary.value!.system.id}/audits/${item.item_id}`) }
  catch (caught) { selectedDetail.value = { error: caught instanceof Error ? caught.message : String(caught) } }
  finally { detailLoading.value = false }
}

function resetHome() {
  view.value = 'home'; preview.value = null; summary.value = null; job.value = null; error.value = ''
  connectedEventJobId = ''
  resetAiStreamState()
  resetResultGate()
  void loadRecent()
}

function reportUrl() { return summary.value ? `${API_BASE}/api/systems/${summary.value.system.id}/report` : '#' }
function extractedPreview(item: AuditItem) {
  if (!item.extracted_summary?.length) return '未生成提取字段'
  return item.extracted_summary.map(entry => `${entry.field}：${entry.value}`).join(' ｜ ')
}
function inputMaterials(item: AuditItem) {
  const materials = (item.source_documents || []).map(source => {
    const fileName = source.replace(/\\/g, '/').split('/').pop() || source
    return fileName.replace(/\.(?:md|json|docx?|xlsx?|pdf|png|jpe?g)$/i, '')
  }).filter(Boolean)
  return [...new Set(materials)]
}
function inputMaterialLabel(item: AuditItem) {
  const materials = inputMaterials(item)
  return materials.length ? materials.join('、') : '未取得输入材料'
}
function detailNarration(): AiNarration | null {
  return selectedDetail.value?.ai_narration
    || selectedDetail.value?.narration
    || selectedItem.value?.ai_narration
    || null
}
function narrationFailure(narration: AiNarration | null) {
  return narration?.error_message || narration?.message || narration?.text || narration?.error_code || narration?.error_type || 'AI 说明生成失败，已按现有逻辑完成稽核'
}
function narrationFailureTitle(narration: AiNarration | null) {
  return narration?.status === 'skipped' ? 'AI 说明未启用' : 'AI 说明生成失败'
}
function detailFields() {
  const fields = selectedDetail.value?.fields
  return fields && typeof fields === 'object' && !Array.isArray(fields) ? Object.entries(fields as Record<string, unknown>) : []
}
function formatFieldValue(value: unknown) {
  if (value === null || value === undefined || value === '') return '未提取'
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}
function formatSize(bytes: number) { if (bytes < 1024) return `${bytes} B`; if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`; return `${(bytes / 1048576).toFixed(1)} MB` }
function updateMotionPreference(event: MediaQueryListEvent | MediaQueryList) {
  reducedMotion.value = event.matches
  if (!event.matches) return
  if (typingTimer !== null) window.clearTimeout(typingTimer)
  typingTimer = null
  const state = currentAiItem.value
  if (state) state.displayText = state.targetText
  maybeAdvanceAiItem()
}

onMounted(async () => {
  motionPreference = window.matchMedia('(prefers-reduced-motion: reduce)')
  updateMotionPreference(motionPreference)
  motionPreference.addEventListener('change', updateMotionPreference)
  await Promise.all([loadRecent(), loadHealth()])
  animate('.brand-lockup', { opacity: [0, 1], x: [-18, 0] }, { duration: .7 })
  const current = localStorage.getItem('audit-current-job'); if (current) await resumeJob(current)
  window.addEventListener('resize', renderChart)
})
onBeforeUnmount(() => {
  events?.close()
  if (pollTimer) window.clearInterval(pollTimer)
  clearAiTimers()
  clearResultGateTimers()
  motionPreference?.removeEventListener('change', updateMotionPreference)
  chart?.dispose()
  window.removeEventListener('resize', renderChart)
})
</script>

<template>
  <div class="app-shell">
    <div class="ambient ambient-a"></div><div class="ambient ambient-b"></div><div class="cyber-grid"></div>
    <div class="particles" aria-hidden="true"><i v-for="n in 18" :key="n" :style="{ '--i': n }"></i></div>
    <header class="app-header">
      <button class="brand-lockup" @click="resetHome" aria-label="返回首页">
        <span class="brand-mark"><b></b><i></i></span>
        <span><strong>浙江移动-定级备案报告稽核系统</strong></span>
      </button>
      <button v-if="view !== 'running' && view !== 'settings'" class="header-link-btn" @click="enterSettings">模型配置</button>
      <div class="header-status"><span class="live-dot"></span><b>{{ auditItemCount || '—' }}</b><small>稽核项在线</small></div>
    </header>

    <main>
      <section v-if="view === 'home'" class="home-view">
        <div class="launch-console glass-panel">
          <div class="panel-top"><div><h2>启动全量稽核</h2></div><span class="system-ready">系统就绪</span></div>
          <label class="path-field" :class="{ focused: sourcePath }"><span>业务系统文件夹</span><div><i>/</i><input v-model="sourcePath" @keyup.enter="validateSource" placeholder="选择文件夹，或粘贴完整路径" autocomplete="off"><input ref="folderInput" type="file" webkitdirectory multiple class="folder-input" @change="onFolderPicked"><button class="folder-button" @click.prevent="pickLocalFolder" :disabled="uploading || validating">{{ uploading ? `上传中 ${uploadDone}/${uploadTotal}` : '选择文件夹' }}</button><button @click.prevent="validateSource" :disabled="validating || uploading">{{ validating ? '识别中' : '识别材料' }}</button></div></label>
          <p v-if="uploading" class="upload-progress">正在上传本机文件夹：{{ uploadDone }} / {{ uploadTotal }} 个文件…</p>
          <p v-if="error" class="error-banner">{{ error }}</p>
          <div v-if="!preview && recentSystems.length" class="recent-block"><div class="section-label">最近处理</div><button v-for="system in recentSystems.slice(0, 4)" :key="system.id" class="recent-row" @click="sourcePath = system.source_path; validateSource()"><span><b>{{ system.display_name }}</b><small>{{ system.department }}</small></span><i>→</i></button></div>
          <div v-if="preview" class="preview-block">
            <div class="preview-head preview-enter"><div class="system-orb"><span></span></div><div><small>{{ preview.department }}</small><h3>{{ preview.system_name }}</h3><p>已识别 {{ preview.present_category_count ?? groupedPreview.length }}/{{ preview.expected_category_count ?? groupedPreview.length }} 类 · 共 {{ preview.recognized_count }} 份文件</p></div><span v-if="preview.has_previous_result" class="overwrite-tag">将覆盖上次结果</span></div>
            <div v-if="preview.missing_categories?.length" class="missing-materials-alert preview-enter"><span class="missing-alert-icon">!</span><div><b>缺少 {{ preview.missing_categories.length }} 类材料</b><p>{{ preview.missing_categories.join('、') }}</p></div></div>
            <div class="category-grid"><div v-for="category in groupedPreview" :key="category.category_code || category.category_name" class="category-chip preview-enter" :class="{ missing: category.missing }"><span>{{ category.category_name }}</span><span class="category-state"><small v-if="category.missing">缺失</small><b v-else>{{ category.count }}</b></span></div></div>
            <details class="document-details preview-enter"><summary>查看识别材料 <span>{{ preview.documents.length }}</span></summary><div class="document-list"><div v-for="doc in preview.documents" :key="doc.relative_path"><span><b>{{ doc.name }}</b><small>{{ doc.category_name }}</small></span><i>{{ formatSize(doc.size) }}</i></div></div></details>
            <button class="primary-action preview-enter" @click="startJob" :disabled="starting"><span>{{ starting ? '正在创建任务…' : '开始解析并运行全部稽核' }}</span><i>→</i></button>
          </div>
        </div>
      </section>

      <section v-else-if="view === 'running'" class="running-view">
        <div class="running-heading"><div><div class="kicker"><span></span>任务处理中</div><h1>{{ runningSystemName }}</h1><p>{{ job?.message }}</p></div><button v-if="job && !terminalStatuses.has(job.status)" class="ghost-danger" @click="cancelJob">取消任务</button></div>
        <div class="progress-layout">
          <aside class="progress-card glass-panel" data-testid="narration-progress">
            <div class="progress-orbit" :style="{ '--progress': `${progressPercent * 3.6}deg` }">
              <div><strong data-testid="narration-progress-percent">{{ progressPercent }}</strong><span>%</span><small>稽核结果展示</small></div>
            </div>
            <div class="progress-count" data-testid="narration-progress-count">已完整展示 {{ narrationCompleted }} / {{ narrationTotal || auditItemCount }}</div>
            <div class="stage-list">
              <div :class="{ active: !materialStageDone, done: materialStageDone }"><i>1</i><span><b>材料解析</b><small>识别并转换全部系统材料</small></span></div>
              <div :class="{ active: auditStageActive, done: auditStageDone }"><i>2</i><span><b>顺序稽核</b><small>后台按编号顺序处理，完成后逐条展示</small></span></div>
              <div :class="{ active: reportStageActive, done: reportStageDone }"><i>3</i><span><b>生成报告</b><small>生成可离线查看的报告</small></span></div>
            </div>
          </aside>
          <section class="audit-stream-panel glass-panel">
            <div ref="streamEl" class="audit-stream-scroll" data-testid="audit-stream" @scroll="handleStreamScroll">
              <p v-if="!liveAiOrder.length" class="audit-stream-empty">正在准备稽核项…</p>
              <div v-else class="audit-stream-list">
                <article
                  v-for="itemId in liveAiOrder"
                  v-show="liveAiItems[itemId]?.visible"
                  :key="itemId"
                  class="audit-stream-item"
                  :class="[liveAiItems[itemId]?.status, { current: itemId === activeAiItemId }]"
                  :data-item-id="itemId"
                  :data-kind="liveAiItems[itemId]?.kind"
                >
                  <header><h2>{{ liveAiItems[itemId]?.itemName }}</h2><small>{{ itemId }}</small></header>
                  <p
                    v-if="streamItemBody(liveAiItems[itemId]) || showStreamCursor(liveAiItems[itemId])"
                    class="audit-stream-copy"
                    :class="{ 'program-only': liveAiItems[itemId]?.kind === 'program', 'ai-stream-failed': liveAiItems[itemId]?.status === 'failed', 'ai-stream-skipped': liveAiItems[itemId]?.status === 'skipped' }"
                    aria-live="polite"
                  >{{ streamItemBody(liveAiItems[itemId]) }}<i v-if="showStreamCursor(liveAiItems[itemId])" class="audit-stream-cursor" aria-hidden="true"></i></p>
                </article>
              </div>
            </div>
          </section>
        </div>
        <div v-if="job?.status === 'cancelled' || job?.status === 'failed'" class="terminal-state glass-panel"><h3>{{ job.status === 'cancelled' ? '任务已取消' : '任务执行失败' }}</h3><p>{{ job.error || job.message }}</p><button class="secondary-action" @click="resetHome">返回首页</button></div>
      </section>

      <section v-else-if="view === 'settings'" class="settings-view">
        <div class="settings-heading">
          <div><h1>模型配置</h1><p>全系统统一使用同一个模型，可按服务端支持情况调整模型编码</p></div>
          <button class="secondary-action" @click="resetHome">返回首页</button>
        </div>
        <div class="settings-layout">
          <form class="config-panel glass-panel" @submit.prevent="saveModelConfig">
            <div class="panel-title"><span>接入参数</span><small>密钥仅保存在本机服务端</small></div>
            <label class="config-field"><span>接入地址</span><input v-model.trim="modelForm.base_url" autocomplete="off" spellcheck="false"></label>
            <label class="config-field"><span>API Key</span><div class="key-row"><input :type="showKey ? 'text' : 'password'" v-model.trim="modelForm.api_key" :placeholder="modelApiKeySet ? `已保存 ${modelApiKeyMasked}，留空保持不变` : '填写已轮换的新密钥'" autocomplete="new-password"><button type="button" @click="showKey = !showKey">{{ showKey ? '隐藏' : '显示' }}</button></div></label>
            <label class="config-field"><span>统一模型</span><input v-model.trim="modelForm.model" list="model-code-options" aria-label="统一模型" autocomplete="off" spellcheck="false"><datalist id="model-code-options"><option v-for="model in modelSuggestions" :key="model" :value="model"></option></datalist><small>可选择 glm-5v-turbo、glm-5.3-flash，或输入服务端支持的其他模型编码；图片稽核必须选择支持视觉输入的模型</small></label>
            <label class="config-field"><span>请求超时（秒）</span><input type="number" v-model.number="modelForm.timeout_seconds" min="3" max="300" step="1"></label>
            <div class="config-group" :class="{ custom: !isDefaultRequestShape }">
              <div class="config-group-title">请求形态<small>默认适配 OpenAI 兼容服务；自建网关按需修改，改错了「测试连接」会报出实际请求的地址</small></div>
              <label class="config-field"><span>认证方式</span><select :value="modelPresetKey" @change="onPresetChange"><option value="">自定义</option><option v-for="preset in modelPresets" :key="preset.key" :value="preset.key">{{ preset.label }}</option></select></label>
              <label class="config-field"><span>认证头名称</span><input v-model.trim="modelForm.auth_header" autocomplete="off" spellcheck="false"><small>OpenAI 兼容服务填 Authorization；自定义网关可能是 Authorization-Gateway</small></label>
              <label class="config-field"><span>认证值前缀</span><input v-model.trim="modelForm.auth_scheme" placeholder="留空表示这个头里只放密钥本身" autocomplete="off" spellcheck="false"><small>标准为 Bearer，即 Authorization: Bearer &lt;key&gt;；留空则只发 &lt;key&gt;</small></label>
              <label class="config-field"><span>对话路径</span><input v-model.trim="modelForm.chat_path" placeholder="留空表示接入地址本身就是完整端点" autocomplete="off" spellcheck="false"><small>OpenAI 兼容服务填 /chat/completions；自建网关通常留空</small></label>
              <label class="config-checkbox"><input type="checkbox" v-model="modelForm.stream"><span>使用流式响应（SSE）</span><small>部分网关只接受 stream=true</small></label>
              <label class="config-field"><span>最大输出 token</span><input type="number" v-model.number="modelForm.max_tokens" min="128" max="32768" step="128"><small>思考内容也算在这个额度里。推理模型建议 4096 以上，1024 很容易被思考吃光</small></label>
              <label class="config-field"><span>附加请求参数（JSON）</span><textarea v-model="modelForm.extra_body" rows="4" spellcheck="false" autocomplete="off" placeholder='{"enable_thinking": true}'></textarea><small>厂商特有的参数写这里，原样合并进请求体。<b>填了就完全以它为准</b>，平台不再自动添加思考类参数。思考的开关和强度也在这里控制——各家的参数名并不统一（Qwen 用 enable_thinking + thinking_budget，智谱和 DeepSeek 用 thinking.type + reasoning_effort），写在这里就不用为换模型改代码</small></label>
              <label class="config-field"><span>快速填入</span><select :value="''" @change="onExtraExampleChange"><option value="">— 选一个厂商自动填入上面 —</option><option v-for="example in extraBodyExamples" :key="example.key" :value="example.key">{{ example.label }}</option></select></label>
              <label class="config-field"><span>附加请求头（JSON）</span><textarea v-model="modelForm.extra_headers" rows="2" spellcheck="false" autocomplete="off" placeholder='{"X-Custom-Header": "value"}'></textarea><small>需要额外请求头的网关用；认证头一般用上面的「认证方式」配置即可</small></label>
              <p v-if="modelForm.extra_body.trim()" class="config-endpoint">已启用附加参数，平台不会自动添加 thinking / reasoning_effort，请求体以你写的为准</p>
              <p class="config-endpoint">实际请求：<code>{{ endpointPreview || '（未填写接入地址）' }}</code></p>
            </div>
            <div class="config-actions"><button type="submit" class="primary-action" :disabled="savingModel"><span>{{ savingModel ? '正在保存…' : '保存配置' }}</span></button><button type="button" class="secondary-action" :disabled="testingModel" @click="runModelTest">{{ testingModel ? '测试中…' : '测试连接' }}</button></div>
            <p v-if="settingsError" class="error-banner">{{ settingsError }}</p><p v-else-if="settingsSaved" class="saved-banner">配置已保存，后续稽核将使用这套统一模型配置</p>
          </form>
          <aside class="model-status-panel glass-panel">
            <div class="panel-title"><span>连接状态</span><small>{{ modelForm.model || '未填写模型' }}</small></div>
            <div class="model-status-main" :class="modelTest?.ok ? 'ok' : modelTest ? 'failed' : ''"><i></i><b>{{ modelTest ? (modelTest.ok ? '连接成功' : '连接失败') : (modelApiKeySet ? '密钥已配置' : '等待配置') }}</b></div>
            <p>{{ modelTest?.message || '保存配置后可以发送一次最小请求，确认地址、密钥和模型是否可用。' }}</p>
            <dl><div><dt>模型</dt><dd>{{ modelForm.model || '未填写' }}</dd></div><div><dt>超时</dt><dd>{{ modelForm.timeout_seconds }} 秒</dd></div><div><dt>密钥</dt><dd>{{ modelApiKeySet ? modelApiKeyMasked : '未配置' }}</dd></div></dl>
          </aside>
        </div>
      </section>

      <section v-else-if="summary" class="results-view">
        <div class="result-heading"><div><div class="kicker"><span></span>稽核已完成</div><h1>{{ summary.system.name }}</h1><p>{{ summary.system.department }} · {{ summary.generated_at }}</p></div><div class="result-actions"><button class="secondary-action" @click="resetHome">稽核其他系统</button><a class="primary-link" :href="reportUrl()">下载离线汇总报告 <i>↓</i></a></div></div>
        <div class="metric-grid"><article class="metric-card glass-panel"><span>稽核项总数</span><strong>{{ summary.audit.item_count }}</strong><small>全部已执行</small></article><article class="metric-card glass-panel success"><span>通过</span><strong>{{ count('passed') }}</strong><small>{{ passRate }}% 总体通过率</small></article><article class="metric-card glass-panel danger"><span>不通过</span><strong>{{ count('failed') }}</strong><small>包含缺材料、提取异常和规则不通过</small></article><article class="metric-card glass-panel warning"><span>不适用</span><strong>{{ notApplicableCount }}</strong><small>业务明确无需执行</small></article><article class="metric-card glass-panel"><span>解析材料</span><strong>{{ summary.parse.file_count }}</strong><small>{{ summary.parse.failed_file_count ? `${summary.parse.failed_file_count} 份失败` : '全部完成' }}</small></article></div>
        <div class="result-layout"><aside class="chart-panel glass-panel"><div class="panel-title"><span>状态分布</span><small>通过、不通过与不适用</small></div><div ref="chartEl" class="status-chart"></div><div class="chart-legend"><div><i class="green"></i><span>通过</span><b>{{ count('passed') }}</b></div><div><i class="red"></i><span>不通过</span><b>{{ count('failed') }}</b></div><div><i class="amber"></i><span>不适用</span><b>{{ notApplicableCount }}</b></div></div><div class="parse-summary"><div><span>材料解析</span><b :class="summary.parse.failed_file_count ? 'text-amber' : 'text-green'">{{ summary.parse.failed_file_count ? '部分异常' : '正常' }}</b></div><div><span>离线报告</span><b class="text-green">已生成</b></div></div></aside>
          <section class="audit-panel glass-panel"><div class="audit-toolbar"><div><div class="panel-title"><span>稽核结果明细</span><small>支持搜索和条件筛选</small></div><p>点击任意稽核项查看字段、问题和来源证据</p></div><div class="filters"><label><i></i><input v-model="search" placeholder="搜索编号或问题"></label><select v-model="groupFilter"><option value="">全部分类</option><option v-for="group in groups" :key="group.code" :value="group.code">{{ group.label }}</option></select><select v-model="statusFilter"><option value="">全部状态</option><option v-for="status in statuses" :key="status" :value="status">{{ statusLabels[status] }}</option></select></div></div>
            <div class="audit-table-head"><span>稽核项</span><span>状态</span><span>中间提取内容</span><span>稽核结论</span><span></span></div>
            <div class="audit-list"><button v-for="item in filteredItems" :key="item.item_id" class="audit-row" @click="openDetail(item)"><span class="audit-identity"><b class="audit-name">{{ item.item_name }}</b><small class="audit-input" :title="item.source_documents?.join('\n')"><i>输入</i>{{ inputMaterialLabel(item) }}</small><small class="audit-id">{{ item.item_id }}</small></span><span class="status-pill" :class="item.status"><i></i>{{ statusLabels[item.status] || item.status }}</span><span class="audit-extracted" :class="{ empty: !item.extracted_summary?.length }">{{ extractedPreview(item) }}</span><span class="audit-message">{{ item.message }} <small v-if="item.issues?.length">{{ item.issues.length }} 条问题</small></span><i class="row-arrow">→</i></button><div v-if="!filteredItems.length" class="empty-result">没有符合当前筛选条件的结果</div></div>
          </section>
        </div>
      </section>
    </main>

    <Transition name="drawer"><div v-if="selectedItem" class="drawer-backdrop" @click.self="selectedItem = null"><aside class="detail-drawer"><header><div class="drawer-audit-title"><span><b class="audit-name">{{ selectedItem.item_name }}</b><small class="audit-id">{{ selectedItem.item_id }}</small></span><span class="status-pill" :class="selectedItem.status"><i></i>{{ statusLabels[selectedItem.status] }}</span></div><button @click="selectedItem = null" aria-label="关闭详情">×</button></header><div class="drawer-body"><div class="detail-hero"><small>稽核结论</small><h2>{{ selectedItem.message }}</h2></div><div v-if="detailLoading" class="detail-loading"><i></i><span>正在读取字段与稽核证据…</span></div><template v-else><section><div class="section-label">中间提取内容</div><div v-if="detailFields().length" class="extracted-field-list"><div v-for="([field, value]) in detailFields()" :key="field"><b>{{ field }}</b><pre>{{ formatFieldValue(value) }}</pre></div></div><p v-else class="no-issue">该稽核项没有生成提取字段。</p></section><section class="detail-ai-section"><div class="section-label">模型说明</div><div v-if="detailNarration()?.status === 'completed'" class="saved-ai-narration"><div><b>{{ detailNarration()?.model || '统一模型' }}</b><small v-if="detailNarration()?.cached">缓存结果</small></div><p>{{ detailNarration()?.text || '模型未返回可显示的说明。' }}</p></div><div v-else-if="detailNarration()?.status === 'skipped'" class="saved-ai-skipped"><p>{{ narrationFailure(detailNarration()) }}</p></div><div v-else-if="detailNarration()" class="saved-ai-failure"><b>{{ narrationFailureTitle(detailNarration()) }}</b><p>{{ narrationFailure(detailNarration()) }}</p><small>原稽核状态未受影响。</small></div><p v-else class="no-issue">未生成模型说明，本项已按现有逻辑完成稽核。</p></section><section><div class="section-label">问题与证据</div><div v-if="selectedItem.issues?.length" class="issue-stack"><div v-for="issue in selectedItem.issues" :key="issue">{{ issue }}</div></div><p v-else class="no-issue">该稽核项未记录问题。</p></section><section><div class="section-label">来源材料</div><div class="source-stack"><div v-for="source in selectedItem.source_documents" :key="source">{{ source }}</div><p v-if="!selectedItem.source_documents.length">未关联来源文件</p></div></section><details class="technical-details"><summary>查看完整技术 JSON</summary><pre>{{ JSON.stringify(selectedDetail, null, 2) }}</pre></details></template></div></aside></div></Transition>
  </div>
</template>
