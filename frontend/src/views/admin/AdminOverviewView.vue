<script setup lang="ts">
/** 管理后台总览：核心资产、近 14 天趋势、系统健康状态与最近活跃成员/客户。 */
import {
  ArrowUpRight,
  Building2,
  Cpu,
  HardDrive,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Users,
} from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'

import { adminApi, type AdminOverview } from '@/api'
import { errorText } from '@/api/http'
import Mascot from '@/components/brand/Mascot.vue'
import UserAvatar from '@/components/brand/UserAvatar.vue'
import StackedBarChart from '@/components/stats/StackedBarChart.vue'
import { compact, duration, integer, percent, shortDate } from '@/components/stats/format'
import { shortTime } from '@/composables/conversationGroups'
import { executionFileSize } from '@/composables/executionPresentation'

const data = ref<AdminOverview | null>(null)
const loading = ref(false)
const error = ref('')
const chartMode = ref<'tasks' | 'tokens'>('tasks')
const activeTab = ref<'users' | 'customers'>('users')

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await adminApi.overview()
  } catch (err) {
    error.value = errorText(err)
  } finally {
    loading.value = false
  }
}

onMounted(load)

const DONE = 'var(--chart-done)'
const FAIL = 'var(--chart-fail)'
const INPUT = 'var(--chart-input)'
const OUTPUT = 'var(--chart-output)'

const taskSeries = [
  { key: 'completed', label: '完成', color: DONE },
  { key: 'failed', label: '异常', color: FAIL },
]
const tokenSeries = [
  { key: 'prompt', label: '输入', color: INPUT },
  { key: 'completion', label: '输出', color: OUTPUT },
]

const taskColumns = computed(() =>
  (data.value?.recent_metrics.daily ?? []).map((d) => ({
    key: d.date,
    label: shortDate(d.date),
    values: { completed: d.completed, failed: d.failed },
  })),
)

const tokenColumns = computed(() =>
  (data.value?.recent_metrics.daily ?? []).map((d) => ({
    key: d.date,
    label: shortDate(d.date),
    values: { prompt: d.prompt_tokens, completion: d.completion_tokens },
  })),
)
</script>

