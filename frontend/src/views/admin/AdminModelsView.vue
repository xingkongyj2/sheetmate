<script setup lang="ts">
/** 模型配置页：单列展示对话模型与底部结构协议（沿用原设置页 760px 宽度）。 */
import { settingsApi } from '@/api'
import LLMConfigPanel from '@/components/settings/LLMConfigPanel.vue'

const chatModelApi = {
  load: settingsApi.getLLM,
  save: settingsApi.saveLLM,
  test: settingsApi.testLLM,
}
</script>

<template>
  <div class="h-full overflow-y-auto">
    <div class="mx-auto max-w-[760px] px-5 py-6 pb-32 sm:px-8 sm:py-7">
      <header>
        <h1 class="text-xl font-semibold tracking-tight">模型配置</h1>
      </header>

      <div class="mt-6">
        <LLMConfigPanel
          title="对话模型"
          hide-title
          :api="chatModelApi"
        />
      </div>
    </div>
  </div>
</template>
