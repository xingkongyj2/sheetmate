<script setup lang="ts">
/** 设置：模型（默认 DeepSeek，填 API Key 就能用）、账号 */
import { CircleCheck, CircleAlert, KeyRound, LoaderCircle, Save, Zap } from '@lucide/vue'
import { computed, onMounted, ref, watch } from 'vue'

import { settingsApi } from '@/api'
import { errorText } from '@/api/http'
import type { LLMConfig } from '@/api/types'
import Mascot from '@/components/brand/Mascot.vue'
import { useToast } from '@/composables/useToast'
import { useAuthStore } from '@/stores/auth'

const toast = useToast()
const auth = useAuthStore()

const config = ref<LLMConfig | null>(null)
const provider = ref('deepseek')
const apiKey = ref('')
const baseUrl = ref('')
const model = ref('')
const thinkingEnabled = ref(false)
const saving = ref(false)
const testing = ref(false)
const testResult = ref<{ ok: boolean; text: string } | null>(null)
const displayName = ref(auth.user?.display_name ?? '')

const providerInfo = computed(() => config.value?.providers.find((p) => p.id === provider.value))

watch([provider, apiKey, baseUrl, model, thinkingEnabled], () => {
  testResult.value = null
})

onMounted(async () => {
  try {
    config.value = await settingsApi.getLLM()
    baseUrl.value = config.value.base_url
    model.value = config.value.model || config.value.default_model
    thinkingEnabled.value = config.value.thinking_enabled ?? false
    provider.value = config.value.providers.find((p) => p.base_url && config.value!.base_url.startsWith(p.base_url))?.id ?? 'custom'
  } catch (err) {
    toast.error(errorText(err))
  }
})

function pickProvider(id: string) {
  provider.value = id
  const info = config.value?.providers.find((p) => p.id === id)
  if (info?.base_url) baseUrl.value = info.base_url
  if (info?.models.length) model.value = id === 'deepseek' ? config.value!.default_model : info.models[0]
}

async function save() {
  saving.value = true
  try {
    config.value = await settingsApi.saveLLM({
      api_key: apiKey.value || undefined, base_url: baseUrl.value, model: model.value,
      thinking_enabled: thinkingEnabled.value,
    })
    model.value = config.value.model
    thinkingEnabled.value = config.value.thinking_enabled
    apiKey.value = ''
    toast.success('模型设置已保存，立即生效')
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    saving.value = false
  }
}

async function test() {
  testing.value = true
  testResult.value = null
  try {
    const result = await settingsApi.testLLM({
      api_key: apiKey.value || undefined, base_url: baseUrl.value, model: model.value,
      thinking_enabled: thinkingEnabled.value,
    })
    testResult.value = result.ok ? { ok: true, text: `连接成功（${result.model}）：${result.reply}` } : { ok: false, text: result.error?.message ?? '连接失败' }
  } catch (err) {
    testResult.value = { ok: false, text: errorText(err) }
  } finally {
    testing.value = false
  }
}

async function saveName() {
  try {
    await auth.updateProfile({ display_name: displayName.value })
    toast.success('已保存')
  } catch (err) {
    toast.error(errorText(err))
  }
}
</script>

