<script setup lang="ts">
/** 客户/会话资源与单次回答的处理流程；表名按来源工作区定位。 */
import { Download, Eye, FileCheck2, FileSpreadsheet, Maximize2, Trash2 } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { conversationApi, fileApi } from '@/api'
import type { ConversationDetail, FileInfo, RecipeBrief, RecipeFlowData, TableBrief } from '@/api/types'
import RecipeFlow from '@/components/recipe/RecipeFlow.vue'
import type { RecipeProgress, UiMessage } from '@/composables/chatModel'
import { executionFileGroups, executionRecipeSections, executionTableKey, executionTables } from '@/composables/executionPresentation'
import { progressFlow } from '@/composables/recipeFlow'
import { useConfirm } from '@/composables/useConfirm'
import { usePrivacyStatus } from '@/composables/usePrivacyStatus'
import ExecutionPrivacy from './ExecutionPrivacy.vue'
import PrivacyStatusIcon from './PrivacyStatusIcon.vue'
import ExecutionStatusNotice from './ExecutionStatusNotice.vue'
import ExecutionUsage from './ExecutionUsage.vue'

type Tab = 'recipes' | 'files' | 'data' | 'usage' | 'privacy'
const props = withDefaults(defineProps<{
  detail: ConversationDetail | null
  message: UiMessage | null
  messages?: UiMessage[]
  refreshingUsage?: boolean
  activeTab?: Tab
  width: number
}>(), { activeTab: 'recipes', messages: () => [], refreshingUsage: false })
const emit = defineEmits<{
  close: []; openTable: [table: TableBrief]; openFile: [file: FileInfo]; refresh: []
  'update:activeTab': [tab: Tab]
  'update:width': [width: number]
}>()
const { confirmDelete } = useConfirm()
const tab = computed({ get: () => props.activeTab, set: (value: Tab) => emit('update:activeTab', value) })
const tabs = [
  { key: 'recipes', label: '流程' }, { key: 'files', label: '文件' },
  { key: 'data', label: '数据' }, { key: 'usage', label: '消耗' }, { key: 'privacy', label: '脱敏' },
] as const
const progresses = computed(() => (props.message?.artifacts ?? []).flatMap((artifact) => artifact.kind === 'recipe' ? [artifact.progress] : []))
const recipeSections = computed(() => executionRecipeSections(props.detail?.recipes ?? [], progresses.value))
const tables = computed(() => executionTables(props.detail?.tables ?? []))
const fileGroups = computed(() => executionFileGroups(props.detail?.files ?? []).filter((group) => group.files.length))
// 每个表格文件有没有做过脱敏识别：打开「文件」tab 时才取，刚上传的文件会自己更新状态
const privacy = usePrivacyStatus(() => (tab.value === 'files' ? fileGroups.value.flatMap((group) => group.files.map((file) => file.file_id)) : []))
const emptyState = computed(() => {
  if (tab.value === 'recipes' && !recipeSections.value.saved.length && !recipeSections.value.progresses.length) {
    return { emoji: '🙃', text: '暂无已保存的流程' }
  }
  if (tab.value === 'files' && !fileGroups.value.length) return { emoji: '🫠', text: '还没有文件' }
  if (tab.value === 'data' && !tables.value.length) return { emoji: '😴', text: '暂无中间数据' }
  return null
})
const viewportWidth = ref(window.innerWidth)
const resizing = ref(false)
const maxWidth = computed(() => Math.min(600, viewportWidth.value))
const minWidth = computed(() => Math.min(280, maxWidth.value))
const panelWidth = computed(() => clampWidth(props.width))
let finishResize: (() => void) | undefined

