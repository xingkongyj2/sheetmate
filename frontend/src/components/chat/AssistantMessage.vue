<script setup lang="ts">
/**
 * 助手消息：阶段回复与折叠的处理记录按发生顺序穿插，最后展示回答和交付结果。
 */
import { Check, ChevronRight, CircleAlert, CircleHelp, Code, Copy, Download, Eye, FileSpreadsheet, LoaderCircle, MessageCircleQuestion, Table2 } from '@lucide/vue'
import { computed, onBeforeUnmount, ref } from 'vue'

import { fileApi } from '@/api'
import { errorText } from '@/api/http'
import type { FileInfo, TableBrief } from '@/api/types'
import Mascot from '@/components/brand/Mascot.vue'
import MarkdownView from '@/components/common/MarkdownView.vue'
import RecipeProgressCard from '@/components/recipe/RecipeProgressCard.vue'
import { assistantDisplayBlocks, groupAssistantBlocks, type AssistantDisplayGroup, type AssistantStage } from '@/composables/assistantTimeline'
import { processGroupCategories, processGroupHeader } from '@/composables/assistantProcessPresentation'
import type { UiMessage } from '@/composables/chatModel'
import { fileKindLabel, standaloneFiles } from '@/composables/filePresentation'
import { executionNotice } from '@/composables/executionStatus'
import { missingLLMKeyPresentation } from '@/composables/llmErrorPresentation'
import { copyMarkdown } from '@/composables/markdown'
import { messageMarkdown, PLAN_STATUS_LABELS, recipeAttempts, recipeFailure, recipeOutcomeSummary } from '@/composables/messagePresentation'
import { recipeViewReply } from '@/composables/recipeViewPresentation'
import { recipeDeliveries } from '@/composables/recipeDeliveries'
import { auditLine } from '@/composables/thinkingPresentation'
import { userFacingSummary, userFacingText } from '@/composables/timelinePresentation'
import { useToast } from '@/composables/useToast'
import { useProgressiveReply } from '@/composables/useProgressiveReply'

import RecipeStageDetails from './RecipeStageDetails.vue'
import RecipeViewCard from './RecipeViewCard.vue'
import TimelineList from './TimelineList.vue'
import ExecutionStatusNotice from './ExecutionStatusNotice.vue'

const props = defineProps<{ message: UiMessage; files?: FileInfo[]; tables?: TableBrief[]; customerId?: string | null; conversationId?: string | null; mascot?: string | null; answersDisabled?: boolean; answersClosed?: boolean }>()
const emit = defineEmits<{ answer: [text: string]; previewFile: [file: FileInfo]; refresh: [] }>()
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
const blocks = computed(() => assistantDisplayBlocks(props.message))
const groups = computed(() => groupAssistantBlocks(blocks.value).flatMap((group): AssistantDisplayGroup[] =>
  group.kind === 'process' ? processGroupCategories(group) : [group]))
const processHeaders = computed(() => new Map(groups.value.flatMap((group) =>
  group.kind === 'process' ? [[group.key, processGroupHeader(group)] as const] : [])))
const stages = computed(() => blocks.value.flatMap((block) => block.kind === 'process' ? [block.stage] : []))
const fileContext = computed(() => ({ files: props.files, tables: props.tables }))
const missingKeyError = computed(() => missingLLMKeyPresentation(props.message))
const markdown = computed(() => missingKeyError.value?.duplicateContent ? missingKeyError.value.text
  : messageMarkdown(props.message, stages.value.map((stage) => ({ title: stage.title, summary: stageSummary(stage) })), fileContext.value))
