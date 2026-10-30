<script setup lang="ts">
/** 执行过程：中间说明、工具调用（状态 / 耗时 / 结果）、计划步骤、多表专员、记忆等 */
import { Brain, Check, ChevronRight, CircleAlert, ListChecks, LoaderCircle, MessageSquareText, Sparkles, Users } from '@lucide/vue'
import { ref } from 'vue'

import { duration, type TimelineItem } from '@/composables/chatModel'

withDefaults(defineProps<{ items: TimelineItem[]; detailed?: boolean }>(), { detailed: false })
const open = ref<Record<string, boolean>>({})
</script>

<template>
  <ol class="space-y-1">
    <li v-for="item in items" :key="item.key" class="text-[13px]">
      <!-- 中间说明 -->
      <div v-if="item.kind === 'note'" class="flex gap-2 py-0.5 text-mute">
        <MessageSquareText class="mt-0.5 size-3.5 shrink-0" />
        <span class="line-clamp-3 leading-relaxed">{{ item.text }}</span>
      </div>

      <!-- 模型思考 -->
      <div v-else-if="item.kind === 'thinking'" class="py-0.5">
        <button class="flex items-center gap-2 text-faint hover:text-mute" @click="open[item.key] = !open[item.key]">
          <Brain class="size-3.5" />思考过程<ChevronRight class="size-3 transition" :class="{ 'rotate-90': open[item.key] }" />
        </button>
        <p v-if="open[item.key]" class="mt-1 whitespace-pre-wrap border-l-2 border-line pl-3 text-xs leading-relaxed text-faint">{{ item.text }}</p>
      </div>

      <!-- 工具调用 -->
      <div v-else-if="item.kind === 'tool'">
        <button class="flex w-full items-center gap-2 rounded-lg py-1 text-left hover:text-ink" @click="open[item.key] = !open[item.key]">
          <LoaderCircle v-if="item.status === 'running'" class="size-3.5 shrink-0 animate-spin text-info" />
          <Check v-else-if="item.status === 'ok'" class="size-3.5 shrink-0 text-success" />
          <CircleAlert v-else class="size-3.5 shrink-0 text-error" />
          <span class="shrink-0 font-medium text-ink">{{ item.label }}</span>
          <span v-if="item.summary" class="min-w-0 truncate text-mute">— {{ item.summary }}</span>
          <span v-if="item.duration_ms" class="ml-auto shrink-0 font-mono text-[10px] text-faint">{{ duration(item.duration_ms) }}</span>
        </button>
        <div v-if="open[item.key] || (detailed && item.status === 'error')" class="mb-1 ml-5 space-y-1.5">
          <pre class="max-h-40 overflow-auto rounded-lg bg-paper p-2 font-mono text-[11px] leading-relaxed text-mute">{{ JSON.stringify(item.params, null, 2) }}</pre>
          <pre v-if="item.text" class="max-h-48 overflow-auto whitespace-pre-wrap rounded-lg p-2 font-mono text-[11px] leading-relaxed" :class="item.status === 'error' ? 'bg-error/5 text-error' : 'bg-paper text-mute'">{{ item.text }}</pre>
        </div>
      </div>

      <!-- 计划步骤 -->
      <div v-else-if="item.kind === 'plan'" class="flex items-center gap-2 py-1">
        <LoaderCircle v-if="item.status === 'running'" class="size-3.5 animate-spin text-info" />
        <ListChecks v-else class="size-3.5 text-success" />
        <span class="font-medium">第 {{ item.num }}/{{ item.total }} 步</span>
        <span class="truncate text-mute">{{ item.description }}</span>
      </div>

      <!-- 多表专员 -->
      <div v-else-if="item.kind === 'agent'" class="py-1">
        <button class="flex items-center gap-2" @click="open[item.key] = !open[item.key]">
          <LoaderCircle v-if="item.status === 'running'" class="size-3.5 animate-spin text-info" />
          <Users v-else class="size-3.5" :class="item.status === 'error' ? 'text-error' : 'text-success'" />
          <span class="font-medium">专员 · {{ item.table }}</span>
          <ChevronRight v-if="item.conclusion" class="size-3 text-faint transition" :class="{ 'rotate-90': open[item.key] }" />
        </button>
        <p v-if="open[item.key] && item.conclusion" class="mt-1 whitespace-pre-wrap border-l-2 border-line pl-3 text-xs leading-relaxed text-mute">{{ item.conclusion }}</p>
      </div>

      <!-- 其他信息 -->
      <div v-else-if="item.kind === 'info'" class="flex items-center gap-2 py-0.5 text-mute">
        <Sparkles class="size-3.5 shrink-0" :class="item.tone === 'memory' || item.tone === 'mapping' ? 'text-fun-purple' : 'text-faint'" />
        <span>{{ item.text }}</span>
      </div>
    </li>
  </ol>
</template>
