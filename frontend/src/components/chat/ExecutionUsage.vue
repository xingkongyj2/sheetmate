<script setup lang="ts">
import { ArrowDownLeft, ArrowUpRight, RefreshCw } from '@lucide/vue'
import { computed } from 'vue'

import type { UiMessage } from '@/composables/chatModel'
import { conversationTokenUsage, messageTokenUsage } from '@/composables/tokenUsage'

const props = withDefaults(defineProps<{
  message: UiMessage | null
  messages: UiMessage[]
  refreshing?: boolean
}>(), { refreshing: false })
const emit = defineEmits<{ refresh: [] }>()
const current = computed(() => messageTokenUsage(props.message))
const cumulative = computed(() => conversationTokenUsage(props.messages))
const pending = computed(() => props.message?.streaming || props.message?.execution?.llm_usage_pending === true)
const inputShare = computed(() => current.value.total ? current.value.input / current.value.total * 100 : 0)
const number = new Intl.NumberFormat('en-US')
const format = (value: number, available: boolean) => available ? number.format(value) : '—'
</script>

<template>
  <section class="usage-view" aria-label="Token 消耗">
    <div class="usage-heading">
      <span>TOKEN 用量</span>
      <button type="button" class="icon-btn size-7" :disabled="refreshing" title="刷新消耗" aria-label="刷新消耗" @click="emit('refresh')">
        <RefreshCw class="size-3.5" :class="{ 'animate-spin': refreshing }" />
      </button>
    </div>

    <article class="usage-current" aria-label="本轮消耗">
      <header class="usage-card-heading">
        <h3>本轮消耗</h3>
        <span v-if="pending" class="usage-badge"><i class="usage-live" />统计中{{ current.estimated ? ' · 含估算' : '' }}</span>
        <span v-else-if="current.estimated" class="usage-badge">含估算</span>
        <span v-else-if="current.available && current.partial" class="usage-badge">部分记录</span>
      </header>
      <div class="usage-total" :class="{ 'usage-total-empty': !current.available }">
        {{ format(current.total, current.available) }}
      </div>
      <div class="usage-unit">tokens</div>

      <div class="usage-split" aria-hidden="true">
        <span v-if="current.total > 0" class="usage-split-input" :style="{ width: `${inputShare}%` }" />
        <span v-if="current.total > 0" class="usage-split-output" :style="{ width: `${100 - inputShare}%` }" />
      </div>
      <dl class="usage-breakdown">
        <div>
          <dt><ArrowDownLeft class="size-3.5" />输入</dt>
          <dd>{{ format(current.input, current.available) }}</dd>
          <span v-if="current.cached > 0" class="usage-included">含缓存 {{ number.format(current.cached) }}</span>
        </div>
        <div>
          <dt><ArrowUpRight class="size-3.5" />输出</dt>
          <dd>{{ format(current.output, current.available) }}</dd>
          <span v-if="current.reasoning > 0" class="usage-included">含推理 {{ number.format(current.reasoning) }}</span>
        </div>
      </dl>
      <footer class="usage-current-footer">
        <span>{{ current.available ? '模型调用' : pending ? '正在统计用量' : '暂无用量记录' }}</span>
        <span v-if="current.available"><b>{{ number.format(current.calls) }}</b> 次</span>
      </footer>
    </article>

    <article class="usage-cumulative" aria-label="当前对话累计消耗">
      <header class="usage-card-heading">
        <h3>对话累计</h3>
        <span v-if="cumulative.available && cumulative.partial" class="usage-badge usage-badge-light">{{ cumulative.estimated ? '含估算 · ' : '' }}部分记录</span>
        <span v-else-if="cumulative.estimated" class="usage-badge usage-badge-light">含估算</span>
      </header>
      <div class="usage-cumulative-total">
        <strong>{{ format(cumulative.total, cumulative.available) }}</strong>
        <span>tokens</span>
      </div>
      <dl class="usage-cumulative-breakdown">
        <div><dt><i class="usage-dot usage-dot-input" />输入</dt><dd>{{ format(cumulative.input, cumulative.available) }}</dd></div>
        <div><dt><i class="usage-dot usage-dot-output" />输出</dt><dd>{{ format(cumulative.output, cumulative.available) }}</dd></div>
      </dl>
      <footer class="usage-cumulative-footer">
        <span>{{ number.format(cumulative.turns) }} 轮对话</span>
        <span v-if="cumulative.available">{{ number.format(cumulative.calls) }} 次调用</span>
      </footer>
    </article>
  </section>
