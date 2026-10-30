<script setup lang="ts">
import { Check, CircleAlert, CircleHelp, ListChecks, LoaderCircle, Pause } from '@lucide/vue'
import { computed } from 'vue'

import MarkdownView from '@/components/common/MarkdownView.vue'
import type { UiMessage } from '@/composables/chatModel'
import { planSteps } from '@/composables/messagePresentation'

const props = defineProps<{ message: UiMessage }>()
const steps = computed(() => planSteps(props.message))
const completed = computed(() => steps.value.filter((step) => step.status === 'done').length)
</script>

<template>
  <section v-if="steps.length" class="overflow-hidden rounded-2xl border border-line bg-white" aria-label="计划执行情况">
    <div class="flex items-center justify-between border-b border-line bg-paper/60 px-4 py-3">
      <h3 class="flex items-center gap-2 text-sm font-semibold"><ListChecks class="size-4" />执行计划</h3>
      <span class="text-xs text-mute">{{ completed }} / {{ steps.length }} 步已完成</span>
    </div>
    <ol class="divide-y divide-line px-4">
      <li v-for="step in steps" :key="step.key" class="py-3.5">
        <div class="flex items-start gap-2.5">
          <span class="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-paper text-[11px] font-semibold">
            <LoaderCircle v-if="step.status === 'running'" class="size-3.5 animate-spin text-info" />
            <Check v-else-if="step.status === 'done'" class="size-3.5 text-success" />
            <CircleAlert v-else-if="step.status === 'error' || step.status === 'warning'" class="size-3.5" :class="step.status === 'error' ? 'text-error' : 'text-[#9a6b00]'" />
            <CircleHelp v-else-if="step.status === 'waiting'" class="size-3.5 text-[#9a6b00]" />
            <Pause v-else-if="step.status === 'stopped'" class="size-3 text-mute" />
            <template v-else>{{ step.num }}</template>
          </span>
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
              <h4 class="text-[13px] font-semibold leading-6">第 {{ step.num }} 步 · {{ step.description }}</h4>
              <span class="shrink-0 text-[11px]" :class="step.status === 'done' ? 'text-success' : step.status === 'error' ? 'text-error' : 'text-mute'">{{ step.statusLabel }}</span>
            </div>
            <MarkdownView v-if="step.summary" :source="step.summary" class="mt-1 !text-[13px] !leading-6" />
            <p v-if="step.calls" class="mt-1.5 text-[11px] text-faint">{{ step.calls }} 次工具调用 · 详情见执行过程</p>
          </div>
        </div>
      </li>
    </ol>
  </section>
</template>
