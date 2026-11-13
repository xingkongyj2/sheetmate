<script setup lang="ts">
/**
 * 工具调用统计模块：上面是汇总，下面按分类列出「全部」工具——每个工具一行：
 * 中文名 + 一句话作用 + 调用次数条（成功 / 失败 / 待完成分段）+ 成功率 + 失败数 + 平均耗时。
 * 这段时间没用过的工具也在列，显示为灰色；可以切到「只看用过的」。
 */
import { computed, ref } from 'vue'

import { duration, integer, percent } from './format'

export interface ToolRecord {
  tool: string
  calls: number
  completed: number
  failed: number
  pending: number
  average_duration_ms: number | null
}
export interface ToolCatalogItem { tool: string; label: string; group: string; purpose: string }

interface Row extends ToolRecord { label: string; purpose: string; used: boolean }
interface Group { name: string; rows: Row[]; used: number; calls: number }

const props = defineProps<{
  /** 这段时间实际用到的工具 */
  tools: ToolRecord[]
  /** 全部工具的目录（后端提供，含没用过的） */
  catalog: ToolCatalogItem[]
}>()

const DONE = 'var(--chart-done)'
const FAIL = 'var(--chart-fail)'
const NEUTRAL = 'var(--chart-neutral)'
const OTHER_GROUP = '其他（客户自定义公式等）'

const onlyUsed = ref(false)

const empty = (tool: string): ToolRecord =>
  ({ tool, calls: 0, completed: 0, failed: 0, pending: 0, average_duration_ms: null })

/** 目录里的每个工具一行；目录里没有、但实际用到的（比如客户自己建的公式）归到最后一组。 */
const groups = computed<Group[]>(() => {
  const usage = new Map(props.tools.map((tool) => [tool.tool, tool]))
  const known = new Set(props.catalog.map((item) => item.tool))
  const rows: { group: string; row: Row }[] = props.catalog.map((item) => {
    const record = usage.get(item.tool) ?? empty(item.tool)
    return { group: item.group, row: { ...record, label: item.label, purpose: item.purpose, used: record.calls > 0 } }
  })
  for (const record of props.tools) {
    if (!known.has(record.tool)) rows.push({ group: OTHER_GROUP, row: { ...record, label: record.tool, purpose: '客户专属的处理公式', used: record.calls > 0 } })
  }
  const byGroup = new Map<string, Row[]>()
  for (const { group, row } of rows) byGroup.set(group, [...(byGroup.get(group) ?? []), row])
  return [...byGroup.entries()].map(([name, list]) => {
    // 组内：用得多的在前，没用过的沉到最后；次数相同时保持目录里的顺序
    const sorted = list.map((row, order) => ({ row, order })).sort((a, b) => b.row.calls - a.row.calls || a.order - b.order).map((item) => item.row)
    return {
      name,
      rows: sorted,
      used: sorted.filter((row) => row.used).length,
      calls: sorted.reduce((sum, row) => sum + row.calls, 0),
    }
  })
})

const visibleGroups = computed(() => groups.value
  .map((group) => ({ ...group, rows: onlyUsed.value ? group.rows.filter((row) => row.used) : group.rows }))
  .filter((group) => group.rows.length))

const maxCalls = computed(() => Math.max(1, ...props.tools.map((tool) => tool.calls)))
const totalKinds = computed(() => groups.value.reduce((sum, group) => sum + group.rows.length, 0))
const usedKinds = computed(() => groups.value.reduce((sum, group) => sum + group.used, 0))

/** 已结束的调用里成功的比例；还没结束的不参与。 */
const successRate = (tool: Pick<ToolRecord, 'completed' | 'failed'>): number | null =>
  tool.completed + tool.failed > 0 ? tool.completed / (tool.completed + tool.failed) : null

