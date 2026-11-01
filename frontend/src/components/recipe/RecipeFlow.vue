<script setup lang="ts">
/**
 * Recipe 流程图：输入表 → 每一步（公式 + 人话说明）→ 输出 sheet。
 * 机器执行的是公式和 SQL，这里只给人看「人话卡片」；执行时显示每一步的实时状态。
 */
import { Check, CircleAlert, LoaderCircle, TriangleAlert, Wrench } from '@lucide/vue'
import { computed } from 'vue'

import type { RecipeFlowData } from '@/api/types'
import FunObject, { type FunKind } from '@/components/brand/FunObject.vue'
import { formulaArt, percent, type StepState } from '@/composables/chatModel'

const props = withDefaults(
  defineProps<{ flow: RecipeFlowData; states?: StepState[]; direction?: 'horizontal' | 'vertical'; activeStep?: number | null; compact?: boolean }>(),
  { states: () => [], direction: 'horizontal', activeStep: null, compact: false },
)
const emit = defineEmits<{ select: [stepId: number] }>()

const stateById = computed(() => Object.fromEntries(props.states.map((s) => [s.step_id, s])))
const STATUS_LABELS: Record<StepState['status'], string> = {
  pending: '等待执行', running: '执行中', repairing: '修复中', success: '已完成',
  repaired: '已修复', warning: '有提示', failed: '执行失败',
}

function state(id: number): StepState | undefined {
  return stateById.value[id]
}

function stateLabel(id: number): string {
  const status = state(id)?.status
  return status ? STATUS_LABELS[status] : ''
}

function art(formula: string, kind: string): FunKind {
  return formulaArt(formula, kind) as FunKind
}

function rowsText(s?: StepState): string {
  const r = s?.report
  if (!r || r.rows_out === undefined) return ''
  const first = r.rows_in ? Object.values(r.rows_in)[0] : undefined
  return first !== undefined ? `${first} → ${r.rows_out} 行` : `${r.rows_out} 行`
}

const running = computed(() => props.states.some((s) => s.status === 'running' || s.status === 'repairing'))
</script>

