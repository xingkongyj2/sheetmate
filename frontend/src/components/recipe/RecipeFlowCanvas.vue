<script setup lang="ts">
/** Shared flow canvas; selection and inspection are managed by the containing view. */
import { Check, CircleAlert, LoaderCircle, TriangleAlert, Wrench } from '@lucide/vue'
import { computed } from 'vue'

import type { FlowStep, RecipeFlowData } from '@/api/types'
import FunObject, { type FunKind } from '@/components/brand/FunObject.vue'
import { formulaArt, percent, type StepState } from '@/composables/chatModel'
import { flowInputFiles, flowOutputFiles } from '@/composables/recipeFlow'
import type { FlowFileNode } from '@/composables/recipeFlowInteraction'
import { recipeToolKind, recipeToolLabel } from '@/composables/recipeTools'

const props = withDefaults(
  defineProps<{
    flow: RecipeFlowData
    states?: StepState[]
    direction?: 'horizontal' | 'vertical'
    activeStep?: number | null
    selectedFileKey?: string | null
    compact?: boolean
    busy?: boolean
    failed?: boolean
  }>(),
  { states: () => [], direction: 'horizontal', activeStep: null, selectedFileKey: null, compact: false, busy: false, failed: false },
)
const emit = defineEmits<{ select: [stepId: number]; selectFile: [file: FlowFileNode] }>()

type Tone = 'idle' | 'pending' | 'active' | 'completed' | 'warning' | 'failed'
const stateById = computed(() => new Map(props.states.map((step) => [step.step_id, step])))
const STATUS_LABELS: Record<StepState['status'], string> = {
  pending: '等待执行', running: '执行中', repairing: '修复中', success: '已完成',
  repaired: '已修复', warning: '有提示', failed: '执行失败',
}
const finished = (status?: StepState['status']) => !!status && ['success', 'repaired', 'warning'].includes(status)

function state(id: number) {
  return stateById.value.get(id)
}
function stateLabel(id: number) {
  const status = state(id)?.status
  return status ? STATUS_LABELS[status] : ''
}
function stepTone(id: number): Tone {
  const status = state(id)?.status
  if (!status) return props.states.length ? 'pending' : 'idle'
  if (status === 'running' || status === 'repairing') return 'active'
  if (status === 'success' || status === 'repaired') return 'completed'
  return status
}
function stateIcon(id: number) {
  switch (state(id)?.status) {
    case 'running': return LoaderCircle
    case 'repairing':
    case 'repaired': return Wrench
    case 'success': return Check
    case 'warning': return TriangleAlert
    case 'failed': return CircleAlert
    default: return null
  }
}
function art(step: FlowStep): FunKind {
  return recipeToolKind(step) === 'formula' ? formulaArt(step.formula, step.formula_kind) as FunKind : 'code'
}
function stepLabel(step: FlowStep) {
  return `查看第 ${step.id} 步：${step.name}${stateLabel(step.id) ? `，${stateLabel(step.id)}` : ''}`
}
function rowsText(step?: StepState) {
  const report = step?.report
  if (!report) return ''
  if (recipeToolKind(report) === 'python' && !report.columns?.length) return report.output_files?.length ? `${report.output_files.length} 个文件` : '处理完成'
  if (report.rows_out === undefined) return ''
  const first = report.rows_in ? Object.values(report.rows_in)[0] : undefined
  return first !== undefined ? `${first} → ${report.rows_out} 行` : `${report.rows_out} 行`
}

const inputs = computed<FlowFileNode[]>(() => {
  const files = flowInputFiles(props.flow)
  return files.length ? files.map((file, index) => ({
    key: `input:${index}:${file.name}`, direction: 'input', name: file.name,
    aliases: [...file.aliases], pattern: file.file_pattern,
    fileId: (file as typeof file & { file_id?: string }).file_id,
  })) : [{ key: 'input:unrecorded', direction: 'input', name: '输入文件未记录', aliases: [] }]
})
const outputs = computed<FlowFileNode[]>(() => {
  const files = flowOutputFiles(props.flow)
  return files.length ? files.map((file, index) => ({
    key: `output:${index}:${file.name}`, direction: 'output', name: file.name,
    aliases: [], source: file.source, sources: [...(file.sources ?? [])],
    fileId: (file as typeof file & { file_id?: string }).file_id,
  })) : [{ key: 'output:unrecorded', direction: 'output', name: '输出文件未记录', aliases: [] }]
})
function fileLabel(file: FlowFileNode) {
  return `查看${file.direction === 'input' ? '输入' : '输出'}文件：${file.name}${file.aliases.length ? `，包含 ${file.aliases.join('、')}` : ''}`
}

