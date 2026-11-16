<script setup lang="ts">
/** 详情面板「脱敏」：当前会话的脱敏情况（只读）。哪些文件、哪些列、怎么处理、每次发送前检查的结果，都写清楚。 */
import { LoaderCircle, RefreshCw, ShieldAlert, ShieldCheck } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { conversationApi } from '@/api'
import { errorText } from '@/api/http'
import type { ConversationDetail, PrivacyEventItem, PrivacyOverview } from '@/api/types'
import type { UiMessage } from '@/composables/chatModel'
import { formatCount } from '@/composables/privacyPresentation'

import PrivacyFiles from './PrivacyFiles.vue'

const props = defineProps<{ detail: ConversationDetail | null; message: UiMessage | null }>()

const overview = ref<PrivacyOverview | null>(null)
const loading = ref(false)
const error = ref('')
let version = 0
let timer = 0

async function load() {
  const id = props.detail?.id
  if (!id) return
  const current = ++version
  loading.value = true
  try {
    const data = await conversationApi.privacy(id)
    if (current !== version) return
    overview.value = data
    error.value = ''
  } catch (err) {
    if (current === version) error.value = errorText(err)
  } finally {
    if (current === version) loading.value = false
  }
}

// 本轮对话进行中时，每隔几秒刷新一次；结束、有新文件、脱敏结果到达时立即刷新。
const streaming = computed(() => props.message?.streaming === true)
function schedule() {
  clearTimeout(timer)
  if (!streaming.value) return
  timer = window.setTimeout(async () => { await load(); schedule() }, 2500)
}
const fileKey = computed(() => (props.detail?.files ?? []).map((file) => file.file_id).join(','))
watch(() => props.detail?.id, () => { overview.value = null; void load() })
watch(streaming, () => { void load(); schedule() })
watch([fileKey, () => props.message?.privacy], () => { void load() })
onMounted(() => { void load(); schedule() })
onBeforeUnmount(() => { clearTimeout(timer); version++ })

const empty = computed(() => !!overview.value && !overview.value.files.length && !overview.value.events.length)
const failed = computed(() => overview.value?.files.some((file) => file.status === 'failed') ?? false)
const status = computed(() => {
  const data = overview.value
  if (!data) return null
  const { blocked, requests } = data.totals
  if (blocked > 0) return { error: true, text: `有 ${formatCount(blocked)} 次发送被拦截，这些内容没有发给 AI` }
  if (failed.value) return { error: true, text: '有文件脱敏失败，相关处理已停止，没有数据发给 AI' }
  const sent = requests ? `发送给 AI 的 ${formatCount(requests)} 次请求全部通过检查` : '还没有向 AI 发送请求'
  return { error: false, text: `本会话已脱敏 ${formatCount(data.files.length)} 个文件，${sent}` }
})
const numbers = computed(() => {
  const totals = overview.value?.totals
  return totals ? [
    { label: '保护的列', value: totals.columns, warn: false },
    { label: '替换的值', value: totals.values, warn: false },
    { label: '发送检查', value: totals.requests, warn: false },
    { label: '拦截', value: totals.blocked, warn: totals.blocked > 0 },
  ] : []
})

function clock(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleTimeString('zh-CN', { hour12: false })
}
function levelClass(level: PrivacyEventItem['level']): string {
  return level === 'error' ? '!bg-error/10 !text-error' : level === 'warn' ? '!bg-[#fff1d2] !text-[#9a6b00]' : ''
}
</script>

<template>
  <section class="grid gap-4" aria-label="脱敏">
    <div class="flex h-7 items-center justify-between px-0.5">
      <span class="label-mini">脱敏</span>
      <button type="button" class="icon-btn size-7" :disabled="loading" title="刷新" aria-label="刷新脱敏情况" @click="load">
        <RefreshCw class="size-3.5" :class="{ 'animate-spin': loading }" />
      </button>
    </div>

    <p v-if="error" class="break-words rounded-xl bg-error/5 px-3 py-2 text-xs leading-5 text-error" role="alert">{{ error }}</p>

    <div v-if="!overview && loading" class="flex justify-center py-8" role="status" aria-label="加载中">
      <LoaderCircle class="size-5 animate-spin text-mute" />
    </div>

    <div v-else-if="empty" class="flex flex-col items-center justify-center gap-3 py-8 text-center text-sm text-faint">
      <span class="text-2xl leading-none" aria-hidden="true">🔒</span>
      <p>还没有文件需要脱敏</p>
    </div>

    <template v-else-if="overview && status">
      <div class="flex items-start gap-2 rounded-xl px-3 py-2.5 text-[13px] leading-5" :class="status.error ? 'bg-error/5 text-error' : 'bg-paper text-ink'" :role="status.error ? 'alert' : 'status'" data-testid="privacy-status">
        <ShieldAlert v-if="status.error" class="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        <ShieldCheck v-else class="mt-0.5 size-4 shrink-0 text-[#318b76]" aria-hidden="true" />
        <span class="min-w-0 break-words">{{ status.text }}</span>
      </div>
      <p class="-mt-2 px-0.5 text-xs leading-5 text-faint" data-testid="privacy-mode">
        {{ overview.mode === 'rules' ? '未配置脱敏模型，仅使用内置规则' : `脱敏模型：${overview.model ?? '已配置'}` }}
      </p>

      <dl class="grid grid-cols-2 gap-2">
        <div v-for="item in numbers" :key="item.label" class="min-w-0 rounded-xl border border-line px-3 py-2.5">
          <dd class="text-xl font-medium tabular-nums tracking-tight" :class="item.warn ? 'text-error' : ''">{{ formatCount(item.value) }}</dd>
          <dt class="mt-0.5 text-[11px] text-faint">{{ item.label }}</dt>
        </div>
      </dl>

      <div v-if="overview.files.length" class="min-w-0">
        <div class="label-mini mb-2 flex items-center gap-2">文件<span class="font-normal tabular-nums tracking-normal">{{ overview.files.length }}</span></div>
        <PrivacyFiles :files="overview.files" open-first />
      </div>

      <div>
        <div class="label-mini mb-2 flex items-center gap-2">处理记录<span class="font-normal tabular-nums tracking-normal">{{ overview.events.length }}</span></div>
        <p v-if="!overview.events.length" class="px-0.5 text-xs text-faint">暂无记录</p>
        <ol v-else class="divide-y divide-line rounded-xl border border-line" aria-label="处理记录">
          <li v-for="event in overview.events" :key="event.id" class="flex items-start gap-2 px-3 py-2 font-mono text-[11px] leading-5">
            <time class="shrink-0 tabular-nums text-faint" :datetime="event.time">{{ clock(event.time) }}</time>
            <span class="tag shrink-0 font-sans" :class="levelClass(event.level)">{{ event.kind }}</span>
            <span class="min-w-0 flex-1 break-words" :class="event.level === 'error' ? 'text-error' : 'text-mute'">{{ event.text }}</span>
          </li>
        </ol>
      </div>

      <p class="px-0.5 text-[11px] leading-5 text-faint">完整日志见服务器 数据目录/log/privacy.log</p>
    </template>
  </section>
</template>
