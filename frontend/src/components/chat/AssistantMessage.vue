<script setup lang="ts">
/**
 * 助手消息：阶段回复与折叠的思考细节按发生顺序穿插，最后展示回答和交付结果。
 */
import { Check, ChevronRight, CircleAlert, Code, Copy, Download, Eye, FileSpreadsheet, LoaderCircle, MessageCircleQuestion } from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { fileApi } from '@/api'
import { errorText } from '@/api/http'
import type { FileInfo, TableBrief } from '@/api/types'
import Mascot from '@/components/brand/Mascot.vue'
import MarkdownView from '@/components/common/MarkdownView.vue'
import RecipeProgressCard from '@/components/recipe/RecipeProgressCard.vue'
import { assistantStages, type AssistantStage } from '@/composables/assistantTimeline'
import type { UiMessage } from '@/composables/chatModel'
import { fileKindLabel, standaloneFiles } from '@/composables/filePresentation'
import { copyMarkdown } from '@/composables/markdown'
import { lastResultTable, messageMarkdown, recipeAttempts, recipeFailure, recipeOutcomeSummary } from '@/composables/messagePresentation'
import { recipeViewReply } from '@/composables/recipeViewPresentation'
import { toolPresentation, userFacingSummary, userFacingText } from '@/composables/timelinePresentation'
import { useToast } from '@/composables/useToast'

import RecipeStageDetails from './RecipeStageDetails.vue'
import RecipeViewCard from './RecipeViewCard.vue'
import TableCard from './TableCard.vue'
import TimelineList from './TimelineList.vue'

const props = defineProps<{ message: UiMessage; files?: FileInfo[]; tables?: TableBrief[]; customerId?: string | null; mascot?: string | null; answersDisabled?: boolean }>()
const emit = defineEmits<{ answer: [text: string]; previewFile: [file: FileInfo] }>()
const toast = useToast()
const openingFile = ref<string | null>(null)

async function previewFile(fileId: string) {
  if (openingFile.value) return
  openingFile.value = fileId
  try {
    emit('previewFile', await fileApi.get(fileId))
  } catch (error) {
    toast.error(errorText(error))
  } finally {
    openingFile.value = null
  }
}
const copying = ref(false)
const copied = ref<'formatted' | 'markdown' | null>(null)
let copiedTimer = 0
const stages = computed(() => assistantStages(props.message))
const fileContext = computed(() => ({ files: props.files, tables: props.tables }))
const markdown = computed(() => messageMarkdown(props.message, stages.value.map((stage) => ({ title: stage.title, summary: stageSummary(stage) })), fileContext.value))
const attempts = computed(() => recipeAttempts(props.message))
const recipeViews = computed(() => props.message.artifacts.flatMap((artifact) => artifact.kind === 'recipe_view' ? [artifact] : []))
const deliveries = computed(() => {
  const available = attempts.value.filter((attempt) => attempt.progress.file || attempt.progress.saved)
  return [...new Map(available.map((attempt) => [attempt.progress.file?.file_id ?? `${attempt.progress.saved?.recipe_id}:${attempt.progress.saved?.version}`, attempt])).values()]
})
const reply = computed(() => {
  const content = recipeViewReply(props.message)
  return content.trim() ? content : recipeOutcomeSummary(props.message)
})
const errorMessage = computed(() => userFacingText(props.message.error, '处理遇到问题，请稍后重试。', 400))

function stageSummary(stage: AssistantStage): string {
  const summaries = [userFacingSummary(stage.summary, '', 1600)]
  for (const attempt of stage.attempts) {
    const failure = recipeFailure(attempt.progress)
    if (failure) summaries.push(userFacingText(failure.summary, '这次尝试未完成，需要调整处理方式。', 400))
    else if (attempt.progress.status === 'done') summaries.push(userFacingText(attempt.progress.runSummary, '本次处理已完成。', 800))
  }
  return [...new Set(summaries.filter(Boolean))].join('\n\n')
}

