<script setup lang="ts">
/**
 * 对话页：中间是消息流和输入框，右侧是执行明细。
 * 顶部：标题（可改）、绑定的客户（或「保存到客户」）、执行明细开关。
 */
import { LoaderCircle, PanelRight, Pencil, Save } from '@lucide/vue'
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { conversationApi, runApi } from '@/api'
import { ApiError, errorText } from '@/api/http'
import type { StreamHandle } from '@/api/sse'
import type { AgentEvent, ConversationDetail, FileInfo, PlanStep, TableBrief } from '@/api/types'
import FunObject from '@/components/brand/FunObject.vue'
import Mascot from '@/components/brand/Mascot.vue'
import AssistantMessage from '@/components/chat/AssistantMessage.vue'
import ChatComposer, { type Attachment } from '@/components/chat/ChatComposer.vue'
import ExecutionPanel from '@/components/chat/ExecutionPanel.vue'
import PlanEditor from '@/components/chat/PlanEditor.vue'
import SaveToCustomerDialog from '@/components/chat/SaveToCustomerDialog.vue'
import UserMessage from '@/components/chat/UserMessage.vue'
import TableViewer from '@/components/common/TableViewer.vue'
import DataTable from '@/components/common/DataTable.vue'
import BaseModal from '@/components/common/BaseModal.vue'
import { attachmentMessage } from '@/composables/attachmentFiles'
import { applyEvent, finishMessage, fromStoredMessages, markRecipeDeleted, newAssistant, newUser, uid, type UiMessage } from '@/composables/chatModel'
import { useToast } from '@/composables/useToast'
import { formatFileReference, formatTableReference, parseTableReferences, serializeTableReferences, type FileReference, type TableReference } from '@/composables/tableReferences'
import { conversationTables, executionTableKey } from '@/composables/executionPresentation'
import { useWorkspaceStore } from '@/stores/conversations'
import { pendingStarts } from '@/stores/pending'

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
const text = ref('')
const planMode = ref(false)
const attachments = ref<Attachment[]>([])
const composer = ref<InstanceType<typeof ChatComposer> | null>(null)
const recentlyUploaded = ref<FileInfo[]>([])
const uploadedReferences = ref<TableReference[]>([])
const plan = ref<{ title: string; steps: PlanStep[]; message: string; attachments: string[] } | null>(null)
const planning = ref(false)
const panelOpen = ref(false) // 执行明细默认关闭，需要时点右上角打开
const panelTab = ref<'recipes' | 'files' | 'data'>('recipes')
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
let sideRefreshVersion = 0
let loadVersion = 0
let disposed = false
let scrollFrame = 0
let forceScroll = false
let followOutput = true
let previousScrollTop = 0

const selectedMessage = computed(() => {
  const assistants = messages.value.filter((m) => m.role === 'assistant')
  return assistants.find((m) => m.key === selectedKey.value) ?? assistants[assistants.length - 1] ?? null
})
const customer = computed(() => detail.value?.customer ?? null)
// 消息中的表名只在当前会话里解析；全客户的同名表由执行明细按来源定位。
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

