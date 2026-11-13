<script setup lang="ts">
/**
 * 统计：和「设置」同级的页面。先给 4 个关键数字，再用图看趋势（每日任务、每日 Token），
 * 最后按排行看任务状态、失败原因、工具和模型调用阶段。
 */
import { ChartColumn, LoaderCircle, RefreshCw } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'

import { errorText } from '@/api/http'
import { getExecutionMetrics } from '@/api/observability'
import type { ExecutionMetrics } from '@/api/observability'
import RankBars from '@/components/stats/RankBars.vue'
import type { RankRow } from '@/components/stats/RankBars.vue'
import StackedBarChart from '@/components/stats/StackedBarChart.vue'
import ToolStats from '@/components/stats/ToolStats.vue'
import { compact, duration, integer, label, percent, shortDate } from '@/components/stats/format'

const RANGES = [7, 30, 90] as const
const days = ref<number>(30)
const metrics = ref<ExecutionMetrics | null>(null)
const loading = ref(false)
const error = ref('')
let requestVersion = 0

async function load() {
  const version = ++requestVersion
  loading.value = true
  error.value = ''
  try {
    const result = await getExecutionMetrics(days.value)
    if (version === requestVersion) metrics.value = result
  } catch (err) {
    if (version === requestVersion) { metrics.value = null; error.value = errorText(err) }
  } finally {
    if (version === requestVersion) loading.value = false
  }
}

function selectRange(value: number) {
  if (value === days.value) return
  days.value = value
  load()
}

onMounted(load)

// ---- 颜色：含义固定，全页一致 ----
const DONE = 'var(--chart-done)'
const FAIL = 'var(--chart-fail)'
const NEUTRAL = 'var(--chart-neutral)'
const INPUT = 'var(--chart-input)'
const OUTPUT = 'var(--chart-output)'

const range = computed(() => metrics.value ? `${metrics.value.from.slice(0, 10)} 至 ${metrics.value.to.slice(0, 10)}` : '')

const taskSeries = [
  { key: 'completed', label: '完成', color: DONE },
  { key: 'failed', label: '失败或未完成', color: FAIL },
]
const tokenSeries = [
  { key: 'prompt', label: '输入', color: INPUT },
  { key: 'completion', label: '输出', color: OUTPUT },
]
const taskColumns = computed(() => (metrics.value?.daily ?? []).map((day) => ({
  key: day.date, label: shortDate(day.date), values: { completed: day.completed, failed: day.failed },
})))
const tokenColumns = computed(() => (metrics.value?.daily ?? []).map((day) => ({
  key: day.date, label: shortDate(day.date), values: { prompt: day.prompt_tokens, completion: day.completion_tokens },
})))

const statusRows = computed<RankRow[]>(() => Object.entries(metrics.value?.statuses ?? {})
  .filter(([, count]) => count > 0)
  .map(([status, count]) => ({
    key: status,
    label: label(status),
    segments: [{
      value: count, label: label(status),
      color: status === 'completed' ? DONE : status === 'failed' || status === 'incomplete' ? FAIL : NEUTRAL,
    }],
  })))

const failureRows = computed<RankRow[]>(() => (metrics.value?.failures ?? []).map((item) => ({
  key: item.code, label: label(item.code), segments: [{ value: item.tasks, label: '任务', color: FAIL }],
})))

const stageRows = computed<RankRow[]>(() => (metrics.value?.stages ?? []).map((stage) => ({
  key: stage.stage,
  label: label(stage.stage),
  note: `${integer(stage.model_calls)} 次调用`,
  segments: [
    { value: stage.prompt_tokens, label: '输入', color: INPUT },
    { value: stage.completion_tokens, label: '输出', color: OUTPUT },
  ],
})))

const cacheKnown = computed(() => !!metrics.value &&
  metrics.value.usage.cached_tokens_missing_calls === 0 && metrics.value.usage.cache_miss_tokens_missing_calls === 0)
const usageIncomplete = computed(() => !!metrics.value &&
  (metrics.value.usage.usage_missing_calls > 0 || metrics.value.usage.missing_tasks > 0 || metrics.value.usage.pending_tasks > 0))
</script>

