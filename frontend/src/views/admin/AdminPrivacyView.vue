<script setup lang="ts">
/** 管理后台数据脱敏页：左侧总开关与规则管理，右侧脱敏专用识别模型。 */
import { computed, ref } from 'vue'

import { settingsApi } from '@/api'
import type { LLMConfig } from '@/api/types'
import LLMConfigPanel from '@/components/settings/LLMConfigPanel.vue'
import PrivacyCard from '@/components/settings/PrivacyCard.vue'

const privacyModelApi = {
  load: settingsApi.getPrivacyLLM,
  save: settingsApi.savePrivacyLLM,
  test: settingsApi.testPrivacyLLM,
}
const modelConfig = ref<LLMConfig | null>(null)

const modelBadge = computed(() => {
  if (!modelConfig.value) return ''
  return modelConfig.value.configured ? modelConfig.value.model : '未启用'
})
</script>

<template>
  <div class="h-full overflow-y-auto">
    <div class="mx-auto max-w-[1080px] px-5 py-6 sm:px-8 sm:py-8">
      <header class="flex flex-wrap items-center justify-between gap-3">
        <h1 class="text-2xl font-semibold tracking-tight text-ink">数据脱敏</h1>
        <span
          v-if="modelBadge"
          class="inline-flex items-center gap-1.5 rounded-full border border-line bg-white px-3 py-1 text-xs text-mute"
        >
          <span
            class="size-1.5 rounded-full"
            :class="modelConfig?.configured ? 'bg-success' : 'bg-faint'"
          />
          <span>脱敏模型 · {{ modelBadge }}</span>
        </span>
      </header>

      <div class="mt-6 grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_400px]">
        <section aria-label="脱敏规则">
          <h2 class="mb-3 text-sm font-semibold text-ink">规则开关</h2>
          <PrivacyCard />
        </section>

        <section aria-label="脱敏专用模型">
          <h2 class="mb-3 text-sm font-semibold text-ink">脱敏模型</h2>
          <LLMConfigPanel
            title="脱敏模型"
            hide-title
            id-prefix="privacy-"
            default-provider="custom"
            :providers="['custom']"
            :show-protocol="false"
            :api="privacyModelApi"
            @change="modelConfig = $event"
          />
        </section>
      </div>
    </div>
  </div>
</template>
