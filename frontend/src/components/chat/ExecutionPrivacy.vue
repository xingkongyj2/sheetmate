<script setup lang="ts">
/** 详情面板「脱敏」：当前会话的脱敏情况（只读）。哪些文件、哪些列、怎么处理、每次发送前检查的结果，都写清楚。 */
import { ChevronRight, LoaderCircle, RefreshCw, ShieldAlert, ShieldCheck, ShieldOff } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { conversationApi } from '@/api'
import { errorText } from '@/api/http'
import type { ConversationDetail, PrivacyEventItem, PrivacyOverview } from '@/api/types'
import type { UiMessage } from '@/composables/chatModel'
import { formatCount } from '@/composables/privacyPresentation'

import PrivacyFiles from './PrivacyFiles.vue'
import PrivacySendsDialog from './PrivacySendsDialog.vue'

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
// 客户文件页里改了某个文件的列脱敏，这里要同步：回到这个页面时立即刷新，开着的时候也隔几秒刷新一次
const onVisible = () => { if (document.visibilityState === 'visible') void load() }
let idle = 0
onMounted(() => {
  void load(); schedule()
  document.addEventListener('visibilitychange', onVisible)
  window.addEventListener('focus', onVisible)
  idle = window.setInterval(onVisible, 8000)
})
onBeforeUnmount(() => {
  clearTimeout(timer); clearInterval(idle); version++
  document.removeEventListener('visibilitychange', onVisible)
  window.removeEventListener('focus', onVisible)
})

const failed = computed(() => overview.value?.files.some((file) => file.status === 'failed') ?? false)
// 模型脱敏这一块的一句话：被拦截时标红，否则写发了几次
const sendStatus = computed(() => {
  const totals = overview.value?.totals
  if (!totals) return null
  if (totals.blocked > 0) return { error: true, text: `${formatCount(totals.blocked)} 次发送被拦截，这些内容没有发给模型` }
  return { error: false, text: totals.requests ? `发给模型的 ${formatCount(totals.requests)} 次请求全部通过检查` : '还没有向模型发送请求' }
})
// 两个环节分开：上传的文件先脱敏；之后每次发给 AI 前，再把文件之外的内容（对话、工具结果、说明）查一遍，文件之外的敏感内容在这里补上
const fileNumbers = computed(() => {
  const totals = overview.value?.totals
  return totals ? [
    { label: '脱敏的列', value: totals.columns, warn: false },
    { label: '替换的值', value: totals.values, warn: false },
  ] : []
})
const sendNumbers = computed(() => {
  const totals = overview.value?.totals
  return totals ? [
    { label: '发送次数', value: totals.requests, warn: false },
    { label: '脱敏', value: totals.sent_values ?? 0, warn: false },
    { label: '还原', value: totals.restored, warn: false },
    { label: '拦截', value: totals.blocked, warn: totals.blocked > 0 },
  ] : []
})
const showSends = ref<false | 'sends' | 'restores'>(false)
const hasDetail = computed(() => (overview.value?.totals.sent_values ?? 0) > 0 || (overview.value?.totals.restored ?? 0) > 0)
// 处理记录里的「发送检查」「还原」可以点开，直接看对应的明细
function detailTab(event: PrivacyEventItem): 'sends' | 'restores' | null {
  return event.kind === '发送检查' ? 'sends' : event.kind === '还原' ? 'restores' : null
}

