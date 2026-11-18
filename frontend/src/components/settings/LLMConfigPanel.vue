<script setup lang="ts">
/**
 * 多服务商模型配置（服务商标签、Key / 地址 / 模型、启用与思考开关、测试连接、结构协议）。
 * 「大模型」和「脱敏模型」是两份完全同构、各自保存的配置，共用这一个组件，区别只在接口和标题。
 */
import { ArrowUpRight, Eye, EyeOff, LoaderCircle, Zap } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import { errorText } from '@/api/http'
import type { LLMAdvancedConfig, LLMConfig, LLMConfigUpdate, LLMProviderConfig, LLMProviderId } from '@/api/types'
import BaseSelect from '@/components/common/BaseSelect.vue'
import { useToast } from '@/composables/useToast'

interface LLMTestResult { ok: boolean; reply?: string; model?: string; error?: { message: string } }

const props = withDefaults(defineProps<{
  title: string
  /** 标题下面的一句说明 */
  description?: string
  /** 同一页面有多份配置时用来区分元素 id */
  idPrefix?: string
  /** 还没有启用任何服务商时默认选中的标签 */
  defaultProvider?: LLMProviderId
  /** 开放哪些服务商；只有一个时不显示标签条（脱敏模型只有一个配置项） */
  providers?: LLMProviderId[]
  /** 是否显示下方的「结构协议」设置 */
  showProtocol?: boolean
  /** 嵌在别的设置分组里：不再单独一个大标题，用卡片内的小标题 */
  embedded?: boolean
  api: {
    load: () => Promise<LLMConfig>
    save: (payload: LLMConfigUpdate) => Promise<LLMConfig>
    test: (payload: LLMConfigUpdate) => Promise<LLMTestResult>
  }
}>(), { description: '', idPrefix: '', defaultProvider: 'deepseek', providers: () => ['deepseek', 'qwen', 'custom'], showProtocol: true })

const toast = useToast()

const config = ref<LLMConfig | null>(null)
const selectedProviderId = ref<LLMProviderId>(props.defaultProvider)
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

const STRUCTURED_OPTIONS = [
  { value: 'auto', label: '自动兼容' },
  { value: 'json_schema', label: '优先 JSON Schema' },
  { value: 'json_object', label: '优先 JSON 模式' },
  { value: 'prompt', label: '仅提示词' },
] as const
const THINKING_OPTIONS = [
  { value: 'auto', label: '自动识别' },
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'qwen', label: '千问' },
  { value: 'none', label: '不自动发送思考参数' },
] as const

const PROVIDER_ORDER: LLMProviderId[] = ['deepseek', 'qwen', 'custom']
const PROVIDER_LABELS: Record<LLMProviderId, string> = { deepseek: 'DeepSeek', qwen: '千问', custom: '自定义' }
const PROVIDER_KEY_URLS: Partial<Record<LLMProviderId, string>> = {
  deepseek: 'https://platform.deepseek.com/api_keys',
  qwen: 'https://bailian.console.aliyun.com/cn-beijing/model/settings/api-key',
}
const tabIds = computed(() => PROVIDER_ORDER.filter((id) => props.providers.includes(id)))
const orderedProviders = computed(() => [...(config.value?.providers ?? [])]
  .filter((provider) => tabIds.value.includes(provider.id))
  .sort((left, right) => PROVIDER_ORDER.indexOf(left.id) - PROVIDER_ORDER.indexOf(right.id)))
const anyBusy = computed(() => loading.value || !!savingProvider.value || !!testingProvider.value)
const ns = computed(() => props.idPrefix)

