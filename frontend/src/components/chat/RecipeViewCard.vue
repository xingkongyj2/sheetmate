<script setup lang="ts">
import { ref } from 'vue'

import type { FlowStep, RecipeFlowData } from '@/api/types'
import BaseModal from '@/components/common/BaseModal.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import RecipeFlow from '@/components/recipe/RecipeFlow.vue'
import { userFacingSummary, userFacingText } from '@/composables/timelinePresentation'

const props = defineProps<{
  view: {
    name: string
    description: string
    status: string
    flow: RecipeFlowData
    deleted?: boolean
  }
}>()
const selected = ref<FlowStep | null>(null)

function openStep(id: number) {
  selected.value = props.view.flow.steps.find((step) => step.id === id) ?? null
}
</script>

<template>
  <article class="recipe-view min-w-0 space-y-3" :aria-label="view.name">
    <header class="flex flex-wrap items-center gap-x-3 gap-y-1 px-1">
      <h3 class="min-w-0 break-words text-[16px] font-semibold leading-relaxed">{{ view.name }}</h3>
      <span v-if="view.deleted" class="text-xs text-faint">流程已删除</span>
      <StatusPill v-else :status="view.status" />
    </header>
    <div class="min-w-0 rounded-[24px] bg-white p-4 shadow-[0_4px_24px_-8px_rgba(34,34,66,0.10),0_0_0_1px_rgba(34,34,66,0.035)] sm:p-5">
      <p v-if="view.description" class="mb-5 whitespace-pre-line break-words text-[13px] leading-[1.8] text-mute">{{ userFacingSummary(view.description) }}</p>
      <RecipeFlow :flow="view.flow" @select="openStep" />
    </div>

    <BaseModal v-if="selected" :title="selected.name" width="max-w-lg" @close="selected = null">
      <div class="space-y-4 text-sm">
        <p class="whitespace-pre-line leading-7 text-ink">{{ userFacingSummary(selected.description || selected.action, '按照流程设置处理输入数据。') }}</p>
        <p v-if="selected.inputs.length" class="break-words text-[13px] leading-6 text-mute">使用：{{ selected.inputs.map((name) => userFacingText(name)).filter(Boolean).join('、') }}</p>
        <p v-if="selected.output" class="break-words text-[13px] leading-6 text-mute">产出：{{ userFacingText(selected.output) }}</p>
      </div>
    </BaseModal>
  </article>
</template>