<template>
  <div class="h-full overflow-y-auto">
    <div class="mx-auto max-w-[960px] px-5 py-8 sm:px-8 sm:py-10">
      <!-- 标题与时间范围 -->
      <header class="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div>
          <h1 class="text-[28px] font-semibold tracking-tight">统计</h1>
          <p class="mt-1 text-sm text-mute">
            近 {{ metrics?.days ?? days }} 天的任务与模型使用情况<span v-if="range" class="whitespace-nowrap text-faint"> · {{ range }}</span>
          </p>
        </div>
        <div class="flex items-center gap-2">
          <div class="flex gap-0.5 rounded-xl bg-paper p-1 text-sm" role="group" aria-label="统计时间范围">
            <button
              v-for="value in RANGES"
              :key="value"
              type="button"
              class="rounded-lg px-3 py-1.5 transition"
              :class="days === value ? 'bg-white font-medium text-ink shadow-sm' : 'text-mute hover:text-ink'"
              :aria-pressed="days === value"
              @click="selectRange(value)"
            >近 {{ value }} 天</button>
          </div>
          <button type="button" class="icon-btn !size-9 border border-line" title="刷新统计" aria-label="刷新统计" :disabled="loading" @click="load">
            <RefreshCw class="size-4" :class="loading ? 'animate-spin' : ''" />
          </button>
        </div>
      </header>

      <div v-if="error" class="card-soft mt-8 flex flex-wrap items-center gap-2 p-5 text-sm text-error" role="alert">
        <span>统计加载失败：{{ error }}</span>
        <button type="button" class="font-medium underline" @click="load">重试</button>
      </div>

      <div v-else-if="!metrics" class="mt-24 flex justify-center" role="status" aria-label="正在加载统计">
        <LoaderCircle class="size-6 animate-spin text-mute" />
      </div>

      <div v-else-if="metrics.tasks === 0" class="card-soft mt-8 flex flex-col items-center gap-3 px-6 py-16 text-center">
        <span class="flex size-12 items-center justify-center rounded-2xl bg-paper text-mute"><ChartColumn class="size-6" /></span>
        <p class="font-medium">这段时间还没有任务记录</p>
        <p class="max-w-sm text-sm text-mute">开始一次对话处理表格后，这里会显示任务数、成功率、耗时和 Token 用量。</p>
      </div>

      <div v-else class="mt-8 space-y-5 transition-opacity" :class="loading ? 'opacity-60' : ''" :aria-busy="loading">
        <p v-if="metrics.coverage.truncated" class="rounded-xl bg-warning/10 px-4 py-2.5 text-xs text-ink">
          范围内任务较多，仅统计最近 {{ integer(metrics.coverage.limit) }} 个任务。
        </p>

        <!-- 4 个关键数字 -->
        <section class="card-soft p-5 sm:p-6" aria-label="关键指标">
          <dl class="grid grid-cols-2 gap-x-6 gap-y-7 lg:grid-cols-4 lg:gap-x-0 lg:divide-x lg:divide-line">
            <div class="lg:pr-6">
              <dt class="text-xs text-mute">任务数</dt>
              <dd class="mt-2 text-[32px] font-semibold leading-none tracking-tight tabular-nums">{{ integer(metrics.tasks) }}</dd>
              <dd class="mt-2 text-xs text-faint">{{ metrics.recovered_tasks ? `其中 ${integer(metrics.recovered_tasks)} 个曾恢复执行` : '全部一次跑完' }}</dd>
            </div>
            <div class="lg:px-6">
              <dt class="text-xs text-mute">成功率</dt>
              <dd class="mt-2 text-[32px] font-semibold leading-none tracking-tight tabular-nums">{{ percent(metrics.success_rate) }}</dd>
              <dd class="mt-3 h-1.5 overflow-hidden rounded-full bg-paper" aria-hidden="true">
                <div class="h-full rounded-full" :style="{ width: `${(metrics.success_rate ?? 0) * 100}%`, backgroundColor: DONE }" />
              </dd>
              <dd class="mt-2 text-xs text-faint">按 {{ integer(metrics.success_rate_tasks) }} 个已结束任务计算</dd>
            </div>
            <div class="lg:px-6">
              <dt class="text-xs text-mute">耗时中位数</dt>
              <dd class="mt-2 text-[32px] font-semibold leading-none tracking-tight tabular-nums">{{ duration(metrics.latency.p50_ms) }}</dd>
              <dd class="mt-2 text-xs text-faint">95% 的任务不超过 {{ duration(metrics.latency.p95_ms) }}</dd>
            </div>
            <div class="lg:pl-6">
              <dt class="text-xs text-mute">Token 用量</dt>
              <dd class="mt-2 text-[32px] font-semibold leading-none tracking-tight tabular-nums">{{ compact(metrics.usage.total_tokens) }}</dd>
              <dd class="mt-2 text-xs text-faint">
                {{ integer(metrics.usage.model_calls) }} 次调用<template v-if="cacheKnown && metrics.usage.cache_hit_rate !== null"> · 缓存命中 {{ percent(metrics.usage.cache_hit_rate) }}</template><template v-if="usageIncomplete"> · 仅含已上报部分</template>
              </dd>
            </div>
          </dl>
        </section>

        <!-- 趋势 -->
        <StackedBarChart
          title="每日任务"
          subtitle="每天结束的任务数，按结果分色"
          :series="taskSeries"
          :columns="taskColumns"
          :format="integer"
          :axis-format="compact"
        />
        <StackedBarChart
          title="每日 Token 用量"
          subtitle="每天模型消耗的 Token，输入与输出分开看"
          :series="tokenSeries"
          :columns="tokenColumns"
          :format="integer"
          :axis-format="compact"
        />

        <!-- 构成 -->
        <div class="grid gap-5 lg:grid-cols-2">
          <RankBars title="任务状态" subtitle="范围内全部任务" :rows="statusRows" :format="integer" />
          <RankBars
            title="失败与未完成的原因"
            subtitle="同一个任务只记一个主要原因"
            :rows="failureRows"
            :format="integer"
            empty-text="没有失败或未完成的任务"
          />
        </div>

        <!-- 工具调用：单独成块，有自己的标题 -->
        <section aria-labelledby="tool-stats-heading" class="pt-3">
          <div class="mb-3 px-1">
            <h2 id="tool-stats-heading" class="text-[18px] font-semibold tracking-tight">工具调用</h2>
            <p class="mt-0.5 text-sm text-mute">AI 处理表格时可以使用的全部工具：每个工具做什么、用了多少次、成不成功、花了多久</p>
          </div>
          <ToolStats :tools="metrics.tools" :catalog="metrics.catalog" />
        </section>

        <!-- 模型调用 -->
        <RankBars
          title="模型调用阶段"
          subtitle="各环节消耗的 Token"
          :rows="stageRows"
          :format="compact"
          :legend="[{ label: '输入', color: INPUT }, { label: '输出', color: OUTPUT }]"
        />
      </div>
    </div>
  </div>
</template>
