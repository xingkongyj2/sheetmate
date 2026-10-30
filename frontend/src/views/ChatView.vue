<script setup lang="ts">
/**
 * 对话页：中间是消息流和输入框，右侧是执行明细。
 * 顶部：标题（可改）、绑定的客户（或「保存到客户」）、执行明细开关。
 */
import { LoaderCircle, PanelRight, Pencil, Save } from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { conversationApi, fileApi } from '@/api'
import { errorText } from '@/api/http'
import type { StreamHandle } from '@/api/sse'
import type { ConversationDetail, FileInfo, PlanStep } from '@/api/types'
import FunObject from '@/components/brand/FunObject.vue'
import Mascot from '@/components/brand/Mascot.vue'
import AssistantMessage from '@/components/chat/AssistantMessage.vue'
import ChatComposer, { type Attachment } from '@/components/chat/ChatComposer.vue'
import ExecutionPanel from '@/components/chat/ExecutionPanel.vue'
import PlanEditor from '@/components/chat/PlanEditor.vue'
import SaveToCustomerDialog from '@/components/chat/SaveToCustomerDialog.vue'
import UserMessage from '@/components/chat/UserMessage.vue'
import TableViewer from '@/components/common/TableViewer.vue'
import { applyEvent, finishMessage, fromStored, newAssistant, newUser, uid, type UiMessage } from '@/composables/chatModel'
import { useToast } from '@/composables/useToast'
import { useWorkspaceStore } from '@/stores/conversations'
import { pendingStarts } from '@/stores/pending'

const props = defineProps<{ id: string }>()
const router = useRouter()
const store = useWorkspaceStore()
const toast = useToast()

const detail = ref<ConversationDetail | null>(null)
const messages = ref<UiMessage[]>([])
const loading = ref(true)
const streaming = ref(false)
const backgroundRunning = ref(false)
const text = ref('')
const planMode = ref(false)
const attachments = ref<Attachment[]>([])
const recentlyUploaded = ref<FileInfo[]>([])
const plan = ref<{ title: string; steps: PlanStep[]; message: string; attachments: string[] } | null>(null)
const planning = ref(false)
const panelOpen = ref(false)
const selectedKey = ref<string | null>(null)
const viewing = ref<string | null>(null)
const viewingFile = ref<FileInfo | null>(null)
const saveOpen = ref(false)
const editingTitle = ref(false)
const titleDraft = ref('')
const scroller = ref<HTMLElement | null>(null)
const content = ref<HTMLElement | null>(null)
let handle: StreamHandle | null = null
let pollTimer = 0
let sideRefreshVersion = 0

const selectedMessage = computed(() => {
  const assistants = messages.value.filter((m) => m.role === 'assistant')
  return assistants.find((m) => m.key === selectedKey.value) ?? assistants[assistants.length - 1] ?? null
})
const customer = computed(() => detail.value?.customer ?? null)
const messageFiles = computed(() => {
  const files = new Map(recentlyUploaded.value.map((file) => [file.file_id, file]))
  for (const file of detail.value?.files ?? []) files.set(file.file_id, file)
  return [...files.values()]
})

function acknowledgeUploads(files: FileInfo[]) {
  const ids = new Set(files.map((file) => file.file_id))
  recentlyUploaded.value = recentlyUploaded.value.filter((file) => !ids.has(file.file_id))
}

// ------------------------------------------------------------------ 加载

async function load(silent = false) {
  const version = sideRefreshVersion
  if (!silent) loading.value = true
  try {
    const data = await conversationApi.get(props.id)
    if (detail.value && version !== sideRefreshVersion) data.files = detail.value.files
    detail.value = data
    acknowledgeUploads(data.files)
    if (!streaming.value) {
      messages.value = data.messages.map((m) => reactive(fromStored(m)) as UiMessage)
      scrollToBottom(true) // 打开会话（包括后台任务跑完刷新）都停在最新一条
    }
    backgroundRunning.value = data.running && !streaming.value
    if (backgroundRunning.value) schedulePoll()
  } catch (err) {
    toast.error(errorText(err))
    router.replace({ name: 'home' })
  } finally {
    loading.value = false
  }
}

/** 页面刷新时如果上一次的任务还在后台跑，定时刷新直到完成 */
function schedulePoll() {
  clearTimeout(pollTimer)
  pollTimer = window.setTimeout(async () => {
    await load(true)
  }, 3000)
}