<template>
  <div class="h-full overflow-y-auto">
    <div class="mx-auto max-w-[760px] px-8 py-10">
      <h1 class="text-[28px] font-semibold tracking-tight">设置</h1>

      <section class="card-soft mt-8 p-6">
        <div class="flex items-start gap-4">
          <Mascot :size="64" :mood="config?.configured ? 'happy' : 'thinking'" />
          <div class="flex-1">
            <h2 class="text-[16px] font-semibold">模型</h2>
            <p class="mt-1 text-sm text-mute">默认使用 {{ config?.default_model ?? 'DeepSeek' }}，可在下方切换模型，填 API Key 就能用；也支持其他 OpenAI 兼容的服务。Key 只保存在本机。</p>
            <div class="mt-2 text-sm">
              <span v-if="config?.configured" class="inline-flex items-center gap-1.5 text-success"><CircleCheck class="size-4" />已配置 {{ config.key_hint }}</span>
              <span v-else class="inline-flex items-center gap-1.5 text-[#b7770c]"><CircleAlert class="size-4" />还没有配置 API Key</span>
            </div>
          </div>
        </div>

        <div class="mt-6 space-y-4">
          <div>
            <div class="mb-2 text-xs font-medium text-mute">服务商</div>
            <div class="flex flex-wrap gap-2">
              <button
                v-for="p in config?.providers ?? []"
                :key="p.id"
                class="rounded-full border px-3.5 py-1.5 text-sm transition"
                :class="provider === p.id ? 'border-ink bg-ink text-white' : 'border-line hover:border-ink/30'"
                @click="pickProvider(p.id)"
              >
                {{ p.name }}
              </button>
            </div>
          </div>
          <label class="block">
            <span class="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-mute"><KeyRound class="size-3.5" />API Key</span>
            <input v-model="apiKey" type="password" class="field font-mono" :placeholder="config?.configured ? `已配置 ${config.key_hint}，留空表示不修改` : 'sk-...'" autocomplete="off" />
            <span v-if="provider === 'deepseek'" class="mt-1 block text-xs text-faint">在 platform.deepseek.com 创建 API Key</span>
          </label>
          <div class="grid gap-4 sm:grid-cols-2">
            <label class="block">
              <span class="mb-1.5 block text-xs font-medium text-mute">接口地址</span>
              <input v-model="baseUrl" class="field font-mono text-[13px]" placeholder="https://api.deepseek.com" />
            </label>
            <label class="block">
              <span class="mb-1.5 block text-xs font-medium text-mute">模型</span>
              <select v-if="provider === 'deepseek'" v-model="model" class="field font-mono text-[13px]" :disabled="!config">
                <option v-for="m in providerInfo?.models ?? []" :key="m" :value="m">{{ m }}{{ m === config?.default_model ? '（默认）' : '' }}</option>
              </select>
              <template v-else>
                <input v-model="model" class="field font-mono text-[13px]" list="model-options" :placeholder="providerInfo?.models[0] ?? '输入模型名称'" />
                <datalist id="model-options">
                  <option v-for="m in providerInfo?.models ?? []" :key="m" :value="m" />
                </datalist>
              </template>
            </label>
          </div>
          <label v-if="provider === 'deepseek'" class="flex cursor-pointer items-center justify-between gap-4 rounded-xl border border-line px-4 py-3">
            <span>
              <span class="block text-sm font-medium">开启思考</span>
              <span class="mt-1 block text-xs leading-relaxed text-mute">开启后适合复杂分析，但响应更慢；关闭可更快获得回答。</span>
            </span>
            <input v-model="thinkingEnabled" type="checkbox" role="switch" class="toggle toggle-sm shrink-0" :disabled="!config || saving" />
          </label>
          <div v-if="testResult" class="rounded-xl px-3 py-2 text-sm" :class="testResult.ok ? 'bg-success/10 text-success' : 'bg-error/5 text-error'">{{ testResult.text }}</div>
          <div class="flex justify-end gap-2">
            <button class="btn-line" :disabled="testing" @click="test"><LoaderCircle v-if="testing" class="size-4 animate-spin" /><Zap v-else class="size-4" />测试连接</button>
            <button class="btn-ink" :disabled="saving" @click="save"><LoaderCircle v-if="saving" class="size-4 animate-spin" /><Save v-else class="size-4" />保存</button>
          </div>
        </div>
      </section>

      <section class="card-soft mt-6 p-6">
        <h2 class="text-[16px] font-semibold">账号</h2>
        <div class="mt-4 flex gap-2">
          <input v-model="displayName" class="field" placeholder="昵称" />
          <button class="btn-line shrink-0" @click="saveName">保存</button>
        </div>
        <p class="mt-3 text-xs text-faint">
          账号：{{ auth.user?.account }} · {{ auth.user?.role === 'admin' ? '管理员（可以看到所有客户）' : '成员（只能看到自己负责的客户）' }}
          <template v-if="auth.user?.profile?.job"> · 岗位：{{ auth.user.profile.job }}</template>
        </p>
      </section>

      <section class="mt-6 rounded-2xl bg-paper p-6 text-xs leading-relaxed text-mute">
        <div class="mb-1 font-medium text-ink">关于 SheetMate</div>
        Agent 框架基于 TabClaw（ReAct 工具循环、计划、多表并行、记忆）；修改与重复执行基于 SheetAgent 的输入输出比对与 Recipe 结构；
        执行引擎是 SheetMate 的 DuckDB 引擎闭环（公式 → SQL → 校验 → 修复 → 写回）。所有数据保存在本机 backend/data 目录。
      </section>
    </div>
  </div>
</template>
