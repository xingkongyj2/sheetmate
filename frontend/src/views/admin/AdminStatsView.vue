<script setup lang="ts">
/**
 * 后台数据统计：全平台视角 + 按成员下钻 + 7/30/90 天范围。
 * 宽屏 Bento 排版：4 张 KPI 卡片、双列每日趋势、成员/客户用量排行、任务诊断与模型阶段、工具调用明细。
 */
import { ChartColumn, RefreshCw, X } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'

import { adminApi, type AdminExecutionMetrics } from '@/api'
import { errorText } from '@/api/http'
import Mascot from '@/components/brand/Mascot.vue'
import MascotLoader from '@/components/brand/MascotLoader.vue'
import UserAvatar from '@/components/brand/UserAvatar.vue'
import BaseSelect from '@/components/common/BaseSelect.vue'
import RankBars, { type RankRow } from '@/components/stats/RankBars.vue'
import StackedBarChart from '@/components/stats/StackedBarChart.vue'
import ToolStats from '@/components/stats/ToolStats.vue'
import { compact, duration, integer, label, percent, shortDate } from '@/components/stats/format'

const RANGES = [7, 30, 90] as const
const days = ref<number>(7)
const selectedUserId = ref<string>('all')
const metrics = ref<AdminExecutionMetrics | null>(null)
const loading = ref(false)
const error = ref('')
const diagTab = ref<'status' | 'failure'>('status')
let requestVersion = 0

async function load() {
  const version = ++requestVersion
  loading.value = true
  error.value = ''
  try {
    const uid = selectedUserId.value === 'all' ? null : Number(selectedUserId.value)
    const result = await adminApi.stats(days.value, uid)
    if (version === requestVersion) metrics.value = result
  } catch (err) {
    if (version === requestVersion) {
      metrics.value = null
      error.value = errorText(err)
    }
  } finally {
    if (version === requestVersion) loading.value = false
  }
}

function selectRange(value: number) {
  if (value === days.value) return
  days.value = value
  void load()
}

function selectUser(value: string) {
  selectedUserId.value = value
  void load()
}

onMounted(load)

const userOptions = computed(() => [
  { value: 'all', label: '全部成员' },
  ...(metrics.value?.users ?? []).map((u) => ({
    value: String(u.id),
    label: u.display_name === u.account ? u.display_name : `${u.display_name} (@${u.account})`,
  })),
])

const activeUserLabel = computed(() => {
  if (selectedUserId.value === 'all') return ''
  return metrics.value?.users.find((u) => String(u.id) === selectedUserId.value)?.display_name ?? ''
})

const DONE = 'var(--chart-done)'
const FAIL = 'var(--chart-fail)'
const NEUTRAL = 'var(--chart-neutral)'
const INPUT = 'var(--chart-input)'
const OUTPUT = 'var(--chart-output)'

const taskSeries = [
  { key: 'completed', label: '完成', color: DONE },
  { key: 'failed', label: '失败/未完成', color: FAIL },
]
const tokenSeries = [
  { key: 'prompt', label: '输入', color: INPUT },
  { key: 'completion', label: '输出', color: OUTPUT },
]

const taskColumns = computed(() =>
  (metrics.value?.daily ?? []).map((day) => ({
    key: day.date,
    label: shortDate(day.date),
    values: { completed: day.completed, failed: day.failed },
  })),
)

const tokenColumns = computed(() =>
  (metrics.value?.daily ?? []).map((day) => ({
    key: day.date,
    label: shortDate(day.date),
    values: { prompt: day.prompt_tokens, completion: day.completion_tokens },
  })),
)

const statusRows = computed<RankRow[]>(() =>
  Object.entries(metrics.value?.statuses ?? {})
    .filter(([, count]) => count > 0)
    .map(([status, count]) => ({
      key: status,
      label: label(status),
      segments: [{
        value: count,
        label: label(status),
        color: status === 'completed' ? DONE : status === 'failed' || status === 'incomplete' ? FAIL : NEUTRAL,
      }],
    })),
)

const failureRows = computed<RankRow[]>(() =>
  (metrics.value?.failures ?? []).map((item) => ({
    key: item.code,
    label: label(item.code),
    segments: [{ value: item.tasks, label: '任务', color: FAIL }],
  })),
)