const summary = computed(() => {
  const calls = props.tools.reduce((sum, tool) => sum + tool.calls, 0)
  const completed = props.tools.reduce((sum, tool) => sum + tool.completed, 0)
  const failed = props.tools.reduce((sum, tool) => sum + tool.failed, 0)
  // 平均耗时按各工具「成功调用数」加权；没有耗时记录的工具不参与。
  const timed = props.tools.filter((tool) => tool.average_duration_ms !== null && tool.completed > 0)
  const weight = timed.reduce((sum, tool) => sum + tool.completed, 0)
  const average = weight ? timed.reduce((sum, tool) => sum + (tool.average_duration_ms ?? 0) * tool.completed, 0) / weight : null
  return { calls, failed, rate: successRate({ completed, failed }), average }
})

const segments = (row: Row) => [
  { key: 'done', value: row.completed, color: DONE },
  { key: 'fail', value: row.failed, color: FAIL },
  { key: 'rest', value: Math.max(0, row.calls - row.completed - row.failed), color: NEUTRAL },
].filter((segment) => segment.value > 0)

const detail = (row: Row) => row.used
  ? `${row.label}：${row.purpose}。共 ${integer(row.calls)} 次，成功 ${integer(row.completed)}，失败 ${integer(row.failed)}，待完成 ${integer(Math.max(0, row.calls - row.completed - row.failed))}`
  : `${row.label}：${row.purpose}。这段时间没有用过`
const dash = '—'
</script>