<template>
  <div class="h-full overflow-y-auto">
    <div class="mx-auto flex min-h-full max-w-[1200px] flex-col px-4 py-6 sm:px-8 sm:py-7">
      <header class="flex shrink-0 items-center justify-between gap-4">
        <h1 class="text-xl font-semibold tracking-tight">总览</h1>
        <button
          type="button"
          class="icon-btn !size-8 border border-line"
          title="刷新"
          aria-label="刷新总览"
          :disabled="loading"
          @click="load"
        >
          <RefreshCw class="size-4" :class="loading ? 'animate-spin' : ''" />
        </button>
      </header>

      <div v-if="error" class="card-soft mt-6 flex items-center gap-3 p-5 text-sm text-error" role="alert">
        <span>{{ error }}</span>
        <button type="button" class="font-medium underline" @click="load">重试</button>
      </div>

      <div v-else-if="!data" class="flex justify-center py-28" role="status">
        <LoaderCircle class="size-6 animate-spin text-mute" />
      </div>

      <div v-else class="mt-6 flex min-h-0 flex-1 flex-col gap-5 transition-opacity" :class="loading ? 'opacity-65' : ''">
        <!-- 第一排：4 个核心指标卡 -->
        <section class="grid shrink-0 grid-cols-2 gap-4 lg:grid-cols-4" aria-label="核心资产">
          <RouterLink :to="{ name: 'admin-users' }" class="card-soft group p-5 transition hover:border-black/15">
            <div class="flex items-center justify-between text-xs text-mute">
              <span>团队成员</span>
              <Users class="size-4 text-faint transition group-hover:text-ink" />
            </div>
            <p class="mt-3 text-[30px] font-semibold leading-none tracking-tight tabular-nums">{{ integer(data.counts.users) }}</p>
            <p class="mt-2.5 text-xs text-faint">{{ data.counts.admins }} 管理员 · {{ data.counts.members }} 成员</p>
          </RouterLink>

          <RouterLink :to="{ name: 'admin-customers' }" class="card-soft group p-5 transition hover:border-black/15">
            <div class="flex items-center justify-between text-xs text-mute">
              <span>客户管理</span>
              <Building2 class="size-4 text-faint transition group-hover:text-ink" />
            </div>
            <p class="mt-3 text-[30px] font-semibold leading-none tracking-tight tabular-nums">{{ integer(data.counts.customers) }}</p>
            <p class="mt-2.5 text-xs text-faint">{{ integer(data.counts.recipes) }} 流程 · {{ integer(data.counts.formulas_custom + data.counts.formulas_builtin) }} 公式</p>
          </RouterLink>

          <RouterLink :to="{ name: 'admin-stats' }" class="card-soft group p-5 transition hover:border-black/15">
            <div class="flex items-center justify-between text-xs text-mute">
              <span>近 14 天任务</span>
              <span class="rounded-full bg-paper px-2 py-0.5 text-[11px] font-medium tabular-nums text-ink">
                {{ percent(data.recent_metrics.success_rate) }}
              </span>
            </div>
            <p class="mt-3 text-[30px] font-semibold leading-none tracking-tight tabular-nums">{{ integer(data.recent_metrics.tasks) }}</p>
            <p class="mt-2.5 text-xs text-faint">中位耗时 {{ duration(data.recent_metrics.latency.p50_ms) }}</p>
          </RouterLink>

          <RouterLink :to="{ name: 'admin-stats' }" class="card-soft group p-5 transition hover:border-black/15">
            <div class="flex items-center justify-between text-xs text-mute">
              <span>近 14 天 Token</span>
              <Sparkles class="size-4 text-faint transition group-hover:text-ink" />
            </div>
            <p class="mt-3 text-[30px] font-semibold leading-none tracking-tight tabular-nums">{{ compact(data.recent_metrics.usage.total_tokens) }}</p>
            <p class="mt-2.5 text-xs text-faint">{{ integer(data.recent_metrics.usage.model_calls) }} 次模型调用</p>
          </RouterLink>
        </section>

        <!-- 第二排：系统配置状态条（两个模型紧邻放置） -->
        <section class="grid shrink-0 grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-label="系统状态">
          <RouterLink :to="{ name: 'admin-models' }" class="status-pill group">
            <span class="flex size-8 shrink-0 items-center justify-center rounded-xl bg-paper text-ink/80">
              <Cpu class="size-4" />
            </span>
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-1.5 text-xs text-mute">
                <span class="size-1.5 rounded-full" :class="data.system.llm.configured ? 'bg-success' : 'bg-warning'" />
                <span>对话模型</span>
              </div>
              <p class="mt-0.5 truncate text-[13px] font-medium text-ink">
                {{ data.system.llm.configured ? (data.system.llm.model || data.system.llm.provider_name) : '未配置' }}
              </p>
            </div>
            <ArrowUpRight class="size-3.5 shrink-0 text-faint transition group-hover:text-ink" />
          </RouterLink>

          <RouterLink :to="{ name: 'admin-privacy' }" class="status-pill group">
            <span class="flex size-8 shrink-0 items-center justify-center rounded-xl bg-paper text-ink/80">
              <Sparkles class="size-4" />
            </span>
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-1.5 text-xs text-mute">
                <span class="size-1.5 rounded-full" :class="data.system.privacy.llm_configured ? 'bg-success' : 'bg-faint'" />
                <span>脱敏模型</span>
              </div>
              <p class="mt-0.5 truncate text-[13px] font-medium text-ink">
                {{ data.system.privacy.llm_configured ? data.system.privacy.llm_model : '未启用' }}
              </p>
            </div>
            <ArrowUpRight class="size-3.5 shrink-0 text-faint transition group-hover:text-ink" />
          </RouterLink>

          <RouterLink :to="{ name: 'admin-privacy' }" class="status-pill group">
            <span class="flex size-8 shrink-0 items-center justify-center rounded-xl bg-paper text-ink/80">
              <ShieldCheck class="size-4" />
            </span>
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-1.5 text-xs text-mute">
                <span class="size-1.5 rounded-full" :class="data.system.privacy.enabled ? 'bg-success' : 'bg-faint'" />
                <span>数据脱敏</span>
              </div>
              <p class="mt-0.5 truncate text-[13px] font-medium text-ink">
                {{ data.system.privacy.enabled ? `${data.system.privacy.enabled_rules}/${data.system.privacy.total_rules} 条规则开启` : '已关闭' }}
              </p>
            </div>
            <ArrowUpRight class="size-3.5 shrink-0 text-faint transition group-hover:text-ink" />
          </RouterLink>

          <RouterLink :to="{ name: 'admin-customers' }" class="status-pill group">
            <span class="flex size-8 shrink-0 items-center justify-center rounded-xl bg-paper text-ink/80">
              <HardDrive class="size-4" />
            </span>
            <div class="min-w-0 flex-1">
              <div class="text-xs text-mute">文件存储</div>
              <p class="mt-0.5 truncate text-[13px] font-medium tabular-nums text-ink">
                {{ integer(data.counts.files) }} 份 · {{ executionFileSize(data.counts.storage_bytes) || '0 B' }}
              </p>
            </div>
            <ArrowUpRight class="size-3.5 shrink-0 text-faint transition group-hover:text-ink" />
          </RouterLink>
        </section>

        <!-- 第三排：左 14 天趋势图 + 右活跃列表（等高铺满剩余屏幕） -->
        <section class="grid min-h-[340px] flex-1 gap-5 lg:grid-cols-12">
          <div class="flex min-h-0 flex-col lg:col-span-7">
            <div class="mb-2.5 flex h-7 shrink-0 items-center justify-between px-0.5">
              <h2 class="text-sm font-semibold text-ink">近 14 天趋势</h2>
              <div class="flex gap-0.5 rounded-lg bg-paper p-0.5 text-xs">
                <button
                  type="button"
                  class="rounded-md px-2.5 py-1 transition"
                  :class="chartMode === 'tasks' ? 'bg-white font-medium text-ink shadow-xs' : 'text-mute hover:text-ink'"
                  @click="chartMode = 'tasks'"
                >任务</button>
                <button
                  type="button"
                  class="rounded-md px-2.5 py-1 transition"
                  :class="chartMode === 'tokens' ? 'bg-white font-medium text-ink shadow-xs' : 'text-mute hover:text-ink'"
                  @click="chartMode = 'tokens'"
                >Token</button>
              </div>
            </div>
            <StackedBarChart
              v-if="chartMode === 'tasks'"
              class="min-h-0 flex-1"
              fill-height
              title="每日任务"
              :series="taskSeries"
              :columns="taskColumns"
              :format="integer"
              :axis-format="compact"
            />
            <StackedBarChart
              v-else
              class="min-h-0 flex-1"
              fill-height
              title="每日 Token"
              :series="tokenSeries"
              :columns="tokenColumns"
              :format="integer"
              :axis-format="compact"
            />
          </div>

          <section class="flex min-h-0 flex-col lg:col-span-5">
            <div class="mb-2.5 flex h-7 shrink-0 items-center justify-between px-0.5">
              <div class="flex gap-0.5 rounded-lg bg-paper p-0.5 text-xs">
                <button
                  type="button"
                  class="rounded-md px-2.5 py-1 transition"
                  :class="activeTab === 'users' ? 'bg-white font-medium text-ink shadow-xs' : 'text-mute hover:text-ink'"
                  @click="activeTab = 'users'"
                >成员动态</button>
                <button
                  type="button"
                  class="rounded-md px-2.5 py-1 transition"
                  :class="activeTab === 'customers' ? 'bg-white font-medium text-ink shadow-xs' : 'text-mute hover:text-ink'"
                  @click="activeTab = 'customers'"
                >客户动态</button>
              </div>
              <RouterLink
                :to="{ name: activeTab === 'users' ? 'admin-users' : 'admin-customers' }"
                class="inline-flex items-center gap-0.5 text-xs text-mute transition hover:text-ink"
              >全部<ArrowUpRight class="size-3.5" /></RouterLink>
            </div>

            <div class="card-soft flex min-h-0 flex-1 flex-col p-5 sm:p-6">
              <div v-if="activeTab === 'users'" class="min-h-0 flex-1 divide-y divide-line/60 overflow-y-auto">
                <div
                  v-for="u in data.recent_users"
                  :key="u.id"
                  class="flex items-center gap-3 py-3 first:pt-0 last:pb-0"
                >
                  <UserAvatar :seed="u.account || u.display_name" :src="u.avatar" class="size-8" />
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center gap-1.5">
                      <span class="truncate text-sm font-medium text-ink">{{ u.display_name }}</span>
                      <span
                        v-if="u.role === 'admin'"
                        class="rounded bg-black/[0.06] px-1.5 py-0.5 text-[10px] font-medium text-ink"
                      >管理员</span>
                    </div>
                    <p class="truncate text-xs text-faint">{{ u.conversation_count }} 对话 · {{ u.task_count }} 任务</p>
                  </div>
                  <span class="shrink-0 text-[11px] tabular-nums text-faint">{{ shortTime(u.last_active_at) || '—' }}</span>
                </div>
                <p v-if="!data.recent_users.length" class="py-10 text-center text-xs text-faint">暂无成员记录</p>
              </div>

              <div v-else class="min-h-0 flex-1 divide-y divide-line/60 overflow-y-auto">
                <RouterLink
                  v-for="c in data.recent_customers"
                  :key="c.id"
                  :to="{ name: 'customer', params: { id: c.id } }"
                  class="-mx-2 flex items-center gap-3 rounded-xl px-2 py-3 transition hover:bg-paper/80"
                >
                  <Mascot :variant="c.mascot" :size="28" :shadow="false" :track="false" mood="idle" />
                  <div class="min-w-0 flex-1">
                    <p class="truncate text-sm font-medium text-ink">{{ c.name }}</p>
                    <p class="truncate text-xs text-faint">{{ c.recipe_count }} 流程 · {{ c.conversation_count }} 对话</p>
                  </div>
                  <span class="shrink-0 text-[11px] tabular-nums text-faint">{{ shortTime(c.last_activity) || '—' }}</span>
                </RouterLink>
                <p v-if="!data.recent_customers.length" class="py-10 text-center text-xs text-faint">暂无客户</p>
              </div>
            </div>
          </section>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.status-pill {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  border-radius: 1rem;
  border: 1px solid var(--color-line);
  background: #fff;
  padding: 0.875rem 1rem;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.status-pill:hover {
  border-color: rgb(11 11 12 / 0.18);
  box-shadow: 0 4px 16px rgb(0 0 0 / 0.04);
}
</style>
