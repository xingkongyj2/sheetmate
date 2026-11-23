<script setup lang="ts">
/**
 * 对话页：中间是消息流和输入框，右侧是工作区面板。
 * 顶部：标题（可改）、绑定的客户（或「保存到客户」）、任务目录和工作区面板开关。
 */
import { LayoutDashboard, ListTree, LoaderCircle, Pencil, Play, ShieldOff, UserRoundPlus, X } from '@lucide/vue'
import { storeToRefs } from 'pinia'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { conversationApi, runApi } from '@/api'
import { ApiError, errorText } from '@/api/http'
import type { StreamHandle } from '@/api/sse'
import type { AgentEvent, ConversationDetail, FileInfo, RecipeEditRequest, RunSteer, TableBrief } from '@/api/types'
import ChatWelcomeCharms from '@/components/brand/ChatWelcomeCharms.vue'
import Mascot from '@/components/brand/Mascot.vue'
import MascotLoader from '@/components/brand/MascotLoader.vue'
import AssistantMessage from '@/components/chat/AssistantMessage.vue'
import ChatComposer, { type Attachment } from '@/components/chat/ChatComposer.vue'
import ChatOutline from '@/components/chat/ChatOutline.vue'
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
import { buildChatOutline, resolveActiveOutlineKey } from '@/composables/chatOutline'
import { useFilePreview } from '@/composables/useFilePreview'
import { usePrivacySwitch } from '@/composables/usePrivacySwitch'
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