</template>

<style scoped>
.usage-view { display: grid; gap: 14px; font-variant-numeric: tabular-nums; }
.usage-heading { display: flex; height: 28px; align-items: center; justify-content: space-between; padding: 0 3px; color: var(--color-faint); font-size: 10px; font-weight: 600; letter-spacing: .12em; }
.usage-current { overflow: hidden; padding: 22px 22px 0; border-radius: 22px; background: #18181c; color: #fff; }
.usage-card-heading { display: flex; min-height: 22px; align-items: center; justify-content: space-between; gap: 8px; }
.usage-card-heading h3 { font-size: 13px; font-weight: 500; }
.usage-current .usage-card-heading h3 { color: #d5d5dc; }
.usage-badge { display: inline-flex; align-items: center; gap: 5px; padding: 3px 7px; border-radius: 6px; background: #ffffff10; color: #c7c7d0; font-size: 10px; white-space: nowrap; }
.usage-live { width: 5px; height: 5px; border-radius: 50%; background: #b8a6ed; }
.usage-total { margin-top: 26px; font-size: clamp(30px, 3.5vw, 46px); font-weight: 500; line-height: 1.1; letter-spacing: -.045em; overflow-wrap: anywhere; }
.usage-total-empty { color: #66666f; }
.usage-unit { margin-top: 5px; color: #8e8e9a; font-size: 12px; }
.usage-split { display: flex; height: 5px; gap: 3px; overflow: hidden; margin-top: 25px; border-radius: 3px; background: #ffffff10; }
.usage-split span { min-width: 0; border-radius: 3px; transition: width .25s ease; }
.usage-split-input { background: #d7d3e2; }
.usage-split-output { background: #a68bdc; }
.usage-breakdown { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); margin: 18px 0 22px; }
.usage-breakdown > div + div { padding-left: 20px; border-left: 1px solid #ffffff12; }
.usage-breakdown dt { display: flex; align-items: center; gap: 5px; color: #a6a6b1; font-size: 11px; }
.usage-breakdown > div + div dt { color: #bda9e5; }
.usage-breakdown dd { margin-top: 7px; font-size: 21px; font-weight: 500; letter-spacing: -.025em; overflow-wrap: anywhere; }
.usage-included { display: block; margin-top: 5px; color: #8e8e9a; font-size: 10px; }
.usage-current-footer { display: flex; align-items: center; justify-content: space-between; padding: 13px 0; border-top: 1px solid #ffffff12; color: #92929f; font-size: 11px; }
.usage-current-footer b { color: #dedee4; font-size: 13px; font-weight: 500; }
.usage-cumulative { padding: 21px 22px 0; border: 1px solid var(--color-line); border-radius: 20px; }
.usage-badge-light { background: var(--color-paper); color: var(--color-mute); }
.usage-cumulative-total { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; margin-top: 16px; }
.usage-cumulative-total strong { font-size: 32px; font-weight: 500; line-height: 1.3; letter-spacing: -.04em; overflow-wrap: anywhere; }
.usage-cumulative-total > span { color: var(--color-faint); font-size: 11px; }
.usage-cumulative-breakdown { display: grid; gap: 11px; margin: 21px 0; font-size: 12px; }
.usage-cumulative-breakdown > div { display: flex; justify-content: space-between; gap: 12px; }
.usage-cumulative-breakdown dt { display: flex; align-items: center; gap: 7px; color: var(--color-mute); }
.usage-cumulative-breakdown dd { font-weight: 500; }
.usage-dot { width: 6px; height: 6px; border-radius: 2px; }
.usage-dot-input { background: #4d465e; }
.usage-dot-output { background: #a68bdc; }
.usage-cumulative-footer { display: flex; justify-content: space-between; gap: 8px; padding: 13px 0; border-top: 1px solid var(--color-line); color: var(--color-faint); font-size: 11px; }
@media (prefers-reduced-motion: reduce) { .usage-split span { transition: none; } }
</style>
