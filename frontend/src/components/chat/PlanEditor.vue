<script setup lang="ts">
/** 计划确认：AI 先拆好步骤，用户可以改、删、加、调顺序，确认后按步执行（TabClaw 的计划模式） */
import { ArrowDown, ArrowUp, ListChecks, Play, Plus, Trash2 } from '@lucide/vue'
import { ref } from 'vue'

import type { PlanStep } from '@/api/types'

const props = defineProps<{ title: string; steps: PlanStep[] }>()
const emit = defineEmits<{ run: [steps: PlanStep[]]; cancel: [] }>()

const items = ref(props.steps.map((s) => ({ ...s })))

function move(i: number, delta: number) {
  const j = i + delta
  if (j < 0 || j >= items.value.length) return
  const list = items.value
  ;[list[i], list[j]] = [list[j], list[i]]
}

function run() {
  const steps = items.value.filter((s) => s.description.trim()).map((s, i) => ({ id: i + 1, description: s.description.trim() }))
  if (steps.length) emit('run', steps)
}
</script>

<template>
  <div class="card-soft animate-rise p-4">
    <div class="mb-3 flex items-center gap-2">
      <ListChecks class="size-4" />
      <span class="text-[14px] font-semibold">{{ title }}</span>
      <span class="text-xs text-faint">确认或修改后执行</span>
    </div>
    <ol class="space-y-2">
      <li v-for="(step, i) in items" :key="i" class="group flex items-start gap-2">
        <span class="mt-2 flex size-5 shrink-0 items-center justify-center rounded-full bg-paper text-[11px] font-semibold">{{ i + 1 }}</span>
        <textarea v-model="step.description" rows="1" class="field min-h-[38px] resize-y py-2 text-[13px]" />
        <div class="mt-1 flex opacity-0 transition group-hover:opacity-100">
          <button class="icon-btn size-7" title="上移" @click="move(i, -1)"><ArrowUp class="size-3.5" /></button>
          <button class="icon-btn size-7" title="下移" @click="move(i, 1)"><ArrowDown class="size-3.5" /></button>
          <button class="icon-btn size-7 hover:text-error" title="删除" @click="items.splice(i, 1)"><Trash2 class="size-3.5" /></button>
        </div>
      </li>
    </ol>
    <div class="mt-3 flex items-center gap-2">
      <button class="btn-text" @click="items.push({ id: items.length + 1, description: '' })"><Plus class="size-3.5" />加一步</button>
      <button class="btn-line ml-auto" @click="emit('cancel')">取消</button>
      <button class="btn-ink" @click="run"><Play class="size-4" />按计划执行</button>
    </div>
  </div>
</template>
