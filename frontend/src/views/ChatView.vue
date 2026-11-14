<script setup lang="ts">
/**
 * 对话页：中间是消息流和输入框，右侧是工作区面板。
 * 顶部：标题（可改）、绑定的客户（或「保存到客户」）、任务目录和工作区面板开关。
 */
import { LayoutDashboard, ListTree, LoaderCircle, Pencil, Play, Save, X } from '@lucide/vue'
import { storeToRefs } from 'pinia'
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { conversationApi, runApi } from '@/api'
import { ApiError, errorText } from '@/api/http'
import type { StreamHandle } from '@/api/sse'
import type { AgentEvent, ConversationDetail, FileInfo, RecipeEditRequest, RunSteer, TableBrief } from '@/api/types'
import FunObject from '@/components/brand/FunObject.vue'
import Mascot from '@/components/brand/Mascot.vue'
import AssistantMessage from '@/components/chat/AssistantMessage.vue'
import ChatComposer, { type Attachment } from '@/components/chat/ChatComposer.vue'
import ExecutionPanel from '@/components/chat/ExecutionPanel.vue'
import SaveToCustomerDialog from '@/components/chat/SaveToCustomerDialog.vue'
import RunSteeringQueue from '@/components/chat/RunSteeringQueue.vue'
import TaskChecklist from '@/components/chat/TaskChecklist.vue'
import UserMessage from '@/components/chat/UserMessage.vue'
import TableViewer from '@/components/common/TableViewer.vue'
import DataTable from '@/components/common/DataTable.vue'
import BaseModal from '@/components/common/BaseModal.vue'
import { attachmentMessage } from '@/composables/attachmentFiles'
import { applyDeliveryManifest, applyEvent, finishMessage, restoreConversationMessages, markRecipeDeleted, newAssistant, newUser, uid, type UiMessage } from '@/composables/chatModel'
import { useToast } from '@/composables/useToast'
import { serializeTableReferences, type FileReference, type TableReference } from '@/composables/tableReferences'
import { conversationTables, executionTableKey } from '@/composables/executionPresentation'
import { checklistItems } from '@/composables/thinkingPresentation'
import { syncMessageUsage } from '@/composables/usageSync'
import { applyRunSteerEvent, committedRunMessages, mergeRunSteer, reconcileRunSteers, restoreRunSteers } from '@/composables/runControls'
import { useWorkspaceStore } from '@/stores/conversations'
import { useAuthStore } from '@/stores/auth'
import { useChatPreferencesStore } from '@/stores/chatPreferences'
import { pendingStarts } from '@/stores/pending'
import { pendingRecipeEdits } from '@/stores/recipeEdits'
import { recipeEditLabel, recipeEditPlaceholder, type RecipeEditDraft } from '@/composables/recipeEditing'

const WorkbookPreview = defineAsyncComponent(() => import('@/features/workbook-preview/WorkbookPreview.vue'))

const props = defineProps<{ id: string }>()
const router = useRouter()
const store = useWorkspaceStore()
const toast = useToast()

const detail = ref<ConversationDetail | null>(null)
const messages = ref<UiMessage[]>([])
const loading = ref(true)
const streaming = ref(false)
const reconnecting = ref(false)
const steers = ref<RunSteer[]>([])
const sendingSteer = ref(false)
const startingResume = ref(false)
const withdrawingSteer = ref<number | null>(null)
const text = ref('')
const recipeEdit = ref<RecipeEditDraft | null>(null)
const preferences = useChatPreferencesStore()
const auth = useAuthStore()
if (auth.user) preferences.hydrate(auth.user.id)
const { thinking } = storeToRefs(preferences)
const attachments = ref<Attachment[]>([])
const composer = ref<InstanceType<typeof ChatComposer> | null>(null)
const recentlyUploaded = ref<FileInfo[]>([])
const uploadedReferences = ref<TableReference[]>([])
const checklistOpen = ref(true) // 任务目录默认显示，可用顶部图标隐藏
const panelOpen = ref(false) // 工作区面板默认关闭，需要时点右上角打开
const panelWidth = ref(320)
const panelTab = ref<'recipes' | 'files' | 'data' | 'usage'>('recipes')
const refreshingUsage = ref(false)
const selectedKey = ref<string | null>(null)
const viewing = ref<TableBrief | null>(null)
const viewingFile = ref<FileInfo | null>(null)
const saveOpen = ref(false)
const editingTitle = ref(false)
const titleDraft = ref('')
const scroller = ref<HTMLElement | null>(null)
const content = ref<HTMLElement | null>(null)
let handle: StreamHandle | null = null
let pollTimer = 0
let usageRefreshTimer = 0
let usagePendingSince = 0
let metadataRefreshVersion = 0
let savingTitle = false
let sideRefreshVersion = 0
let sideRequestVersion = 0
let steerRevision = 0
let loadVersion = 0
let disposed = false
let scrollFrame = 0
let forceScroll = false
let followOutput = true
let previousScrollTop = 0

