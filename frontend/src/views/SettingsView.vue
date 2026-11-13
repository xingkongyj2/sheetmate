<script setup lang="ts">
/** 设置：多服务商模型配置 */
import { ArrowUpRight, Eye, EyeOff, LoaderCircle, Save, Zap } from '@lucide/vue'
import { computed, onMounted, reactive, ref } from 'vue'

import { settingsApi } from '@/api'
import { errorText } from '@/api/http'
import type { LLMAdvancedConfig, LLMConfig, LLMProviderConfig, LLMProviderId } from '@/api/types'
import { useToast } from '@/composables/useToast'
import ExecutionMetrics from '@/components/settings/ExecutionMetrics.vue'

const toast = useToast()

const config = ref<LLMConfig | null>(null)
const selectedProviderId = ref<LLMProviderId>('deepseek')
const loading = ref(true)
const loadError = ref('')
const drafts = reactive<Partial<Record<LLMProviderId, ProviderDraft>>>({})
const keyVisible = reactive<Record<LLMProviderId, boolean>>({ deepseek: false, qwen: false, custom: false })
const savingProvider = ref<LLMProviderId | null>(null)
const testingProvider = ref<LLMProviderId | null>(null)
const testResults = reactive<Partial<Record<LLMProviderId, { ok: boolean; text: string } | null>>>({})

interface ProviderDraft extends LLMAdvancedConfig {
  enabled: boolean
  api_key: string
  base_url: string
  model: string
  thinking_enabled: boolean
}

const PROVIDER_ORDER: LLMProviderId[] = ['deepseek', 'qwen', 'custom']
const PROVIDER_LABELS: Record<LLMProviderId, string> = { deepseek: 'DeepSeek', qwen: '千问', custom: '自定义' }
const PROVIDER_KEY_URLS: Partial<Record<LLMProviderId, string>> = {
  deepseek: 'https://platform.deepseek.com/api_keys',
  qwen: 'https://bailian.console.aliyun.com/cn-beijing/model/settings/api-key',
}
const orderedProviders = computed(() => [...(config.value?.providers ?? [])]
  .sort((left, right) => PROVIDER_ORDER.indexOf(left.id) - PROVIDER_ORDER.indexOf(right.id)))
const anyBusy = computed(() => loading.value || !!savingProvider.value || !!testingProvider.value)

function onTabKeydown(event: KeyboardEvent) {
  const currentIndex = PROVIDER_ORDER.indexOf(selectedProviderId.value)
  let index: number
  if (event.key === 'ArrowRight') index = (currentIndex + 1) % PROVIDER_ORDER.length
  else if (event.key === 'ArrowLeft') index = (currentIndex + PROVIDER_ORDER.length - 1) % PROVIDER_ORDER.length
  else if (event.key === 'Home') index = 0
  else if (event.key === 'End') index = PROVIDER_ORDER.length - 1
  else return
  event.preventDefault()
  selectedProviderId.value = PROVIDER_ORDER[index]!
  document.getElementById(`provider-tab-${selectedProviderId.value}`)?.focus()
}

function draftFrom(provider: LLMProviderConfig): ProviderDraft {
  return {
    enabled: provider.enabled,
    api_key: provider.api_key,
    base_url: provider.base_url,
    model: provider.model || '',
    thinking_enabled: provider.thinking_enabled,
    capabilities: { ...provider.capabilities },
  }
}

function applyConfig(next: LLMConfig, refreshedProvider?: LLMProviderId) {
  if (!config.value && next.active_provider_id) selectedProviderId.value = next.active_provider_id
  config.value = next
  for (const provider of next.providers) {
    if (!drafts[provider.id] || !refreshedProvider || provider.id === refreshedProvider) {
      drafts[provider.id] = draftFrom(provider)
    }
  }
}

async function loadConfig() {
  loading.value = true
  loadError.value = ''
  try {
    applyConfig(await settingsApi.getLLM())
  } catch (err) {
    loadError.value = errorText(err)
  } finally {
    loading.value = false
  }
}

onMounted(loadConfig)

