<script setup lang="ts">
/** 处理前 / 处理后对比卡片：输入表 → 输出 sheet 的行列变化、新增/删除/改名的列、关键变化 */
import { ArrowRight } from '@lucide/vue'

import type { DiffCard } from '@/api/types'

defineProps<{ cards: DiffCard[]; title?: string }>()
</script>

<template>
  <div class="space-y-2">
    <div v-if="title" class="label-mini">{{ title }}</div>
    <div v-for="card in cards" :key="card.output_sheet" class="rounded-xl border border-line bg-white p-3">
      <div class="flex items-center gap-2 text-[13px]">
        <span class="rounded-md bg-paper px-2 py-0.5">{{ card.input_table }}</span>
        <span class="font-mono text-[11px] text-faint">{{ card.rows[0] }}×{{ card.cols[0] }}</span>
        <ArrowRight class="size-3.5 text-faint" />
        <span class="rounded-md bg-ink px-2 py-0.5 text-white">{{ card.output_sheet }}</span>
        <span class="font-mono text-[11px] text-faint">{{ card.rows[1] }}×{{ card.cols[1] }}</span>
      </div>
      <div v-if="card.highlights.length" class="mt-2 flex flex-wrap gap-1.5">
        <span v-for="h in card.highlights" :key="h" class="rounded-full bg-fun-yellow/20 px-2 py-0.5 text-[11px] text-[#8a6400]">{{ h }}</span>
      </div>
      <div class="mt-2 flex flex-wrap gap-1 text-[11px]">
        <span v-for="c in card.added" :key="`a${c}`" class="rounded-md bg-success/10 px-1.5 py-0.5 text-success">+ {{ c }}</span>
        <span v-for="c in card.removed" :key="`r${c}`" class="rounded-md bg-error/10 px-1.5 py-0.5 text-error line-through decoration-error/40">{{ c }}</span>
        <span v-for="(oldName, newName) in card.renamed" :key="`n${newName}`" class="rounded-md bg-info/10 px-1.5 py-0.5 text-info">{{ oldName }} → {{ newName }}</span>
      </div>
    </div>
  </div>
</template>