watch([() => pendingRecipeEdits.get(props.id), streaming, reconnecting], ([draft, busy, reconnecting]) => {
  if (!draft || busy || reconnecting) return
  recipeEdit.value = draft
  pendingRecipeEdits.delete(props.id)
  void nextTick(() => composer.value?.focus())
}, { immediate: true })

const selectedMessage = computed(() => {
  const assistants = messages.value.filter((m) => m.role === 'assistant')
  return assistants.find((m) => m.key === selectedKey.value) ?? assistants[assistants.length - 1] ?? null
})
const customer = computed(() => detail.value?.customer ?? null)
const canResume = computed(() => detail.value?.execution?.can_resume === true && !streaming.value && !reconnecting.value && !startingResume.value)
const visibleSteers = computed(() => steers.value.filter((item) => item.status !== 'committed' ||
  !messages.value.some((message) => message.role === 'user' && message.id === item.message_id)))
const currentAssistant = computed(() => [...messages.value].reverse().find((message) => message.role === 'assistant'))
// 思考模式的进度跟随本轮助手消息，刷新后也可恢复步骤总结。
const checklist = computed(() => {
  const last = currentAssistant.value
  const items = checklistItems(last)
  return last && items.length ? { key: last.key, items, running: last.streaming } : null
})
watch(() => checklist.value?.key, (key) => {
  if (key) checklistOpen.value = true
})
// 消息中的表名只在当前会话里解析；全客户的同名表由工作区面板按来源定位。
const messageTables = computed(() => conversationTables(detail.value?.tables ?? [], props.id))
const messageFiles = computed(() => {
  const files = new Map((detail.value?.files ?? []).map((file) => [file.file_id, file]))
  for (const file of recentlyUploaded.value) files.set(file.file_id, file)
  return [...files.values()]
})
const referenceFiles = computed(() => messageFiles.value.map((file, index) => ({ file, index })).sort((left, right) => {
  const first = Date.parse(left.file.created_at ?? '')
  const second = Date.parse(right.file.created_at ?? '')
  return Number.isFinite(first) && Number.isFinite(second) && first !== second ? first - second : left.index - right.index
}).map(({ file }) => file))
const fileReferences = computed<FileReference[]>(() => referenceFiles.value.map((file) => ({
  filename: file.filename,
  sourceId: file.file_id,
  kind: file.kind === 'doc' ? undefined : file.kind,
})))
const referencesLoading = computed(() => loading.value || attachments.value.some((attachment) => attachment.status === 'uploading'))
const referencesError = computed(() => attachments.value.some((attachment) => attachment.status === 'error')
  ? '部分文件上传失败，暂时无法选择其中的工作表。请重新上传文件。'
  : '')
const tableReferences = computed<TableReference[]>(() => {
  const references = new Map<string, TableReference>()
  for (const file of referenceFiles.value) {
    for (const sheet of file.sheets) {
      const table = messageTables.value.find((item) => item.file_id === file.file_id && item.sheet === sheet.sheet)
      const reference: TableReference = {
        filename: file.filename,
        sourceId: file.file_id,
        kind: file.kind === 'doc' ? undefined : file.kind,
        sheet: sheet.sheet,
        tableName: table?.name,
      }
      references.set(JSON.stringify([reference.sourceId, reference.sheet]), reference)
    }
  }
  for (const reference of uploadedReferences.value) {
    references.set(JSON.stringify([reference.sourceId, reference.sheet]), reference)
  }
  return [...references.values()]
})

function previewAttachment(key: string) {
  const file = messageFiles.value.find((item) => item.file_id === key)
  if (!file) return
  composer.value?.captureSelection()
  viewingFile.value = file
}

function acknowledgeUploads(files: FileInfo[]) {
  const ids = new Set(files.map((file) => file.file_id))
  recentlyUploaded.value = recentlyUploaded.value.filter((file) => !ids.has(file.file_id))
}

// ------------------------------------------------------------------ 加载