<template>
  <div class="card-soft p-5 sm:p-6">
    <!-- 汇总 -->
    <dl class="grid grid-cols-2 gap-x-6 gap-y-5 sm:grid-cols-4 sm:gap-x-0 sm:divide-x sm:divide-line">
      <div class="sm:pr-5">
        <dt class="text-xs text-mute">调用次数</dt>
        <dd class="mt-1.5 text-2xl font-semibold leading-none tabular-nums">{{ integer(summary.calls) }}</dd>
        <dd class="mt-1.5 text-xs text-faint">用到 {{ usedKinds }} / {{ totalKinds }} 种工具</dd>
      </div>
      <div class="sm:px-5">
        <dt class="text-xs text-mute">成功率</dt>
        <dd class="mt-1.5 text-2xl font-semibold leading-none tabular-nums">{{ percent(summary.rate) }}</dd>
        <dd class="mt-1.5 text-xs text-faint">只算已结束的调用</dd>
      </div>
      <div class="sm:px-5">
        <dt class="text-xs text-mute">失败次数</dt>
        <dd class="mt-1.5 text-2xl font-semibold leading-none tabular-nums">{{ integer(summary.failed) }}</dd>
        <dd class="mt-1.5 text-xs text-faint">{{ summary.calls ? `占调用的 ${((summary.failed / summary.calls) * 100).toFixed(1)}%` : dash }}</dd>
      </div>
      <div class="sm:pl-5">
        <dt class="text-xs text-mute">平均耗时</dt>
        <dd class="mt-1.5 text-2xl font-semibold leading-none tabular-nums">{{ duration(summary.average) }}</dd>
        <dd class="mt-1.5 text-xs text-faint">不含经过恢复的调用</dd>
      </div>
    </dl>

    <!-- 明细：按分类列出全部工具 -->
    <div class="mt-6 border-t border-line pt-4">
      <div class="mb-1 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h3 class="text-[13px] font-medium">全部工具</h3>
          <ul class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-mute" aria-label="图例">
            <li class="flex items-center gap-1.5"><span class="size-2.5 rounded-[3px]" :style="{ backgroundColor: DONE }" aria-hidden="true" />成功</li>
            <li class="flex items-center gap-1.5"><span class="size-2.5 rounded-[3px]" :style="{ backgroundColor: FAIL }" aria-hidden="true" />失败</li>
            <li class="flex items-center gap-1.5"><span class="size-2.5 rounded-[3px]" :style="{ backgroundColor: NEUTRAL }" aria-hidden="true" />待完成</li>
          </ul>
        </div>
        <div class="flex gap-0.5 rounded-lg bg-paper p-0.5 text-xs" role="group" aria-label="工具显示范围">
          <button
            v-for="option in ([[false, `全部 ${totalKinds}`], [true, `只看用过的 ${usedKinds}`]] as const)"
            :key="String(option[0])"
            type="button"
            class="rounded-md px-2.5 py-1 transition"
            :class="onlyUsed === option[0] ? 'bg-white font-medium text-ink shadow-sm' : 'text-mute hover:text-ink'"
            :aria-pressed="onlyUsed === option[0]"
            @click="onlyUsed = option[0]"
          >{{ option[1] }}</button>
        </div>
      </div>

      <div class="mt-3 hidden grid-cols-[minmax(0,16rem)_minmax(0,1fr)_4.5rem_4rem_5.5rem] gap-3 px-2 text-[11px] text-faint sm:grid" aria-hidden="true">
        <span>工具与作用</span><span>调用次数</span><span class="text-right">成功率</span><span class="text-right">失败</span><span class="text-right">平均耗时</span>
      </div>

      <div v-for="group in visibleGroups" :key="group.name" class="mt-4">
        <div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 border-b border-line px-2 pb-1.5">
          <h4 class="text-[13px] font-semibold">{{ group.name }}</h4>
          <p class="text-xs text-faint">{{ group.rows.length }} 个工具 · {{ group.used }} 个用过 · 共 {{ integer(group.calls) }} 次</p>
        </div>
        <ul>
          <li
            v-for="row in group.rows"
            :key="row.tool"
            class="grid grid-cols-1 items-center gap-x-3 gap-y-1 rounded-lg px-2 py-2 transition-colors hover:bg-paper sm:grid-cols-[minmax(0,16rem)_minmax(0,1fr)_4.5rem_4rem_5.5rem]"
            :title="detail(row)"
          >
            <div class="min-w-0">
              <p class="truncate text-sm" :class="row.used ? '' : 'text-mute'">
                {{ row.label }}<span v-if="!row.used" class="ml-2 text-[11px] text-faint sm:hidden">未使用</span>
              </p>
              <p class="text-[11px] leading-snug text-faint sm:truncate">{{ row.purpose }}</p>
            </div>
            <template v-if="row.used">
              <!-- 调用次数：条 + 数字（条的长度按用得最多的工具归一） -->
              <div class="flex min-w-0 items-center gap-3">
                <div class="min-w-0 flex-1">
                  <div class="flex h-2 items-center" :style="{ width: `${(row.calls / maxCalls) * 100}%`, minWidth: '4px' }" aria-hidden="true">
                    <div
                      v-for="(segment, i) in segments(row)"
                      :key="segment.key"
                      class="h-full min-w-[2px]"
                      :class="[i === 0 ? 'rounded-l-[2px]' : 'ml-[2px]', i === segments(row).length - 1 ? 'rounded-r-[4px]' : '']"
                      :style="{ flex: `${segment.value} 1 0`, backgroundColor: segment.color }"
                    />
                  </div>
                </div>
                <span class="w-12 shrink-0 text-right text-sm font-medium tabular-nums">{{ integer(row.calls) }}</span>
              </div>
              <!-- 窄屏：其余指标折到下面一行 -->
              <p class="truncate text-[11px] text-mute sm:hidden">
                成功率 {{ percent(successRate(row)) }} · 失败 {{ integer(row.failed) }} · 平均 {{ duration(row.average_duration_ms) }}
              </p>
              <!-- 宽屏：独立的列 -->
              <p class="hidden text-right text-sm tabular-nums sm:block">{{ percent(successRate(row)) }}</p>
              <p class="hidden text-right text-sm tabular-nums sm:block">{{ integer(row.failed) }}</p>
              <p class="hidden text-right text-sm tabular-nums sm:block">{{ duration(row.average_duration_ms) }}</p>
            </template>
            <p v-else class="hidden text-xs text-faint sm:col-span-4 sm:block">这段时间没用过</p>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>
