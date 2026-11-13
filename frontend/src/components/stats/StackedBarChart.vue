<script setup lang="ts">
/**
 * 按天的堆叠柱状图（纯 HTML/CSS，不依赖图表库）。
 * - 单一纵轴、从 0 起；柱子细、顶端 4px 圆角、段与段之间留 2px 间隙。
 * - 悬停某一天：整列高亮 + 提示框；聚焦图表后可用左右方向键逐天查看。
 * - 只有图，没有表格；读屏软件通过图表的文字说明和悬停提示读取数据。
 */
import { computed, ref } from 'vue'

import { niceScale } from './format'

export interface ChartSeries { key: string; label: string; color: string }
export interface ChartColumn { key: string; label: string; values: Record<string, number> }

const props = defineProps<{
  title: string
  subtitle?: string
  series: ChartSeries[]
  columns: ChartColumn[]
  /** 提示框里的数值格式 */
  format: (value: number) => string
  /** 纵轴刻度的数值格式（通常是缩写） */
  axisFormat: (value: number) => string
  emptyText?: string
}>()

const PLOT_HEIGHT = 208
const active = ref<number | null>(null)

const totals = computed(() => props.columns.map((column) =>
  props.series.reduce((sum, item) => sum + (column.values[item.key] ?? 0), 0)))
const seriesTotals = computed(() => props.series.map((item) =>
  props.columns.reduce((sum, column) => sum + (column.values[item.key] ?? 0), 0)))
const hasData = computed(() => totals.value.some((value) => value > 0))
const scale = computed(() => niceScale(Math.max(0, ...totals.value)))

/** 每隔几列标一次日期，保证横轴不挤。 */
const labelStep = computed(() => Math.max(1, Math.ceil(props.columns.length / 6)))

function stackFor(column: ChartColumn) {
  const parts = props.series.map((item) => ({ ...item, value: column.values[item.key] ?? 0 })).filter((item) => item.value > 0)
  return parts
}

const activeColumn = computed(() => active.value === null ? null : props.columns[active.value] ?? null)
const tooltipStyle = computed(() => {
  if (active.value === null) return {}
  const count = props.columns.length
  const edge = active.value < count * 0.18 ? 'left' : active.value > count * 0.82 ? 'right' : 'center'
  return {
    left: `${((active.value + 0.5) / count) * 100}%`,
    transform: edge === 'left' ? 'translateX(-12px)' : edge === 'right' ? 'translateX(calc(-100% + 12px))' : 'translateX(-50%)',
  }
})
const activeSummary = computed(() => {
  const column = activeColumn.value
  if (!column) return ''
  return `${column.label}：${props.series.map((item) => `${item.label} ${props.format(column.values[item.key] ?? 0)}`).join('，')}`
})
const chartDescription = computed(() =>
  `${props.title}，共 ${props.columns.length} 天，${props.series.map((item, i) => `${item.label}合计 ${props.format(seriesTotals.value[i] ?? 0)}`).join('，')}。可用左右方向键逐天查看。`)

function onKeydown(event: KeyboardEvent) {
  const last = props.columns.length - 1
  if (last < 0) return
  if (event.key === 'ArrowLeft') active.value = active.value === null ? last : Math.max(0, active.value - 1)
  else if (event.key === 'ArrowRight') active.value = active.value === null ? last : Math.min(last, active.value + 1)
  else if (event.key === 'Escape') active.value = null
  else return
  event.preventDefault()
}
</script>