async function load(silent = false) {
  const requestVersion = ++loadVersion
  const version = sideRefreshVersion
  const metadataVersion = ++metadataRefreshVersion
  clearTimeout(pollTimer)
  if (!silent) loading.value = true
  try {
    const data = await conversationApi.get(props.id)
    if (disposed || requestVersion !== loadVersion) return
    if (detail.value && version !== sideRefreshVersion) {
      data.files = detail.value.files
    }
    if (detail.value && (savingTitle || version !== sideRefreshVersion || metadataVersion !== metadataRefreshVersion)) {
      data.title = detail.value.title
    }
    detail.value = data
    acknowledgeUploads(data.files)
    if (!streaming.value) {
      steers.value = restoreRunSteers(data.messages, data.pending_steers, data.execution?.execution_id)
      const restored = restoreConversationMessages(committedRunMessages(data.messages), data.running_job)
      messages.value = restored.messages.map((m) => reactive(m) as UiMessage)
      scrollToBottom(true) // 打开会话（包括后台任务跑完刷新）都停在最新一条
      reconnecting.value = false
      store.patchConversation(props.id, { running: data.running, title: data.title })
      const job = data.running_job
      if (job) {
        const assistant = restored.assistant!
        selectedKey.value = assistant.key
        void receiveStream(assistant, (onEvent) => conversationApi.events(props.id, job.id, onEvent), true)
      }
      scheduleUsageRefresh()
    }
  } catch (err) {
    if (disposed || requestVersion !== loadVersion) return
    if (silent && !(err instanceof ApiError && [401, 403, 404].includes(err.status))) {
      reconnecting.value = true
      schedulePoll()
    } else {
      toast.error(errorText(err))
      router.replace({ name: 'home' })
    }
  } finally {
    if (!disposed && requestVersion === loadVersion) loading.value = false
  }
}

function schedulePoll(delay = 1000) {
  clearTimeout(pollTimer)
  if (!disposed) pollTimer = window.setTimeout(() => { void load(true) }, delay)
}

async function refreshSide() {
  const requestVersion = ++sideRequestVersion
  const version = ++sideRefreshVersion
  const metadataVersion = ++metadataRefreshVersion
  const queueRevision = steerRevision
  refreshingUsage.value = true
  try {
    const data = await conversationApi.get(props.id)
    if (!disposed && detail.value && version === sideRefreshVersion) {
      detail.value.tables = data.tables
      detail.value.files = data.files
      detail.value.recipes = data.recipes
      if (!savingTitle && metadataVersion === metadataRefreshVersion) {
        detail.value.title = data.title
        store.patchConversation(props.id, { title: data.title })
      }
      detail.value.customer = data.customer
      detail.value.messages = data.messages
      detail.value.execution = data.execution
      detail.value.pending_steers = data.pending_steers
      steers.value = reconcileRunSteers(steers.value,
        restoreRunSteers(data.messages, data.pending_steers, data.execution?.execution_id),
        data.execution?.execution_id, queueRevision !== steerRevision)
      syncMessageUsage(messages.value, data.messages)
      if (!streaming.value && steers.value.some((item) => item.status === 'committed')) {
        messages.value = restoreConversationMessages(committedRunMessages(data.messages)).messages.map((message) => reactive(message) as UiMessage)
      }
      acknowledgeUploads(data.files)
    }
  } catch {
    /* 忽略 */
  } finally {
    if (!disposed && requestVersion === sideRequestVersion) {
      refreshingUsage.value = false
      scheduleUsageRefresh()
    }
  }
}

function scheduleUsageRefresh() {
  clearTimeout(usageRefreshTimer)
  if (disposed) return
  const pending = messages.value.some((message) => message.execution?.llm_usage_pending === true)
  if (!pending) usagePendingSince = 0
  else if (!usagePendingSince) usagePendingSince = Date.now()
  if (streaming.value && panelOpen.value && panelTab.value === 'usage' || pending && Date.now() - usagePendingSince < 180000) {
    usageRefreshTimer = window.setTimeout(() => { void refreshMetadata() }, 2000)
  }
}

async function refreshMetadata() {
  const requestVersion = ++metadataRefreshVersion
  const version = sideRefreshVersion
  try {
    const data = await conversationApi.get(props.id)
    if (disposed || !detail.value || version !== sideRefreshVersion || requestVersion !== metadataRefreshVersion) return
    if (!savingTitle) {
      detail.value.title = data.title
      store.patchConversation(props.id, { title: data.title })
    }
    syncMessageUsage(messages.value, data.messages)
  } catch {
    /* 临时断网时继续有界轮询，已有正文保持可读。 */
  } finally {
    if (!disposed && requestVersion === metadataRefreshVersion) scheduleUsageRefresh()
  }
}

watch([panelOpen, panelTab], ([open, tab]) => {
  clearTimeout(usageRefreshTimer)
  usagePendingSince = 0
  if (open && tab === 'usage') void refreshSide()
  else scheduleUsageRefresh()
})

let resizeObserver: ResizeObserver | null = null

onMounted(async () => {
  // 内容高度变化（逐字显示、表格渲染、图片加载）时，如果用户本来就在底部附近，就跟着滚到底
  if (typeof ResizeObserver !== 'undefined' && content.value) {
    resizeObserver = new ResizeObserver(() => scrollToBottom())
    resizeObserver.observe(content.value)
  }
  await load()
  if (disposed) return
  const pending = pendingStarts.get(props.id)
  if (pending) {
    pendingStarts.delete(props.id)
    await send(pending.message, { attachments: pending.attachments, thinking: pending.thinking })
  }
})