function stageActivity(stage: AssistantStage): string {
  const activeTool = stage.items.find((item) => item.kind === 'tool' && item.status === 'running')
  if (activeTool?.kind === 'tool') return `正在${toolPresentation(activeTool).label}…`
  const progress = stage.attempts.find((attempt) => ['thinking', 'running', 'verifying', 'refining'].includes(attempt.progress.status))?.progress
  if (progress) {
    if (progress.status === 'verifying') return '正在核对处理结果…'
    if (progress.status === 'refining') return '正在调整处理步骤…'
    const step = progress.steps.find((item) => item.status === 'running' || item.status === 'repairing')
    if (step) return `正在${userFacingText(step.name, '处理数据', 80)}…`
  }
  if (stage.status === 'running') return '正在思考…'
  if (stage.plan) return userFacingText(stage.plan.description, '处理过程', 120)
  if (stage.attempts.length) return stage.attempts[0]!.label
  const labels = [...new Set(stage.items.flatMap((item) => item.kind === 'tool' ? [toolPresentation(item).label] : []))]
  return labels.slice(0, 2).join('、') || '分析需求'
}

function hasProcess(stage: AssistantStage): boolean {
  return stage.items.some((item) => item.kind !== 'thinking') || stage.attempts.length > 0 || !!stage.plan || stage.status === 'running'
}

async function copyReply(format: 'formatted' | 'markdown') {
  if (copying.value) return
  copying.value = true
  try {
    await copyMarkdown(markdown.value, format)
    copied.value = format
    window.clearTimeout(copiedTimer)
    copiedTimer = window.setTimeout(() => { copied.value = null }, 2000)
    toast.success(format === 'formatted' ? '已复制带格式的回复' : '已复制 Markdown 源文')
  } catch (error) {
    toast.error(error instanceof Error ? error.message : '复制失败，请重试')
  } finally {
    copying.value = false
  }
}

const runningTool = computed(() => [...props.message.timeline].reverse().find((t) => t.kind === 'tool' && t.status === 'running'))
const mood = computed(() => {
  if (props.message.streaming) return runningTool.value ? 'working' : 'thinking'
  const latest = attempts.value.at(-1)
  return props.message.error || (latest && recipeFailure(latest.progress)) ? 'idle' : 'happy'
})

// 正式输入输出只交付文件；小型临时表完整内联，其余计算数据可在执行明细中查看。
const lastTable = computed(() => lastResultTable(props.message))
const fileCards = computed(() => standaloneFiles(props.message, fileContext.value))
const questions = computed(() => props.message.artifacts.flatMap((a) => (a.kind === 'clarify' ? [a] : [])))

// 回答正文逐字显示：后端把最终回答整段给过来，这里做一个很快的打字动画；打开历史消息时直接整段显示
const animate = props.message.streaming
const shown = ref(animate ? '' : reply.value)
let timer = 0
function tick() {
  const full = reply.value
  if (!full.startsWith(shown.value)) {
    // 过滤重复流程图或撤回过程说明时，保留仍然有效的前文，避免从头打字。
    let shared = 0
    while (shared < shown.value.length && shared < full.length && shown.value[shared] === full[shared]) shared++
    shown.value = full.slice(0, shared)
  }
  const remaining = full.length - shown.value.length
  if (remaining <= 0) {
    window.clearInterval(timer)
    timer = 0
    return
  }
  shown.value = full.slice(0, shown.value.length + Math.max(3, Math.ceil(remaining / 14)))
}
watch(
  reply,
  (full) => {
    if (!animate) shown.value = full
    else if (!timer) timer = window.setInterval(tick, 30)
  },
  { immediate: true },
)
onBeforeUnmount(() => {
  window.clearInterval(timer)
  window.clearTimeout(copiedTimer)
})
const typing = computed(() => props.message.streaming || shown.value !== reply.value)
</script>