<template>
  <section class="card-soft p-5 sm:p-6">
    <header class="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
      <div class="min-w-0">
        <h2 class="text-[15px] font-semibold">{{ title }}</h2>
        <p v-if="subtitle" class="mt-0.5 text-xs text-mute">{{ subtitle }}</p>
      </div>
      <div class="flex flex-wrap items-center gap-x-4 gap-y-2">
        <ul class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs" aria-label="图例">
          <li v-for="(item, i) in series" :key="item.key" class="flex items-center gap-1.5 text-mute">
            <span class="size-2.5 rounded-[3px]" :style="{ backgroundColor: item.color }" aria-hidden="true" />
            <span>{{ item.label }}</span>
            <span class="font-medium tabular-nums text-ink">{{ axisFormat(seriesTotals[i] ?? 0) }}</span>
          </li>
        </ul>
      </div>
    </header>

    <!-- 图表 -->
    <div class="mt-8">
      <div v-if="!hasData" class="flex items-center justify-center rounded-xl bg-paper text-sm text-mute" :style="{ height: `${PLOT_HEIGHT}px` }">
        {{ emptyText ?? '这段时间没有数据' }}
      </div>
      <div
        v-else
        class="rounded-lg outline-none focus-visible:ring-4 focus-visible:ring-black/5"
        tabindex="0"
        role="img"
        :aria-label="chartDescription"
        @keydown="onKeydown"
        @blur="active = null"
      >
        <div class="flex">
          <!-- 纵轴刻度 -->
          <div class="relative w-11 shrink-0" :style="{ height: `${PLOT_HEIGHT}px` }" aria-hidden="true">
            <span
              v-for="tick in scale.ticks"
              :key="tick"
              class="absolute right-2 -translate-y-1/2 text-[11px] tabular-nums text-faint"
              :style="{ bottom: `${(tick / scale.max) * 100}%` }"
            >{{ axisFormat(tick) }}</span>
          </div>
          <!-- 绘图区 -->
          <div class="relative min-w-0 flex-1" :style="{ height: `${PLOT_HEIGHT}px` }" @mouseleave="active = null">
            <div
              v-for="tick in scale.ticks"
              :key="tick"
              class="pointer-events-none absolute inset-x-0 border-t"
              :class="tick === 0 ? 'border-black/15' : 'border-line'"
              :style="{ bottom: `${(tick / scale.max) * 100}%` }"
              aria-hidden="true"
            />
            <div class="absolute inset-0 flex items-stretch gap-[2px]">
              <div
                v-for="(column, i) in columns"
                :key="column.key"
                class="relative min-w-0 flex-1 rounded-md transition-colors"
                :class="active === i ? 'bg-black/[0.045]' : ''"
                @mouseenter="active = i"
              >
                <div
                  class="absolute inset-x-0 bottom-0 mx-auto flex w-full max-w-[26px] flex-col-reverse gap-[2px] px-px"
                  :style="{ height: `${(totals[i]! / scale.max) * 100}%` }"
                >
                  <div
                    v-for="(part, index) in stackFor(column)"
                    :key="part.key"
                    class="min-h-[2px] w-full"
                    :class="index === stackFor(column).length - 1 ? 'rounded-t-[4px]' : ''"
                    :style="{ flex: `${part.value} 1 0`, backgroundColor: part.color }"
                  />
                </div>
              </div>
            </div>
            <!-- 提示框 -->
            <div
              v-if="activeColumn"
              class="pointer-events-none absolute top-1 z-10 min-w-[8.5rem] rounded-xl bg-ink px-3 py-2 text-xs text-white shadow-lg"
              :style="tooltipStyle"
            >
              <p class="mb-1 font-medium">{{ activeColumn.label }}</p>
              <p v-for="item in series" :key="item.key" class="flex items-center justify-between gap-4 py-px">
                <span class="flex items-center gap-1.5 text-white/75">
                  <span class="size-2 rounded-[2px]" :style="{ backgroundColor: item.color }" aria-hidden="true" />{{ item.label }}
                </span>
                <span class="font-medium tabular-nums">{{ format(activeColumn.values[item.key] ?? 0) }}</span>
              </p>
            </div>
          </div>
        </div>
        <!-- 横轴日期 -->
        <div class="ml-11 mt-2 flex gap-[2px]" aria-hidden="true">
          <div v-for="(column, i) in columns" :key="column.key" class="relative h-4 min-w-0 flex-1">
            <span
              v-if="i % labelStep === 0"
              class="absolute left-1/2 -translate-x-1/2 whitespace-nowrap text-[11px] tabular-nums text-faint"
            >{{ column.label }}</span>
          </div>
        </div>
      </div>
      <p class="sr-only" aria-live="polite">{{ activeSummary }}</p>
    </div>
  </section>
</template>