onBeforeUnmount(() => {
  disposed = true
  resizeObserver?.disconnect()
  clearTimeout(pollTimer)
  clearTimeout(usageRefreshTimer)
  cancelAnimationFrame(scrollFrame)
  handle?.abort() // 只是断开连接，后台任务会继续执行并保存结果
})

// ------------------------------------------------------------------ 发送

function scrollToBottom(force = false) {
  forceScroll ||= force
  if (scrollFrame) return
  nextTick(() => {
    if (scrollFrame || disposed) return
    scrollFrame = requestAnimationFrame(() => {
      scrollFrame = 0
      const el = scroller.value
      if (el && (forceScroll || followOutput)) {
        el.scrollTop = el.scrollHeight
        previousScrollTop = el.scrollTop
        followOutput = true
      }
      forceScroll = false
    })
  })
}

function onChatScroll() {
  const el = scroller.value
  if (!el) return
  if (el.scrollHeight - el.scrollTop - el.clientHeight < 80) followOutput = true
  else if (el.scrollTop < previousScrollTop - 1) followOutput = false
  previousScrollTop = el.scrollTop
}

watch(
  () => {
    const last = messages.value[messages.value.length - 1]
    return last ? `${last.content.length}:${last.timeline.length}:${last.artifacts.length}` : ''
  },
  () => scrollToBottom(),
)

async function onSend() {
  if (reconnecting.value || sendingSteer.value) return
  if (streaming.value) {
    await steer()
    return
  }
  if (attachments.value.some((a) => a.status === 'uploading')) {
    toast.info('文件还在上传，请稍等')
    return
  }
  if (attachments.value.some((a) => a.status === 'error')) {
    toast.error('请移除上传失败的附件后重新添加')
    return
  }
  const message = attachmentMessage(text.value, attachments.value.filter((a) => a.status === 'done'))
  if (!message) return
  const submittedText = text.value
  const submittedAttachments = [...attachments.value]
  const submittedEdit = recipeEdit.value
  const ids = attachments.value.filter((a) => a.status === 'done').map((a) => a.key)
  text.value = ''
  recipeEdit.value = null
  await send(message, { attachments: ids, thinking: thinking.value, recipeEdit: submittedEdit?.request,
    ...(submittedEdit ? { onRejected: (err: unknown) => {
      recipeEdit.value ??= submittedEdit
      text.value = [submittedText, text.value].filter(Boolean).join('\n')
      attachments.value = [...new Map([...submittedAttachments, ...attachments.value].map((item) => [item.key, item])).values()]
      toast.error(errorText(err))
      void nextTick(() => composer.value?.focus())
    } } : {}),
  })
}

async function send(message: string, opts: { attachments?: string[]; thinking?: boolean; recipeEdit?: RecipeEditRequest; onRejected?: (err: unknown) => void } = {}) {
  if (disposed || streaming.value || reconnecting.value) return
  const useThinking = opts.thinking ?? thinking.value
  sideRefreshVersion++
  steerRevision++
  steers.value = []
  message = serializeTableReferences(message, tableReferences.value, fileReferences.value)
  const userMessage = reactive(newUser(message, { attachments: opts.attachments ?? [], ...(useThinking ? { thinking: true } : {}),
    ...(opts.recipeEdit ? { recipe_edit: opts.recipeEdit } : {}) })) as UiMessage
  messages.value.push(userMessage)
  const assistant = reactive(newAssistant()) as UiMessage
  messages.value.push(assistant)
  selectedKey.value = assistant.key
  attachments.value = []
  scrollToBottom(true)
  store.patchConversation(props.id, { running: true })

  await receiveStream(assistant, (onEvent) => conversationApi.chat(
    props.id, { message, attachments: opts.attachments, ...(useThinking ? { thinking: true } : {}),
      ...(opts.recipeEdit ? { recipe_edit: opts.recipeEdit } : {}) }, onEvent,
  ), false, opts.onRejected ? (err) => {
    messages.value = messages.value.filter((item) => item.key !== userMessage.key && item.key !== assistant.key)
    opts.onRejected!(err)
  } : undefined)
}