function clampWidth(width: number) {
  return Math.min(maxWidth.value, Math.max(minWidth.value, Math.round(width)))
}
function setWidth(width: number) {
  emit('update:width', clampWidth(width))
}
function startResize(event: PointerEvent) {
  if (event.button !== 0 || !event.isPrimary || minWidth.value === maxWidth.value) return
  event.preventDefault()
  finishResize?.()
  const handle = event.currentTarget as HTMLElement
  const pointerId = event.pointerId
  const startX = event.clientX
  const startWidth = panelWidth.value
  const root = document.documentElement
  const previousCursor = root.style.cursor
  const previousSelection = root.style.userSelect
  handle.focus()
  resizing.value = true
  root.style.cursor = 'col-resize'
  root.style.userSelect = 'none'

  const move = (next: PointerEvent) => {
    if (next.pointerId !== pointerId) return
    next.preventDefault()
    setWidth(startWidth + startX - next.clientX)
  }
  const end = (next: PointerEvent) => {
    if (next.pointerId === pointerId) finish()
  }
  const finish = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', end)
    window.removeEventListener('pointercancel', end)
    window.removeEventListener('blur', finish)
    handle.removeEventListener('lostpointercapture', end)
    root.style.cursor = previousCursor
    root.style.userSelect = previousSelection
    resizing.value = false
    finishResize = undefined
    if (handle.hasPointerCapture(pointerId)) handle.releasePointerCapture(pointerId)
  }
  finishResize = finish
  window.addEventListener('pointermove', move, { passive: false })
  window.addEventListener('pointerup', end)
  window.addEventListener('pointercancel', end)
  window.addEventListener('blur', finish)
  handle.addEventListener('lostpointercapture', end)
  try { handle.setPointerCapture(pointerId) } catch { /* Window listeners also support hosts without pointer capture. */ }
}
function resizeWithKey(event: KeyboardEvent) {
  let width: number
  if (event.key === 'ArrowLeft') width = panelWidth.value + 20
  else if (event.key === 'ArrowRight') width = panelWidth.value - 20
  else if (event.key === 'Home') width = minWidth.value
  else if (event.key === 'End') width = maxWidth.value
  else return
  event.preventDefault()
  setWidth(width)
}
function onViewportResize() {
  finishResize?.()
  viewportWidth.value = window.innerWidth
}

function sourceLabel(table: TableBrief): string {
  return table.run_id ? table.run_title || `运行 ${table.run_id}` : table.conversation_title || '当前会话'
}
async function drop(table: TableBrief) {
  if (!props.detail || table.run_id || table.preview_only) return
  const conversationId = props.detail.id
  if (!await confirmDelete({
    title: '删除这张表？', target: table.name,
    description: `这张计算结果表将从「${sourceLabel(table)}」的工作区删除，上传的源文件会保留。`,
    confirmText: '删除表格',
    action: () => conversationApi.dropTable(conversationId, table.name, table.conversation_id ?? undefined),
  })) return
  emit('refresh')
}
async function removeFile(file: FileInfo) {
  if (!props.detail) return
  const conversationId = props.detail.id
  if (!await confirmDelete({
    title: '删除这个文件？', target: file.filename,
    description: '删除后，这个文件不再出现在工作区里。',
    confirmText: '删除文件',
    action: () => fileApi.remove(file.file_id, undefined, undefined, conversationId),
  })) return
  emit('refresh')
}
// 每个流程的全屏入口：行里的按钮调用对应 RecipeFlow 的 openFullscreen
const flows = new Map<string, { openFullscreen: () => Promise<void> }>()
function setFlow(key: string, el: unknown) {
  if (el) flows.set(key, el as { openFullscreen: () => Promise<void> })
  else flows.delete(key)
}
function stepFlow(recipe: RecipeBrief): RecipeFlowData {
  return recipe.flow ?? {
    inputs: recipe.input_patterns.map((input) => ({ alias: input.alias, kind: input.kind, description: input.description, file_pattern: input.file_pattern })),
    steps: recipe.steps.map((step) => ({ ...step, action: '', formula_kind: step.formula_kind as 'builtin' | 'customer', output: '', inputs: [] })),
    outputs: recipe.outputs.map((name) => ({ name, source: '' })),
  }
}
function progressRecipeFlow(progress: RecipeProgress, recipe?: RecipeBrief): RecipeFlowData {
  return progress.flow || progress.saved?.flow || progress.steps.length || !recipe ? progressFlow(progress) : stepFlow(recipe)
}
function progressStatus(progress: RecipeProgress): string {
  if (progress.status === 'failed' || progress.failed || ['failed', 'escalated'].includes(progress.runStatus ?? '')) return 'failed'
  if (progress.status !== 'done') return 'running'
  return progress.runStatus === 'warning' ? 'warning' : 'success'
}
function onKey(event: KeyboardEvent) {
  if (event.key === 'Escape' && !document.querySelector('[role="dialog"]')) emit('close')
}
onMounted(() => {
  window.addEventListener('keydown', onKey)
  window.addEventListener('resize', onViewportResize)
})
onBeforeUnmount(() => {
  finishResize?.()
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('resize', onViewportResize)
})
</script>

