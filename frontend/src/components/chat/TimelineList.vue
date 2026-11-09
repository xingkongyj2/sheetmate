<script setup lang="ts">
/** 执行过程：中间说明、工具调用（状态 / 耗时 / 结果）、计划步骤、多表专员、记忆等 */
import { Brain, Check, ChevronRight, CircleAlert, ListChecks, LoaderCircle, MessageSquareText, Sparkles, Users } from '@lucide/vue'
import { computed, ref } from 'vue'

import { AGENT_STATUS_LABELS, duration, type TimelineItem } from '@/composables/chatModel'
import { PLAN_STATUS_LABELS } from '@/composables/messagePresentation'
import { toolPresentation, userFacingSummary, userFacingText } from '@/composables/timelinePresentation'
import MarkdownView from '@/components/common/MarkdownView.vue'
import ScriptFile from '@/components/common/ScriptFile.vue'
import { scriptReferences } from '@/composables/scriptPresentation'

const props = withDefaults(defineProps<{ items: TimelineItem[]; detailed?: boolean; parentTitle?: string }>(), { detailed: false })
const open = ref<Record<string, boolean>>({})
const visibleItems = computed(() => props.items.filter((item) =>
  (props.detailed || !['thinking', 'script'].includes(item.kind)) &&
  (!['note', 'info'].includes(item.kind) || ('text' in item && !!userFacingText(item.text))),
))
const tools = computed(() => new Map(props.items.flatMap((item) => item.kind === 'tool' ? [[item.key, toolPresentation(item)] as const] : [])))
const recipeTools = new Set(['build_recipe', 'revise_recipe', 'run_recipe'])

function repeatsParentTitle(...titles: (string | undefined)[]): boolean {
  const normalize = (title: string | undefined) => userFacingText(title, '', 120).replace(/^正在/, '')
  const parent = normalize(props.parentTitle)
  return !props.detailed && !!parent && titles.some((title) => normalize(title) === parent)
}
</script>