async function receiveStream(assistant: UiMessage, open: (onEvent: (event: AgentEvent) => void) => StreamHandle, resuming = false, onRejected?: (err: unknown) => void) {
  streaming.value = true
  scheduleUsageRefresh()
  let received = false
  let retry = false
  let retryDelay = 1000
  const currentHandle = open((event) => {
    if (disposed) return
    received = true
    applyEvent(assistant, event)
    const events: AgentEvent[] = event.type === 'stream_snapshot' && Array.isArray(event.events) ? event.events : [event]
    const title = events.filter((item) => item.type === 'title').at(-1)
    const deletionTargets = event.type === 'stream_snapshot' ? messages.value.filter((message) => message !== assistant) : messages.value
    for (const item of events) {
      if (['steer_queued', 'steer_committed', 'steer_withdrawn'].includes(item.type)) steerRevision++
      steers.value = applyRunSteerEvent(steers.value, item)
      if (item.type === 'recipe_deleted') markRecipeDeleted(deletionTargets, item.recipe_id)
      if (item.type === 'delivery_manifest') applyDeliveryManifest(messages.value, item)
    }
    if (title && detail.value) {
      metadataRefreshVersion++
      detail.value.title = title.title
      store.patchConversation(props.id, { title: title.title })
    }
    if (events.some((item) => ['table', 'file', 'delivery_manifest', 'recipes_changed', 'recipe_deleted'].includes(item.type))) refreshSide()
  })
  handle = currentHandle
  try {
    await currentHandle.done
  } catch (err) {
    if ((err as Error).name !== 'AbortError') {
      const status = err instanceof ApiError ? err.status : 0
      retry = received || status === 409 || (resuming && (status === 0 || status >= 500 || status === 404))
      if ([401, 403].includes(status)) retry = false
      if (resuming && [404, 409].includes(status)) retryDelay = 0
      if (onRejected && !received && status >= 400 && status < 500) {
        retry = false
        onRejected(err)
      } else if (!retry) assistant.error = errorText(err)
    }
  } finally {
    if (disposed || handle !== currentHandle) return
    streaming.value = false
    handle = null
    reconnecting.value = retry
    if (retry) schedulePoll(retryDelay)
    else {
      finishMessage(assistant)
      store.patchConversation(props.id, { running: false, updated_at: new Date().toISOString() })
      usagePendingSince = 0
      refreshSide()
    }
  }
}

async function stop() {
  await conversationApi.stop(props.id).catch(() => undefined)
}

async function steer() {
  const message = text.value.trim()
  if (!message || sendingSteer.value) return
  if (attachments.value.length) {
    toast.info('处理中可追加文字要求，文件请在本次处理结束后上传')
    return
  }
  const assistantKey = currentAssistant.value?.key
  sendingSteer.value = true
  try {
    const receipt = await conversationApi.steer(props.id, message)
    if (disposed || currentAssistant.value?.key !== assistantKey) return
    steerRevision++
    if (streaming.value || !detail.value?.execution || ['running', 'interrupted', 'paused'].includes(detail.value.execution.status)) {
      steers.value = mergeRunSteer(steers.value, { ...receipt, content: message })
    }
    if (text.value.trim() === message) text.value = ''
    scrollToBottom(true)
  } catch (err) {
    if (!disposed) toast.error(errorText(err))
  } finally {
    sendingSteer.value = false
  }
}

async function withdrawSteer(messageId: number) {
  if (withdrawingSteer.value !== null) return
  withdrawingSteer.value = messageId
  try {
    await conversationApi.withdrawSteer(props.id, messageId)
    steerRevision++
    steers.value = applyRunSteerEvent(steers.value, { type: 'steer_withdrawn', message_id: messageId })
  } catch (err) {
    toast.error(errorText(err))
    void refreshSide()
  } finally {
    withdrawingSteer.value = null
  }
}

async function resume() {
  if (!canResume.value) return
  const execution = detail.value!.execution!
  startingResume.value = true
  sideRefreshVersion++
  try {
    let assistant = messages.value.find((message) => message.role === 'assistant' &&
      (typeof execution.assistant_message_id === 'number' ? message.id === execution.assistant_message_id :
        execution.assistant_message_id === undefined && execution.execution_id && message.meta.execution_id === execution.execution_id))
    if (!assistant) {
      assistant = reactive(newAssistant()) as UiMessage
      if (typeof execution.assistant_message_id === 'number') assistant.id = execution.assistant_message_id
      assistant.meta.execution_id = execution.execution_id
      messages.value.push(assistant)
    }
    assistant.streaming = true
    assistant.replyStartedAt = Date.now()
    assistant.replyFinishedAt = undefined
    assistant.execution = { ...assistant.execution, status: 'running', started_at: assistant.replyStartedAt, duration_ms: undefined }
    assistant.meta.execution = { ...assistant.execution }
    assistant.error = undefined
    selectedKey.value = assistant.key
    scrollToBottom(true)
    store.patchConversation(props.id, { running: true })
    await receiveStream(assistant, (onEvent) => conversationApi.resume(props.id, onEvent), true)
  } finally {
    startingResume.value = false
  }
}

// ------------------------------------------------------------------ 附件 / 标题

