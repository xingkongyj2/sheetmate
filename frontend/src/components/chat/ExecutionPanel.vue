<script setup lang="ts">
/** 客户/会话资源与单次回答的处理流程；表名按来源工作区定位。 */
import { Download, Eye, FileCheck2, FileSpreadsheet, PanelRightClose, Trash2 } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted } from 'vue'

import { conversationApi } from '@/api'
import type { ConversationDetail, FileInfo, RecipeBrief, RecipeFlowData, TableBrief } from '@/api/types'
import StatusPill from '@/components/common/StatusPill.vue'
import RecipeFlow from '@/components/recipe/RecipeFlow.vue'
import VerifyBadge from '@/components/recipe/VerifyBadge.vue'
import type { RecipeProgress, UiMessage } from '@/composables/chatModel'
import { executionFileGroups, executionRecipeSections, executionTableKey, executionTables } from '@/composables/executionPresentation'
import { progressFlow } from '@/composables/recipeFlow'
import { useConfirm } from '@/composables/useConfirm'
import ExecutionStatusNotice from './ExecutionStatusNotice.vue'
import ExecutionUsage from './ExecutionUsage.vue'

type Tab = 'recipes' | 'files' | 'data' | 'usage'
const props = withDefaults(defineProps<{
  detail: ConversationDetail | null
  message: UiMessage | null
  messages?: UiMessage[]
  refreshingUsage?: boolean
  activeTab?: Tab
}>(), { activeTab: 'recipes', messages: () => [], refreshingUsage: false })
const emit = defineEmits<{
  close: []; openTable: [table: TableBrief]; openFile: [file: FileInfo]; refresh: []
  'update:activeTab': [tab: Tab]
}>()
const { confirmDelete } = useConfirm()
const tab = computed({ get: () => props.activeTab, set: (value: Tab) => emit('update:activeTab', value) })
const tabs = [
  { key: 'recipes', label: '流程' }, { key: 'files', label: '文件' },
  { key: 'data', label: '数据' }, { key: 'usage', label: '消耗' },
] as const
const progresses = computed(() => (props.message?.artifacts ?? []).flatMap((artifact) => artifact.kind === 'recipe' ? [artifact.progress] : []))
const recipeSections = computed(() => executionRecipeSections(props.detail?.recipes ?? [], progresses.value))
const tables = computed(() => executionTables(props.detail?.tables ?? []))
const fileGroups = computed(() => executionFileGroups(props.detail?.files ?? []).filter((group) => group.files.length))

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
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <button class="execution-backdrop" aria-label="收起执行明细" @click="emit('close')" />
  <aside class="execution-panel" aria-label="执行明细">
    <div class="flex h-11 shrink-0 items-center justify-between border-b border-line px-4">
      <span class="text-sm font-semibold">执行明细</span>
      <button class="icon-btn size-7 shrink-0" aria-label="收起执行明细" title="收起" @click="emit('close')"><PanelRightClose class="size-4" /></button>
    </div>
    <div class="flex shrink-0 gap-1 border-b border-line px-3 py-2 text-[13px]" role="tablist" aria-label="执行明细分类">
      <button
        v-for="item in tabs"
        :id="`execution-tab-${item.key}`"
        :key="item.key"
        type="button"
        role="tab"
        :aria-selected="tab === item.key"
        aria-controls="execution-content"
        class="rounded-full px-3 py-1 transition"
        :class="tab === item.key ? 'bg-ink text-white' : 'text-mute hover:bg-paper hover:text-ink'"
        @click="tab = item.key"
      >
        {{ item.label }}
      </button>
    </div>

    <div id="execution-content" role="tabpanel" :aria-labelledby="`execution-tab-${tab}`" class="min-h-0 flex-1 overflow-y-auto p-4">
      <ExecutionStatusNotice v-if="message && tab !== 'usage'" class="mb-4" :message="message" />
      <ExecutionUsage v-if="tab === 'usage'" :message="message" :messages="messages" :refreshing="refreshingUsage" @refresh="emit('refresh')" />
      <template v-else-if="tab === 'recipes'">
        <article v-for="{ progress, recipe, version } in recipeSections.progresses" :key="progress.key" class="mb-6 min-w-0 space-y-3">
          <header class="flex flex-wrap items-center gap-x-3 gap-y-2 px-1">
            <div class="flex min-w-0 flex-1 items-center gap-2">
              <h3 class="min-w-0 break-words text-[16px] font-semibold leading-relaxed">{{ recipe?.name || progress.saved?.name || progress.recipeName || '处理流程' }}</h3>
              <span v-if="version != null" class="tag shrink-0 !bg-[#6265DC]/10 !text-[#6265DC]">v{{ version }}</span>
            </div>
            <StatusPill class="shrink-0" :status="progressStatus(progress)" />
            <span v-if="progress.round > 1" class="tag">第 {{ progress.round }} 轮</span>
          </header>
          <div class="min-w-0 rounded-[24px] bg-white p-4 shadow-[0_4px_24px_-8px_rgba(34,34,66,0.10),0_0_0_1px_rgba(34,34,66,0.035)] sm:p-5">
            <p v-if="recipe?.description ?? progress.recipe?.description" class="mb-5 break-words text-[13px] leading-[1.8] text-mute">{{ recipe?.description ?? progress.recipe?.description }}</p>
            <RecipeFlow
              v-if="progress.flow || progress.saved?.flow || progress.steps.length || recipe"
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
            <div v-if="progress.verify || progress.validation || progress.failed" class="mt-4 space-y-3">
              <VerifyBadge v-if="progress.verify" :report="progress.verify" :rounds="progress.verifyHistory.length" />
              <p v-if="progress.validation" class="break-words text-xs leading-5 text-mute">运行校验：{{ progress.validation.summary }}</p>
              <p v-if="progress.failed" class="break-words text-xs leading-5 text-error">{{ progress.failed }}</p>
            </div>
            <RouterLink v-if="recipe?.customer_id && !progress.deleted" :to="{ name: 'customer', params: { id: recipe.customer_id }, query: { tab: 'recipes' } }" class="btn-text -ml-2 mt-3 text-xs">在客户页查看与重复执行 →</RouterLink>
          </div>
        </article>
        <div v-if="!recipeSections.saved.length && !recipeSections.progresses.length" class="py-14 text-center text-sm text-faint">暂无已保存的流程</div>
        <article v-for="recipe in recipeSections.saved" :key="recipe.recipe_id" class="mb-6 min-w-0 space-y-3">
          <header class="flex flex-wrap items-center gap-x-3 gap-y-2 px-1">
            <h3 class="min-w-0 flex-1 break-words text-[16px] font-semibold leading-relaxed">{{ recipe.name }}</h3>
            <span class="tag shrink-0 !bg-[#6265DC]/10 !text-[#6265DC]">v{{ recipe.version }}</span>
          </header>
          <div class="min-w-0 rounded-[24px] bg-white p-4 shadow-[0_4px_24px_-8px_rgba(34,34,66,0.10),0_0_0_1px_rgba(34,34,66,0.035)] sm:p-5">
            <p v-if="recipe.description" class="mb-5 break-words text-[13px] leading-[1.8] text-mute">{{ recipe.description }}</p>
            <RecipeFlow :flow="stepFlow(recipe)" :context="{ title: recipe.name, recipeId: recipe.recipe_id, version: recipe.version, customerId: recipe.customer_id, conversationId: recipe.conversation_id || detail?.id, recipe: recipe.recipe, files: detail?.files }" @changed="emit('refresh')" />
            <RouterLink v-if="recipe.customer_id" :to="{ name: 'customer', params: { id: recipe.customer_id }, query: { tab: 'recipes' } }" class="btn-text -ml-2 mt-3 text-xs">在客户页查看与重复执行 →</RouterLink>
          </div>
        </article>
      </template>

      <template v-else-if="tab === 'files'">
        <div v-if="!fileGroups.length" class="py-14 text-center text-sm text-faint">还没有文件</div>
        <div v-for="group in fileGroups" :key="group.key" class="mb-5">
          <div class="label-mini mb-2 flex items-center gap-2">{{ group.label }}<span class="font-normal tabular-nums tracking-normal">{{ group.files.length }}</span></div>
          <div v-for="file in group.files" :key="file.file_id" class="mb-1.5 flex items-center gap-2.5 rounded-xl border border-line px-3 py-2">
            <FileCheck2 v-if="file.kind === 'expected'" class="size-4 shrink-0 text-[#b7860b]" />
            <FileSpreadsheet v-else class="size-4 shrink-0" :class="file.kind === 'output' ? 'text-ink' : 'text-fun-green'" />
            <button type="button" class="min-w-0 flex-1 truncate rounded text-left text-[13px] font-medium underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink/40" :title="file.filename" :aria-label="`查看 ${file.filename}`" @click="emit('openFile', file)">{{ file.filename }}</button>
            <a :href="file.download_url" class="icon-btn" :aria-label="`下载 ${file.filename}`" title="下载"><Download class="size-4" /></a>
          </div>
        </div>
      </template>

      <template v-else-if="tab === 'data'">
        <div v-if="!tables.length" class="py-14 text-center text-sm text-faint">暂无中间数据</div>
        <div v-else>
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
.execution-panel { display: flex; height: 100%; width: 392px; flex: none; flex-direction: column; border-left: 1px solid var(--color-line); background: white; }
.execution-backdrop { display: none; }
@media (max-width: 1279px) {
  .execution-panel { position: fixed; top: 0; right: 0; z-index: 35; width: min(392px, 100vw); box-shadow: -12px 0 50px #00000012; }
  .execution-backdrop { position: fixed; inset: 0; z-index: 34; display: block; background: #00000020; }
}
</style>