<template>
  <!-- 横向：卡片、客户页 -->
  <div v-if="direction === 'horizontal'" class="overflow-x-auto pb-2" :class="{ 'recipe-flow-compact': compact }" role="group" aria-label="处理流程图" tabindex="0">
    <div class="recipe-flow-row flex min-w-max items-stretch gap-0">
      <div class="flex flex-col justify-center gap-2 pr-1">
        <div
          v-for="inp in flow.inputs"
          :key="inp.alias"
          class="recipe-flow-input flex items-center gap-2 rounded-xl border border-line bg-white py-1.5 pl-1.5 pr-3"
          :title="inp.description"
        >
          <FunObject kind="sheet" :size="compact ? 28 : 26" />
          <div class="leading-tight">
            <div class="recipe-flow-file-name text-[13px] font-medium">{{ inp.alias }}</div>
            <div v-if="!compact && inp.file_pattern" class="font-mono text-[10px] text-faint">{{ inp.file_pattern }}</div>
          </div>
        </div>
      </div>

      <template v-for="step in flow.steps" :key="step.id">
        <div class="recipe-flow-connector flex w-9 items-center" aria-hidden="true">
          <svg width="36" height="12" viewBox="0 0 36 12" class="text-[#c9c9cf]">
            <line x1="2" y1="6" x2="30" y2="6" stroke="currentColor" stroke-width="1.6" :class="{ 'flow-dash': state(step.id)?.status === 'running' }" />
            <path d="M28 2l5 4-5 4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
          </svg>
        </div>
        <button
          class="recipe-flow-step group relative flex w-[212px] flex-col rounded-2xl border bg-white p-3 text-left transition hover:-translate-y-0.5 hover:shadow-[0_10px_24px_-14px_rgba(0,0,0,0.3)]"
          :class="[
            activeStep === step.id ? 'border-ink' : 'border-line',
            state(step.id)?.status === 'failed' ? 'border-error/60' : '',
            state(step.id)?.status === 'running' ? 'border-info/60' : '',
          ]"
          :aria-label="`查看步骤：${step.name}${state(step.id) ? `，${stateLabel(step.id)}` : ''}`"
          :title="compact ? step.description : undefined"
          @click="emit('select', step.id)"
        >
          <span v-if="compact && state(step.id)" class="absolute right-1.5 top-1.5 flex size-4 items-center justify-center rounded-full bg-white/90" role="img" :aria-label="stateLabel(step.id)" :title="stateLabel(step.id)">
            <LoaderCircle v-if="state(step.id)!.status === 'running'" class="size-3 animate-spin text-info" />
            <Wrench v-else-if="state(step.id)!.status === 'repairing'" class="size-3 animate-pulse text-fun-purple" />
            <Check v-else-if="state(step.id)!.status === 'success'" class="size-3 text-success" />
            <Wrench v-else-if="state(step.id)!.status === 'repaired'" class="size-3 text-fun-purple" />
            <TriangleAlert v-else-if="state(step.id)!.status === 'warning'" class="size-3 text-warning" />
            <CircleAlert v-else-if="state(step.id)!.status === 'failed'" class="size-3 text-error" />
            <span v-else class="size-1.5 rounded-full bg-faint" />
          </span>
          <div class="recipe-flow-step-art flex items-center gap-2">
            <span v-if="!compact" class="flex size-5 items-center justify-center rounded-full bg-ink text-[11px] font-semibold text-white">{{ step.id }}</span>
            <FunObject :kind="art(step.formula, step.formula_kind)" :size="compact ? 34 : 22" />
            <span v-if="!compact" class="tag ml-auto" :class="step.formula_kind === 'customer' || step.formula === 'custom_sql' ? '!bg-fun-purple/10 !text-fun-purple' : ''">
              {{ step.formula_kind === 'customer' || step.formula === 'custom_sql' ? '客户独有' : '内置公式' }}
            </span>
          </div>
          <div class="recipe-flow-step-name mt-2 text-[13.5px] font-semibold leading-snug">{{ step.name }}</div>
          <div v-if="!compact" class="mt-1 line-clamp-2 text-xs leading-relaxed text-mute">{{ step.description }}</div>
          <div v-if="!compact" class="mt-auto flex items-center gap-1.5 pt-2.5 text-[11px] text-faint">
            <template v-if="state(step.id)">
              <LoaderCircle v-if="state(step.id)!.status === 'running'" class="size-3.5 animate-spin text-info" />
              <Wrench v-else-if="state(step.id)!.status === 'repairing'" class="size-3.5 animate-pulse text-fun-purple" />
              <Check v-else-if="state(step.id)!.status === 'success'" class="size-3.5 text-success" />
              <Wrench v-else-if="state(step.id)!.status === 'repaired'" class="size-3.5 text-fun-purple" />
              <TriangleAlert v-else-if="state(step.id)!.status === 'warning'" class="size-3.5 text-warning" />
              <CircleAlert v-else-if="state(step.id)!.status === 'failed'" class="size-3.5 text-error" />
              <span v-if="state(step.id)!.status === 'repairing'" class="text-fun-purple">AI 修复中…</span>
              <span v-else>{{ rowsText(state(step.id)) }}</span>
              <span v-if="state(step.id)!.report?.metrics?.match_rate !== undefined" class="ml-auto">
                匹配 {{ percent(state(step.id)!.report!.metrics!.match_rate as number, 0) }}
              </span>
            </template>
            <span v-else class="font-mono">→ {{ step.output }}</span>
          </div>
        </button>
      </template>

      <div class="recipe-flow-connector flex w-9 items-center" aria-hidden="true">
        <svg width="36" height="12" viewBox="0 0 36 12" class="text-[#c9c9cf]">
          <line x1="2" y1="6" x2="30" y2="6" stroke="currentColor" stroke-width="1.6" :class="{ 'flow-dash': running }" />
          <path d="M28 2l5 4-5 4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
        </svg>
      </div>
      <div class="flex flex-col justify-center gap-2">
        <div v-for="out in flow.outputs" :key="out.name" class="recipe-flow-output flex items-center gap-2 rounded-xl bg-ink py-1.5 pl-1.5 pr-3 text-white">
          <span class="flex size-[26px] items-center justify-center rounded-lg bg-white/15"><FunObject kind="plane" :size="18" /></span>
          <div class="leading-tight">
            <div class="recipe-flow-file-name text-[13px] font-medium">{{ out.name }}</div>
            <div v-if="!compact" class="text-[10px] text-white/50">输出 sheet</div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- 纵向：右侧执行明细面板 -->
  <ol v-else class="relative space-y-2 pl-1">
    <li v-for="step in flow.steps" :key="step.id" class="relative flex gap-3">
      <div class="flex flex-col items-center">
        <span
          class="z-10 flex size-6 shrink-0 items-center justify-center rounded-full border text-[11px] font-semibold"
          :class="{
            'border-ink bg-ink text-white': state(step.id)?.status === 'success' || state(step.id)?.status === 'warning' || state(step.id)?.status === 'repaired',
            'border-info bg-info/10 text-info': state(step.id)?.status === 'running' || state(step.id)?.status === 'repairing',
            'border-error bg-error/10 text-error': state(step.id)?.status === 'failed',
            'border-line bg-white text-faint': !state(step.id) || state(step.id)?.status === 'pending',
          }"
        >
          <LoaderCircle v-if="state(step.id)?.status === 'running'" class="size-3.5 animate-spin" />
          <Wrench v-else-if="state(step.id)?.status === 'repairing'" class="size-3.5" />
          <template v-else>{{ step.id }}</template>
        </span>
        <span class="w-px flex-1 bg-line" />
      </div>
      <button class="mb-1 min-w-0 flex-1 rounded-xl px-2 py-1 text-left hover:bg-paper" @click="emit('select', step.id)">
        <div class="flex items-center gap-1.5">
          <span class="truncate text-[13px] font-medium">{{ step.name }}</span>
          <span class="tag shrink-0" :class="step.formula_kind === 'customer' || step.formula === 'custom_sql' ? '!bg-fun-purple/10 !text-fun-purple' : ''">
            {{ step.formula_kind === 'customer' || step.formula === 'custom_sql' ? '独有' : '内置' }}
          </span>
        </div>
        <div class="line-clamp-2 text-xs text-mute">{{ step.description }}</div>
        <div v-if="state(step.id)?.report" class="mt-0.5 text-[11px] text-faint">
          {{ rowsText(state(step.id)) }}
          <template v-if="state(step.id)?.report?.warnings?.length"> · {{ state(step.id)!.report!.warnings!.length }} 条提示</template>
        </div>
        <div v-else-if="state(step.id)?.error" class="mt-0.5 line-clamp-2 text-[11px] text-error">{{ state(step.id)!.error }}</div>
      </button>
    </li>
  </ol>
