<script setup lang="ts">
/** 数据脱敏页：单列展示，上方为脱敏模型配置，下方为脱敏开关与规则管理（760px 宽度）。 */
import { settingsApi } from '@/api'
import LLMConfigPanel from '@/components/settings/LLMConfigPanel.vue'
import PrivacyCard from '@/components/settings/PrivacyCard.vue'

const privacyModelApi = {
  load: settingsApi.getPrivacyLLM,
  save: settingsApi.savePrivacyLLM,
  test: settingsApi.testPrivacyLLM,
}
</script>

<template>
  <div class="h-full overflow-y-auto">
    <div class="mx-auto max-w-[760px] px-5 py-6 pb-32 sm:px-8 sm:py-7">
      <section aria-labelledby="privacy-model-heading">
        <h1 id="privacy-model-heading" class="text-xl font-semibold tracking-tight">脱敏模型</h1>
        <div class="mt-6">
          <LLMConfigPanel
            title="脱敏模型"
            hide-title
            id-prefix="privacy-"
            default-provider="custom"
            :providers="['custom']"
            :show-protocol="false"
            :api="privacyModelApi"
          />
        </div>
      </section>

      <section class="mt-7" aria-labelledby="privacy-rules-heading">
        <h2 id="privacy-rules-heading" class="mb-3 text-lg font-semibold tracking-tight">脱敏规则</h2>
        <PrivacyCard />
      </section>
    </div>
  </div>
</template>
