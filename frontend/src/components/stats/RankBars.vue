<script setup lang="ts">
/**
 * 横向排行条：每行 = 名称 + 细条 + 数值，按总量从大到小排。
 * 一行可以由多段组成（例如成功 / 失败、输入 / 输出），段之间留 2px 间隙，条从左侧基线长出。
 * 数值直接写在条旁边，所以不需要额外的提示框；行数多时默认只显示前几项。
 */
import { computed, ref } from 'vue'

export interface RankSegment { value: number; color: string; label: string }
export interface RankRow {
  key: string
  label: string
  segments: RankSegment[]
  /** 名称下方的补充说明（可选） */
  note?: string
}

const props = withDefaults(defineProps<{
  title: string
  subtitle?: string
  rows: RankRow[]
  /** 数值格式 */
  format: (value: number) => string
  /** 多段时的图例（单段不需要） */
  legend?: { label: string; color: string }[]
  /** 默认显示几行，其余折叠 */
  limit?: number
  emptyText?: string
}>(), { limit: 6, subtitle: undefined, legend: undefined, emptyText: undefined })

const expanded = ref(false)
const total = (row: RankRow) => row.segments.reduce((sum, segment) => sum + segment.value, 0)
const sorted = computed(() => [...props.rows].sort((a, b) => total(b) - total(a)))
const visible = computed(() => expanded.value ? sorted.value : sorted.value.slice(0, props.limit))
const max = computed(() => Math.max(1, ...sorted.value.map(total)))
const detail = (row: RankRow) => `${row.label}：${row.segments.length > 1
  ? row.segments.map((segment) => `${segment.label} ${props.format(segment.value)}`).join('，')
  : props.format(total(row))}${row.note ? `（${row.note}）` : ''}`
</script>

<template>
  <section class="card-soft flex flex-col p-5 sm:p-6">
    <header class="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
      <div class="min-w-0">
        <h2 class="text-[15px] font-semibold">{{ title }}</h2>
        <p v-if="subtitle" class="mt-0.5 text-xs text-mute">{{ subtitle }}</p>
      </div>
      <ul v-if="legend?.length" class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-mute" aria-label="图例">
        <li v-for="item in legend" :key="item.label" class="flex items-center gap-1.5">
          <span class="size-2.5 rounded-[3px]" :style="{ backgroundColor: item.color }" aria-hidden="true" />{{ item.label }}
        </li>
      </ul>
    </header>

    <p v-if="!rows.length" class="mt-5 flex flex-1 items-center justify-center rounded-xl bg-paper py-10 text-sm text-mute">
      {{ emptyText ?? '这段时间没有数据' }}
    </p>
    <ul v-else class="mt-4 space-y-0.5">
      <li
        v-for="row in visible"
        :key="row.key"
        class="-mx-2 grid grid-cols-[minmax(0,7.5rem)_minmax(0,1fr)_auto] items-center gap-3 rounded-lg px-2 py-1.5 transition-colors hover:bg-paper sm:grid-cols-[minmax(0,9rem)_minmax(0,1fr)_auto]"
        :title="detail(row)"
      >
        <div class="min-w-0">
          <p class="truncate text-sm">{{ row.label }}</p>
          <p v-if="row.note" class="truncate text-[11px] text-faint">{{ row.note }}</p>
        </div>
        <div class="flex h-2 items-center" :style="{ width: `${(total(row) / max) * 100}%`, minWidth: '4px' }" aria-hidden="true">
          <div
            v-for="(segment, i) in row.segments.filter((item) => item.value > 0)"
            :key="segment.label"
            class="h-full min-w-[2px]"
            :class="[i === 0 ? 'rounded-l-[2px]' : '', i === row.segments.filter((item) => item.value > 0).length - 1 ? 'rounded-r-[4px]' : '', i > 0 ? 'ml-[2px]' : '']"
            :style="{ flex: `${segment.value} 1 0`, backgroundColor: segment.color }"
          />
        </div>
        <p class="text-right text-sm font-medium tabular-nums">{{ format(total(row)) }}</p>
      </li>
    </ul>
    <button
      v-if="rows.length > limit"
      type="button"
      class="mt-2 self-start rounded-lg px-2 py-1 text-xs text-mute transition hover:bg-paper hover:text-ink"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >{{ expanded ? '收起' : `显示全部 ${rows.length} 项` }}</button>
  </section>
</template>