async function attach(files: File[], kind: 'input' | 'expected') {
  if (streaming.value || reconnecting.value) return
  // Register the entire batch before uploading so Send cannot omit later files.
  const batch = files.map((file) => ({ file, local: reactive<Attachment>({ key: uid('f'), name: file.name, kind, status: 'uploading', mediaType: file.type }) }))
  attachments.value.push(...batch.map(({ local }) => local))
  for (const { file, local } of batch) {
    try {
      const result = await conversationApi.upload(props.id, file, kind)
      local.key = result.file.file_id
      local.sourceId = result.file.file_id
      local.name = result.file.filename
      local.mediaType = result.file.media_type ?? file.type
      local.previewUrl = result.file.preview_url ?? undefined
      local.status = 'done'
      local.tables = result.tables.map((t) => t.name)
      uploadedReferences.value.push(...result.tables.flatMap((table) => table.sheet
        ? [{ filename: result.file.filename, sourceId: result.file.file_id, kind, sheet: table.sheet, tableName: table.name }]
        : []))
      sideRefreshVersion++
      recentlyUploaded.value = [...recentlyUploaded.value.filter((item) => item.file_id !== result.file.file_id), result.file]
      if (detail.value) detail.value.files = [result.file, ...detail.value.files.filter((item) => item.file_id !== result.file.file_id)]
      refreshSide()
    } catch (err) {
      local.status = 'error'
      local.error = errorText(err)
    }
  }
}

function removeAttachment(key: string) {
  attachments.value = attachments.value.filter((a) => a.key !== key)
}

function startEditTitle() {
  titleDraft.value = detail.value?.title ?? ''
  editingTitle.value = true
}

async function saveTitle() {
  editingTitle.value = false
  const title = titleDraft.value.trim()
  if (!title || !detail.value || title === detail.value.title || savingTitle) return
  savingTitle = true
  const previous = detail.value.title
  sideRefreshVersion++
  metadataRefreshVersion++
  detail.value.title = title
  store.patchConversation(props.id, { title })
  try {
    await conversationApi.update(props.id, { title })
  } catch (err) {
    if (!disposed && detail.value) {
      detail.value.title = previous
      store.patchConversation(props.id, { title: previous })
      toast.error(errorText(err))
    }
  } finally {
    // Discard GETs issued during the PATCH, which may have read the old title.
    sideRefreshVersion++
    metadataRefreshVersion++
    savingTitle = false
    scheduleUsageRefresh()
  }
}

async function onSaved() {
  saveOpen.value = false
  await load(true)
}

async function loadTable(offset: number, limit: number) {
  const table = viewing.value!
  const page = table.run_id
    ? await runApi.tablePage(table.run_id, table.name, offset, limit)
    : await conversationApi.tablePage(props.id, table.name, offset, limit, table.conversation_id ?? undefined)
  return { columns: page.columns.map((c) => c.name), rows: page.rows, total: page.total }
}

function openTable(table: TableBrief) {
  viewing.value = table
}

function answer(option: string) {
  if (!streaming.value && !reconnecting.value) send(option)
}
</script>