const props = defineProps<{ id: string }>()
const router = useRouter()
const route = useRoute()
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
const panelWidth = ref(390)
const panelTab = ref<'recipes' | 'files' | 'data' | 'usage' | 'privacy'>('recipes')
// 地址里带 ?flow=（全屏流程图）：流程图在工作区面板的「流程」标签里，刷新后要先把面板打开，全屏才能恢复。
watch(() => route.query.flow, (flow) => {
  if (!flow) return
  panelTab.value = 'recipes'
  panelOpen.value = true
}, { immediate: true })
const refreshingUsage = ref(false)
const selectedKey = ref<string | null>(null)
const viewing = ref<TableBrief | null>(null)
const openFilePreview = useFilePreview(() => props.id)
const saveOpen = ref(false)
const editingTitle = ref(false)
const titleDraft = ref('')
const scroller = ref<HTMLElement | null>(null)
const content = ref<HTMLElement | null>(null)
let handle: StreamHandle | null = null
let pollTimer = 0
let watchdogTimer = 0
let serverEnded = false      // 看门狗发现服务端任务早已结束，而事件流还挂着
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
const resumeBlockedReason = computed(() => {
  const execution = detail.value?.execution
  if (!execution || execution.can_resume || streaming.value || reconnecting.value || startingResume.value) return ''
  return ['interrupted', 'paused'].includes(execution.status) ? execution.resume_reason ?? '' : ''
})
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
const outlineItems = computed(() => buildChatOutline(messages.value, messageFiles.value))
const showOutline = computed(() => !loading.value && outlineItems.value.length > 0)
const outlineLastActivity = computed(() => {
  let latest = 0
  for (const message of messages.value) {
    const ts = message.replyFinishedAt ?? Date.parse(message.createdAt ?? '')
    if (Number.isFinite(ts) && ts > latest) latest = ts
  }
  return latest ? new Date(latest).toISOString() : detail.value?.updated_at ?? null
})
const activeOutlineKey = ref<string | null>(null)
const highlightedKey = ref<string | null>(null)
const messageEls = new Map<string, HTMLElement>()
let outlineFrame = 0
let jumpLockKey: string | null = null
let jumpTargetTop: number | null = null
let jumpLockTimer = 0
let highlightTimer = 0
const referenceFiles = computed(() => messageFiles.value.map((file, index) => ({ file, index })).sort((left, right) => {
  const first = Date.parse(left.file.created_at ?? '')
  const second = Date.parse(right.file.created_at ?? '')
  return Number.isFinite(first) && Number.isFinite(second) && first !== second ? first - second : left.index - right.index
}).map(({ file }) => file))
const fileReferences = computed<FileReference[]>(() => referenceFiles.value.map((file) => ({
  filename: file.filename,
  sourceId: file.file_id,
  kind: file.kind,
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
        kind: file.kind,
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
  openFilePreview(file)
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
  if (open && (tab === 'usage' || tab === 'files' || tab === 'recipes')) void refreshSide()
  else scheduleUsageRefresh()
})

let resizeObserver: ResizeObserver | null = null

const privacySwitch = usePrivacySwitch()
const syncOnFocus = () => {
  void privacySwitch.refresh()
  if (document.visibilityState === 'visible' && detail.value?.customer && !streaming.value && !loading.value) {
    void refreshSide()
  }
}

onMounted(async () => {
  void privacySwitch.refresh()
  window.addEventListener('focus', syncOnFocus)   // 从设置页或客户详情页回来时，状态与绑定文件立刻同步
  document.addEventListener('visibilitychange', syncOnFocus)
  // 内容高度变化（逐字显示、表格渲染、图片加载）时，如果用户本来就在底部附近，就跟着滚到底
  if (typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => {
      scrollToBottom()
      scheduleActiveOutlineSync()
    })
    if (content.value) resizeObserver.observe(content.value)
    if (scroller.value) resizeObserver.observe(scroller.value)
  }
  await load()
  if (disposed) return
  scheduleActiveOutlineSync()
  const pending = pendingStarts.get(props.id)
  if (pending) {
    pendingStarts.delete(props.id)
    await send(pending.message, { attachments: pending.attachments, thinking: pending.thinking })
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('focus', syncOnFocus)
  document.removeEventListener('visibilitychange', syncOnFocus)
  disposed = true
  resizeObserver?.disconnect()
  clearTimeout(pollTimer)
  clearInterval(watchdogTimer)
  clearTimeout(usageRefreshTimer)
  clearTimeout(jumpLockTimer)
  clearTimeout(highlightTimer)
  cancelAnimationFrame(scrollFrame)
  cancelAnimationFrame(outlineFrame)
  handle?.abort() // 只是断开连接，后台任务会继续执行并保存结果
})

// ------------------------------------------------------------------ 左侧对话目录联动

function setMessageEl(key: string, el: unknown) {
  const element = el && typeof el === 'object' && (typeof HTMLElement === 'undefined' || el instanceof HTMLElement)
    ? (el as HTMLElement)
    : null
  if (element) messageEls.set(key, element)
  else messageEls.delete(key)
}

function clearJumpLock() {
  jumpLockKey = null
  jumpTargetTop = null
  window.clearTimeout(jumpLockTimer)
  jumpLockTimer = 0
}

function updateActiveOutlineNow() {
  const items = outlineItems.value
  if (!items.length) {
    activeOutlineKey.value = null
    return
  }
  const el = scroller.value
  if (!el) {
    activeOutlineKey.value = items[items.length - 1]!.key
    return
  }
  if (jumpLockKey) {
    if (jumpTargetTop !== null && Math.abs(el.scrollTop - jumpTargetTop) <= 4) {
      clearJumpLock()
    } else {
      activeOutlineKey.value = jumpLockKey
      return
    }
  }
  const scrollerRect = el.getBoundingClientRect()
  const metrics: { key: string; top: number }[] = []
  for (const item of items) {
    const node = messageEls.get(item.key)
    if (!node) continue
    metrics.push({
      key: item.key,
      top: node.getBoundingClientRect().top - scrollerRect.top + el.scrollTop,
    })
  }
  const composerEl = el.querySelector('.chat-composer-lane') as HTMLElement | null
  const visibleHeight = Math.max(120, el.clientHeight - (composerEl?.offsetHeight ?? 0))
  activeOutlineKey.value = resolveActiveOutlineKey(metrics, el.scrollTop, el.clientHeight, el.scrollHeight, {
    anchorRatio: Math.min(0.32, (visibleHeight * 0.28) / Math.max(1, el.clientHeight)),
  }) ?? items[items.length - 1]!.key
}

function scheduleActiveOutlineSync() {
  if (outlineFrame || disposed) return
  outlineFrame = requestAnimationFrame(() => {
    outlineFrame = 0
    if (!disposed) updateActiveOutlineNow()
  })
}

function jumpToTurn(key: string) {
  const el = scroller.value
  const target = messageEls.get(key)
  if (!el || !target) return
  cancelAnimationFrame(scrollFrame)
  scrollFrame = 0
  forceScroll = false
  followOutput = false
  const scrollerRect = el.getBoundingClientRect()
  const targetTop = target.getBoundingClientRect().top - scrollerRect.top + el.scrollTop
  const maxScroll = Math.max(0, el.scrollHeight - el.clientHeight)
  const destination = Math.max(0, Math.min(maxScroll, Math.round(targetTop - 28)))
  activeOutlineKey.value = key
  jumpLockKey = key
  jumpTargetTop = destination
  window.clearTimeout(jumpLockTimer)
  jumpLockTimer = window.setTimeout(() => {
    clearJumpLock()
    scheduleActiveOutlineSync()
  }, 750)
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  el.scrollTo({ top: destination, behavior: reduced ? 'auto' : 'smooth' })
  if (reduced || Math.abs(el.scrollTop - destination) <= 2) {
    clearJumpLock()
  }
  window.clearTimeout(highlightTimer)
  highlightedKey.value = null
  void nextTick(() => {
    if (disposed) return
    highlightedKey.value = key
    highlightTimer = window.setTimeout(() => {
      if (highlightedKey.value === key) highlightedKey.value = null
    }, 980)
  })
}

watch(
  () => outlineItems.value.map((item) => item.key).join(','),
  () => {
    void nextTick(() => scheduleActiveOutlineSync())
  },
)

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
        scheduleActiveOutlineSync()
      }
      forceScroll = false
    })
  })
}