async function refreshSide() {
  const version = ++sideRefreshVersion
  try {
    const data = await conversationApi.get(props.id)
    if (detail.value && version === sideRefreshVersion) {
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
  const pending = pendingStarts.get(props.id)
  if (pending) {
    pendingStarts.delete(props.id)
    if (pending.plan) {
      attachments.value = pending.attachments.flatMap((id) => {
        const file = detail.value?.files.find((item) => item.file_id === id)
        return file ? [{ key: id, name: file.filename, kind: file.kind === 'expected' ? 'expected' as const : 'input' as const, status: 'done' as const }] : []
      })
      await requestPlan(pending.message, pending.attachments)
    } else await send(pending.message, { attachments: pending.attachments })
  }
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  clearTimeout(pollTimer)
  handle?.abort() // 只是断开连接，后台任务会继续执行并保存结果
})

// ------------------------------------------------------------------ 发送

function scrollToBottom(force = false) {
  nextTick(() => {
    const el = scroller.value
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 240
    if (force || nearBottom) el.scrollTo({ top: el.scrollHeight, behavior: force ? 'auto' : 'smooth' })
    // 强制滚动时 Markdown / 表格可能还没排完版，下一帧再贴底一次
    if (force) requestAnimationFrame(() => el.scrollTo({ top: el.scrollHeight, behavior: 'auto' }))
  })
}

watch(
  () => {
    const last = messages.value[messages.value.length - 1]
    return last ? `${last.content.length}:${last.timeline.length}:${last.artifacts.length}` : ''
  },
  () => scrollToBottom(),
)

async function onSend() {
  const message = text.value.trim()
  if (!message || streaming.value) return
  if (attachments.value.some((a) => a.status === 'uploading')) {
    toast.info('文件还在上传，请稍等')
    return
  }
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
    const result = await conversationApi.plan(props.id, message)
    plan.value = { ...result, message, attachments: [...ids] }
  } catch (err) {
    toast.error(errorText(err))
    messages.value.pop()
    text.value = message
  } finally {
    planning.value = false
    scrollToBottom(true)
  }
}

async function runPlan(steps: PlanStep[]) {
  if (!plan.value) return
  const message = plan.value.message
  const ids = plan.value.attachments
  plan.value = null
  messages.value.pop() // 计划阶段临时显示的用户消息，正式发送时重新加上
  const draftAttachments = attachments.value
  const running = send(message, { planSteps: steps, attachments: ids })
  attachments.value = draftAttachments.filter((item) => !ids.includes(item.key))
  await running
}

function cancelPlan() {
  if (plan.value) text.value = plan.value.message
  plan.value = null
  messages.value.pop()
}

async function send(message: string, opts: { attachments?: string[]; planSteps?: PlanStep[] } = {}) {
  streaming.value = true
  messages.value.push(reactive(newUser(message, { attachments: opts.attachments ?? [], plan: opts.planSteps })) as UiMessage)
  const assistant = reactive(newAssistant()) as UiMessage
  messages.value.push(assistant)
  selectedKey.value = assistant.key
  attachments.value = []
  scrollToBottom(true)
  store.patchConversation(props.id, { running: true })

  handle = conversationApi.chat(
    props.id,
    { message, plan_steps: opts.planSteps, attachments: opts.attachments },
    (event) => {
      applyEvent(assistant, event)
      if (event.type === 'title' && detail.value) {
        detail.value.title = event.title
        store.patchConversation(props.id, { title: event.title })
      }
      if (event.type === 'table' || event.type === 'file' || event.type === 'recipes_changed') refreshSide()
    },
  )
  try {
    await handle.done
  } catch (err) {
    if ((err as Error).name !== 'AbortError') assistant.error = errorText(err)
  } finally {
    finishMessage(assistant)
    streaming.value = false
    handle = null
    store.patchConversation(props.id, { running: false, updated_at: new Date().toISOString() })
    refreshSide()
  }
}

async function stop() {
  await conversationApi.stop(props.id).catch(() => undefined)
}

// ------------------------------------------------------------------ 附件 / 模式 / 标题

