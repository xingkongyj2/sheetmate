<script setup lang="ts">
/**
 * 助手消息：阶段小结（边做边说的几句话）→ 处理流程卡片 → 执行过程（默认折叠成一行）→ 回答（Markdown）→ 结果表 / 文件 / 澄清问题。
 * 阶段小结让用户知道大概干了什么；工具调用、参数等细节藏起来，想看再点开。
 */
import { ChevronRight, CircleAlert, Download, FileSpreadsheet, LoaderCircle, MessageCircleQuestion, PanelRight } from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import Mascot from '@/components/brand/Mascot.vue'
import MarkdownView from '@/components/common/MarkdownView.vue'
import RecipeProgressCard from '@/components/recipe/RecipeProgressCard.vue'
import type { UiMessage } from '@/composables/chatModel'

import TableCard from './TableCard.vue'
import TimelineList from './TimelineList.vue'

const props = defineProps<{ message: UiMessage; customerId?: string | null; selected?: boolean; mascot?: string | null }>()
const emit = defineEmits<{ answer: [text: string]; openTable: [name: string]; inspect: [] }>()

// 执行过程默认折叠：正在执行时也不会自动展开，想看自己点开
const showProcess = ref(false)
// 阶段小结：模型每做完一个阶段说的一两句话，留在消息里（不是被后面的内容替换掉）
const stageNotes = computed(() => props.message.timeline.flatMap((t) => (t.kind === 'note' ? [t] : [])))
const processItems = computed(() => props.message.timeline.filter((t) => t.kind !== 'note'))
const toolCount = computed(() => props.message.timeline.filter((t) => t.kind === 'tool').length)
const runningTool = computed(() => [...props.message.timeline].reverse().find((t) => t.kind === 'tool' && t.status === 'running'))
const mood = computed(() => (props.message.streaming ? (runningTool.value ? 'working' : 'thinking') : props.message.error ? 'idle' : 'happy'))

// 一条回答里可能有好几张中间表，消息里只放最后一张（最终结果）；其余的在右侧「执行明细」里看
// 「查询结果」是看数据时的探索（取值分布等），不是用户要的结果，不放进消息；它们在右侧「执行明细」里
const lastTable = computed(() => props.message.artifacts.flatMap((a) => (a.kind === 'table' && a.table.role !== 'query' ? [a.table] : [])).at(-1))
const recipes = computed(() => props.message.artifacts.flatMap((a) => (a.kind === 'recipe' ? [a] : [])))
const others = computed(() => props.message.artifacts.flatMap((a) => (a.kind === 'file' || a.kind === 'clarify' ? [a] : [])))

// 回答正文逐字显示：后端把最终回答整段给过来，这里做一个很快的打字动画；打开历史消息时直接整段显示
const animate = props.message.streaming
const shown = ref(animate ? '' : props.message.content)
let timer = 0
function tick() {
  const full = props.message.content
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
  () => props.message.content,
  (full) => {
    if (!animate) shown.value = full
    else if (!timer) timer = window.setInterval(tick, 30)
  },
)
onBeforeUnmount(() => window.clearInterval(timer))
const typing = computed(() => props.message.streaming || shown.value.length < props.message.content.length)
</script>

<template>
  <div class="group flex gap-3.5">
    <div class="w-9 shrink-0 pt-0.5">
      <Mascot :variant="mascot ?? 'blob'" :size="34" :mood="mood" :shadow="false" :track="false" />
    </div>
    <div class="min-w-0 flex-1 space-y-3">
      <!-- 阶段小结：知道大概干了什么 -->
      <MarkdownView v-for="n in stageNotes" :key="n.key" :source="n.text" class="animate-rise text-[14px] leading-relaxed text-mute" />

      <!-- 处理流程：一张精简卡片（细节折叠在里面） -->
      <RecipeProgressCard v-for="a in recipes" :key="a.key" :progress="a.progress" :customer-id="customerId" />

      <!-- 执行过程：默认折叠成一行，正在执行时这一行显示当前在做什么 -->
      <div v-if="processItems.length || message.streaming">
        <button class="flex items-center gap-1.5 text-xs text-mute hover:text-ink" @click="showProcess = !showProcess">
          <LoaderCircle v-if="message.streaming" class="size-3.5 animate-spin" />
          <ChevronRight v-else class="size-3.5 transition" :class="{ 'rotate-90': showProcess }" />
          <template v-if="message.streaming">
            {{ runningTool && runningTool.kind === 'tool' ? `正在${runningTool.label}…` : '思考中…' }}
          </template>
          <template v-else>{{ showProcess ? '收起执行过程' : `查看执行过程${toolCount ? ` · ${toolCount} 步` : ''}` }}</template>
        </button>
        <div v-if="showProcess && processItems.length" class="mt-2 border-l-2 border-line pl-3.5">
          <TimelineList :items="processItems" />
        </div>
      </div>

      <!-- 最终回答 -->
      <MarkdownView v-if="shown" :source="shown" :streaming="typing" />

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
        <div v-else-if="a.kind === 'clarify'" class="rounded-2xl border border-fun-yellow/50 bg-fun-yellow/10 p-4">
          <div class="flex items-center gap-2 text-[14px] font-medium"><MessageCircleQuestion class="size-4 text-[#b7860b]" />{{ a.question }}</div>
          <div class="mt-3 flex flex-wrap gap-2">
            <button v-for="opt in a.options" :key="opt" class="btn-line bg-white" :disabled="message.streaming" @click="emit('answer', opt)">{{ opt }}</button>
          </div>
        </div>
      </template>

      <div v-if="message.error" class="flex gap-2 rounded-xl bg-error/5 px-3.5 py-2.5 text-[13px] text-error">
        <CircleAlert class="mt-0.5 size-4 shrink-0" />
        <span>
          {{ message.error }}
          <RouterLink v-if="message.error.includes('API Key') || message.error.includes('设置')" :to="{ name: 'settings' }" class="ml-1 font-medium underline">去设置</RouterLink>
        </span>
      </div>

      <div v-if="!message.streaming && message.timeline.length" class="opacity-0 transition group-hover:opacity-100">
        <button class="btn-text -ml-2 text-xs" :class="{ '!text-ink': selected }" @click="emit('inspect')"><PanelRight class="size-3.5" />在右侧查看执行明细</button>
      </div>
    </div>
  </div>
</template>