function onChatScroll() {
  const el = scroller.value
  if (!el) return
  const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  if (jumpLockKey) {
    if (nearBottom && jumpTargetTop !== null && el.scrollHeight - jumpTargetTop - el.clientHeight < 80) {
      followOutput = true
    }
  } else if (nearBottom) {
    followOutput = true
  } else if (el.scrollTop < previousScrollTop - 1) {
    followOutput = false
  }
  previousScrollTop = el.scrollTop
  scheduleActiveOutlineSync()
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
  serverEnded = false
  scheduleUsageRefresh()
  startWatchdog()
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
    clearInterval(watchdogTimer)
    streaming.value = false
    handle = null
    reconnecting.value = retry
    if (retry) schedulePoll(retryDelay)
    else {
      const stale = serverEnded
      serverEnded = false
      finishMessage(assistant)
      store.patchConversation(props.id, { running: false, updated_at: new Date().toISOString() })
      usagePendingSince = 0
      refreshSide()
      // 事件流里没有最终回复的情形（失败、停止、连接挂死）：以服务端保存的这一轮为准重建，不停在中间状态
      if (stale || assistant.execution?.status !== 'completed') void load(true)
    }
  }
}

/** 事件流在处理时可能被网络或代理悄悄掐断而不报错：定期问服务端任务还在不在，已经结束就收尾并按保存的结果重建。 */
function startWatchdog() {
  clearInterval(watchdogTimer)
  watchdogTimer = window.setInterval(async () => {
    if (disposed || !streaming.value) return
    const current = handle
    try {
      const data = await conversationApi.get(props.id)
      if (disposed || !streaming.value || handle !== current || data.running_job) return
      serverEnded = true
      current?.abort()
    } catch {
      /* 网络暂时不通：下一次再看 */
    }
  }, 6000)
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
  <div class="chat-page flex h-full min-h-0 gap-2">
    <div class="chat-workspace app-panel flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-[14px] bg-panel">
      <!-- 顶部 -->
      <header class="flex h-10 shrink-0 items-center gap-2 px-5">
        <div class="flex min-w-0 flex-1 items-center gap-2">
          <input
            v-if="editingTitle"
            v-model="titleDraft"
            class="w-full max-w-md rounded-lg border border-line px-2 py-0.5 text-sm font-semibold outline-none"
            autofocus
            @blur="saveTitle"
            @keydown.enter="saveTitle"
          />
          <!-- 编辑图标平时不占位置（不然标题和后面的「脱敏已关闭」中间空出一大块），悬停时才展开 -->
          <button v-else class="group flex max-w-full items-center text-left" @click="startEditTitle">
            <span class="truncate text-[15px] text-ink/85">{{ detail?.title ?? '…' }}</span>
            <Pencil class="size-3.5 max-w-0 shrink-0 text-faint opacity-0 transition-all group-hover:ml-1.5 group-hover:max-w-3.5 group-hover:opacity-100 group-focus-visible:ml-1.5 group-focus-visible:max-w-3.5 group-focus-visible:opacity-100" />
          </button>
          <span
            v-if="privacySwitch.enabled.value === false"
            class="flex shrink-0 items-center gap-1 rounded-full bg-error/10 px-2 py-0.5 text-xs font-medium text-error"
            title="脱敏已关闭：上传文件和发给模型的内容均不做脱敏"
            data-testid="privacy-off-notice"
          >
            <ShieldOff class="size-3.5" />脱敏已关闭
          </span>
        </div>

        <RouterLink
          v-if="customer"
          :to="{ name: 'customer', params: { id: customer.id } }"
          target="_blank"
          rel="noopener noreferrer"
          class="flex h-8 shrink-0 items-center gap-1.5 rounded-full py-0.5 pl-0.5 pr-2.5 text-xs text-mute transition hover:bg-black/[0.05] hover:text-ink"
          title="在新标签页打开客户详情"
        >
          <span class="flex size-5 shrink-0 items-center justify-center overflow-hidden rounded-full bg-paper">
            <Mascot :variant="customer.mascot" :size="22" :shadow="false" :track="false" />
          </span>
          <span class="max-w-[128px] truncate font-medium">{{ customer.name }}</span>
        </RouterLink>
        <button
          v-else-if="detail"
          type="button"
          class="icon-btn shrink-0"
          :class="{ '!bg-black/[0.06] !text-ink': saveOpen }"
          title="保存到客户：把这次对话的数据、已保存的流程和记忆保存到客户"
          aria-label="保存到客户"
          @click="saveOpen = true"
        >
          <UserRoundPlus class="size-4" aria-hidden="true" />
        </button>

        <button
          v-if="checklist"
          type="button"
          class="icon-btn shrink-0"
          :class="{ '!bg-black/[0.06] !text-ink': checklistOpen }"
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
          class="icon-btn shrink-0"
          :class="{ '!bg-black/[0.06] !text-ink': panelOpen }"
          :title="panelOpen ? '收起工作区面板' : '展开工作区面板'"
          :aria-label="panelOpen ? '收起工作区面板' : '展开工作区面板'"
          :aria-expanded="panelOpen"
          aria-controls="workspace-panel"
          @click="panelTab = 'recipes'; panelOpen = !panelOpen"
        >
          <LayoutDashboard class="size-4" aria-hidden="true" />
        </button>
      </header>

      <div class="chat-body min-h-0 flex-1" :class="{ 'has-directory': checklist && checklistOpen, 'has-outline': showOutline }">
      <Transition name="chat-outline-fade">
        <ChatOutline
          v-if="showOutline"
          class="chat-outline-dock"
          :title="detail?.title ?? ''"
          :items="outlineItems"
          :active-key="activeOutlineKey"
          :fallback-start="detail?.created_at"
          :fallback-end="outlineLastActivity"
          @select="jumpToTurn"
        />
      </Transition>
      <aside v-if="checklist" v-show="checklistOpen" id="task-directory" class="chat-progress" aria-label="本轮任务进度">
        <TaskChecklist :key="checklist.key" :items="checklist.items" :running="checklist.running" />
      </aside>

      <div class="chat-conversation">
      <!-- 消息 -->
      <div
        ref="scroller"
        class="chat-scroller min-h-0 flex-1 overflow-y-auto"
        @scroll.passive="onChatScroll"
        @wheel.passive="clearJumpLock"
        @touchstart.passive="clearJumpLock"
        @pointerdown.passive="clearJumpLock"
      >
      <div ref="content" class="chat-scroll-content">
        <div class="chat-messages pb-6 pt-8">
          <MascotLoader v-if="loading" class="py-24" />

          <!-- 空对话 -->
          <div v-else-if="!messages.length" class="flex flex-col items-center py-16 text-center">
            <RouterLink
              v-if="customer"
              :to="{ name: 'customer', params: { id: customer.id } }"
              target="_blank"
              rel="noopener noreferrer"
              class="inline-flex rounded-2xl transition-transform hover:scale-105 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ink"
              title="在新标签页打开客户详情"
              :aria-label="`在新标签页打开 ${customer.name} 详情`"
            >
              <Mascot :variant="customer.mascot" :size="96" />
            </RouterLink>
            <Mascot v-else variant="blob" :size="96" />
            <h2 class="mt-5 text-xl font-semibold">{{ customer ? `我已经了解「${customer.name}」了` : '把表格拖进来，告诉我要做什么' }}</h2>
            <p class="mt-2 max-w-md text-sm leading-relaxed text-mute">
              {{ customer ? '我会按这个客户的处理逻辑、流程和业务规则来处理。' : '直接说你的需求，我会自动判断处理方式。' }}
            </p>
            <ChatWelcomeCharms />
          </div>

          <div v-else class="space-y-8">
            <div
              v-for="m in messages"
              :key="m.key"
              :ref="(el) => m.role === 'user' && setMessageEl(m.key, el)"
              :data-message-key="m.key"
              class="chat-message-item"
              :class="{ 'is-jump-highlighted': highlightedKey === m.key }"
            >
              <UserMessage v-if="m.role === 'user'" :message="m" :files="messageFiles" @preview="openFilePreview" />
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
                @preview-file="openFilePreview"
                @refresh="refreshSide"
              />
            </div>
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
          <div v-else-if="resumeBlockedReason" class="mb-3 rounded-xl border border-line bg-paper/50 px-3.5 py-3 text-xs text-mute" role="status">{{ resumeBlockedReason }}</div>
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
      @open-file="openFilePreview"
      @refresh="refreshSide"
    />

    <BaseModal v-if="viewing?.preview_only" :key="executionTableKey(viewing)" :title="viewing.label || viewing.name" width="max-w-5xl" @close="viewing = null">
      <p class="mb-3 text-xs leading-5 text-mute">{{ viewing.conversation_title }} · 临时查询共 {{ viewing.rows }} 行 × {{ viewing.cols }} 列，保留前 {{ viewing.preview?.length ?? 0 }} 行预览；完整临时表已释放。</p>
      <DataTable :columns="viewing.columns" :rows="viewing.preview ?? []" max-height="62vh" />
    </BaseModal>
    <TableViewer v-else-if="viewing" :key="executionTableKey(viewing)" :title="viewing.label || viewing.name" :subtitle="viewing.run_title || viewing.conversation_title" :loader="loadTable" @close="viewing = null" />
    <SaveToCustomerDialog v-if="saveOpen" :conversation-id="id" @close="saveOpen = false" @saved="onSaved" />
  </div>
</template>

<style scoped>
.chat-page {
  --chat-header-space: 40px;
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
  background: var(--color-panel);
}
.chat-messages {
  --chat-message-width: calc(680px - 3rem);
  width: calc(100% - 5.5rem);
  max-width: var(--chat-message-width);
  margin-left: max(4rem, calc((100% - var(--chat-message-width)) / 2));
  margin-right: 1.5rem;
}
.chat-message-item.is-jump-highlighted :deep(.user-bubble) {
  animation: chat-turn-jump-pulse 0.95s cubic-bezier(0.22, 1, 0.36, 1);
}
@keyframes chat-turn-jump-pulse {
  0% {
    box-shadow: 0 0 0 0 rgba(11, 11, 12, 0.26);
  }
  35% {
    box-shadow: 0 0 0 6px rgba(11, 11, 12, 0.14);
  }
  100% {
    box-shadow: 0 0 0 11px rgba(11, 11, 12, 0);
  }
}
.chat-outline-fade-enter-active {
  transition: opacity 260ms cubic-bezier(0.22, 1, 0.36, 1), transform 300ms cubic-bezier(0.22, 1, 0.36, 1);
}
.chat-outline-fade-leave-active {
  transition: opacity 160ms ease, transform 180ms ease;
}
.chat-outline-fade-enter-from,
.chat-outline-fade-leave-to {
  opacity: 0;
  transform: translate3d(-8px, 0, 0);
}
@container chat-workspace (max-width: 559px) {
  .chat-outline-dock {
    display: none;
  }
}
@container chat-workspace (min-width: 560px) {
  .chat-body.has-outline .chat-messages {
    width: calc(100% - 8.25rem);
    margin-left: max(6.75rem, calc((100% - var(--chat-message-width)) / 2));
  }
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
@media (prefers-reduced-motion: reduce) {
  .chat-message-item.is-jump-highlighted :deep(.user-bubble) {
    animation: none;
  }
  .chat-outline-fade-enter-active,
  .chat-outline-fade-leave-active {
    transition: none;
  }
}
</style>