const stageRows = computed<RankRow[]>(() =>
  (metrics.value?.stages ?? []).map((stage) => ({
    key: stage.stage,
    label: label(stage.stage),
    note: `${integer(stage.model_calls)} 次`,
    segments: [
      { value: stage.prompt_tokens, label: '输入', color: INPUT },
      { value: stage.completion_tokens, label: '输出', color: OUTPUT },
    ],
  })),
)

const maxUserTokens = computed(() => Math.max(1, ...(metrics.value?.by_user ?? []).map((u) => u.total_tokens || u.tasks)))
const maxCustomerTasks = computed(() => Math.max(1, ...(metrics.value?.by_customer ?? []).map((c) => c.tasks || c.conversations)))

const promptShare = computed(() => {
  const total = metrics.value?.usage.total_tokens ?? 0
  if (!total) return 0
  return Math.round(((metrics.value?.usage.prompt_tokens ?? 0) / total) * 100)
})

const cacheKnown = computed(
  () =>
    !!metrics.value &&
    metrics.value.usage.cached_tokens_missing_calls === 0 &&
    metrics.value.usage.cache_miss_tokens_missing_calls === 0,
)
</script>

<template>
  <div class="h-full overflow-y-auto">
    <div class="mx-auto max-w-[1200px] px-4 py-6 sm:px-8 sm:py-7">
      <!-- 顶部栏 -->
      <header class="flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-2.5">
          <h1 class="text-xl font-semibold tracking-tight">数据统计</h1>
          <button
            v-if="activeUserLabel"
            type="button"
            class="inline-flex items-center gap-1 rounded-full bg-paper px-2.5 py-1 text-xs font-medium text-ink transition hover:bg-black/[0.08]"
            @click="selectUser('all')"
          >
            {{ activeUserLabel }}
            <X class="size-3 text-mute" />
          </button>
        </div>

        <div class="flex flex-wrap items-center gap-2">
          <div class="w-44">
            <BaseSelect
              :model-value="selectedUserId"
              :options="userOptions"
              searchable
              search-placeholder="搜索成员"
              aria-label="成员筛选"
              @update:model-value="selectUser(String($event))"
            />
          </div>
          <div class="flex gap-0.5 rounded-xl bg-paper p-1 text-xs" role="group" aria-label="统计范围">
            <button
              v-for="value in RANGES"
              :key="value"
              type="button"
              class="rounded-lg px-3 py-1.5 transition"
              :class="days === value ? 'bg-white font-medium text-ink shadow-xs' : 'text-mute hover:text-ink'"
              :aria-pressed="days === value"
              @click="selectRange(value)"
            >近 {{ value }} 天</button>
          </div>
          <button
            type="button"
            class="icon-btn !size-9 border border-line"
            title="刷新"
            aria-label="刷新统计"
            :disabled="loading"
            @click="load"
          >
            <RefreshCw class="size-4" :class="loading ? 'animate-spin' : ''" />
          </button>
        </div>
      </header>

      <div v-if="error" class="card-soft mt-6 flex flex-wrap items-center gap-2 p-5 text-sm text-error" role="alert">
        <span>{{ error }}</span>
        <button type="button" class="font-medium underline" @click="load">重试</button>
      </div>

      <MascotLoader v-else-if="!metrics" size="lg" class="py-24" />

      <div v-else-if="metrics.tasks === 0" class="card-soft mt-6 flex flex-col items-center gap-3 px-6 py-20 text-center">
        <span class="flex size-12 items-center justify-center rounded-2xl bg-paper text-mute">
          <ChartColumn class="size-6" />
        </span>
        <p class="text-sm font-medium text-ink">当前筛选范围内暂无任务记录</p>
      </div>

      <div v-else class="mt-6 space-y-5 transition-opacity" :class="loading ? 'opacity-65' : ''">
        <!-- 4 张 KPI 指标卡 -->
        <section class="grid grid-cols-2 gap-4 lg:grid-cols-4" aria-label="关键指标">
          <div class="card-soft p-5">
            <div class="text-xs text-mute">任务总数</div>
            <div class="mt-2.5 text-[30px] font-semibold leading-none tracking-tight tabular-nums">
              {{ integer(metrics.tasks) }}
            </div>
            <div class="mt-3 flex items-center gap-2 text-xs text-faint tabular-nums">
              <span>完成 {{ integer(metrics.statuses.completed ?? 0) }}</span>
              <span v-if="(metrics.statuses.failed ?? 0) + (metrics.statuses.incomplete ?? 0) > 0" class="text-error/80">
                异常 {{ integer((metrics.statuses.failed ?? 0) + (metrics.statuses.incomplete ?? 0)) }}
              </span>
            </div>
          </div>

          <div class="card-soft p-5">
            <div class="flex items-center justify-between text-xs text-mute">
              <span>成功率</span>
              <span class="tabular-nums text-faint">{{ integer(metrics.success_rate_tasks) }} 笔计入</span>
            </div>
            <div class="mt-2.5 text-[30px] font-semibold leading-none tracking-tight tabular-nums">
              {{ percent(metrics.success_rate) }}
            </div>
            <div class="mt-3.5 h-1.5 overflow-hidden rounded-full bg-paper" aria-hidden="true">
              <div
                class="h-full rounded-full transition-all"
                :style="{ width: `${(metrics.success_rate ?? 0) * 100}%`, backgroundColor: DONE }"
              />
            </div>
          </div>

          <div class="card-soft p-5">
            <div class="text-xs text-mute">响应中位耗时</div>
            <div class="mt-2.5 text-[30px] font-semibold leading-none tracking-tight tabular-nums">
              {{ duration(metrics.latency.p50_ms) }}
            </div>
            <div class="mt-3 text-xs text-faint tabular-nums">
              P95 {{ duration(metrics.latency.p95_ms) }}
            </div>
          </div>

          <div class="card-soft p-5">
            <div class="flex items-center justify-between text-xs text-mute">
              <span>Token 消耗</span>
              <span class="tabular-nums text-faint">{{ integer(metrics.usage.model_calls) }} 次</span>
            </div>
            <div class="mt-2.5 text-[30px] font-semibold leading-none tracking-tight tabular-nums">
              {{ compact(metrics.usage.total_tokens) }}
            </div>
            <div class="mt-3 flex items-center gap-2">
              <div class="flex h-1.5 flex-1 overflow-hidden rounded-full bg-paper" aria-hidden="true">
                <div class="h-full" :style="{ width: `${promptShare}%`, backgroundColor: INPUT }" />
                <div class="h-full flex-1" :style="{ backgroundColor: OUTPUT }" />
              </div>
              <span v-if="cacheKnown && metrics.usage.cache_hit_rate !== null" class="shrink-0 text-[11px] tabular-nums text-faint">
                缓存 {{ percent(metrics.usage.cache_hit_rate) }}
              </span>
            </div>
          </div>
        </section>

        <!-- 双列每日趋势 -->
        <section class="grid gap-5 xl:grid-cols-2">
          <StackedBarChart
            title="每日任务"
            :series="taskSeries"
            :columns="taskColumns"
            :format="integer"
            :axis-format="compact"
          />
          <StackedBarChart
            title="每日 Token"
            :series="tokenSeries"
            :columns="tokenColumns"
            :format="integer"
            :axis-format="compact"
          />
        </section>

        <!-- 成员用量 & 客户活跃排行（全局视角展示） -->
        <section v-if="selectedUserId === 'all' && (metrics.by_user.length || metrics.by_customer.length)" class="grid gap-5 lg:grid-cols-2">
          <div class="card-soft p-5 sm:p-6">
            <div class="flex items-center justify-between">
              <h2 class="text-[15px] font-semibold">成员用量</h2>
              <span class="text-xs text-faint">{{ metrics.by_user.length }} 人</span>
            </div>
            <div v-if="!metrics.by_user.length" class="mt-4 rounded-xl bg-paper py-10 text-center text-sm text-mute">暂无数据</div>
            <ul v-else class="mt-4 space-y-1.5">
              <li v-for="u in metrics.by_user.slice(0, 8)" :key="u.user_id">
                <button
                  type="button"
                  class="flex w-full items-center gap-3 rounded-xl px-2.5 py-2 text-left transition hover:bg-paper"
                  @click="selectUser(String(u.user_id))"
                >
                  <UserAvatar :seed="u.account || u.display_name" :src="u.avatar" class="size-7" />
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center justify-between gap-2 text-sm">
                      <span class="truncate font-medium text-ink">{{ u.display_name }}</span>
                      <span class="shrink-0 text-xs tabular-nums text-ink">{{ compact(u.total_tokens) }}</span>
                    </div>
                    <div class="mt-1.5 flex items-center gap-2">
                      <div class="h-1.5 flex-1 overflow-hidden rounded-full bg-paper">
                        <div
                          class="h-full rounded-full"
                          :style="{
                            width: `${Math.max(4, Math.round(((u.total_tokens || u.tasks) / maxUserTokens) * 100))}%`,
                            backgroundColor: INPUT,
                          }"
                        />
                      </div>
                      <span class="shrink-0 text-[11px] tabular-nums text-faint">{{ integer(u.tasks) }} 任务</span>
                    </div>
                  </div>
                </button>
              </li>
            </ul>
          </div>

          <div class="card-soft p-5 sm:p-6">
            <div class="flex items-center justify-between">
              <h2 class="text-[15px] font-semibold">客户活跃</h2>
              <span class="text-xs text-faint">{{ metrics.by_customer.length }} 个</span>
            </div>
            <div v-if="!metrics.by_customer.length" class="mt-4 rounded-xl bg-paper py-10 text-center text-sm text-mute">暂无数据</div>
            <ul v-else class="mt-4 space-y-1.5">
              <li v-for="c in metrics.by_customer.slice(0, 8)" :key="c.customer_id">
                <RouterLink
                  :to="{ name: 'customer', params: { id: c.customer_id } }"
                  class="flex items-center gap-3 rounded-xl px-2.5 py-2 transition hover:bg-paper"
                >
                  <Mascot :variant="c.mascot" :size="26" :shadow="false" :track="false" mood="idle" />
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center justify-between gap-2 text-sm">
                      <span class="truncate font-medium text-ink">{{ c.name }}</span>
                      <span class="shrink-0 text-xs tabular-nums text-ink">{{ integer(c.tasks) }} 任务</span>
                    </div>
                    <div class="mt-1.5 flex items-center gap-2">
                      <div class="h-1.5 flex-1 overflow-hidden rounded-full bg-paper">
                        <div
                          class="h-full rounded-full"
                          :style="{
                            width: `${Math.max(4, Math.round(((c.tasks || c.conversations) / maxCustomerTasks) * 100))}%`,
                            backgroundColor: DONE,
                          }"
                        />
                      </div>
                      <span class="shrink-0 text-[11px] tabular-nums text-faint">{{ c.conversations }} 对话 · {{ c.recipes }} 流程</span>
                    </div>
                  </div>
                </RouterLink>
              </li>
            </ul>
          </div>
        </section>

        <!-- 任务诊断 & 模型阶段 -->
        <section class="grid gap-5 lg:grid-cols-2">
          <div class="flex flex-col">
            <div class="mb-2 flex items-center justify-between px-0.5">
              <h2 class="text-sm font-semibold text-ink">执行诊断</h2>
              <div class="flex gap-0.5 rounded-lg bg-paper p-0.5 text-xs">
                <button
                  type="button"
                  class="rounded-md px-2.5 py-1 transition"
                  :class="diagTab === 'status' ? 'bg-white font-medium text-ink shadow-xs' : 'text-mute hover:text-ink'"
                  @click="diagTab = 'status'"
                >状态分布</button>
                <button
                  type="button"
                  class="rounded-md px-2.5 py-1 transition"
                  :class="diagTab === 'failure' ? 'bg-white font-medium text-ink shadow-xs' : 'text-mute hover:text-ink'"
                  @click="diagTab = 'failure'"
                >异常归因</button>
              </div>
            </div>
            <RankBars
              v-if="diagTab === 'status'"
              title="任务状态"
              :rows="statusRows"
              :format="integer"
              class="flex-1"
            />
            <RankBars
              v-else
              title="异常原因"
              :rows="failureRows"
              :format="integer"
              empty-text="暂无异常任务"
              class="flex-1"
            />
          </div>

          <div class="flex flex-col">
            <div class="mb-2 flex items-center justify-between px-0.5">
              <h2 class="text-sm font-semibold text-ink">模型阶段</h2>
              <span class="text-xs text-faint">按 Token 消耗</span>
            </div>
            <RankBars
              title="各阶段消耗"
              :rows="stageRows"
              :format="compact"
              :legend="[{ label: '输入', color: INPUT }, { label: '输出', color: OUTPUT }]"
              class="flex-1"
            />
          </div>
        </section>

        <!-- 工具调用 -->
        <section aria-labelledby="admin-tool-stats-heading" class="pt-1">
          <h2 id="admin-tool-stats-heading" class="mb-3 px-0.5 text-sm font-semibold text-ink">工具调用</h2>
          <ToolStats :tools="metrics.tools" :catalog="metrics.catalog" />
        </section>
      </div>
    </div>
  </div>
</template>