async function attach(files: File[], kind: 'input' | 'expected') {
  for (const file of files) {
    const local: Attachment = reactive({ key: uid('f'), name: file.name, kind, status: 'uploading' })
    attachments.value.push(local)
    try {
      const result = await conversationApi.upload(props.id, file, kind)
      sideRefreshVersion++
      recentlyUploaded.value = [...recentlyUploaded.value.filter((item) => item.file_id !== result.file.file_id), result.file]
      if (detail.value) detail.value.files = [result.file, ...detail.value.files.filter((item) => item.file_id !== result.file.file_id)]
      local.key = result.file.file_id
      local.status = 'done'
      local.tables = result.tables.map((t) => t.name)
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
  const page = await conversationApi.tablePage(props.id, viewing.value!, offset, limit)
  return { columns: page.columns.map((c) => c.name), rows: page.rows, total: page.total }
}

async function loadFile(offset: number, limit: number) {
  const file = viewingFile.value!
  const page = await fileApi.preview(file.file_id, file.sheets[0]?.sheet, offset, limit)
  return { columns: page.columns, rows: page.rows, total: page.total }
}

function answer(option: string) {
  if (!streaming.value) send(option)
}
</script>

<template>
  <div class="flex h-full min-h-0">
    <div class="flex min-w-0 flex-1 flex-col">
      <!-- 顶部 -->
      <header class="flex h-14 shrink-0 items-center gap-3 border-b border-line px-5">
        <div class="min-w-0 flex-1">
          <input
            v-if="editingTitle"
            v-model="titleDraft"
            class="w-full max-w-md rounded-lg border border-line px-2 py-1 text-sm font-semibold outline-none"
            autofocus
            @blur="saveTitle"
            @keydown.enter="saveTitle"
          />
          <button v-else class="group flex max-w-full items-center gap-1.5 text-left" @click="startEditTitle">
            <span class="truncate text-[15px] font-semibold">{{ detail?.title ?? '…' }}</span>
            <Pencil class="size-3.5 shrink-0 text-faint opacity-0 group-hover:opacity-100" />
          </button>
        </div>

        <RouterLink
          v-if="customer"
          :to="{ name: 'customer', params: { id: customer.id } }"
          class="flex items-center gap-1.5 rounded-full border border-line py-0.5 pl-0.5 pr-3 text-sm transition hover:border-ink/30"
          title="打开客户页"
        >
          <span class="flex size-7 items-center justify-center overflow-hidden rounded-full bg-paper">
            <Mascot :variant="customer.mascot" :size="30" :shadow="false" :track="false" />
          </span>
          <span class="max-w-[160px] truncate font-medium">{{ customer.name }}</span>
        </RouterLink>
        <button v-else-if="detail" class="btn-line py-1.5" title="把这次对话的数据、流程和记忆保存到客户" @click="saveOpen = true">
          <Save class="size-4" />保存到客户
        </button>

        <button class="icon-btn" :class="{ '!bg-paper !text-ink': panelOpen }" title="执行明细" @click="panelOpen = !panelOpen">
          <PanelRight class="size-4" />
        </button>
      </header>

      <!-- 消息 -->
      <div ref="scroller" class="min-h-0 flex-1 overflow-y-auto">
        <div ref="content" class="mx-auto w-full max-w-[820px] px-6 pb-6 pt-8">
          <div v-if="loading" class="flex justify-center py-20 text-mute"><LoaderCircle class="size-5 animate-spin" /></div>

          <!-- 空对话 -->
          <div v-else-if="!messages.length && !plan" class="flex flex-col items-center py-16 text-center">
            <Mascot :variant="customer?.mascot ?? 'blob'" :size="96" />
            <h2 class="mt-5 text-xl font-semibold">{{ customer ? `我已经了解「${customer.name}」了` : '把表格拖进来，告诉我要做什么' }}</h2>
            <p class="mt-2 max-w-md text-sm leading-relaxed text-mute">
              {{ customer ? '我会按这个客户的处理逻辑、流程和业务规则来处理。' : '直接说你的需求，我会自动判断处理方式。修改表格时保留原有格式，源文件始终保留。' }}
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
                :customer-id="customer?.id"
                :mascot="customer?.mascot"
                @answer="answer"
                @open-table="(name) => (viewing = name)"
              />
            </template>

            <div v-if="planning" class="flex items-center gap-3 text-sm text-mute">
              <Mascot :size="34" mood="thinking" :shadow="false" />正在拆解步骤…
            </div>
            <PlanEditor v-if="plan" :title="plan.title" :steps="plan.steps" @run="runPlan" @cancel="cancelPlan" />
          </div>

          <div v-if="backgroundRunning" class="mt-6 flex items-center gap-2 rounded-xl bg-info/5 px-4 py-3 text-sm text-info">
            <LoaderCircle class="size-4 animate-spin" />上一条消息还在后台处理中，完成后会自动显示结果…
          </div>
        </div>
      </div>

      <!-- 输入框 -->
      <div class="shrink-0 px-6 pb-5">
        <div class="mx-auto w-full max-w-[820px]">
          <ChatComposer
            v-model="text"
            v-model:plan="planMode"
            :attachments="attachments"
            :busy="streaming"
            placeholder="继续追问、提出修改要求，或上传新的表格…"
            @attach="attach"
            @remove="removeAttachment"
            @send="onSend"
            @stop="stop"
          />
          <p class="mt-2 text-center text-[11px] text-faint">AI 生成的结果可能存在错误，请核对重要数据后再使用。</p>
        </div>
      </div>
    </div>

    <ExecutionPanel
      v-if="panelOpen"
      :detail="detail"
      :message="selectedMessage"
      @close="panelOpen = false"
      @open-table="(name) => (viewing = name)"
      @refresh="refreshSide"
    />

    <TableViewer v-if="viewing" :key="viewing" :title="viewing" :loader="loadTable" @close="viewing = null" />
    <TableViewer v-if="viewingFile" :key="viewingFile.file_id" :title="viewingFile.filename" :subtitle="viewingFile.sheets[0] ? `工作表：${viewingFile.sheets[0].sheet}${viewingFile.sheets.length > 1 ? `（共 ${viewingFile.sheets.length} 个工作表）` : ''}` : ''" :loader="loadFile" @close="viewingFile = null" />
    <SaveToCustomerDialog v-if="saveOpen" :conversation-id="id" @close="saveOpen = false" @saved="onSaved" />
  </div>
</template>
