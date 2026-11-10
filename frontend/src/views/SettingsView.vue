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
    timeout: provider.timeout,
    max_tokens: provider.max_tokens,
    capabilities: { ...provider.capabilities },
    runtime_policy: { ...provider.runtime_policy },
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
  return !!draft && (provider.id === 'qwen' || !!provider.thinking_models?.includes(draft.model))
}

function isComplete(provider: LLMProviderConfig) {
  const draft = drafts[provider.id]
  return !!draft?.api_key.trim() && !!draft.base_url.trim() && !!draft.model.trim() && !validationMessage(provider)
}

function canSave(provider: LLMProviderConfig) {
  const draft = drafts[provider.id]
  return !!draft && !validationMessage(provider) && (!draft.enabled || isComplete(provider))
}

function validationMessage(provider: LLMProviderConfig): string {
  const draft = drafts[provider.id]
  if (!draft) return ''
  const numberInRange = (value: number, min: number, max: number, integer = false) =>
    typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max && (!integer || Number.isInteger(value))
  if (!numberInRange(draft.timeout, 5, 900)) return '请求超时需为 5–900 秒。'
  if (!numberInRange(draft.max_tokens, 1, 131072, true)) return '输出 Token 上限需为 1–131072 的整数。'
  if (!numberInRange(draft.runtime_policy.structured_retries, 0, 3, true)) return '结构输出纠错次数需为 0–3 的整数。'
  if (!numberInRange(draft.runtime_policy.tool_protocol_retries, 0, 5, true)) return '工具调用纠错次数需为 0–5 的整数。'
  if (!numberInRange(draft.runtime_policy.max_model_calls, 0, 1000, true)) return '每任务模型调用上限需为 0–1000 的整数。'
  if (!numberInRange(draft.runtime_policy.max_prompt_tokens, 0, 10000000, true) ||
      !numberInRange(draft.runtime_policy.max_total_tokens, 0, 10000000, true)) return '任务 Token 预算需为 0–10000000 的整数。'
  if (!numberInRange(draft.runtime_policy.max_task_seconds, 0, 7200)) return '任务模型调用时限需为 0–7200 秒。'
  if (!numberInRange(draft.runtime_policy.max_input_chars, 0, 2000000, true)) return '单次输入字符上限需为 0–2000000 的整数。'
  return ''
}

function resetTestResult(id: LLMProviderId) {
  testResults[id] = null
}