const attempts = computed(() => recipeAttempts(props.message))
const workflows = computed(() => recipeDeliveries(props.message))
const recipeViews = computed(() => workflows.value.views)
const deliveries = computed(() => workflows.value.deliveries)
const fallbackReply = computed(() => {
  if (missingKeyError.value?.duplicateContent) return ''
  if (blocks.value.some((block) => block.kind === 'text' && block.text.trim())) return ''
  const content = recipeViewReply(props.message)
  return content.trim() ? content : recipeOutcomeSummary(props.message)
})
const replyGroups = computed<AssistantDisplayGroup[]>(() => [
  ...groups.value,
  ...(fallbackReply.value ? [{ kind: 'text' as const, key: `${props.message.key}_fallback`, text: fallbackReply.value, closed: !props.message.streaming }] : []),
])
// Previously received text is immediate; only newly streamed text reveals progressively.
const { groups: displayGroups, pending: revealing } = useProgressiveReply(
  () => replyGroups.value,
  (props.message.streaming || props.message.id == null) && !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches,
  { initialVisible: props.message.id != null, immediateRevision: () => props.message.replayRevision },
)
function isFollowedByProcess(index: number): boolean {
  return displayGroups.value.slice(index + 1).find((group) =>
    group.kind === 'process' || (group.kind === 'text' && !!group.text.trim()),
  )?.kind === 'process'
}
const typing = computed(() => props.message.streaming || revealing.value)
const activity = computed(() => {
  if (!props.message.streaming) return ''
  return props.message.activity?.trim() || '正在回复'
})
const audit = computed(() => auditLine(props.message.audit))
const errorMessage = computed(() => missingKeyError.value?.text ?? userFacingText(props.message.error, '处理遇到问题，请稍后重试。', 400))
const stopped = computed(() => executionNotice(props.message))
const showError = computed(() => !!missingKeyError.value || !!props.message.error && (!stopped.value || props.message.error.includes('设置') || props.message.error.includes('API Key')))

function stageSummary(stage: AssistantStage): string {
  const summaries = [userFacingSummary(stage.summary, '', 1600)]
  for (const attempt of stage.attempts) {
    const failure = recipeFailure(attempt.progress)
    if (failure) summaries.push(userFacingText(failure.summary, '这次尝试未完成，需要调整处理方式。', 400))
    else if (attempt.progress.status === 'done') summaries.push(userFacingText(attempt.progress.runSummary, '本次处理已完成。', 800))
  }
  return [...new Set(summaries.filter(Boolean))].join('\n\n')
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
  if (typing.value) return runningTool.value ? 'working' : 'thinking'
  const latest = attempts.value.at(-1)
  return props.message.error || stopped.value || (latest && recipeFailure(latest.progress)) ? 'idle' : 'happy'
})

// 回复表格统一由正文 Markdown 展示，计算数据可在执行明细中查看。
const fileCards = computed(() => standaloneFiles(props.message, fileContext.value))
const questions = computed(() => props.message.artifacts.flatMap((a) => (a.kind === 'clarify' ? [a] : [])))
const answerDrafts = ref<Record<string, string>>({})
const answersLocked = computed(() => typing.value || props.answersDisabled || props.answersClosed)

function submitAnswer(key: string) {
  const answer = answerDrafts.value[key]?.trim()
  if (answersLocked.value || !answer) return
  emit('answer', answer)
  answerDrafts.value[key] = ''
}

function onAnswerKeydown(event: KeyboardEvent, key: string) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  submitAnswer(key)
}

onBeforeUnmount(() => {
  window.clearTimeout(copiedTimer)
})
</script>