<template>
  <button type="button" class="execution-backdrop" aria-label="收起工作区面板" @click="emit('close')" />
  <aside id="workspace-panel" class="execution-panel" :class="{ 'is-resizing': resizing }" :style="{ width: `${panelWidth}px` }" aria-label="工作区面板">
    <div
      class="execution-resizer"
      role="separator"
      :tabindex="maxWidth > minWidth ? 0 : -1"
      aria-label="调整工作区面板宽度"
      aria-orientation="vertical"
      aria-controls="workspace-panel"
      :aria-valuemin="minWidth"
      :aria-valuemax="maxWidth"
      :aria-valuenow="panelWidth"
      :aria-valuetext="`${panelWidth} 像素`"
      title="拖动调整宽度"
      @pointerdown="startResize"
      @keydown="resizeWithKey"
    />
    <div class="flex h-11 shrink-0 items-center border-b border-line px-4">
      <span class="text-sm font-semibold">工作区面板</span>
    </div>
    <div class="flex shrink-0 gap-1 border-b border-line px-3 py-2 text-[13px]" role="tablist" aria-label="工作区面板分类">
      <button
        v-for="item in tabs"
        :id="`execution-tab-${item.key}`"
        :key="item.key"
        type="button"
        role="tab"
        :aria-selected="tab === item.key"
        aria-controls="execution-content"
        class="shrink-0 rounded-full px-2.5 py-1 transition"
        :class="tab === item.key ? 'bg-ink text-white' : 'text-mute hover:bg-paper hover:text-ink'"
        @click="tab = item.key"
      >
        {{ item.label }}
      </button>
    </div>

    <div id="execution-content" role="tabpanel" :aria-labelledby="`execution-tab-${tab}`" class="min-h-0 flex-1 overflow-y-auto p-4" :class="{ 'flex flex-col': emptyState }">
      <ExecutionStatusNotice v-if="message && tab !== 'usage'" class="mb-4 shrink-0" :message="message" />
      <div v-if="emptyState" class="flex flex-1 flex-col items-center justify-center gap-3 py-8 text-center text-sm text-faint">
        <span class="text-2xl leading-none" aria-hidden="true">{{ emptyState.emoji }}</span>
        <p>{{ emptyState.text }}</p>
      </div>
      <ExecutionUsage v-else-if="tab === 'usage'" :message="message" :messages="messages" :refreshing="refreshingUsage" @refresh="emit('refresh')" />
      <ExecutionPrivacy v-else-if="tab === 'privacy'" :detail="detail" :message="message" />
      <template v-else-if="tab === 'recipes'">
        <!-- 每个流程只留一行：标题和全屏按钮；版本、状态、流程图、说明都在全屏里看 -->
        <article v-for="{ progress, recipe, version } in recipeSections.progresses" :key="progress.key" class="mb-2 min-w-0">
          <div class="flex min-w-0 items-center gap-2 rounded-xl border border-line px-3 py-2.5">
            <h3 class="min-w-0 flex-1 truncate text-[15px] font-semibold" :title="recipe?.name || progress.saved?.name || progress.recipeName || '处理流程'">{{ recipe?.name || progress.saved?.name || progress.recipeName || '处理流程' }}</h3>
            <button type="button" class="icon-btn shrink-0" title="全屏" aria-label="全屏查看流程图" aria-haspopup="dialog" :disabled="!(progress.flow || progress.saved?.flow || progress.steps.length || recipe)" @click="flows.get(progress.key)?.openFullscreen()"><Maximize2 class="size-4" /></button>
          </div>
          <RecipeFlow
            v-if="progress.flow || progress.saved?.flow || progress.steps.length || recipe"
            :ref="(el) => setFlow(progress.key, el)"
            toolbar-placement="hidden" :inline-canvas="false"
            :flow="progressRecipeFlow(progress, recipe)"
            :states="progress.steps"
            :context="{
              title: recipe?.name || progress.saved?.name || progress.recipeName,
              recipeId: progress.saved?.recipe_id || progress.recipeId || progress.recipe?.id,
              version, recipe: recipe?.recipe || progress.recipe, runId: progress.runId,
              customerId: recipe?.customer_id || detail?.customer?.id, conversationId: detail?.id,
              files: detail?.files, resultFiles: progress.files || (progress.file ? [progress.file] : []),
              running: !['done', 'failed'].includes(progress.status), failed: progressStatus(progress) === 'failed', deleted: progress.deleted,
            }"
            @changed="emit('refresh')"
          />
        </article>
        <article v-for="recipe in recipeSections.saved" :key="recipe.recipe_id" class="mb-2 min-w-0">
          <div class="flex min-w-0 items-center gap-2 rounded-xl border border-line px-3 py-2.5">
            <h3 class="min-w-0 flex-1 truncate text-[15px] font-semibold" :title="recipe.name">{{ recipe.name }}</h3>
            <button type="button" class="icon-btn shrink-0" title="全屏" aria-label="全屏查看流程图" aria-haspopup="dialog" @click="flows.get(`saved:${recipe.recipe_id}`)?.openFullscreen()"><Maximize2 class="size-4" /></button>
          </div>
          <RecipeFlow
            :ref="(el) => setFlow(`saved:${recipe.recipe_id}`, el)" toolbar-placement="hidden" :inline-canvas="false"
            :flow="stepFlow(recipe)"
            :context="{ title: recipe.name, recipeId: recipe.recipe_id, version: recipe.version, customerId: recipe.customer_id, conversationId: recipe.conversation_id || detail?.id, recipe: recipe.recipe, files: detail?.files }"
            @changed="emit('refresh')"
          />
        </article>
      </template>

      <template v-else-if="tab === 'files'">
        <div v-for="group in fileGroups" :key="group.key" class="mb-5">
          <div class="label-mini mb-2 flex items-center gap-2">{{ group.label }}<span class="font-normal tabular-nums tracking-normal">{{ group.files.length }}</span></div>
          <div v-for="file in group.files" :key="file.file_id" class="mb-1.5 flex items-center gap-2.5 rounded-xl border border-line px-3 py-2">
            <FileCheck2 v-if="file.kind === 'expected'" class="size-4 shrink-0 text-[#b7860b]" />
            <FileSpreadsheet v-else class="size-4 shrink-0" :class="file.kind === 'output' ? 'text-ink' : 'text-fun-green'" />
            <button type="button" class="min-w-0 flex-1 truncate rounded text-left text-[13px] font-medium underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink/40" :title="file.filename" :aria-label="`查看 ${file.filename}`" @click="emit('openFile', file)">{{ file.filename }}</button>
            <div class="-mr-1.5 flex shrink-0 items-center">
              <PrivacyStatusIcon :status="privacy[file.file_id]" :file="file" :conversation-id="detail?.id" class="size-7" />
              <a :href="file.download_url" class="icon-btn size-7" :aria-label="`下载 ${file.filename}`" title="下载"><Download class="size-4" /></a>
              <button type="button" class="icon-btn size-7 hover:text-error" :aria-label="`删除 ${file.filename}`" title="删除" @click="removeFile(file)"><Trash2 class="size-4" /></button>
            </div>
          </div>
        </div>
      </template>

      <template v-else-if="tab === 'data'">
        <div>
          <div class="label-mini mb-2 flex items-center gap-2">中间数据<span class="font-normal tabular-nums tracking-normal">{{ tables.length }}</span></div>
          <div v-for="table in tables" :key="executionTableKey(table)" class="group mb-1.5 rounded-xl border border-line px-3 py-2.5 transition hover:border-ink/20">
            <div class="flex items-center gap-2">
              <FileCheck2 v-if="table.role === 'expected'" class="size-4 shrink-0 text-[#b7860b]" />
              <FileSpreadsheet v-else class="size-4 shrink-0 text-fun-green" />
              <span class="min-w-0 flex-1 truncate font-mono text-[13px] font-medium" :title="table.name">{{ table.name }}</span>
              <span class="shrink-0 font-mono text-[11px] text-faint">{{ table.preview_only ? '预览 · ' : '' }}{{ table.rows }}×{{ table.cols }}</span>
            </div>
            <div class="mt-1 truncate text-[11px] text-faint" :title="sourceLabel(table)">{{ [table.label, detail?.customer ? sourceLabel(table) : ''].filter(Boolean).join(' · ') }}</div>
            <div class="mt-2 flex gap-1 opacity-70 transition group-hover:opacity-100">
              <button class="btn-text px-1.5 text-xs" @click="emit('openTable', table)"><Eye class="size-3.5" />查看</button>
              <button v-if="!table.run_id && !table.preview_only" class="btn-text px-1.5 text-xs hover:!text-error" :aria-label="`删除中间表 ${table.name}`" @click="drop(table)"><Trash2 class="size-3.5" />删除</button>
            </div>
          </div>
        </div>
      </template>

    </div>
  </aside>
</template>

<style scoped>
.execution-panel { position: relative; display: flex; height: 100%; flex: none; flex-direction: column; border-left: 1px solid var(--color-line); background: var(--color-panel); }
.execution-resizer { position: absolute; inset: 0 auto 0 0; z-index: 1; width: 8px; cursor: col-resize; touch-action: none; outline: none; }
.execution-resizer::after { position: absolute; inset: 0 auto 0 0; width: 2px; background: transparent; content: ''; transition: background-color 150ms; }
.execution-resizer:hover::after,
.execution-resizer:focus-visible::after,
.is-resizing .execution-resizer::after { background: #6265dc; }
.execution-backdrop { display: none; }
@media (max-width: 1279px) {
  .execution-panel { position: fixed; top: var(--chat-header-space, 44px); right: 0; z-index: 35; height: calc(100% - var(--chat-header-space, 44px)); box-shadow: -12px 0 50px #00000012; }
  .execution-backdrop { position: fixed; inset: var(--chat-header-space, 44px) 0 0; z-index: 34; display: block; background: #00000020; }
}
</style>