<template>
  <div class="assistant-message group/message relative min-h-12">
    <div class="absolute right-full top-0 mr-4 size-12">
      <Mascot :variant="mascot ?? 'blob'" :size="48" :mood="mood" :shadow="false" :track="false" />
    </div>
    <div class="assistant-content w-full min-w-0 space-y-3">
      <!-- 回复文字留在原位，思考细节穿插在对应阶段，不能集中到正文之前。 -->
      <section v-for="stage in stages" :key="stage.key" class="assistant-stage space-y-2" :aria-label="stageActivity(stage)" :data-stage="stage.number">
        <details v-if="hasProcess(stage)" class="assistant-process group/process">
          <summary class="flex w-fit max-w-full cursor-pointer list-none items-center gap-1.5 rounded py-1 text-[13px] leading-5 text-mute transition hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink [&::-webkit-details-marker]:hidden">
            <LoaderCircle v-if="message.streaming && stage.status === 'running'" class="size-3.5 shrink-0 animate-spin" />
            <ChevronRight v-else class="size-3.5 shrink-0 transition group-open/process:rotate-90" />
            <span class="min-w-0 break-words">思考细节：{{ stageActivity(stage) }}</span>
          </summary>
          <div class="mb-3 ml-1.5 mt-2 space-y-2 border-l-2 border-line py-1 pl-3.5">
            <div v-if="stage.plan || stage.attempts.length" class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 text-xs leading-5">
              <p class="font-medium text-mute">{{ stage.plan ? `${stage.plan.num}. ${userFacingText(stage.plan.description, '处理数据', 120)}` : stage.attempts[0]?.label }}</p>
              <span :class="stage.status === 'error' ? 'text-error' : 'text-faint'">{{ stage.statusLabel }}</span>
            </div>
            <TimelineList v-if="stage.items.some((item) => item.kind !== 'thinking')" :items="stage.items" />
            <RecipeStageDetails v-for="attempt in stage.attempts" :key="attempt.key" :progress="attempt.progress" />
            <p v-if="stage.items.every((item) => item.kind === 'thinking') && !stage.attempts.length && !stageSummary(stage)" class="text-xs leading-5 text-mute">{{ stage.status === 'running' ? '正在分析需求，整理处理步骤。' : stage.status === 'pending' ? '尚未开始处理。' : stage.statusLabel }}</p>
          </div>
        </details>
        <div v-if="stageSummary(stage)" class="assistant-stage-summary">
          <MarkdownView :source="stageSummary(stage)" class="!text-[14px] !leading-6" />
        </div>
      </section>

      <!-- 简单回答直接显示正文；文件卡片收到后即可操作。 -->
      <MarkdownView v-if="shown" :source="shown" :streaming="typing" />
      <!-- 表格和流程结果等正文接收和打字都结束后再展示。 -->
      <template v-if="!typing">
        <RecipeViewCard v-for="artifact in recipeViews" :key="artifact.key" :view="artifact.view" />
        <RecipeProgressCard v-for="attempt in deliveries" :key="attempt.key" :progress="attempt.progress" :customer-id="customerId" @preview-file="(file) => emit('previewFile', file)" />

        <TableCard v-if="lastTable" :table="lastTable" />
      </template>
      <div v-for="file in fileCards" :key="`file-${file.file_id}`" class="card-soft flex flex-wrap items-center gap-3 px-4 py-3" data-testid="file-delivery">
        <FileSpreadsheet class="size-5 text-fun-green" />
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-2">
            <div class="min-w-0 truncate text-[13px] font-semibold" :title="file.filename">{{ file.filename }}</div>
            <span v-if="fileKindLabel(file.kind)" class="tag shrink-0">{{ fileKindLabel(file.kind) }}</span>
          </div>
          <div class="text-[11px] text-faint">{{ (file.sheets ?? []).map((s) => `${s.sheet} ${s.rows} 行`).join(' · ') }}</div>
        </div>
        <button type="button" class="btn-line py-2" :disabled="!!openingFile" @click="previewFile(file.file_id)"><LoaderCircle v-if="openingFile === file.file_id" class="size-4 animate-spin" /><Eye v-else class="size-4" />查看</button>
        <a :href="file.download_url" class="btn-ink py-2"><Download class="size-4" />下载</a>
      </div>
      <template v-if="!typing">
        <section v-for="a in questions" :key="`b${a.key}`" class="overflow-hidden rounded-2xl border border-[#eadfc9] bg-[#fffcf6]">
          <div class="flex items-start gap-3 px-4 pb-3 pt-4 sm:px-5 sm:pt-5">
            <span class="flex size-9 shrink-0 items-center justify-center rounded-xl bg-[#fff1d2] text-[#a56d0d]">
              <MessageCircleQuestion class="size-[18px]" />
            </span>
            <div class="min-w-0 flex-1">
              <div class="text-[11px] font-semibold tracking-wide text-[#9a6b20]">需要你确认</div>
              <h3 class="mt-1 whitespace-pre-line break-words text-[15px] font-semibold leading-6 [overflow-wrap:anywhere]">{{ a.question }}</h3>
              <p class="mt-1 text-xs leading-5 text-mute">{{ answersDisabled ? '该问题已结束，可在下方继续对话' : '请选择一项，点击后将继续处理' }}</p>
            </div>
          </div>
          <ol class="space-y-2 px-3 pb-3 sm:px-4 sm:pb-4" :aria-label="a.question">
            <li v-for="(opt, index) in a.options" :key="opt">
              <button
                type="button"
                class="group/option flex min-h-12 w-full items-center gap-3 rounded-xl border border-[#eee9df] bg-white px-3.5 py-3 text-left text-[13px] leading-5 text-ink shadow-[0_1px_2px_rgba(28,25,17,0.03)] transition enabled:hover:border-[#d6bf8d] enabled:hover:bg-[#fffdf8] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#d7a337] disabled:cursor-not-allowed disabled:opacity-50"
                :disabled="message.streaming || answersDisabled"
                @click="emit('answer', opt)"
              >
                <span aria-hidden="true" class="flex size-6 shrink-0 items-center justify-center rounded-lg bg-paper text-[11px] font-semibold text-mute transition group-hover/option:bg-[#fff1d2] group-hover/option:text-[#9a6b20]">{{ index + 1 }}</span>
                <span class="min-w-0 flex-1 break-words [overflow-wrap:anywhere]">{{ opt }}</span>
                <ChevronRight aria-hidden="true" class="size-4 shrink-0 text-faint transition group-hover/option:translate-x-0.5 group-hover/option:text-[#a56d0d]" />
              </button>
            </li>
          </ol>
        </section>
      </template>

      <div v-if="!typing && message.error" class="flex gap-2 rounded-xl bg-error/5 px-3.5 py-2.5 text-[13px] text-error">
        <CircleAlert class="mt-0.5 size-4 shrink-0" />
        <span>
          {{ errorMessage }}
          <RouterLink v-if="message.error.includes('API Key') || message.error.includes('设置')" :to="{ name: 'settings' }" class="ml-1 font-medium underline">去设置</RouterLink>
        </span>
      </div>

      <div v-if="!typing && markdown" class="assistant-actions pointer-events-none flex items-center gap-1 pt-2 opacity-0 transition-opacity group-hover/message:pointer-events-auto group-hover/message:opacity-100 group-focus-within/message:pointer-events-auto group-focus-within/message:opacity-100">
        <button type="button" class="icon-btn -ml-2 disabled:opacity-50" :disabled="copying" aria-label="复制带格式的回复" title="复制带格式的回复，含阶段回复、回答、简要结果表和文件链接，可粘贴到支持富文本的编辑器" @click="copyReply('formatted')">
          <Check v-if="copied === 'formatted'" class="size-4 text-success" /><Copy v-else class="size-4" />
        </button>
        <button type="button" class="icon-btn disabled:opacity-50" :disabled="copying" aria-label="复制 Markdown" title="复制原生 Markdown，保留标题、列表和表格语法" @click="copyReply('markdown')">
          <Check v-if="copied === 'markdown'" class="size-4 text-success" /><Code v-else class="size-4" />
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.assistant-message {
  --assistant-avatar-size: 3rem;
}

.assistant-content > :deep(.prose-sm:first-child > :first-child) {
  padding-top: max(0px, calc((var(--assistant-avatar-size) - 1lh) / 2));
}

.assistant-content > :deep(.prose-sm:first-child > :is(ul, ol):first-child > li:first-child),
.assistant-content > :deep(.prose-sm:first-child > :is(ul, ol):first-child > li:first-child > p:first-child),
.assistant-content > :deep(.prose-sm:first-child > blockquote:first-child > :first-child) {
  margin-top: 0;
}

.assistant-stage:first-child {
  padding-top: max(0px, calc((var(--assistant-avatar-size) - 1.25rem) / 2));
}
</style>