<template>
  <div class="assistant-message group/message relative min-h-12">
    <div class="absolute right-full top-0 mr-4 size-12">
      <Mascot :variant="mascot ?? 'blob'" :size="48" :mood="mood" :shadow="false" :track="false" />
    </div>
    <div class="assistant-content w-full min-w-0 space-y-3">
      <!-- 按发生顺序只合并相邻同类步骤，正文保持原位。 -->
      <template v-for="(group, index) in displayGroups" :key="group.key">
        <section v-if="group.kind === 'process'" class="assistant-stage space-y-2" :class="{ 'assistant-stage--before-process': isFollowedByProcess(index) }" :aria-label="processHeaders.get(group.key)?.title" :data-stage="group.stages[0]?.number">
          <details class="assistant-process group/process">
            <summary class="flex w-fit max-w-full cursor-pointer list-none items-start gap-1.5 rounded py-1 text-[13px] leading-5 text-mute transition hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink [&::-webkit-details-marker]:hidden" :title="processHeaders.get(group.key)?.title">
              <LoaderCircle v-if="processHeaders.get(group.key)?.status === 'running'" class="mt-[3px] size-3.5 shrink-0 animate-spin motion-reduce:animate-none" />
              <ChevronRight v-else class="mt-[3px] size-3.5 shrink-0 transition group-open/process:rotate-90" />
              <span class="min-w-0 break-words [overflow-wrap:anywhere]" :class="{ 'process-title-running': processHeaders.get(group.key)?.status === 'running', 'text-error': processHeaders.get(group.key)?.status === 'error' }" aria-live="polite" aria-atomic="true">{{ processHeaders.get(group.key)?.title }}</span>
              <span v-if="processHeaders.get(group.key)?.status && !['running', 'done'].includes(processHeaders.get(group.key)!.status)" class="shrink-0 text-[11px]" :class="processHeaders.get(group.key)?.status === 'error' ? 'text-error' : 'text-faint'">{{ PLAN_STATUS_LABELS[processHeaders.get(group.key)!.status] }}</span>
            </summary>
            <div class="mb-3 ml-1.5 mt-2 space-y-3 border-l-2 border-line py-1 pl-3.5">
              <div v-for="(stage, index) in group.stages" :key="stage.key" class="space-y-2" :class="{ 'border-t border-line pt-3': index > 0 }">
                <TimelineList v-if="stage.plan || stage.items.some((item) => item.kind !== 'thinking')" :items="stage.plan ? [stage.plan, ...stage.items] : stage.items" :parent-title="processHeaders.get(group.key)?.title" />
                <RecipeStageDetails v-for="attempt in stage.attempts" :key="attempt.key" :progress="attempt.progress" />
                <MarkdownView v-if="userFacingSummary(stage.summary)" :source="userFacingSummary(stage.summary)" class="!text-xs !leading-5 text-mute" />
              </div>
            </div>
          </details>
        </section>
        <MarkdownView v-else-if="group.text" :source="group.text" :streaming="!group.closed && typing" stable />
      </template>

      <!-- 整轮回复结束前持续显示状态；已有正文和历史操作不代表 AI 已经停止。 -->
      <div v-if="activity" class="assistant-activity flex items-start gap-1.5 py-0.5 text-[13px] leading-5 text-mute" role="status" aria-live="polite" data-testid="activity">
        <LoaderCircle class="mt-[3px] size-3.5 shrink-0 animate-spin motion-reduce:animate-none" aria-hidden="true" />
        <span class="process-title-running min-w-0 flex-1 break-words [overflow-wrap:anywhere]">{{ activity }}…</span>
      </div>

      <!-- 思考模式：回答发出前对照执行结果核对的结果，放在回答下面；等回答显示完再出现。 -->
      <p v-if="!typing && audit" class="flex items-start gap-1.5 text-xs leading-5" :class="audit.tone === 'warn' ? 'text-[#9a6b00]' : 'text-mute'" data-testid="audit-line">
        <CircleAlert v-if="audit.tone === 'warn'" class="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
        <CircleHelp v-else-if="audit.tone === 'skip'" class="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
        <Check v-else class="mt-0.5 size-3.5 shrink-0 text-success" aria-hidden="true" />
        <span class="min-w-0 break-words [overflow-wrap:anywhere]">{{ audit.text }}</span>
      </p>

      <!-- 文件和流程结果等回复接收和渐进展示都完成后再显示。 -->
      <template v-if="!typing">
        <RecipeViewCard v-for="artifact in recipeViews" :key="artifact.key" :view="artifact.view" :files="files" :customer-id="customerId" :conversation-id="conversationId" @refresh="emit('refresh')" />
        <RecipeProgressCard v-for="attempt in deliveries" :key="attempt.key" :progress="attempt.progress" :files="files" :customer-id="customerId" :conversation-id="conversationId" @preview-file="(file) => emit('previewFile', file)" @refresh="emit('refresh')" />

        <div v-for="file in fileCards" :key="`file-${file.file_id}`" class="card-soft min-w-0 space-y-2.5 px-3.5 py-3" data-testid="file-delivery">
          <div class="flex flex-wrap items-center gap-x-3 gap-y-2">
            <div class="flex min-w-0 flex-1 basis-48 items-center gap-2">
              <FileSpreadsheet class="size-[18px] shrink-0 text-fun-green" aria-hidden="true" />
              <div class="min-w-0 truncate text-[13px] font-semibold" :title="file.filename">{{ file.filename }}</div>
              <span v-if="fileKindLabel(file.kind)" class="tag shrink-0 whitespace-nowrap">{{ fileKindLabel(file.kind) }}</span>
            </div>
            <div class="ml-auto flex shrink-0 items-center gap-1.5">
              <button type="button" class="btn-line h-7 gap-1 rounded-lg px-2.5 py-0 text-xs focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink/40" :disabled="!!openingFile" :aria-label="`查看 ${file.filename}`" @click="previewFile(file.file_id)"><LoaderCircle v-if="openingFile === file.file_id" class="size-3.5 animate-spin" /><Eye v-else class="size-3.5" />查看</button>
              <a :href="file.download_url" class="btn-ink h-7 gap-1 rounded-lg px-2.5 py-0 text-xs focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink/40" :aria-label="`下载 ${file.filename}`"><Download class="size-3.5" />下载</a>
            </div>
          </div>
          <ul v-if="file.sheets?.length" class="flex min-w-0 flex-wrap gap-1.5" aria-label="工作表">
            <li v-for="sheet in file.sheets" :key="sheet.sheet" class="inline-flex min-w-0 max-w-full items-start gap-1.5 rounded-md border border-line/70 bg-paper/60 px-2 py-0.5 text-[11px] leading-5 text-mute">
              <Table2 class="mt-1 size-3 shrink-0 text-fun-green" aria-hidden="true" />
              <span class="min-w-0 break-words [overflow-wrap:anywhere]">{{ sheet.sheet }}</span>
            </li>
          </ul>
        </div>
      </template>
      <template v-if="!typing">
        <section v-for="a in questions" :key="`b${a.key}`" class="overflow-hidden rounded-2xl border border-[#eadfc9] bg-[#fffcf6]">
          <div class="flex items-start gap-3 px-4 pb-3 pt-4 sm:px-5 sm:pt-5">
            <span class="flex size-9 shrink-0 items-center justify-center rounded-xl bg-[#fff1d2] text-[#a56d0d]">
              <MessageCircleQuestion class="size-[18px]" />
            </span>
            <div class="min-w-0 flex-1">
              <div class="text-[11px] font-semibold tracking-wide text-[#9a6b20]">需要你确认</div>
              <h3 class="mt-1 whitespace-pre-line break-words text-[15px] font-semibold leading-6 [overflow-wrap:anywhere]">{{ a.question }}</h3>
              <p class="mt-1 text-xs leading-5 text-mute">{{ answersClosed ? '该问题已结束，可在下方继续对话' : typing || answersDisabled ? '请稍等，正在更新回复' : '选择或直接输入回答' }}</p>
            </div>
          </div>
          <ol class="space-y-2 px-3 pb-3 sm:px-4 sm:pb-4" :aria-label="a.question">
            <li v-for="(opt, index) in a.options" :key="opt">
              <button
                type="button"
                class="group/option flex min-h-12 w-full items-center gap-3 rounded-xl border border-[#eee9df] bg-white px-3.5 py-3 text-left text-[13px] leading-5 text-ink shadow-[0_1px_2px_rgba(28,25,17,0.03)] transition enabled:hover:border-[#d6bf8d] enabled:hover:bg-[#fffdf8] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#d7a337] disabled:cursor-not-allowed disabled:opacity-50"
                :disabled="answersLocked"
                @click="emit('answer', opt)"
              >
                <span aria-hidden="true" class="flex size-6 shrink-0 items-center justify-center rounded-lg bg-paper text-[11px] font-semibold text-mute transition group-hover/option:bg-[#fff1d2] group-hover/option:text-[#9a6b20]">{{ index + 1 }}</span>
                <span class="min-w-0 flex-1 break-words [overflow-wrap:anywhere]">{{ opt }}</span>
                <ChevronRight aria-hidden="true" class="size-4 shrink-0 text-faint transition group-hover/option:translate-x-0.5 group-hover/option:text-[#a56d0d]" />
              </button>
            </li>
          </ol>
          <form class="border-t border-[#eadfc9] px-3 py-3 sm:px-4 sm:py-4" @submit.prevent="submitAnswer(a.key)">
            <label class="mb-2 block text-xs font-medium text-mute">
              自定义回答
              <textarea
                v-model="answerDrafts[a.key]"
                rows="2"
                class="mt-2 block w-full min-w-0 resize-y rounded-xl border border-[#eee9df] bg-white px-3.5 py-3 text-[13px] leading-5 text-ink outline-none transition focus:border-[#d6bf8d] focus:ring-2 focus:ring-[#d7a337]/15 disabled:cursor-not-allowed disabled:opacity-50"
                :disabled="answersLocked"
                @keydown="onAnswerKeydown($event, a.key)"
              />
            </label>
            <div class="flex justify-end">
              <button type="submit" class="btn-ink h-9 px-4 py-0 text-xs disabled:cursor-not-allowed disabled:opacity-50" :disabled="answersLocked || !answerDrafts[a.key]?.trim()">发送</button>
            </div>
          </form>
        </section>
      </template>

      <ExecutionStatusNotice v-if="!typing && !showError" :message="message" />

      <div v-if="!typing && showError" class="flex gap-2 rounded-xl bg-error/5 px-3.5 py-2.5 text-[13px] text-error">
        <CircleAlert class="mt-0.5 size-4 shrink-0" />
        <span>
          {{ errorMessage }}
          <RouterLink v-if="missingKeyError || message.error?.includes('API Key') || message.error?.includes('设置')" :to="{ name: 'settings' }" class="ml-1 font-medium underline">去设置</RouterLink>
        </span>
      </div>

      <div v-if="!typing && markdown" class="assistant-actions pointer-events-none flex items-center gap-1 pt-2 opacity-0 transition-opacity group-hover/message:pointer-events-auto group-hover/message:opacity-100 group-focus-within/message:pointer-events-auto group-focus-within/message:opacity-100">
        <button type="button" class="icon-btn -ml-2 disabled:opacity-50" :disabled="copying" aria-label="复制带格式的回复" title="复制带格式的回复，含阶段回复、回答、正文表格和文件链接，可粘贴到支持富文本的编辑器" @click="copyReply('formatted')">
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

