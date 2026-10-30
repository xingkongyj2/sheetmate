<script setup lang="ts">
/** 紧凑的数据表格预览：表头吸顶、行号、数字右对齐、长文本省略。 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{ columns: string[]; rows: unknown[][]; maxHeight?: string; offset?: number; dense?: boolean }>(),
  { maxHeight: '320px', offset: 0, dense: false },
)

const numericCols = computed(() =>
  props.columns.map((_, i) => props.rows.slice(0, 30).every((r) => r[i] === null || r[i] === '' || typeof r[i] === 'number')),
)

function show(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : String(Math.round(value * 10000) / 10000)
  return String(value)
}
</script>

<template>
  <div class="overflow-auto rounded-xl border border-line bg-white" :style="{ maxHeight }">
    <table class="w-full border-separate border-spacing-0 text-left" :class="dense ? 'text-[12px]' : 'text-[13px]'">
      <thead>
        <tr>
          <th class="sticky top-0 z-10 w-10 border-b border-line bg-paper px-2 py-1.5 text-right font-normal text-faint">#</th>
          <th
            v-for="(col, i) in columns"
            :key="i"
            class="sticky top-0 z-10 whitespace-nowrap border-b border-line bg-paper px-3 py-1.5 font-medium text-ink"
            :class="{ 'text-right': numericCols[i] }"
          >
            {{ col }}
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, r) in rows" :key="r" class="hover:bg-paper/60">
          <td class="border-b border-line/70 px-2 py-1 text-right font-mono text-[11px] text-faint">{{ offset + r + 1 }}</td>
          <td
            v-for="(cell, c) in row"
            :key="c"
            class="max-w-[260px] truncate border-b border-line/70 px-3 py-1"
            :class="{ 'text-right font-mono tabular-nums': numericCols[c], 'text-faint': cell === null }"
            :title="show(cell)"
          >
            {{ cell === null ? '—' : show(cell) }}
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="px-3 py-6 text-center text-faint">没有数据</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