</template>

<style scoped>
.recipe-flow-compact { padding: 6px 2px 10px; }
.recipe-flow-compact:focus-visible { outline: 2px solid #a6a2df; outline-offset: 4px; border-radius: 12px; }
.recipe-flow-compact .recipe-flow-row { align-items: center; justify-content: space-between; gap: 0; min-width: max-content; }
.recipe-flow-compact .recipe-flow-step { width: 76px; min-height: 102px; flex-shrink: 0; align-items: center; justify-content: center; padding: 12px 7px; border: 0; background: #f8f8fc; text-align: center; box-shadow: 0 2px 8px #34345205; }
.recipe-flow-compact .recipe-flow-step:hover { background: #f0effb; box-shadow: 0 5px 14px #6265dc12; }
.recipe-flow-compact .recipe-flow-step:focus-visible { outline: 2px solid #8581cc; outline-offset: 3px; }
.recipe-flow-compact .recipe-flow-step-art { justify-content: center; }
.recipe-flow-compact .recipe-flow-step-name { width: 100%; margin-top: 9px; overflow-wrap: anywhere; font-size: 12px; font-weight: 500; line-height: 1.5; }
.recipe-flow-compact .recipe-flow-connector { width: 20px; flex: 0 0 20px; }
.recipe-flow-compact .recipe-flow-connector svg { width: 20px; }
.recipe-flow-compact .recipe-flow-input, .recipe-flow-compact .recipe-flow-output { width: 88px; min-height: 50px; justify-content: flex-start; gap: 5px; border: 0; padding: 7px 5px; background: #f5f8f7; color: #495c53; }
.recipe-flow-compact .recipe-flow-output { background: #faf6f1; color: #7c6246; }
.recipe-flow-compact .recipe-flow-input > svg, .recipe-flow-compact .recipe-flow-output > span { flex-shrink: 0; }
.recipe-flow-compact .recipe-flow-file-name { overflow-wrap: anywhere; font-size: 11px; line-height: 1.5; }
</style>