function isThinkingSupported(provider: LLMProviderConfig) {
  const draft = drafts[provider.id]
  if (!draft || draft.capabilities.thinking_protocol === 'none') return false
  if (draft.capabilities.thinking_protocol !== 'auto') return true
  const url = draft.base_url.trim().replace(/\/+$/, '')
  if (draft.model === provider.model && url === provider.base_url.replace(/\/+$/, '') &&
    provider.capabilities.thinking_protocol === 'auto') return provider.thinking_supported
  if (['https://api.deepseek.com', 'https://api.deepseek.com/v1'].includes(url)) {
    const known = config.value?.providers.find((item) => item.id === 'deepseek')
    return [...(known?.thinking_models ?? []), ...(known?.thinking_model_aliases ?? [])].includes(draft.model)
  }
  const knownQwen = config.value?.providers.find((item) => item.id === 'qwen')
  return ['https://dashscope.aliyuncs.com/compatible-mode/v1',
    'https://dashscope-intl.aliyuncs.com/compatible-mode/v1',
    'https://dashscope-us.aliyuncs.com/compatible-mode/v1'].includes(url) &&
    (!!knownQwen?.thinking_models?.includes(draft.model) || /^(qwen-plus|qwen-flash)(-|$)/.test(draft.model))
}

function isComplete(provider: LLMProviderConfig) {
  const draft = drafts[provider.id]
  return !!draft?.api_key.trim() && !!draft.base_url.trim() && !!draft.model.trim()
}

function canSave(provider: LLMProviderConfig) {
  const draft = drafts[provider.id]
  return !!draft && (!draft.enabled || isComplete(provider))
}

function resetTestResult(id: LLMProviderId) {
  testResults[id] = null
}

function onModelChanged(provider: LLMProviderConfig) {
  resetTestResult(provider.id)
}

async function save(provider: LLMProviderConfig) {
  if (anyBusy.value || !canSave(provider)) return
  const draft = drafts[provider.id]
  if (!draft) return
  savingProvider.value = provider.id
  try {
    const next = await settingsApi.saveLLM({
      provider_id: provider.id,
      enabled: draft.enabled,
      api_key: draft.api_key,
      base_url: draft.base_url,
      model: draft.model,
      thinking_enabled: draft.thinking_enabled,
      capabilities: { ...draft.capabilities },
    })
    applyConfig(next, provider.id)
    resetTestResult(provider.id)
    toast.success('已保存')
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    savingProvider.value = null
  }
}

async function test(provider: LLMProviderConfig) {
  if (anyBusy.value || !isComplete(provider)) return
  const draft = drafts[provider.id]
  if (!draft) return
  testingProvider.value = provider.id
  testResults[provider.id] = null
  try {
    const result = await settingsApi.testLLM({
      provider_id: provider.id,
      enabled: draft.enabled,
      api_key: draft.api_key,
      base_url: draft.base_url,
      model: draft.model,
      thinking_enabled: draft.thinking_enabled,
      capabilities: { ...draft.capabilities },
    })
    testResults[provider.id] = result.ok
      ? { ok: true, text: '连接成功' }
      : { ok: false, text: result.error?.message ?? '连接失败' }
  } catch (err) {
    testResults[provider.id] = { ok: false, text: errorText(err) }
  } finally {
    testingProvider.value = null
  }
}

</script>