/* 首行文字中心与头像中心对齐；多行内容的后续行自然向下排。 */
.assistant-content > .assistant-activity:first-child,
.assistant-stage:first-child > .assistant-process > summary,
.assistant-content > :deep(.prose-sm:first-child) {
  padding-top: max(0px, calc((var(--assistant-avatar-size) - 1lh) / 2));
}

.assistant-content > :deep(.prose-sm:first-child > :is(ul, ol):first-child > li:first-child),
.assistant-content > :deep(.prose-sm:first-child > :is(ul, ol):first-child > li:first-child > p:first-child),
.assistant-content > :deep(.prose-sm:first-child > blockquote:first-child > :first-child),
.assistant-content > :deep(.prose-sm:first-child > .markdown-block:first-child > :is(ul, ol):first-child > li:first-child),
.assistant-content > :deep(.prose-sm:first-child > .markdown-block:first-child > :is(ul, ol):first-child > li:first-child > p:first-child),
.assistant-content > :deep(.prose-sm:first-child > .markdown-block:first-child > blockquote:first-child > :first-child) {
  margin-top: 0;
}

.assistant-content.space-y-3 > .assistant-stage--before-process {
  margin-block-end: 0.25rem;
}

.process-title-running {
  display: inline-block;
  background-image: linear-gradient(90deg, var(--color-mute) 35%, var(--color-ink) 50%, var(--color-mute) 65%);
  background-size: 250% 100%;
  background-repeat: no-repeat;
  background-clip: text;
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  animation: process-shimmer 2s linear infinite;
}

@keyframes process-shimmer {
  /* With an oversized background, decreasing its position moves the highlight right. */
  from { background-position: 100% 0; }
  to { background-position: 0% 0; }
}

@media (prefers-reduced-motion: reduce) {
  .process-title-running {
    animation: none;
    background: none;
    -webkit-text-fill-color: currentColor;
  }
}
</style>

