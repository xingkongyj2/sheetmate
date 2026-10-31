<script setup lang="ts">
/**
 * 助手消息：计划步骤与状态 → 处理流程 → 折叠的执行过程 → Markdown 回答与产物 → 两种复制操作。
 */
import { Check, ChevronRight, CircleAlert, Code, Copy, Download, FileSpreadsheet, LoaderCircle, MessageCircleQuestion } from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import Mascot from '@/components/brand/Mascot.vue'
import MarkdownView from '@/components/common/MarkdownView.vue'
import RecipeProgressCard from '@/components/recipe/RecipeProgressCard.vue'
import type { UiMessage } from '@/composables/chatModel'
import { copyMarkdown } from '@/composables/markdown'
import { lastResultTable, messageMarkdown, planSteps, recipeAttempts, recipeFailure, recipeOutcomeSummary } from '@/composables/messagePresentation'
import { useToast } from '@/composables/useToast'

import PlanProgress from './PlanProgress.vue'
import TableCard from './TableCard.vue'
import TimelineList from './TimelineList.vue'

const props = defineProps<{ message: UiMessage; customerId?: string | null; mascot?: string | null; answersDisabled?: boolean }>()
const emit = defineEmits<{ answer: [text: string]; openTable: [name: string] }>()
const toast = useToast()
const copying = ref(false)
const copied = ref<'formatted' | 'markdown' | null>(null)
let copiedTimer = 0
const markdown = computed(() => messageMarkdown(props.message))
const hasPlan = computed(() => planSteps(props.message).length > 0)
const attempts = computed(() => recipeAttempts(props.message))
const previousAttempts = computed(() => {
  const previous = attempts.value.slice(0, -1)
  return previous.length && previous.every((attempt) => recipeFailure(attempt.progress) && !attempt.progress.file) ? previous : []
})
const visibleAttempts = computed(() => previousAttempts.value.length ? attempts.value.slice(-1) : attempts.value)
const reply = computed(() => props.message.content.trim() ? props.message.content : recipeOutcomeSummary(props.message))

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

// 执行过程默认折叠：正在执行时也不会自动展开，想看自己点开
const showProcess = ref(false)
// 非计划任务执行时显示最新进展，结束后过程说明收进执行明细。
const stageNotes = computed(() => props.message.timeline.flatMap((t) => (t.kind === 'note' ? [t] : [])))
const stageNote = computed(() => {
  const latest = stageNotes.value.at(-1)?.text
  return latest && !attempts.value.some((attempt) => attempt.notes.includes(latest)) ? latest : ''
})
const processItems = computed(() => props.message.timeline.filter((t) => t.kind !== 'plan'))
const toolCount = computed(() => props.message.timeline.filter((t) => t.kind === 'tool').length)
const runningTool = computed(() => [...props.message.timeline].reverse().find((t) => t.kind === 'tool' && t.status === 'running'))
const mood = computed(() => {
  if (props.message.streaming) return runningTool.value ? 'working' : 'thinking'
  const latest = attempts.value.at(-1)
  return props.message.error || (latest && recipeFailure(latest.progress)) ? 'idle' : 'happy'
})

// 一条回答里可能有好几张中间表，消息里只放最后一张（最终结果）；其余的在右侧「执行明细」里看
// 「查询结果」是看数据时的探索（取值分布等），不是用户要的结果，不放进消息；它们在右侧「执行明细」里
const lastTable = computed(() => lastResultTable(props.message))
const others = computed(() => props.message.artifacts.flatMap((a) => (a.kind === 'file' || a.kind === 'clarify' ? [a] : [])))

// 回答正文逐字显示：后端把最终回答整段给过来，这里做一个很快的打字动画；打开历史消息时直接整段显示
const animate = props.message.streaming
const shown = ref(animate ? '' : reply.value)
let timer = 0
function tick() {
  const full = reply.value
  if (!full.startsWith(shown.value)) shown.value = '' // 已显示的部分被撤回（其实是过程说明）：从头来
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
)
onBeforeUnmount(() => {
  window.clearInterval(timer)
  window.clearTimeout(copiedTimer)
})
const typing = computed(() => props.message.streaming || shown.value.length < reply.value.length)
</script>

