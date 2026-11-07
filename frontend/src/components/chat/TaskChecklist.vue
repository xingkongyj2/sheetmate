<script setup lang="ts">
/**
 * 任务清单：AI 选择展示整体进度时出现，挂在输入框上方的「抽屉」里。
 * 进行中的一项转圈，做完一项打一个勾；任务进行时展开，结束后自动收成一行，随时可以点开看。
 * 后端没有要求展示清单时不渲染这个组件。
 */
import { Check, ChevronDown, CircleAlert, CircleHelp, ListChecks, LoaderCircle, Pause } from '@lucide/vue'
import { computed, nextTick, ref, watch } from 'vue'

import { checklistSummary, type ChecklistItem } from '@/composables/thinkingPresentation'

const props = defineProps<{ items: ChecklistItem[]; running: boolean }>()

const summary = computed(() => checklistSummary(props.items, props.running))
const manual = ref<boolean | null>(null) // 用户手动展开 / 收起后，不再自动变化
const expanded = computed(() => manual.value ?? props.running)
const list = ref<HTMLElement | null>(null)
let currentRow: HTMLElement | null = null // 正在做的那一行（由函数 ref 记下，不靠 DOM 查询）
const currentKey = computed(() => props.items.find((item) => item.status === 'running')?.key)

function toggle() {
  manual.value = !expanded.value
}

function trackRow(item: ChecklistItem, el: unknown) {
  if (item.status === 'running') currentRow = (el as HTMLElement | null) ?? null
}

// 清单比较长时，让正在做的那一项始终看得见。只滚清单自己：scrollIntoView 会连聊天区一起滚。
watch([currentKey, expanded], async ([key, open]) => {
  if (!key || !open) return
  await nextTick()
  const box = list.value
  const row = currentRow
  if (!box || !row) return
  const top = row.offsetTop - box.offsetTop
  if (top < box.scrollTop) box.scrollTop = top
  else if (top + row.offsetHeight > box.scrollTop + box.clientHeight) box.scrollTop = top + row.offsetHeight - box.clientHeight
}, { immediate: true })

const LABELS: Record<string, string> = {
  pending: '待处理', running: '进行中', done: '已完成', warning: '已完成，有需要留意的地方',
  waiting: '等待确认', error: '出错', stopped: '已停止',
}
</script>

<template>
  <section
    class="relative -mb-5 rounded-t-[22px] bg-white pb-6 shadow-[0_4px_24px_rgba(0,0,0,0.06),0_1px_4px_rgba(0,0,0,0.03)]"
    aria-label="任务进度"
    data-testid="task-checklist"
  >
    <button
      type="button"
      class="flex w-full items-center gap-2 rounded-t-[22px] px-4 py-2.5 text-left focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ink"
      :aria-expanded="expanded"
      @click="toggle"
    >
      <LoaderCircle v-if="summary.tone === 'running'" class="size-4 shrink-0 animate-spin text-info motion-reduce:animate-none" aria-hidden="true" />
      <CircleAlert v-else-if="summary.tone === 'error'" class="size-4 shrink-0 text-error" aria-hidden="true" />
      <CircleHelp v-else-if="summary.tone === 'waiting' || summary.tone === 'warn'" class="size-4 shrink-0 text-[#9a6b00]" aria-hidden="true" />
      <Pause v-else-if="summary.tone === 'stopped'" class="size-3.5 shrink-0 text-mute" aria-hidden="true" />
      <ListChecks v-else class="size-4 shrink-0 text-success" aria-hidden="true" />
      <span class="shrink-0 text-[13px] font-medium">任务进度</span>
      <span class="shrink-0 text-xs tabular-nums text-mute">{{ summary.done }}/{{ summary.total }}</span>
      <span class="min-w-0 flex-1 truncate text-xs text-mute" aria-live="polite">{{ summary.label }}</span>
      <ChevronDown class="size-4 shrink-0 text-faint transition" :class="{ 'rotate-180': expanded }" aria-hidden="true" />
    </button>

    <ol v-show="expanded" ref="list" class="max-h-40 overflow-y-auto px-4 pb-1">
      <li
        v-for="item in items"
        :key="item.key"
        :ref="(el) => trackRow(item, el)"
        class="flex items-start gap-2.5 py-1 text-[13px] leading-5"
        :data-status="item.status"
      >
        <span class="mt-0.5 flex size-4 shrink-0 items-center justify-center" role="img" :aria-label="LABELS[item.status]">
          <LoaderCircle v-if="item.status === 'running'" class="size-4 animate-spin text-info motion-reduce:animate-none" />
          <span v-else-if="item.status === 'done'" class="flex size-4 items-center justify-center rounded-full bg-success text-white"><Check class="size-3" :stroke-width="3" /></span>
          <span v-else-if="item.status === 'warning'" class="flex size-4 items-center justify-center rounded-full bg-[#d99a00] text-white"><Check class="size-3" :stroke-width="3" /></span>
          <CircleAlert v-else-if="item.status === 'error'" class="size-4 text-error" />
          <CircleHelp v-else-if="item.status === 'waiting'" class="size-4 text-[#9a6b00]" />
          <Pause v-else-if="item.status === 'stopped'" class="size-3.5 text-mute" />
          <span v-else class="size-4 rounded-full border border-faint/70" />
        </span>
        <span
          class="min-w-0 flex-1 break-words [overflow-wrap:anywhere]"
          :class="item.status === 'running' ? 'font-medium text-ink' : item.status === 'pending' ? 'text-faint' : 'text-mute'"
        >{{ item.description }}</span>
      </li>
    </ol>
  </section>
</template>
