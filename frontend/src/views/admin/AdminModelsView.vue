<script setup lang="ts">
/** 管理后台大模型配置独立页：左侧服务商与密钥配置，右侧结构输出与思考协议。 */
import { computed, ref } from 'vue'

import { settingsApi } from '@/api'
import type { LLMConfig } from '@/api/types'
import LLMConfigPanel from '@/components/settings/LLMConfigPanel.vue'

const chatModelApi = { load: settingsApi.getLLM, save: settingsApi.saveLLM, test: settingsApi.testLLM }
const currentConfig = ref<LLMConfig | null>(null)

const PROVIDER_NAMES: Record<string, string> = { deepseek: 'DeepSeek', qwen: '千问', custom: '自定义' }

const statusLabel = computed(() => {
  const cfg = currentConfig.value
  if (!cfg) return ''
  if (!cfg.configured || !cfg.active_provider_id) return '未启用'
  const provider = PROVIDER_NAMES[cfg.active_provider_id] ?? cfg.active_provider_id
  return `${provider} · ${cfg.model}`
})
</script>

<template>
  <div class="h-full overflow-y-auto">
    <div class="mx-auto max-w-[1080px] px-5 py-6 sm:px-8 sm:py-8">
      <header class="flex flex-wrap items-center justify-between gap-3">
        <h1 class="text-2xl font-semibold tracking-tight text-ink">模型配置</h1>
        <span
          v-if="statusLabel"
          class="inline-flex items-center gap-1.5 rounded-full border border-line bg-white px-3 py-1 text-xs font-medium text-ink"
        >
          <span
            class="size-1.5 rounded-full"
            :class="currentConfig?.configured ? 'bg-success' : 'bg-warning'"
          />
          <span class="font-mono text-[12px]">{{ statusLabel }}</span>
        </span>
      </header>

      <div class="mt-6">
        <LLMConfigPanel
          title="对话模型"
          hide-title
          split
          :api="chatModelApi"
          @change="currentConfig = $event"
        />
      </div>
    </div>
  </div>
</template>