<template>
  <div class="assistant-message group flex items-start gap-3 sm:gap-4">
    <div class="size-12 shrink-0">
      <Mascot :variant="mascot ?? 'blob'" :size="48" :mood="mood" :shadow="false" :track="false" />
    </div>
    <div class="assistant-content min-w-0 flex-1 space-y-3">
      <MarkdownView v-if="shown" :source="shown" :streaming="typing" />

      <!-- 阶段小结：知道大概干了什么 -->
      <PlanProgress v-if="hasPlan" :message="message" />
      <MarkdownView v-else-if="message.streaming && !shown && stageNote" :source="stageNote" class="animate-rise !text-[14px] !text-mute" />

      <!-- 处理流程：一张精简卡片（细节折叠在里面） -->
      <template v-for="attempt in visibleAttempts" :key="attempt.key">
        <p v-if="attempt.notes.length && attempts.length > 1" class="text-xs font-medium text-mute">{{ attempt.label }}</p>
        <MarkdownView v-if="attempt.notes.length" :source="attempt.notes.join('\n\n')" class="!text-[14px] !leading-6" />
        <p v-else class="assistant-attempt-note text-[14px] leading-6 text-mute">{{ attempt.label }}</p>
        <RecipeProgressCard :progress="attempt.progress" :customer-id="customerId" />
      </template>

      <details v-if="previousAttempts.length" class="group/attempts rounded-xl border border-line px-4 py-3">
        <summary class="flex cursor-pointer list-none items-center gap-1.5 text-xs text-mute hover:text-ink [&::-webkit-details-marker]:hidden">
          <ChevronRight class="size-3.5 transition group-open/attempts:rotate-90" />
          查看前 {{ previousAttempts.length }} 次未完成的尝试
        </summary>
        <div class="mt-4 space-y-5">
          <section v-for="attempt in previousAttempts" :key="attempt.key" class="space-y-2.5" :aria-label="attempt.label">
            <p class="text-xs font-medium text-mute">{{ attempt.label }}</p>
            <MarkdownView v-if="attempt.notes.length" :source="attempt.notes.join('\n\n')" class="!text-[14px] !leading-6" />
            <RecipeProgressCard :progress="attempt.progress" :customer-id="customerId" />
          </section>
        </div>
      </details>

      <!-- 执行过程：默认折叠成一行，正在执行时这一行显示当前在做什么 -->
      <div v-if="processItems.length || message.streaming" class="assistant-process">
        <button class="flex items-center gap-1.5 text-xs text-mute hover:text-ink" @click="showProcess = !showProcess">
          <LoaderCircle v-if="message.streaming" class="size-3.5 animate-spin" />
          <ChevronRight v-else class="size-3.5 transition" :class="{ 'rotate-90': showProcess }" />
          <template v-if="message.streaming">
            {{ runningTool && runningTool.kind === 'tool' ? `正在${runningTool.label}…` : '思考中…' }}
          </template>
          <template v-else>{{ showProcess ? '收起执行过程' : `查看执行过程${toolCount ? ` · ${toolCount} 次工具调用` : ''}` }}</template>
        </button>
        <div v-if="showProcess && processItems.length" class="mt-2 border-l-2 border-line pl-3.5">
          <TimelineList :items="processItems" />
        </div>
      </div>

      <!-- 结果：最后一张表、文件、需要用户回答的问题 -->
      <TableCard v-if="lastTable" :table="lastTable" @open="(n) => emit('openTable', n)" />
      <template v-for="a in others" :key="`b${a.key}`">
        <div v-if="a.kind === 'file'" class="card-soft flex items-center gap-3 px-4 py-3">
          <FileSpreadsheet class="size-5 text-fun-green" />
          <div class="min-w-0 flex-1">
            <div class="truncate text-[13px] font-semibold">{{ a.file.filename }}</div>
            <div class="text-[11px] text-faint">{{ (a.file.sheets ?? []).map((s) => `${s.sheet} ${s.rows} 行`).join(' · ') }}</div>
          </div>
          <a :href="a.file.download_url" class="btn-ink py-2"><Download class="size-4" />下载</a>
        </div>
        <section v-else-if="a.kind === 'clarify'" class="overflow-hidden rounded-2xl border border-[#eadfc9] bg-[#fffcf6]">
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

      <div v-if="message.error" class="flex gap-2 rounded-xl bg-error/5 px-3.5 py-2.5 text-[13px] text-error">
        <CircleAlert class="mt-0.5 size-4 shrink-0" />
        <span>
          {{ message.error }}
          <RouterLink v-if="message.error.includes('API Key') || message.error.includes('设置')" :to="{ name: 'settings' }" class="ml-1 font-medium underline">去设置</RouterLink>
        </span>
      </div>

      <div v-if="!message.streaming && markdown" class="flex items-center gap-1 border-t border-line/60 pt-2">
        <button type="button" class="icon-btn -ml-2 disabled:opacity-50" :disabled="copying" aria-label="复制带格式的回复" title="复制带格式的回复，含计划摘要、结果预览和文件链接，可粘贴到支持富文本的编辑器" @click="copyReply('formatted')">
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

.assistant-process:first-child > button {
  min-height: var(--assistant-avatar-size);
}

.assistant-attempt-note:first-child {
  display: flex;
  align-items: center;
  min-height: var(--assistant-avatar-size);
}
</style>