<template>
  <div class="chat-page flex h-full min-h-0">
    <div class="chat-workspace flex min-w-0 flex-1 flex-col">
      <!-- 顶部 -->
      <header class="flex h-11 shrink-0 items-center gap-2 border-b border-line px-4">
        <div class="min-w-0 flex-1">
          <input
            v-if="editingTitle"
            v-model="titleDraft"
            class="w-full max-w-md rounded-lg border border-line px-2 py-0.5 text-sm font-semibold outline-none"
            autofocus
            @blur="saveTitle"
            @keydown.enter="saveTitle"
          />
          <button v-else class="group flex max-w-full items-center gap-1.5 text-left" @click="startEditTitle">
            <span class="truncate text-sm font-semibold">{{ detail?.title ?? '…' }}</span>
            <Pencil class="size-3.5 shrink-0 text-faint opacity-0 group-hover:opacity-100" />
          </button>
        </div>

        <RouterLink
          v-if="customer"
          :to="{ name: 'customer', params: { id: customer.id } }"
          target="_blank"
          rel="noopener noreferrer"
          class="flex h-7 shrink-0 items-center gap-1.5 rounded-full border border-line py-0.5 pl-0.5 pr-2 text-xs transition hover:border-ink/30"
          title="在新标签页打开客户详情"
        >
          <span class="flex size-5 shrink-0 items-center justify-center overflow-hidden rounded-full bg-paper">
            <Mascot :variant="customer.mascot" :size="22" :shadow="false" :track="false" />
          </span>
          <span class="max-w-[128px] truncate font-medium">{{ customer.name }}</span>
        </RouterLink>
        <button v-else-if="detail" class="btn-line h-7 shrink-0 gap-1.5 whitespace-nowrap px-2.5 py-0 text-xs" title="把这次对话的数据、已保存的流程和记忆保存到客户" @click="saveOpen = true">
          <Save class="size-3.5" />保存到客户
        </button>

        <button
          v-if="checklist"
          type="button"
          class="icon-btn size-7 shrink-0"
          :class="{ '!bg-paper !text-ink': checklistOpen }"
          :title="checklistOpen ? '隐藏任务目录' : '显示任务目录'"
          aria-label="任务目录"
          :aria-expanded="checklistOpen"
          aria-controls="task-directory"
          @click="checklistOpen = !checklistOpen"
        >
          <ListTree class="size-4" aria-hidden="true" />
        </button>
        <button
          type="button"
          class="icon-btn size-7 shrink-0"
          :class="{ '!bg-paper !text-ink': panelOpen }"
          :title="panelOpen ? '收起工作区面板' : '展开工作区面板'"
          :aria-label="panelOpen ? '收起工作区面板' : '展开工作区面板'"
          :aria-expanded="panelOpen"
          aria-controls="workspace-panel"
          @click="panelTab = 'recipes'; panelOpen = !panelOpen"
        >
          <LayoutDashboard class="size-4" aria-hidden="true" />
        </button>
      </header>

      <div class="chat-body min-h-0 flex-1" :class="{ 'has-directory': checklist && checklistOpen }">
      <aside v-if="checklist" v-show="checklistOpen" id="task-directory" class="chat-progress" aria-label="本轮任务进度">
        <TaskChecklist :key="checklist.key" :items="checklist.items" :running="checklist.running" />
      </aside>

      <div class="chat-conversation">
      <!-- 消息 -->
      <div ref="scroller" class="chat-scroller min-h-0 flex-1 overflow-y-auto" @scroll.passive="onChatScroll">
      <div ref="content" class="chat-scroll-content">
        <div class="chat-messages pb-6 pt-8">
          <div v-if="loading" class="flex justify-center py-20 text-mute"><LoaderCircle class="size-5 animate-spin" /></div>

          <!-- 空对话 -->
          <div v-else-if="!messages.length" class="flex flex-col items-center py-16 text-center">
            <Mascot :variant="customer?.mascot ?? 'blob'" :size="96" />
            <h2 class="mt-5 text-xl font-semibold">{{ customer ? `我已经了解「${customer.name}」了` : '把表格拖进来，告诉我要做什么' }}</h2>
            <p class="mt-2 max-w-md text-sm leading-relaxed text-mute">
              {{ customer ? '我会按这个客户的处理逻辑、流程和业务规则来处理。' : '直接说你的需求，我会自动判断处理方式。' }}
            </p>
            <div class="mt-6 flex gap-6 opacity-90">
              <FunObject kind="sheet" :size="40" class="animate-float" />
              <FunObject kind="magnet" :size="40" class="animate-float [animation-delay:.6s]" />
              <FunObject kind="sigma" :size="40" class="animate-float [animation-delay:1.2s]" />
            </div>
          </div>

          <div v-else class="space-y-8">
            <template v-for="m in messages" :key="m.key">
              <UserMessage v-if="m.role === 'user'" :message="m" :files="messageFiles" @preview="(file) => (viewingFile = file)" />
              <AssistantMessage
                v-else
                :message="m"
                :files="messageFiles"
                :tables="messageTables"
                :answers-disabled="streaming || reconnecting"
                :answers-closed="currentAssistant?.key !== m.key"
                :customer-id="customer?.id"
                :conversation-id="props.id"
                :mascot="customer?.mascot"
                @answer="answer"
                @preview-file="(file) => (viewingFile = file)"
                @refresh="refreshSide"
              />
            </template>
          </div>

          <div v-if="reconnecting" class="mt-6 flex items-center gap-2 rounded-xl bg-info/5 px-4 py-3 text-sm text-info">
            <LoaderCircle class="size-4 animate-spin" />回复连接已中断，正在重新连接…
          </div>
        </div>

      <!-- 输入框 -->
      <div class="chat-composer-lane shrink-0">
      <div class="chat-input mx-auto w-full max-w-[680px] shrink-0 px-6 pb-3">
        <div class="w-full">
          <div class="relative z-10">
          <RunSteeringQueue :items="visibleSteers" :withdrawing="withdrawingSteer" @withdraw="withdrawSteer" />
          <div v-if="recipeEdit" class="mb-3 flex items-center gap-2 rounded-xl bg-paper py-1.5 pl-3.5 pr-1.5 text-[13px]" aria-label="流程修改目标">
            <span class="flex min-w-0 flex-1 items-center gap-2">
              <span class="min-w-0 shrink truncate text-faint">{{ recipeEdit.title }}</span><span class="shrink-0 text-faint">v{{ recipeEdit.request.version }}</span><span class="shrink-0 text-line">|</span>
              <span class="max-w-[70%] shrink-0 truncate font-medium text-ink">{{ recipeEditLabel(recipeEdit.request) }}</span>
            </span>
            <button type="button" class="icon-btn size-6 shrink-0" aria-label="取消流程修改" @click="recipeEdit = null"><X class="size-3.5" /></button>
          </div>
          <div v-if="canResume" class="mb-3 flex flex-wrap items-center gap-3 rounded-xl border border-line bg-paper/50 px-3.5 py-3 text-xs" role="status">
            <p class="min-w-0 flex-1 text-mute">{{ detail?.execution?.resume_reason || '上次处理已中断，可以从已保存的进度继续。' }}</p>
            <button type="button" class="btn-line h-8 gap-1.5 whitespace-nowrap px-3 py-0 text-xs" @click="resume"><Play class="size-3.5" />继续处理</button>
          </div>
          <ChatComposer
            ref="composer"
            v-model="text"
            v-model:thinking="thinking"
            :attachments="attachments"
            :table-references="tableReferences"
            :file-references="fileReferences"
            :references-loading="referencesLoading"
            :references-error="referencesError"
            :busy="streaming || reconnecting"
            :allow-steer="streaming && !reconnecting"
            :steer-pending="sendingSteer"
            large
            appearance="card"
            :placeholder="recipeEdit ? recipeEditPlaceholder(recipeEdit.request) : '继续提问...'"
            @attach="attach"
            @remove="removeAttachment"
            @preview="previewAttachment"
            @send="onSend"
            @stop="stop"
          />
          </div>
        </div>
      </div>
      </div>
      </div>
      </div>
      </div>
      </div>
    </div>

    <ExecutionPanel
      v-if="panelOpen"
      :detail="detail"
      :message="selectedMessage"
      :messages="messages"
      :refreshing-usage="refreshingUsage"
      v-model:active-tab="panelTab"
      v-model:width="panelWidth"
      @close="panelOpen = false"
      @open-table="openTable"
      @open-file="(file) => (viewingFile = file)"
      @refresh="refreshSide"
    />

    <BaseModal v-if="viewing?.preview_only" :key="executionTableKey(viewing)" :title="viewing.label || viewing.name" width="max-w-5xl" @close="viewing = null">
      <p class="mb-3 text-xs leading-5 text-mute">{{ viewing.conversation_title }} · 临时查询共 {{ viewing.rows }} 行 × {{ viewing.cols }} 列，保留前 {{ viewing.preview?.length ?? 0 }} 行预览；完整临时表已释放。</p>
      <DataTable :columns="viewing.columns" :rows="viewing.preview ?? []" max-height="62vh" />
    </BaseModal>
    <TableViewer v-else-if="viewing" :key="executionTableKey(viewing)" :title="viewing.label || viewing.name" :subtitle="viewing.run_title || viewing.conversation_title" :loader="loadTable" @close="viewing = null" />
    <WorkbookPreview
      v-if="viewingFile"
      :key="viewingFile.file_id"
      :file="viewingFile"
      @close="viewingFile = null"
    />
    <SaveToCustomerDialog v-if="saveOpen" :conversation-id="id" @close="saveOpen = false" @saved="onSaved" />
  </div>
