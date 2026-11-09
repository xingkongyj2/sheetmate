<script setup lang="ts">
/** 思考模式的进度卡：步骤和结果各用一句短文，完成后保留小结，由用户控制展开。 */
import { Check, ChevronDown, CircleAlert, CircleHelp, LoaderCircle, Pause } from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { checklistSummary, type ChecklistItem } from '@/composables/thinkingPresentation'

const props = defineProps<{ items: ChecklistItem[]; running: boolean }>()

const summary = computed(() => checklistSummary(props.items, props.running))
const expanded = ref(typeof window === 'undefined' || !window.matchMedia('(max-height: 600px)').matches)
const list = ref<HTMLElement | null>(null)
let currentRow: HTMLElement | null = null
let resizeObserver: ResizeObserver | undefined
const currentKey = computed(() => props.items.find((item) => item.status === 'running')?.key)

// 只滚动清单自己，避免新步骤把聊天正文带走。
async function keepCurrentVisible() {
  if (!currentKey.value || !expanded.value) return
  await nextTick()
  const box = list.value
  const row = currentRow
  if (!box || !row) return
  const top = row.offsetTop - box.offsetTop
  if (top < box.scrollTop) box.scrollTop = top
  else if (top + row.offsetHeight > box.scrollTop + box.clientHeight) box.scrollTop = top + row.offsetHeight - box.clientHeight
}
watch([currentKey, expanded], keepCurrentVisible, { immediate: true })
onMounted(() => {
  if (typeof ResizeObserver === 'undefined' || !list.value) return
  resizeObserver = new ResizeObserver(() => { void keepCurrentVisible() })
  resizeObserver.observe(list.value)
})
onBeforeUnmount(() => resizeObserver?.disconnect())

function trackRow(item: ChecklistItem, el: unknown) {
  if (item.status === 'running') currentRow = (el as HTMLElement | null) ?? null
}

const LABELS: Record<string, string> = {
  pending: '待处理', running: '进行中', done: '已完成', warning: '已完成，有需要留意的地方',
  waiting: '等待确认', error: '出错', stopped: '已停止',
}
</script>

<template>
  <section
    class="overflow-hidden rounded-2xl border border-line bg-white shadow-[0_3px_16px_rgba(0,0,0,0.035)]"
    aria-label="任务进度"
    data-testid="task-checklist"
  >
    <button
      type="button"
      class="flex w-full items-center gap-2 px-4 py-3 text-left focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ink"
      :aria-expanded="expanded"
      aria-label="展开或收起任务进度"
      @click="expanded = !expanded"
    >
      <span class="shrink-0 text-[13px] font-medium text-mute">进度</span>
      <span class="shrink-0 text-[11px] tabular-nums text-faint">{{ summary.done }}/{{ summary.total }}</span>
      <span class="min-w-0 flex-1 truncate text-[11px] text-faint" aria-live="polite">{{ expanded && running ? '进行中' : summary.label }}</span>
      <ChevronDown class="size-3.5 shrink-0 text-faint transition-transform" :class="{ 'rotate-180': !expanded }" aria-hidden="true" />
    </button>

    <ol v-show="expanded" ref="list" class="task-checklist-list overflow-y-auto px-4 pb-3">
      <li
        v-for="item in items"
        :key="item.key"
        :ref="(el) => trackRow(item, el)"
        class="flex items-start gap-2.5 py-2"
        :data-status="item.status"
      >
        <span class="mt-0.5 flex size-[18px] shrink-0 items-center justify-center" role="img" :aria-label="`第 ${item.num} 步：${LABELS[item.status]}`">
          <LoaderCircle v-if="item.status === 'running'" class="size-[18px] animate-spin text-ink motion-reduce:animate-none" aria-hidden="true" />
          <span v-else-if="item.status === 'done' || item.status === 'warning'" class="flex size-4 items-center justify-center rounded-[5px] text-white" :class="item.status === 'warning' ? 'bg-[#9a6b00]' : 'bg-ink'" data-testid="step-complete" aria-hidden="true">
            <Check class="size-3" :stroke-width="3" />
          </span>
          <CircleAlert v-else-if="item.status === 'error'" class="size-[18px] text-error" aria-hidden="true" />
          <CircleHelp v-else-if="item.status === 'waiting'" class="size-[18px] text-[#9a6b00]" aria-hidden="true" />
          <Pause v-else-if="item.status === 'stopped'" class="size-4 text-mute" aria-hidden="true" />
          <span v-else class="flex size-[18px] items-center justify-center rounded-full bg-paper text-[11px] tabular-nums text-faint" aria-hidden="true">{{ item.num }}</span>
        </span>
        <div class="min-w-0 flex-1">
          <p class="break-words text-[13px] leading-[21px] [overflow-wrap:anywhere]" :class="item.status === 'running' ? 'font-medium text-ink' : item.status === 'pending' ? 'text-faint' : 'text-mute'">{{ item.description }}</p>
          <p v-if="item.status !== 'pending'" class="mt-0.5 break-words text-xs leading-[18px] [overflow-wrap:anywhere]" :class="item.status === 'error' ? 'text-error' : item.status === 'warning' || item.status === 'waiting' ? 'text-[#9a6b00]' : 'text-faint'" data-testid="step-summary">{{ item.summary }}</p>
        </div>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.task-checklist-list {
  max-height: min(420px, 44vh);
  scrollbar-width: thin;
}
@media (max-width: 767px) {
  .task-checklist-list { max-height: 160px; }
}
@container chat-workspace (max-width: 1039px) {
  .task-checklist-list { max-height: min(160px, 28vh); }
}
@media (max-height: 600px) {
  @container chat-workspace (max-width: 1039px) {
    .task-checklist-list { max-height: 56px; }
  }
}
</style>