function editableMessage(message: string) {
  return parseTableReferences(message, tableReferences.value, fileReferences.value).map((segment) => {
    if (segment.type === 'table') return formatTableReference(segment.reference)
    if (segment.type === 'file') return formatFileReference(segment.reference)
    return segment.text
  }).join('')
}

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
  clearTimeout(pollTimer)
  if (!silent) loading.value = true
  try {
    const data = await conversationApi.get(props.id)
    if (disposed || requestVersion !== loadVersion) return
    if (detail.value && version !== sideRefreshVersion) data.files = detail.value.files
    detail.value = data
    acknowledgeUploads(data.files)
    if (!streaming.value) {
      messages.value = fromStoredMessages(data.messages, data.running_job?.message_id).map((m) => reactive(m) as UiMessage)
      scrollToBottom(true) // 打开会话（包括后台任务跑完刷新）都停在最新一条
      reconnecting.value = false
      store.patchConversation(props.id, { running: data.running, title: data.title })
      const job = data.running_job
      if (job) {
        const assistant = reactive(newAssistant()) as UiMessage
        messages.value.push(assistant)
        selectedKey.value = assistant.key
        void receiveStream(assistant, (onEvent) => conversationApi.events(props.id, job.id, onEvent), true)
      }
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
  const version = ++sideRefreshVersion
  try {
    const data = await conversationApi.get(props.id)
    if (!disposed && detail.value && version === sideRefreshVersion) {
      detail.value.tables = data.tables
      detail.value.files = data.files
      detail.value.recipes = data.recipes
      detail.value.title = data.title
      detail.value.customer = data.customer
      acknowledgeUploads(data.files)
    }
  } catch {
    /* 忽略 */
  }
}

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
    if (pending.plan) {
      attachments.value = pending.attachments.flatMap((id) => {
        const file = detail.value?.files.find((item) => item.file_id === id)
        return file ? [{ key: id, sourceId: id, name: file.filename, kind: file.kind === 'expected' ? 'expected' as const : 'input' as const, status: 'done' as const, mediaType: file.media_type, previewUrl: file.preview_url ?? undefined }] : []
      })
      await requestPlan(pending.message, pending.attachments)
    } else await send(pending.message, { attachments: pending.attachments })
  }
})