function onModelChanged(provider: LLMProviderConfig) {
  const draft = drafts[provider.id]
  if (draft && !isThinkingSupported(provider)) draft.thinking_enabled = false
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
      thinking_enabled: isThinkingSupported(provider) && draft.thinking_enabled,
      timeout: draft.timeout,
      max_tokens: draft.max_tokens,
      capabilities: { ...draft.capabilities },
      runtime_policy: { ...draft.runtime_policy },
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
      thinking_enabled: isThinkingSupported(provider) && draft.thinking_enabled,
      timeout: draft.timeout,
      max_tokens: draft.max_tokens,
      capabilities: { ...draft.capabilities },
      runtime_policy: { ...draft.runtime_policy },
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
                <div class="grid gap-4 sm:grid-cols-2">
                  <label class="block">
                    <span class="mb-1.5 block text-xs font-medium text-mute">请求超时（秒）</span>
                    <input v-model.number="drafts[provider.id]!.timeout" type="number" min="5" max="900" step="1"
                      class="field" :disabled="anyBusy" @input="resetTestResult(provider.id)" />
                  </label>
                  <label class="block">
                    <span class="mb-1.5 block text-xs font-medium text-mute">输出 Token 上限</span>
                    <input v-model.number="drafts[provider.id]!.max_tokens" type="number" min="1" max="131072" step="1"
                      class="field" :disabled="anyBusy" @input="resetTestResult(provider.id)" />
                  </label>
                  <label class="block">
                    <span class="mb-1.5 block text-xs font-medium text-mute">结构输出纠错次数</span>
                    <input v-model.number="drafts[provider.id]!.runtime_policy.structured_retries" type="number" min="0" max="3" step="1"
                      class="field" :disabled="anyBusy" @input="resetTestResult(provider.id)" />
                  </label>
                  <label class="block">
                    <span class="mb-1.5 block text-xs font-medium text-mute">工具调用纠错次数</span>
                    <input v-model.number="drafts[provider.id]!.runtime_policy.tool_protocol_retries" type="number" min="0" max="5" step="1"
                      class="field" :disabled="anyBusy" @input="resetTestResult(provider.id)" />
                  </label>
                </div>
              </div>
            </section>

            <section class="mt-7" :aria-labelledby="`${provider.id}-budget-heading`">
              <h2 :id="`${provider.id}-budget-heading`" class="mb-3 text-[16px] font-semibold">任务预算</h2>
              <div class="card-soft space-y-4 p-5 sm:p-6">
                <label class="flex cursor-pointer items-center justify-between gap-4 border-b border-line pb-4">
                  <span class="text-sm font-medium">开启任务预算</span>
                  <input v-model="drafts[provider.id]!.runtime_policy.budget_enabled" type="checkbox"
                    role="switch" class="toggle toggle-sm shrink-0" :disabled="anyBusy"
                    @change="resetTestResult(provider.id)" />
                </label>
                <div class="grid gap-4 sm:grid-cols-2">
                  <label class="block">
                    <span class="mb-1.5 block text-xs font-medium text-mute">每任务模型调用上限</span>
                    <input v-model.number="drafts[provider.id]!.runtime_policy.max_model_calls" type="number" min="0" max="1000" step="1" class="field" :disabled="anyBusy || !drafts[provider.id]!.runtime_policy.budget_enabled" />
                  </label>
                  <label class="block">
                    <span class="mb-1.5 block text-xs font-medium text-mute">每任务累计输入 Token 预算</span>
                    <input v-model.number="drafts[provider.id]!.runtime_policy.max_prompt_tokens" type="number" min="0" max="10000000" step="1" class="field" :disabled="anyBusy || !drafts[provider.id]!.runtime_policy.budget_enabled" />
                  </label>
                  <label class="block">
                    <span class="mb-1.5 block text-xs font-medium text-mute">每任务累计总 Token 预算</span>
                    <input v-model.number="drafts[provider.id]!.runtime_policy.max_total_tokens" type="number" min="0" max="10000000" step="1" class="field" :disabled="anyBusy || !drafts[provider.id]!.runtime_policy.budget_enabled" />
                  </label>
                  <label class="block">
                    <span class="mb-1.5 block text-xs font-medium text-mute">每任务模型调用时限（秒）</span>
                    <input v-model.number="drafts[provider.id]!.runtime_policy.max_task_seconds" type="number" min="0" max="7200" class="field" :disabled="anyBusy || !drafts[provider.id]!.runtime_policy.budget_enabled" />
                  </label>
                  <label class="block">
                    <span class="mb-1.5 block text-xs font-medium text-mute">单次输入字符上限（含工具定义）</span>
                    <input v-model.number="drafts[provider.id]!.runtime_policy.max_input_chars" type="number" min="0" max="2000000" step="1" class="field" :disabled="anyBusy || !drafts[provider.id]!.runtime_policy.budget_enabled" />
                  </label>
                  <label class="flex items-center gap-2 text-xs text-mute">
                    <input v-model="drafts[provider.id]!.runtime_policy.auxiliary_thinking_enabled" type="checkbox" :disabled="anyBusy" />辅助判断也启用深度思考
                  </label>
                </div>
              </div>
            </section>

            <div class="mt-7 flex flex-col items-center gap-3">
              <p v-if="validationMessage(provider)" class="text-center text-xs text-error" role="alert">{{ validationMessage(provider) }}</p>
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