<template>
  <ol class="space-y-1">
    <li v-for="item in visibleItems" :key="item.key" class="text-[13px]">
      <!-- 中间说明 -->
      <div v-if="item.kind === 'note'" class="flex gap-2 py-0.5 text-mute">
        <MessageSquareText class="mt-0.5 size-3.5 shrink-0" />
        <MarkdownView :source="userFacingSummary(item.text)" class="min-w-0 flex-1 !text-[13px] !leading-6" />
      </div>

      <!-- 模型思考 -->
      <div v-else-if="item.kind === 'thinking'" class="flex items-center gap-2 py-0.5 text-faint">
        <Brain class="size-3.5" />分析任务与处理步骤
      </div>

      <!-- 工具调用 -->
      <div v-else-if="item.kind === 'tool'">
        <div class="flex w-full items-start gap-2 rounded-lg py-1 text-left">
          <LoaderCircle v-if="item.status === 'running'" class="mt-0.5 size-3.5 shrink-0 animate-spin text-info" />
          <Check v-else-if="item.status === 'ok'" class="mt-0.5 size-3.5 shrink-0 text-success" />
          <CircleAlert v-else class="mt-0.5 size-3.5 shrink-0 text-error" />
          <div class="min-w-0 flex-1 space-y-1 break-words leading-5 [overflow-wrap:anywhere]">
            <p v-if="!repeatsParentTitle(item.label, tools.get(item.key)?.label)" class="font-medium text-ink">{{ tools.get(item.key)?.label }}</p>
            <p v-else-if="!tools.get(item.key)?.details.length && (!tools.get(item.key)?.summary || recipeTools.has(item.tool))" class="text-xs leading-5 text-mute">{{ item.status === 'running' ? '正在处理' : item.status === 'ok' ? '已完成' : '未完成' }}</p>
            <div v-if="tools.get(item.key)?.sections.length" class="space-y-3 pt-1 text-xs leading-5 text-mute">
              <details v-for="section in tools.get(item.key)?.sections" :key="section.label" class="group/tool-section">
                <summary class="flex cursor-pointer list-none items-start gap-1.5 rounded py-0.5 text-[11px] font-medium text-faint transition hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink [&::-webkit-details-marker]:hidden">
                  <ChevronRight class="mt-1 size-3 shrink-0 transition group-open/tool-section:rotate-90" />
                  <span>{{ section.label }}</span>
                </summary>
                <div class="mt-1.5 pl-4.5">
                  <ol v-if="section.ordered" class="list-decimal space-y-2 pl-4 marker:text-faint">
                    <li v-for="(text, index) in section.items" :key="index" class="pl-0.5">
                      <MarkdownView :source="text" class="!text-xs !leading-5 !text-mute [&_p]:my-0" />
                    </li>
                  </ol>
                  <ul v-else-if="section.items.length > 1" class="list-disc space-y-1.5 pl-4 marker:text-faint">
                    <li v-for="(text, index) in section.items" :key="index" class="pl-0.5">
                      <MarkdownView :source="text" class="!text-xs !leading-5 !text-mute [&_p]:my-0" />
                    </li>
                  </ul>
                  <MarkdownView v-else :source="section.items[0] ?? ''" class="!text-xs !leading-5 !text-mute [&_p]:my-0" />
                </div>
              </details>
            </div>
            <p v-for="detail in tools.get(item.key)?.sections.length ? [] : tools.get(item.key)?.details" :key="detail" class="whitespace-pre-line text-xs leading-5 text-mute">{{ detail }}</p>
            <p v-if="tools.get(item.key)?.summary && (detailed || !recipeTools.has(item.tool))" class="text-xs leading-5" :class="item.status === 'error' ? 'text-error' : 'text-mute'">{{ tools.get(item.key)?.summary }}</p>
          </div>
          <span v-if="detailed && item.duration_ms" class="ml-auto shrink-0 font-mono text-[10px] text-faint">{{ duration(item.duration_ms) }}</span>
        </div>
        <div v-if="detailed" class="ml-5 space-y-1">
          <ScriptFile v-for="script in scriptReferences([item.params, item.text])" :key="script.script_id" :script="script" compact />
        </div>
      </div>

      <ScriptFile v-else-if="item.kind === 'script'" :script="item.script" compact />

      <!-- 计划步骤 -->
      <div v-else-if="item.kind === 'plan'" class="flex items-center gap-2 py-1">
        <LoaderCircle v-if="item.status === 'running'" class="size-3.5 animate-spin text-info" />
        <ListChecks v-else class="size-3.5" :class="item.status === 'done' ? 'text-success' : 'text-mute'" />
        <span v-if="detailed" class="font-medium">第 {{ item.num }}/{{ item.total }} 步</span>
        <span v-if="!repeatsParentTitle(item.description)" class="min-w-0 break-words text-mute">{{ userFacingText(item.description) }}</span>
        <span class="shrink-0 text-xs text-faint">{{ PLAN_STATUS_LABELS[item.status] }}</span>
      </div>

      <!-- 多表专员 -->
      <div v-else-if="item.kind === 'agent'" class="py-1">
        <button type="button" class="flex items-center gap-2" :aria-expanded="!!open[item.key]" @click="open[item.key] = !open[item.key]">
          <LoaderCircle v-if="item.status === 'running'" class="size-3.5 animate-spin text-info" />
          <Users v-else class="size-3.5" :class="item.status === 'error' ? 'text-error' : item.status === 'done' ? 'text-success' : item.status === 'waiting' ? 'text-info' : 'text-[#b7770c]'" />
          <span class="font-medium">专员 · {{ item.table }}</span>
          <span class="text-xs" :class="item.status === 'error' ? 'text-error' : item.status === 'done' ? 'text-success' : 'text-mute'">{{ AGENT_STATUS_LABELS[item.status] }}</span>
          <ChevronRight v-if="item.conclusion" class="size-3 text-faint transition" :class="{ 'rotate-90': open[item.key] }" />
        </button>
        <p v-if="open[item.key] && item.conclusion" class="mt-1 whitespace-pre-wrap border-l-2 border-line pl-3 text-xs leading-relaxed text-mute">{{ userFacingText(item.conclusion) }}</p>
      </div>

      <!-- 其他信息 -->
      <div v-else-if="item.kind === 'info'" class="flex items-center gap-2 py-0.5 text-mute">
        <Sparkles class="size-3.5 shrink-0" :class="item.tone === 'memory' || item.tone === 'mapping' ? 'text-fun-purple' : 'text-faint'" />
        <span>{{ userFacingText(item.text) }}</span>
      </div>
    </li>
  </ol>
</template>