</template>

<style scoped>
.chat-page {
  --chat-header-space: 44px;
}
.chat-workspace {
  container: chat-workspace / inline-size;
}
.chat-body {
  --chat-directory-space: 0px;
  position: relative;
  isolation: isolate;
  display: flex;
  flex-direction: column;
}
.chat-conversation {
  display: flex;
  min-width: 0;
  min-height: 0;
  flex: 1;
  flex-direction: column;
}
.chat-progress {
  flex: none;
  align-self: stretch;
  margin: 16px 20px 0;
}
.chat-scroller {
  padding-right: var(--chat-directory-space);
}
.chat-scroll-content {
  display: flex;
  min-height: 100%;
  flex-direction: column;
}
.chat-composer-lane {
  position: sticky;
  bottom: 0;
  z-index: 1;
  margin-top: auto;
  background: #fff;
}
.chat-messages {
  --chat-message-width: calc(680px - 3rem);
  width: calc(100% - 5.5rem);
  max-width: var(--chat-message-width);
  margin-left: max(4rem, calc((100% - var(--chat-message-width)) / 2));
  margin-right: 1.5rem;
}
@container chat-workspace (min-width: 840px) {
  .chat-body.has-directory {
    --chat-directory-space: 324px;
  }
  .chat-progress {
    position: absolute;
    top: 24px;
    right: 20px;
    z-index: 1;
    width: 304px;
    margin: 0;
  }
}
</style>
