<script setup lang="ts">
/**
 * 助手消息：执行过程（可折叠）→ 回答正文（Markdown）→ 产物（结果表、处理流程卡片、文件、澄清问题）。
 */
import { ChevronRight, CircleAlert, Download, FileSpreadsheet, MessageCircleQuestion, PanelRight } from '@lucide/vue'
import { computed, ref } from 'vue'

import Mascot from '@/components/brand/Mascot.vue'
import MarkdownView from '@/components/common/MarkdownView.vue'
import RecipeProgressCard from '@/components/recipe/RecipeProgressCard.vue'
import type { UiMessage } from '@/composables/chatModel'

import TableCard from './TableCard.vue'
import TimelineList from './TimelineList.vue'

const props = defineProps<{ message: UiMessage; customerId?: string | null; selected?: boolean; mascot?: string | null }>()
const emit = defineEmits<{ answer: [text: string]; openTable: [name: string]; inspect: [] }>()

const showProcess = ref(false)
const toolCount = computed(() => props.message.timeline.filter((t) => t.kind === 'tool').length)
const runningTool = computed(() => [...props.message.timeline].reverse().find((t) => t.kind === 'tool' && t.status === 'running'))
const processOpen = computed(() => showProcess.value || (props.message.streaming && !props.message.content))
const mood = computed(() => (props.message.streaming ? (runningTool.value ? 'working' : 'thinking') : props.message.error ? 'idle' : 'happy'))
</script>

<template>
  <div class="group flex gap-3.5">
    <div class="w-9 shrink-0 pt-0.5">
      <Mascot :variant="mascot ?? 'blob'" :size="34" :mood="mood" :shadow="false" :track="false" />
    </div>
    <div class="min-w-0 flex-1 space-y-3">
      <!-- 过程 -->
      <div v-if="message.timeline.length">
        <button class="flex items-center gap-1.5 text-xs text-mute hover:text-ink" @click="showProcess = !showProcess">
          <ChevronRight class="size-3.5 transition" :class="{ 'rotate-90': processOpen }" />
          <template v-if="message.streaming && runningTool && runningTool.kind === 'tool'">正在{{ runningTool.label }}…</template>
          <template v-else>执行过程 · {{ toolCount }} 次工具调用</template>
        </button>
        <div v-if="processOpen" class="mt-2 border-l-2 border-line pl-3.5">
          <TimelineList :items="message.timeline" />
        </div>
      </div>

      <!-- 等待第一段输出 -->
      <div v-if="message.streaming && !message.content && !message.timeline.length" class="flex items-center gap-2 py-1 text-sm text-mute">
        <span class="flex gap-1"><span class="size-1.5 animate-bounce rounded-full bg-faint" /><span class="size-1.5 animate-bounce rounded-full bg-faint [animation-delay:.15s]" /><span class="size-1.5 animate-bounce rounded-full bg-faint [animation-delay:.3s]" /></span>
        思考中
      </div>

      <!-- 产物：处理流程放在正文前面，结果表和文件放在后面 -->
      <template v-for="a in message.artifacts" :key="a.key">
        <RecipeProgressCard v-if="a.kind === 'recipe'" :progress="a.progress" :customer-id="customerId" />
      </template>

      <MarkdownView v-if="message.content" :source="message.content" :streaming="message.streaming" />

      <template v-for="a in message.artifacts" :key="`b${a.key}`">
        <TableCard v-if="a.kind === 'table'" :table="a.table" @open="(n) => emit('openTable', n)" />
        <div v-else-if="a.kind === 'file'" class="card-soft flex items-center gap-3 px-4 py-3">
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