onBeforeUnmount(() => {
  disposed = true
  resizeObserver?.disconnect()
  clearTimeout(pollTimer)
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
  if (streaming.value || reconnecting.value || planning.value) return
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
  const ids = attachments.value.filter((a) => a.status === 'done').map((a) => a.key)
  text.value = ''
  if (planMode.value) await requestPlan(message, ids)
  else await send(message, { attachments: ids })
}

async function requestPlan(message: string, ids: string[]) {
  planning.value = true
  messages.value.push(reactive(newUser(message, { attachments: ids })) as UiMessage)
  scrollToBottom(true)
  try {
    const result = await conversationApi.plan(props.id, serializeTableReferences(message, tableReferences.value, fileReferences.value), ids)
    plan.value = { ...result, message, attachments: [...ids] }
  } catch (err) {
    toast.error(errorText(err))
    messages.value.pop()
    text.value = editableMessage(message)
  } finally {
    planning.value = false
    scrollToBottom(true)
  }
}

async function runPlan(steps: PlanStep[]) {
  if (!plan.value) return
  const message = plan.value.message
  const ids = plan.value.attachments
  const draftAttachments = attachments.value
  plan.value = null
  messages.value.pop() // 计划阶段临时显示的用户消息，正式发送时重新加上
  const running = send(message, { planSteps: steps, attachments: ids })
  attachments.value = draftAttachments.filter((item) => !ids.includes(item.key))
  await running
}

function cancelPlan() {
  if (plan.value) text.value = editableMessage(plan.value.message)
  plan.value = null
  messages.value.pop()
}

async function send(message: string, opts: { attachments?: string[]; planSteps?: PlanStep[] } = {}) {
  if (disposed || streaming.value || reconnecting.value) return
  message = serializeTableReferences(message, tableReferences.value, fileReferences.value)
  messages.value.push(reactive(newUser(message, { attachments: opts.attachments ?? [], plan: opts.planSteps })) as UiMessage)
  const assistant = reactive(newAssistant()) as UiMessage
  messages.value.push(assistant)
  selectedKey.value = assistant.key
  attachments.value = []
  scrollToBottom(true)
  store.patchConversation(props.id, { running: true })

  await receiveStream(assistant, (onEvent) => conversationApi.chat(
    props.id, { message, plan_steps: opts.planSteps, attachments: opts.attachments }, onEvent,
  ))
}

async function receiveStream(assistant: UiMessage, open: (onEvent: (event: AgentEvent) => void) => StreamHandle, resuming = false) {
  streaming.value = true
  let received = false
  let retry = false
  let retryDelay = 1000
  const currentHandle = open((event) => {
    if (disposed) return
    received = true
    applyEvent(assistant, event)
    if (event.type === 'recipe_deleted') markRecipeDeleted(messages.value, event.recipe_id)
    if (event.type === 'title' && detail.value) {
      detail.value.title = event.title
      store.patchConversation(props.id, { title: event.title })
    }
    if (event.type === 'table' || event.type === 'file' || event.type === 'recipes_changed' || event.type === 'recipe_deleted') refreshSide()
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
      if (!retry) assistant.error = errorText(err)
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
      refreshSide()
    }
  }
}

async function stop() {
  await conversationApi.stop(props.id).catch(() => undefined)
}

// ------------------------------------------------------------------ 附件 / 标题

async function attach(files: File[], kind: 'input' | 'expected') {
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
  if (!title || !detail.value || title === detail.value.title) return
  detail.value.title = title
  await conversationApi.update(props.id, { title }).catch(() => undefined)
  store.patchConversation(props.id, { title })
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
  <div class="flex h-full min-h-0">
    <div class="flex min-w-0 flex-1 flex-col">
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

        <button class="icon-btn size-7 shrink-0" :class="{ '!bg-paper !text-ink': panelOpen }" title="执行明细" aria-label="执行明细" @click="panelTab = 'recipes'; panelOpen = !panelOpen">
          <PanelRight class="size-4" />
        </button>
      </header>

      <!-- 消息 -->
      <div ref="scroller" class="min-h-0 flex-1 overflow-y-auto" @scroll.passive="onChatScroll">
        <div ref="content" class="chat-messages pb-6 pt-8">
          <div v-if="loading" class="flex justify-center py-20 text-mute"><LoaderCircle class="size-5 animate-spin" /></div>

          <!-- 空对话 -->
          <div v-else-if="!messages.length && !plan" class="flex flex-col items-center py-16 text-center">
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
                :answers-closed="messages.at(-1)?.key !== m.key"
                :customer-id="customer?.id"
                :mascot="customer?.mascot"
                @answer="answer"
                @preview-file="(file) => (viewingFile = file)"
              />
            </template>

            <div v-if="planning" class="flex items-center gap-3 text-sm text-mute">
              <Mascot :size="34" mood="thinking" :shadow="false" />正在拆解步骤…
            </div>
            <PlanEditor v-if="plan" :title="plan.title" :steps="plan.steps" @run="runPlan" @cancel="cancelPlan" />
          </div>

          <div v-if="reconnecting" class="mt-6 flex items-center gap-2 rounded-xl bg-info/5 px-4 py-3 text-sm text-info">
            <LoaderCircle class="size-4 animate-spin" />回复连接已中断，正在重新连接…
          </div>
        </div>
      </div>

      <!-- 输入框 -->
      <div class="mx-auto w-full max-w-[680px] shrink-0 px-6 pb-5">
        <div class="w-full">
          <ChatComposer
            ref="composer"
            v-model="text"
            v-model:plan="planMode"
            :attachments="attachments"
            :table-references="tableReferences"
            :file-references="fileReferences"
            :references-loading="referencesLoading"
            :references-error="referencesError"
            :busy="streaming || reconnecting || planning"
            :stoppable="!planning"
            large
            appearance="card"
            placeholder="继续追问..."
            @attach="attach"
            @remove="removeAttachment"
            @preview="previewAttachment"
            @send="onSend"
            @stop="stop"
          />
          <p class="mt-2 text-center text-[11px] text-faint">AI 生成的结果可能存在错误，请核对重要数据后再使用</p>
        </div>
      </div>
    </div>

    <ExecutionPanel
      v-if="panelOpen"
      :detail="detail"
      :message="selectedMessage"
      v-model:active-tab="panelTab"
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
.chat-messages {
  --chat-message-width: calc(680px - 3rem);
  width: calc(100% - 5.5rem);
  max-width: var(--chat-message-width);
  margin-left: max(4rem, calc((100% - var(--chat-message-width)) / 2));
  margin-right: 1.5rem;
}
</style>