<template>
  <div class="h-full overflow-y-auto">
    <div class="mx-auto max-w-[760px] px-5 py-8 sm:px-8 sm:py-10">
      <h1 class="text-[28px] font-semibold tracking-tight">设置</h1>

      <section class="mt-8" aria-labelledby="providers-heading">
        <h2 id="providers-heading" class="mb-3 text-[16px] font-semibold">大模型提供商</h2>
        <div class="card-soft overflow-hidden">
          <div class="px-5 pt-5 sm:px-6 sm:pt-6">
            <div class="flex gap-1 rounded-xl bg-paper p-1" role="tablist" aria-label="大模型提供商" @keydown="onTabKeydown">
              <button
                v-for="id in PROVIDER_ORDER"
                :id="`provider-tab-${id}`"
                :key="id"
                type="button"
                role="tab"
                :aria-selected="selectedProviderId === id"
                :aria-controls="`provider-panel-${id}`"
                :tabindex="selectedProviderId === id ? 0 : -1"
                class="min-w-0 flex-1 rounded-lg px-2 py-2 text-sm transition"
                :class="selectedProviderId === id ? 'bg-white font-medium text-ink shadow-sm' : 'text-mute hover:text-ink'"
                @click="selectedProviderId = id"
              >{{ PROVIDER_LABELS[id] }}</button>
            </div>
          </div>

          <div v-if="loading" class="flex justify-center p-6" role="status" aria-label="加载中">
            <LoaderCircle class="size-5 animate-spin text-mute" />
          </div>
          <div v-else-if="loadError" class="flex flex-wrap items-center gap-2 p-5 text-sm text-error sm:p-6" role="alert">
            <span>{{ loadError }}</span>
            <button type="button" class="font-medium underline" @click="loadConfig">重试</button>
          </div>
          <div v-else>
            <article
              v-for="provider in orderedProviders"
              :id="`provider-panel-${provider.id}`"
              :key="provider.id"
              v-show="selectedProviderId === provider.id"
              role="tabpanel"
              :aria-labelledby="`provider-tab-${provider.id}`"
              tabindex="0"
              class="p-5 sm:p-6"
            >
              <div v-if="drafts[provider.id]" class="space-y-4">
                <div>
                  <div class="mb-1.5 flex items-center justify-between gap-3 text-xs">
                    <label :for="`${provider.id}-api-key`" class="font-medium text-mute">API Key</label>
                    <a
                      v-if="PROVIDER_KEY_URLS[provider.id]"
                      :href="PROVIDER_KEY_URLS[provider.id]"
                      target="_blank"
                      rel="noopener noreferrer"
                      :aria-label="`${PROVIDER_LABELS[provider.id]} 获取 API Key`"
                      class="inline-flex shrink-0 items-center gap-0.5 text-mute transition hover:text-ink"
                    >获取 API Key<ArrowUpRight class="size-3.5" /></a>
                  </div>
                  <div class="relative">
                    <input
                      :id="`${provider.id}-api-key`"
                      v-model="drafts[provider.id]!.api_key"
                      :type="keyVisible[provider.id] ? 'text' : 'password'"
                      class="field pr-11 font-mono"
                      autocomplete="off"
                      :disabled="savingProvider === provider.id || testingProvider === provider.id"
                      @input="resetTestResult(provider.id)"
                    />
                    <button
                      type="button"
                      class="absolute inset-y-0 right-0 flex w-11 items-center justify-center rounded-r-xl text-mute transition hover:text-ink disabled:opacity-50"
                      :title="keyVisible[provider.id] ? '隐藏 API Key' : '显示 API Key'"
                      :aria-label="keyVisible[provider.id] ? '隐藏 API Key' : '显示 API Key'"
                      :aria-pressed="keyVisible[provider.id]"
                      :disabled="savingProvider === provider.id || testingProvider === provider.id"
                      @click="keyVisible[provider.id] = !keyVisible[provider.id]"
                    >
                      <EyeOff v-if="keyVisible[provider.id]" class="size-4" />
                      <Eye v-else class="size-4" />
                    </button>
                  </div>
                </div>

                <div class="grid gap-4 sm:grid-cols-2">
                  <label class="block">
                    <span class="mb-1.5 block text-xs font-medium text-mute">API Base URL</span>
                    <input
                      v-model="drafts[provider.id]!.base_url"
                      class="field font-mono text-[13px]"
                      :disabled="savingProvider === provider.id || testingProvider === provider.id"
                      @input="resetTestResult(provider.id)"
                    />
                  </label>
                  <label class="block">
                    <span class="mb-1.5 block text-xs font-medium text-mute">模型</span>
                    <input
                      v-model="drafts[provider.id]!.model"
                      class="field font-mono text-[13px]"
                      autocomplete="off"
                      :disabled="savingProvider === provider.id || testingProvider === provider.id"
                      @input="onModelChanged(provider)"
                    />
                  </label>
                </div>

                <div class="flex items-center gap-2 border-t border-line pt-4 sm:gap-6">
                  <label class="flex shrink-0 cursor-pointer items-center gap-1.5 whitespace-nowrap text-xs font-medium sm:gap-2.5 sm:text-sm">
                    <span>启用</span>
                    <input
                      v-model="drafts[provider.id]!.enabled"
                      type="checkbox"
                      role="switch"
                      class="toggle toggle-xs shrink-0 sm:toggle-sm"
                      :disabled="savingProvider === provider.id || testingProvider === provider.id"
                    />
                  </label>
                  <label v-if="isThinkingSupported(provider)" class="flex shrink-0 cursor-pointer items-center gap-1.5 whitespace-nowrap text-xs font-medium sm:gap-2.5 sm:text-sm">
                    <span>思考</span>
                    <input
                      v-model="drafts[provider.id]!.thinking_enabled"
                      type="checkbox"
                      role="switch"
                      class="toggle toggle-xs shrink-0 sm:toggle-sm"
                      :disabled="savingProvider === provider.id || testingProvider === provider.id"
                      @change="resetTestResult(provider.id)"
                    />
                  </label>
                  <button type="button" class="btn-line ml-auto shrink-0 px-2 sm:px-4" :disabled="anyBusy || !isComplete(provider)" @click="test(provider)">
                    <LoaderCircle v-if="testingProvider === provider.id" class="size-4 animate-spin max-[360px]:hidden" /><Zap v-else class="size-4 max-[360px]:hidden" />测试
                  </button>
                </div>

                <div
                  v-if="testResults[provider.id]"
                  class="rounded-xl px-3 py-2 text-sm"
                  :class="testResults[provider.id]!.ok ? 'bg-success/10 text-success' : 'bg-error/5 text-error'"
                  role="status"
                >{{ testResults[provider.id]!.text }}</div>
              </div>
            </article>
          </div>
        </div>
      </section>

      <template v-if="!loading && !loadError">
        <template v-for="provider in orderedProviders" :key="provider.id">
          <div v-if="drafts[provider.id]" v-show="selectedProviderId === provider.id">
            <section class="mt-7" :aria-labelledby="`${provider.id}-protocol-heading`">
              <h2 :id="`${provider.id}-protocol-heading`" class="mb-3 text-[16px] font-semibold">结构协议</h2>
              <div class="card-soft space-y-4 p-5 sm:p-6">
                <p class="text-xs leading-relaxed text-mute">系统会修复常见格式问题，并自动恢复断流。超时、重试次数、输出 Token 上限、任务预算等运行参数都放在后端 .env 环境变量里，默认值很大，一般无需调整。</p>
                <label class="block">
                  <span class="mb-1.5 block text-xs font-medium text-mute">结构输出协议</span>
                  <select v-model="drafts[provider.id]!.capabilities.structured_output" class="field"
                    :disabled="anyBusy" @change="resetTestResult(provider.id)">
                    <option value="auto">自动兼容</option>
                    <option value="json_schema">优先 JSON Schema</option>
                    <option value="json_object">优先 JSON 模式</option>
                    <option value="prompt">仅提示词</option>
                  </select>
                </label>
                <label class="block">
                  <span class="mb-1.5 block text-xs font-medium text-mute">思考参数协议</span>
                  <select v-model="drafts[provider.id]!.capabilities.thinking_protocol" class="field"
                    :disabled="anyBusy" @change="resetTestResult(provider.id)">
                    <option value="auto">自动识别</option>
                    <option value="deepseek">DeepSeek</option>
                    <option value="qwen">千问</option>
                    <option value="none">不自动发送思考参数</option>
                  </select>
                  <span class="mt-1.5 block text-xs leading-relaxed text-mute">自动识别仅适用于已知官方模型或已有手工参数；中转服务和模型别名可按服务文档指定协议。辅助判断默认关闭思考。</span>
                </label>
              </div>
            </section>

            <div class="mt-7 flex flex-col items-center gap-3">
              <button type="button" class="btn-ink min-w-32" :disabled="anyBusy || !canSave(provider)" @click="save(provider)">
                <LoaderCircle v-if="savingProvider === provider.id" class="size-4 animate-spin" /><Save v-else class="size-4" />保存
              </button>
            </div>
          </div>
        </template>
      </template>
      <ExecutionMetrics />
    </div>
  </div>
</template>

<style scoped>
.toggle:checked {
  --input-color: #6265dc;
  background-color: var(--input-color);
}

.toggle:checked::before {
  background-color: #fff;
}

input[type='checkbox']:not(.toggle) {
  accent-color: #6265dc;
}
</style>