// 输入（含期望结果）和输出分开列；没有脱敏的文件后端已经去掉
const fileGroups = computed(() => {
  const files = overview.value?.files ?? []
  return [
    { key: 'input', label: '输入文件', files: files.filter((file) => file.kind !== 'output') },
    { key: 'output', label: '输出文件', files: files.filter((file) => file.kind === 'output') },
  ].filter((group) => group.files.length)
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
  <section class="grid gap-5" aria-label="脱敏">
    <p v-if="error" class="break-words rounded-xl bg-error/5 px-3 py-2 text-xs leading-5 text-error" role="alert">{{ error }}</p>

    <div v-if="!overview && loading" class="flex justify-center py-8" role="status" aria-label="加载中">
      <LoaderCircle class="size-5 animate-spin text-mute" />
    </div>

    <template v-else-if="overview">
      <section class="grid gap-2.5" aria-label="文件脱敏">
        <div class="flex items-center justify-between gap-2 px-0.5">
          <h4 class="text-[13px] font-medium leading-7 text-ink">文件脱敏</h4>
          <button type="button" class="icon-btn size-7" :disabled="loading" title="刷新" aria-label="刷新脱敏情况" @click="load">
            <RefreshCw class="size-3.5" :class="{ 'animate-spin': loading }" />
          </button>
        </div>
        <div v-if="overview.enabled === false" class="flex items-center gap-2 rounded-xl bg-paper px-3 py-2.5 text-[13px] leading-5 text-mute" data-testid="privacy-off">
          <ShieldOff class="size-4 shrink-0" aria-hidden="true" />脱敏已关闭，上传的文件不做分析
        </div>
        <template v-else>
          <p v-if="failed" class="flex items-start gap-2 rounded-xl bg-error/5 px-3 py-2.5 text-[13px] leading-5 text-error" role="alert">
            <ShieldAlert class="mt-0.5 size-4 shrink-0" aria-hidden="true" />有文件脱敏失败，相关处理已停止，没有数据发给模型
          </p>
          <dl class="grid grid-cols-2 gap-2">
            <div v-for="item in fileNumbers" :key="item.label" class="min-w-0 rounded-xl border border-line px-3 py-2.5">
              <dd class="text-xl font-medium tabular-nums tracking-tight">{{ formatCount(item.value) }}</dd>
              <dt class="mt-0.5 text-[11px] text-faint">{{ item.label }}</dt>
            </div>
          </dl>
          <p class="px-0.5 text-xs leading-5 text-faint" data-testid="privacy-mode">
            {{ overview.mode === 'rules' ? '未配置脱敏模型，仅使用内置规则' : `识别模型：${overview.model ?? '已配置'}` }}
          </p>
          <div v-for="group in fileGroups" :key="group.key" class="min-w-0">
            <div class="label-mini mb-2 mt-1 flex items-center gap-2">{{ group.label }}<span class="font-normal tabular-nums tracking-normal">{{ group.files.length }}</span></div>
            <PrivacyFiles :files="group.files" />
          </div>
        </template>
      </section>

      <section class="grid gap-2.5" aria-label="模型脱敏">
        <div class="flex items-center justify-between gap-2 px-0.5">
          <h4 class="text-[13px] font-medium leading-7 text-ink">模型脱敏</h4>
          <button v-if="hasDetail" type="button" class="flex shrink-0 items-center gap-0.5 text-xs leading-7 text-mute transition hover:text-ink" @click="showSends = (overview.totals.sent_values ?? 0) > 0 ? 'sends' : 'restores'">
            明细<ChevronRight class="size-3.5" />
          </button>
        </div>
        <div v-if="sendStatus" class="flex items-start gap-2 rounded-xl px-3 py-2.5 text-[13px] leading-5" :class="sendStatus.error ? 'bg-error/5 text-error' : 'bg-paper text-ink'" :role="sendStatus.error ? 'alert' : 'status'" data-testid="privacy-status">
          <ShieldAlert v-if="sendStatus.error" class="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <ShieldCheck v-else class="mt-0.5 size-4 shrink-0 text-[#318b76]" aria-hidden="true" />
          <span class="min-w-0 break-words">{{ sendStatus.text }}</span>
        </div>
        <dl class="grid grid-cols-4 gap-2">
          <div v-for="item in sendNumbers" :key="item.label" class="min-w-0 rounded-xl border border-line px-3 py-2.5">
            <dd class="text-xl font-medium tabular-nums tracking-tight" :class="item.warn ? 'text-error' : ''">{{ formatCount(item.value) }}</dd>
            <dt class="mt-0.5 text-[11px] text-faint">{{ item.label }}</dt>
          </div>
        </dl>
      </section>

      <section class="grid gap-2" aria-label="处理记录">
        <div class="label-mini flex items-center gap-2 px-0.5">处理记录<span class="font-normal tabular-nums tracking-normal">{{ overview.events.length }}</span></div>
        <p v-if="!overview.events.length" class="px-0.5 text-xs text-faint">暂无记录</p>
        <ol v-else class="divide-y divide-line rounded-xl border border-line">
          <li v-for="event in overview.events" :key="event.id">
            <component :is="detailTab(event) ? 'button' : 'div'" :type="detailTab(event) ? 'button' : undefined"
              class="group/event flex w-full items-start gap-2 px-3 py-2 text-left text-xs leading-6"
              :class="detailTab(event) ? 'transition hover:bg-paper/60' : ''"
              @click="detailTab(event) && (showSends = detailTab(event)!)">
              <time class="shrink-0 font-mono text-[11px] leading-6 tabular-nums text-faint" :datetime="event.time">{{ clock(event.time) }}</time>
              <span class="tag mt-0.5 shrink-0 !py-0 leading-5" :class="levelClass(event.level)">{{ event.kind }}</span>
              <span class="min-w-0 flex-1 break-words" :class="event.level === 'error' ? 'text-error' : 'text-mute'">{{ event.text }}</span>
              <ChevronRight v-if="detailTab(event)" class="mt-[5px] size-3.5 shrink-0 text-faint transition group-hover/event:text-ink" aria-hidden="true" />
            </component>
          </li>
        </ol>
      </section>
    </template>
    <PrivacySendsDialog v-if="showSends && detail" :conversation-id="detail.id" :tab="showSends" @close="showSends = false" />
  </section>
</template>
