<script setup lang="ts">
/** 与人工结果比对的结果：状态 + 匹配率条 + 可展开的差异明细 */
import { ChevronDown, CircleAlert, CircleCheck, Info, TriangleAlert } from '@lucide/vue'
import { ref } from 'vue'

import type { VerifyReport } from '@/api/types'
import { percent } from '@/composables/chatModel'

defineProps<{ report: VerifyReport; rounds?: number }>()
const open = ref(false)
</script>

<template>
  <div class="rounded-xl border border-line bg-white">
    <button class="flex w-full items-center gap-2.5 px-3 py-2.5 text-left" @click="open = !open">
      <Info v-if="report.informational" class="size-4 shrink-0 text-info" />
      <CircleCheck v-else-if="report.status === 'pass'" class="size-4 shrink-0 text-success" />
      <TriangleAlert v-else-if="report.status === 'warn'" class="size-4 shrink-0 text-warning" />
      <CircleAlert v-else class="size-4 shrink-0 text-error" />
      <div class="min-w-0 flex-1">
        <div v-if="report.informational" class="truncate text-[13px] font-medium">
          与原人工样例对比<span class="font-normal text-mute">（流程已按你的要求修改，有差异是预期的，展开看看改动之外有没有误伤）</span>
        </div>
        <div v-else class="truncate text-[13px] font-medium">
          与人工结果比对：{{ report.status === 'pass' ? '一致' : report.status === 'warn' ? '基本一致' : '不一致' }}
          <span v-if="rounds && rounds > 1" class="font-normal text-faint">（修正了 {{ rounds - 1 }} 轮）</span>
        </div>
        <div class="mt-1 flex items-center gap-2">
          <div class="h-1.5 w-28 overflow-hidden rounded-full bg-line">
            <div
              class="h-full rounded-full"
              :class="report.informational ? 'bg-info' : report.status === 'pass' ? 'bg-success' : report.status === 'warn' ? 'bg-warning' : 'bg-error'"
              :style="{ width: percent(report.overall_match_rate, 2) }"
            />
          </div>
          <span class="font-mono text-[11px] text-mute">单元格匹配 {{ percent(report.overall_match_rate) }}</span>
        </div>
      </div>
      <ChevronDown class="size-4 text-faint transition" :class="{ 'rotate-180': open }" />
    </button>
    <div v-if="open" class="space-y-3 border-t border-line px-3 py-3 text-xs">
      <div v-for="sheet in report.sheets" :key="sheet.sheet">
        <div class="font-medium text-ink">sheet「{{ sheet.sheet }}」 <span class="font-normal text-mute">{{ percent(sheet.match_rate) }}</span></div>
        <ul class="mt-1 list-disc space-y-0.5 pl-4 text-mute">
          <li v-for="(m, i) in sheet.messages" :key="i">{{ m }}</li>
        </ul>
        <table v-if="sheet.mismatches?.length" class="mt-2 w-full text-[11px]">
          <thead class="text-faint"><tr><th class="text-left font-normal">位置</th><th class="text-left font-normal">列</th><th class="text-left font-normal">期望</th><th class="text-left font-normal">实际</th></tr></thead>
          <tbody>
            <tr v-for="(mm, i) in sheet.mismatches.slice(0, 8)" :key="i" class="border-t border-line/60">
              <td class="py-0.5 pr-2 font-mono">{{ mm.key ?? `第${mm.row}行` }}</td>
              <td class="pr-2">{{ mm.column }}</td>
              <td class="pr-2 text-success">{{ mm.expected ?? '空' }}</td>
              <td class="text-error">{{ mm.actual ?? '空' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-for="(d, i) in report.details" :key="i" class="text-mute">{{ d }}</p>
    </div>
  </div>
</template>