function onTabKeydown(event: KeyboardEvent) {
  const ids = tabIds.value
  const currentIndex = Math.max(0, ids.indexOf(selectedProviderId.value))
  let index: number
  if (event.key === 'ArrowRight') index = (currentIndex + 1) % ids.length
  else if (event.key === 'ArrowLeft') index = (currentIndex + ids.length - 1) % ids.length
  else if (event.key === 'Home') index = 0
  else if (event.key === 'End') index = ids.length - 1
  else return
  event.preventDefault()
  selectedProviderId.value = ids[index]!
  document.getElementById(`${ns.value}provider-tab-${selectedProviderId.value}`)?.focus()
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
    applyConfig(await props.api.load())
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

// ---- 自动保存：改动停下 0.7 秒后保存（开关、下拉也一样），离开输入框或页面时马上保存
const AUTOSAVE_DELAY = 700
const timers = new Map<LLMProviderId, ReturnType<typeof setTimeout>>()
const failedAt = new Map<LLMProviderId, string>() // 保存失败时的内容：内容没再改就不重试

const snapshot = (id: LLMProviderId) => JSON.stringify(drafts[id])
const serverProvider = (id: LLMProviderId) => config.value?.providers.find((item) => item.id === id)
function isDirty(id: LLMProviderId) {
  const saved = serverProvider(id)
  return !!saved && !!drafts[id] && snapshot(id) !== JSON.stringify(draftFrom(saved))
}

function schedule(id: LLMProviderId, delay = AUTOSAVE_DELAY) {
  clearTimeout(timers.get(id))
  timers.delete(id)
  const provider = serverProvider(id)
  if (!provider || !isDirty(id) || failedAt.get(id) === snapshot(id) || !canSave(provider)) return
  timers.set(id, setTimeout(() => { timers.delete(id); void save(provider) }, delay))
}

/** 马上保存还在等待的改动（离开输入框、离开页面时） */
function flush(id: LLMProviderId) {
  if (!timers.has(id)) return
  clearTimeout(timers.get(id))
  timers.delete(id)
  const provider = serverProvider(id)
  if (provider) void save(provider)
}

watch(drafts, () => { for (const id of PROVIDER_ORDER) schedule(id) }, { deep: true })
onBeforeUnmount(() => { for (const id of [...timers.keys()]) flush(id) })

async function save(provider: LLMProviderConfig) {
  if (loading.value || !canSave(provider) || !isDirty(provider.id)) return
  if (savingProvider.value || testingProvider.value) { // 正在保存或测试：稍后再试
    schedule(provider.id, 300)
    return
  }
  const draft = drafts[provider.id]
  if (!draft) return
  const sent = snapshot(provider.id)
  savingProvider.value = provider.id
  try {
    const next = await props.api.save({
      provider_id: provider.id,
      enabled: draft.enabled,
      api_key: draft.api_key,
      base_url: draft.base_url,
      model: draft.model,
      thinking_enabled: draft.thinking_enabled,
      capabilities: { ...draft.capabilities },
    })
    failedAt.delete(provider.id)
    // 保存期间又改了的话，保留正在改的内容，只更新已保存的状态
    if (snapshot(provider.id) === sent) applyConfig(next, provider.id)
    else config.value = next
    resetTestResult(provider.id)
  } catch (err) {
    failedAt.set(provider.id, sent)
    toast.error(errorText(err))
  } finally {
    savingProvider.value = null
    schedule(provider.id)
  }
}

async function test(provider: LLMProviderConfig) {
  if (anyBusy.value || !isComplete(provider)) return
  const draft = drafts[provider.id]
  if (!draft) return
  testingProvider.value = provider.id
  testResults[provider.id] = null
  try {
    const result = await props.api.test({
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
  <section :class="embedded ? '' : 'mt-8'" :aria-labelledby="`${ns}providers-heading`">
    <component :is="embedded ? 'h3' : 'h2'" :id="`${ns}providers-heading`"
               :class="embedded ? 'px-5 pt-5 text-[15px] font-semibold sm:px-6' : 'mb-3 text-lg font-semibold tracking-tight'">{{ title }}</component>
    <p v-if="description" class="-mt-1.5 mb-3 text-[13px] leading-5 text-mute">{{ description }}</p>
    <div :class="embedded ? '' : 'card-soft overflow-hidden'">
      <div v-if="tabIds.length > 1" class="px-5 pt-5 sm:px-6 sm:pt-6">
        <div class="flex gap-1 rounded-xl bg-paper p-1" role="tablist" :aria-label="title" @keydown="onTabKeydown">
          <button
            v-for="id in tabIds"
            :id="`${ns}provider-tab-${id}`"
            :key="id"
            type="button"
            role="tab"
            :aria-selected="selectedProviderId === id"
            :aria-controls="`${ns}provider-panel-${id}`"
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
          :id="`${ns}provider-panel-${provider.id}`"
          :key="provider.id"
          v-show="selectedProviderId === provider.id"
          role="tabpanel"
          :aria-labelledby="`${ns}provider-tab-${provider.id}`"
          tabindex="0"
          class="p-5 sm:p-6"
        >
          <div v-if="drafts[provider.id]" class="space-y-4">
            <div>
              <div class="mb-1.5 flex items-center justify-between gap-3 text-xs">
                <label :for="`${ns}${provider.id}-api-key`" class="font-medium text-mute">API Key</label>
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
                  :id="`${ns}${provider.id}-api-key`"
                  v-model="drafts[provider.id]!.api_key"
                  :type="keyVisible[provider.id] ? 'text' : 'password'"
                  class="field pr-11 font-mono"
                  autocomplete="off"
                  :disabled="testingProvider === provider.id"
                  @input="resetTestResult(provider.id)"
                  @blur="flush(provider.id)"
                />
                <button
                  type="button"
                  class="absolute inset-y-0 right-0 flex w-11 items-center justify-center rounded-r-xl text-mute transition hover:text-ink disabled:opacity-50"
                  :title="keyVisible[provider.id] ? '隐藏 API Key' : '显示 API Key'"
                  :aria-label="keyVisible[provider.id] ? '隐藏 API Key' : '显示 API Key'"
                  :aria-pressed="keyVisible[provider.id]"
                  :disabled="testingProvider === provider.id"
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
                  :disabled="testingProvider === provider.id"
                  @input="resetTestResult(provider.id)"
                  @blur="flush(provider.id)"
                />
              </label>
              <label class="block">
                <span class="mb-1.5 block text-xs font-medium text-mute">模型</span>
                <input
                  v-model="drafts[provider.id]!.model"
                  class="field font-mono text-[13px]"
                  autocomplete="off"
                  :disabled="testingProvider === provider.id"
                  @input="onModelChanged(provider)"
                  @blur="flush(provider.id)"
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
                  :disabled="testingProvider === provider.id"
                />
              </label>
              <label v-if="isThinkingSupported(provider)" class="flex shrink-0 cursor-pointer items-center gap-1.5 whitespace-nowrap text-xs font-medium sm:gap-2.5 sm:text-sm">
                <span>思考</span>
                <input
                  v-model="drafts[provider.id]!.thinking_enabled"
                  type="checkbox"
                  role="switch"
                  class="toggle toggle-xs shrink-0 sm:toggle-sm"
                  :disabled="testingProvider === provider.id"
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

  <template v-if="!loading && !loadError && showProtocol">
    <template v-for="provider in orderedProviders" :key="provider.id">
      <div v-if="drafts[provider.id]" v-show="selectedProviderId === provider.id">
        <section class="mt-7" :aria-labelledby="`${ns}${provider.id}-protocol-heading`">
          <h2 :id="`${ns}${provider.id}-protocol-heading`" class="mb-3 text-lg font-semibold tracking-tight">结构协议</h2>
          <div class="card-soft space-y-4 p-5 sm:p-6">
            <div>
              <span class="mb-1.5 block text-xs font-medium text-mute">结构输出协议</span>
              <BaseSelect
                v-model="drafts[provider.id]!.capabilities.structured_output"
                :options="[...STRUCTURED_OPTIONS]"
                :disabled="!!testingProvider"
                aria-label="结构输出协议"
                @change="resetTestResult(provider.id)"
              />
            </div>
            <div>
              <span class="mb-1.5 block text-xs font-medium text-mute">思考参数协议</span>
              <BaseSelect
                v-model="drafts[provider.id]!.capabilities.thinking_protocol"
                :options="[...THINKING_OPTIONS]"
                :disabled="!!testingProvider"
                aria-label="思考参数协议"
                @change="resetTestResult(provider.id)"
              />
            </div>
          </div>
        </section>
      </div>
    </template>
  </template>
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