// The output edge represents delivery: it runs during the final step and export,
// and cannot become complete while any step is pending, running, or failed.
const outputTone = computed<Tone>(() => {
  if (props.failed) return 'failed'
  if (!props.states.length || !props.flow.steps.length) return 'idle'
  const states = props.flow.steps.map((step) => state(step.id))
  if (states.some((step) => step?.status === 'failed')) return 'failed'
  if (states.every((step) => finished(step?.status))) {
    if (props.busy) return 'active'
    return states.some((step) => step?.status === 'warning') ? 'warning' : 'completed'
  }
  const final = states[states.length - 1]
  if (states.slice(0, -1).every((step) => finished(step?.status)) && (final?.status === 'running' || final?.status === 'repairing')) return 'active'
  return 'pending'
})
const inputTone = computed<Tone>(() => props.flow.steps[0] ? stepTone(props.flow.steps[0].id) : 'idle')
</script>

<template>
  <div
    v-if="direction === 'horizontal'"
    class="recipe-flow-canvas overflow-x-auto pb-2"
    :class="{ 'recipe-flow-compact': compact }"
    role="group"
    aria-label="处理流程图"
    tabindex="0"
  >
    <div class="recipe-flow-row flex min-w-max items-stretch gap-0">
      <div class="flex flex-col justify-center gap-2 pr-1">
        <button
          v-for="file in inputs"
          :key="file.key"
          type="button"
          class="recipe-flow-file recipe-flow-input flex items-center gap-2 rounded-xl border border-line bg-white py-1.5 pl-1.5 pr-3 text-left"
          :class="{ 'is-selected': selectedFileKey === file.key }"
          :aria-label="fileLabel(file)"
          :aria-pressed="selectedFileKey === file.key"
          :title="file.aliases.join('、') || file.name"
          @click="emit('selectFile', file)"
        >
          <FunObject kind="folder" :size="compact ? 28 : 26" />
          <span class="min-w-0 leading-tight">
            <span class="recipe-flow-file-name block text-[13px] font-medium">{{ file.name }}</span>
            <span v-if="!compact && file.pattern" class="block font-mono text-[10px] text-faint">{{ file.pattern }}</span>
            <span v-if="!compact" class="block text-[10px] text-faint">输入文件</span>
          </span>
        </button>
      </div>

      <template v-for="step in flow.steps" :key="step.id">
        <div class="recipe-flow-connector flex w-9 shrink-0 items-center" :class="`is-${stepTone(step.id)}`" aria-hidden="true">
          <svg width="36" height="12" viewBox="0 0 36 12">
            <line class="flow-line" x1="2" y1="6" x2="30" y2="6" />
            <path d="M28 2l5 4-5 4" />
          </svg>
        </div>
        <button
          type="button"
          class="recipe-flow-step group relative flex w-[212px] shrink-0 flex-col rounded-2xl border border-line bg-white p-3 text-left transition hover:-translate-y-0.5"
          :class="[`is-${stepTone(step.id)}`, { 'is-selected': activeStep === step.id }]"
          :data-flow-step="step.id"
          :aria-label="stepLabel(step)"
          :aria-pressed="activeStep === step.id"
          :title="compact ? step.description : undefined"
          @click="emit('select', step.id)"
        >
          <span v-if="compact && state(step.id)" class="recipe-flow-status absolute right-1.5 top-1.5 flex size-4 items-center justify-center rounded-full bg-white/90" :class="`is-${stepTone(step.id)}`" aria-hidden="true">
            <component :is="stateIcon(step.id)" v-if="stateIcon(step.id)" class="size-3" :class="{ 'flow-spinner': state(step.id)?.status === 'running' }" />
            <span v-else class="size-1.5 rounded-full bg-faint" />
          </span>
          <span class="recipe-flow-step-art flex w-full items-center gap-2">
            <span v-if="!compact" class="flex size-5 shrink-0 items-center justify-center rounded-full bg-ink text-[11px] font-semibold text-white">{{ step.id }}</span>
            <FunObject :kind="art(step)" :size="compact ? 34 : 22" />
            <span v-if="!compact" class="tag ml-auto" :class="recipeToolKind(step) !== 'formula' || step.formula_kind === 'customer' ? '!bg-fun-purple/10 !text-fun-purple' : ''">{{ recipeToolLabel(step) }}</span>
          </span>
          <span class="recipe-flow-step-name mt-2 block text-[13.5px] font-semibold leading-snug">{{ step.name }}</span>
          <span v-if="!compact" class="mt-1 line-clamp-2 text-xs leading-relaxed text-mute">{{ step.description }}</span>
          <span v-if="!compact" class="mt-auto flex w-full items-center gap-1.5 pt-2.5 text-[11px] text-faint">
            <template v-if="state(step.id)">
              <component :is="stateIcon(step.id)" v-if="stateIcon(step.id)" class="recipe-flow-status size-3.5 shrink-0" :class="[`is-${stepTone(step.id)}`, { 'flow-spinner': state(step.id)?.status === 'running' }]" aria-hidden="true" />
              <span>{{ stateLabel(step.id) }}</span>
              <span v-if="rowsText(state(step.id))" class="ml-auto">{{ rowsText(state(step.id)) }}</span>
              <span v-if="state(step.id)?.report?.metrics?.match_rate !== undefined">匹配 {{ percent(state(step.id)!.report!.metrics!.match_rate as number, 0) }}</span>
            </template>
            <span v-else class="font-mono">→ {{ step.output }}</span>
          </span>
        </button>
      </template>

      <div class="recipe-flow-connector flex w-9 shrink-0 items-center" :class="`is-${outputTone}`" aria-hidden="true">
        <svg width="36" height="12" viewBox="0 0 36 12">
          <line class="flow-line" x1="2" y1="6" x2="30" y2="6" />
          <path d="M28 2l5 4-5 4" />
        </svg>
      </div>
      <div class="flex flex-col justify-center gap-2">
        <button
          v-for="file in outputs"
          :key="file.key"
          type="button"
          class="recipe-flow-file recipe-flow-output flex items-center gap-2 rounded-xl border border-transparent bg-ink py-1.5 pl-1.5 pr-3 text-left text-white"
          :class="[`is-${outputTone}`, { 'is-selected': selectedFileKey === file.key }]"
          :aria-label="fileLabel(file)"
          :aria-pressed="selectedFileKey === file.key"
          @click="emit('selectFile', file)"
        >
          <span class="flex size-[26px] shrink-0 items-center justify-center rounded-lg bg-white/15"><FunObject kind="plane" :size="18" /></span>
          <span class="min-w-0 leading-tight">
            <span class="recipe-flow-file-name block text-[13px] font-medium">{{ file.name }}</span>
            <span v-if="!compact" class="block text-[10px] opacity-60">输出文件</span>
          </span>
        </button>
      </div>
    </div>
  </div>

  <div v-else class="recipe-flow-canvas recipe-flow-vertical" :class="{ 'recipe-flow-compact': compact }" role="group" aria-label="处理流程图">
    <div class="space-y-2 pl-8">
      <button
        v-for="file in inputs"
        :key="file.key"
        type="button"
        class="recipe-flow-file recipe-flow-input flex w-full items-center gap-2 rounded-xl border border-line bg-white px-3 py-2 text-left"
        :class="{ 'is-selected': selectedFileKey === file.key }"
        :aria-label="fileLabel(file)"
        :aria-pressed="selectedFileKey === file.key"
        @click="emit('selectFile', file)"
      >
        <FunObject kind="folder" :size="26" />
        <span class="min-w-0 leading-tight">
          <span class="recipe-flow-file-name block text-[13px] font-medium">{{ file.name }}</span>
          <span class="block text-[10px] text-faint">输入文件<span v-if="file.pattern"> · {{ file.pattern }}</span></span>
        </span>
      </button>
    </div>
    <div v-if="flow.steps.length" class="recipe-flow-connector recipe-flow-connector-vertical ml-2" :class="`is-${inputTone}`" aria-hidden="true">
      <svg width="12" height="26" viewBox="0 0 12 26"><line class="flow-line" x1="6" y1="0" x2="6" y2="21" /><path d="M2 19l4 5 4-5" /></svg>
    </div>
    <ol class="pl-1">
      <li v-for="(step, index) in flow.steps" :key="step.id" class="relative flex gap-3">
        <div class="flex w-6 shrink-0 flex-col items-center">
          <span class="recipe-flow-number z-10 flex size-6 shrink-0 items-center justify-center rounded-full border border-line bg-white text-[11px] font-semibold text-faint" :class="`is-${stepTone(step.id)}`" aria-hidden="true">
            <component :is="stateIcon(step.id)" v-if="stateIcon(step.id)" class="size-3.5" :class="{ 'flow-spinner': state(step.id)?.status === 'running' }" />
            <template v-else>{{ step.id }}</template>
          </span>
          <div v-if="index < flow.steps.length - 1" class="recipe-flow-connector recipe-flow-connector-vertical min-h-5 flex-1" :class="`is-${stepTone(flow.steps[index + 1]!.id)}`" aria-hidden="true">
            <svg width="12" height="100%" preserveAspectRatio="none" viewBox="0 0 12 28"><line class="flow-line" x1="6" y1="0" x2="6" y2="28" /></svg>
          </div>
        </div>
        <button
          type="button"
          class="recipe-flow-step recipe-flow-step-vertical mb-3 flex min-w-0 flex-1 flex-col rounded-xl border border-line bg-white px-3 py-2 text-left"
          :class="[`is-${stepTone(step.id)}`, { 'is-selected': activeStep === step.id }]"
          :data-flow-step="step.id"
          :aria-label="stepLabel(step)"
          :aria-pressed="activeStep === step.id"
          @click="emit('select', step.id)"
        >
          <span class="flex w-full min-w-0 items-center gap-1.5">
            <span class="min-w-0 break-words text-[13px] font-medium">{{ step.name }}</span>
            <span class="tag ml-auto shrink-0" :class="recipeToolKind(step) !== 'formula' || step.formula_kind === 'customer' ? '!bg-fun-purple/10 !text-fun-purple' : ''">{{ recipeToolLabel(step) }}</span>
          </span>
          <span class="mt-1 line-clamp-2 text-xs text-mute">{{ step.description }}</span>
          <span v-if="state(step.id)" class="recipe-flow-status mt-1 flex flex-wrap items-center gap-x-2 text-[11px]" :class="`is-${stepTone(step.id)}`">
            <span>{{ stateLabel(step.id) }}</span><span v-if="rowsText(state(step.id))">{{ rowsText(state(step.id)) }}</span>
          </span>
          <span v-if="state(step.id)?.error" class="mt-1 line-clamp-2 text-[11px] text-error">{{ state(step.id)!.error }}</span>
        </button>
      </li>
    </ol>
    <div class="recipe-flow-connector recipe-flow-connector-vertical ml-2" :class="[`is-${outputTone}`, { '-mt-3': flow.steps.length }]" aria-hidden="true">
      <svg width="12" height="26" viewBox="0 0 12 26"><line class="flow-line" x1="6" y1="0" x2="6" y2="21" /><path d="M2 19l4 5 4-5" /></svg>
    </div>
    <div class="space-y-2 pl-8">
      <button
        v-for="file in outputs"
        :key="file.key"
        type="button"
        class="recipe-flow-file recipe-flow-output flex w-full items-center gap-2 rounded-xl border border-transparent bg-ink px-3 py-2 text-left text-white"
        :class="[`is-${outputTone}`, { 'is-selected': selectedFileKey === file.key }]"
        :aria-label="fileLabel(file)"
        :aria-pressed="selectedFileKey === file.key"
        @click="emit('selectFile', file)"
      >
        <span class="flex size-[26px] shrink-0 items-center justify-center rounded-lg bg-white/15"><FunObject kind="plane" :size="18" /></span>
        <span class="min-w-0 leading-tight">
          <span class="recipe-flow-file-name block text-[13px] font-medium">{{ file.name }}</span>
          <span class="block text-[10px] opacity-60">输出文件</span>
        </span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.recipe-flow-row { width: max-content; margin-inline: auto; }
.recipe-flow-file, .recipe-flow-step { cursor: pointer; transition: background-color .18s, border-color .18s, box-shadow .18s, transform .18s; }
.recipe-flow-file:hover, .recipe-flow-step:hover { box-shadow: 0 5px 16px #34345212; }
.recipe-flow-file:focus-visible, .recipe-flow-step:focus-visible, .recipe-flow-canvas:focus-visible { outline: 2px solid #6265dc; outline-offset: 3px; }
.recipe-flow-file-name { overflow-wrap: anywhere; }
.recipe-flow-connector { color: #c9c9cf; }
.recipe-flow-connector svg { overflow: visible; }
.recipe-flow-connector line, .recipe-flow-connector path { fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; vector-effect: non-scaling-stroke; }
.recipe-flow-connector.is-pending .flow-line { stroke-dasharray: 3 4; }
.recipe-flow-connector.is-active { color: #6265dc; }
.recipe-flow-connector.is-active .flow-line { stroke-dasharray: 4 4; animation: flow-travel .8s linear infinite; }
.recipe-flow-connector.is-completed { color: #22a16a; }
.recipe-flow-connector.is-warning { color: #c58a13; }
.recipe-flow-connector.is-failed { color: #ef4444; }
.recipe-flow-connector-vertical { display: flex; width: 12px; justify-content: center; }
.recipe-flow-status.is-active { color: #6265dc; }
.recipe-flow-status.is-completed { color: #169b61; }
.recipe-flow-status.is-warning { color: #bc8314; }
.recipe-flow-status.is-failed { color: #ef4444; }
.recipe-flow-number.is-active { border-color: #6265dc; background: #eeeefc; color: #6265dc; }
.recipe-flow-number.is-completed { border-color: #1fa463; background: #e9f8f0; color: #169b61; }
.recipe-flow-number.is-warning { border-color: #e1b04f; background: #fff8e9; color: #bc8314; }
.recipe-flow-number.is-failed { border-color: #ef4444; background: #fff1f1; color: #ef4444; }
.recipe-flow-compact { padding: 6px 2px 10px; }
.recipe-flow-compact .recipe-flow-row { align-items: center; gap: 0; min-width: max-content; }
.recipe-flow-compact .recipe-flow-step:not(.recipe-flow-step-vertical) { width: 76px; min-height: 102px; flex-shrink: 0; align-items: center; justify-content: center; padding: 12px 7px; border-color: transparent; background: #f8f8fc; text-align: center; box-shadow: 0 2px 8px #34345205; }
.recipe-flow-compact .recipe-flow-step:hover { background: #f0effb; box-shadow: 0 5px 14px #6265dc12; }
.recipe-flow-compact .recipe-flow-step-art { justify-content: center; }
.recipe-flow-compact .recipe-flow-step-name { width: 100%; margin-top: 9px; overflow-wrap: anywhere; font-size: 12px; font-weight: 500; line-height: 1.5; }
.recipe-flow-compact .recipe-flow-connector:not(.recipe-flow-connector-vertical) { width: 20px; flex: 0 0 20px; }
.recipe-flow-compact .recipe-flow-connector:not(.recipe-flow-connector-vertical) svg { width: 20px; }
.recipe-flow-compact .recipe-flow-input, .recipe-flow-compact .recipe-flow-output { width: 88px; min-height: 50px; justify-content: flex-start; gap: 5px; border-color: transparent; padding: 7px 5px; background: #f5f8f7; color: #495c53; }
.recipe-flow-compact .recipe-flow-output { background: #faf6f1; color: #7c6246; }
.recipe-flow-compact .recipe-flow-input > svg, .recipe-flow-compact .recipe-flow-output > span:first-child { flex-shrink: 0; }
.recipe-flow-compact .recipe-flow-file-name { font-size: 11px; line-height: 1.5; }
.recipe-flow-vertical.recipe-flow-compact .recipe-flow-file { width: 100%; }
.recipe-flow-step.is-active, .recipe-flow-file.is-active { border-color: #6265dc; animation: flow-node-pulse 1.8s ease-in-out infinite; }
.recipe-flow-step.is-completed { border-color: #a5d6bb; }
.recipe-flow-step.is-warning { border-color: #e5c273; }
.recipe-flow-step.is-failed, .recipe-flow-file.is-failed { border-color: #ef7b7b; }
.recipe-flow-step.is-selected, .recipe-flow-file.is-selected { border-color: #6265dc; box-shadow: 0 0 0 2px #6265dc33; }
.flow-spinner { animation: flow-spin 1s linear infinite; }
@keyframes flow-travel { to { stroke-dashoffset: -16; } }
@keyframes flow-spin { to { transform: rotate(360deg); } }
@keyframes flow-node-pulse { 0%, 100% { box-shadow: 0 0 0 1px #6265dc18; } 50% { box-shadow: 0 0 0 4px #6265dc24; } }
@media (prefers-reduced-motion: reduce) {
  .recipe-flow-step, .recipe-flow-file, .recipe-flow-connector .flow-line, .flow-spinner { animation: none !important; transition: none !important; }
  .recipe-flow-step:hover { transform: none; }
}
</style>
