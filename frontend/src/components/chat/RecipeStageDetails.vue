<script setup lang="ts">
import { Check, ChevronRight, CircleAlert, FileSpreadsheet, LoaderCircle, ShieldCheck } from '@lucide/vue'
import { computed } from 'vue'

import type { RecipeProgress, StepState } from '@/composables/chatModel'
import { recipeStepFields, repairDescriptionFields } from '@/composables/recipeProcessDetails'
import { userFacingSummary, userFacingText } from '@/composables/timelinePresentation'

const props = defineProps<{ progress: RecipeProgress }>()
const checks = computed(() => props.progress.validation?.checks ?? [])
const STEP_LABELS: Record<StepState['status'], string> = {
  pending: '待处理', running: '处理中', success: '已完成', warning: '需核对',
  repaired: '已修正', repairing: '正在调整', failed: '未完成',
}

function stepExplanation(step: StepState): string {
  return userFacingSummary(props.progress.recipe?.steps.find((item) => item.id === step.step_id)?.explanation, '', 2400)
}

function repairs(step: StepState) {
  const records = step.repairs.length ? step.repairs : (step.report?.attempts ?? []).map((attempt) => ({ ...attempt, status: 'recorded' }))
  return records.map((repair) => ({
    ...repair,
    fields: repairDescriptionFields(repair.explanation),
    label: repair.status === 'trying' ? '正在制定修正方案' : repair.status === 'gave_up' ? '调整未完成'
      : repair.status === 'retry' ? '修正后重新执行' : '已记录的调整',
  }))
}
</script>

<template>
  <ol v-if="progress.details?.length" class="space-y-2 text-[13px]" aria-label="处理记录">
    <li v-for="(detail, index) in progress.details" :key="index" class="flex items-start gap-2 leading-5">
      <Check v-if="detail.status === 'ok'" class="mt-0.5 size-3.5 shrink-0 text-success" />
      <CircleAlert v-else-if="detail.status === 'error' || detail.status === 'warning'" class="mt-0.5 size-3.5 shrink-0" :class="detail.status === 'error' ? 'text-error' : 'text-warning'" />
      <span v-else class="ml-1 mr-0.5 mt-2 size-1.5 shrink-0 rounded-full bg-faint" />
      <div class="min-w-0 flex-1 space-y-1.5" :class="detail.status === 'error' ? 'text-error' : 'text-mute'">
        <details v-if="detail.fields?.length" class="group/process-detail">
          <summary class="flex cursor-pointer list-none items-start gap-1.5 rounded font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink [&::-webkit-details-marker]:hidden" :class="{ 'text-ink': detail.status !== 'error' }">
            <ChevronRight class="mt-1 size-3 shrink-0 transition group-open/process-detail:rotate-90" />
            <span class="min-w-0 whitespace-pre-line break-words">{{ detail.text }}</span>
          </summary>
          <dl class="mt-1.5 space-y-2 pl-4.5 text-xs leading-5">
            <div v-for="field in detail.fields" :key="field.label" class="space-y-0.5">
              <dt class="font-medium text-mute">{{ field.label }}</dt>
              <dd class="whitespace-pre-line break-words [overflow-wrap:anywhere]">{{ field.text }}</dd>
            </div>
          </dl>
        </details>
        <p v-else class="whitespace-pre-line break-words">{{ detail.text }}</p>
      </div>
    </li>
  </ol>
  <div v-else class="space-y-3 text-[13px]">
    <div v-if="progress.inputs.length" class="space-y-1.5">
      <p v-for="input in progress.inputs" :key="`${input.alias}:${input.sheet}`" class="flex items-start gap-2 leading-5 text-mute">
        <FileSpreadsheet class="mt-0.5 size-3.5 shrink-0 text-fun-green" />
        <span class="min-w-0 break-words">已读取 {{ input.filename }} · {{ input.sheet }} · {{ input.rows }} 行</span>
      </p>
    </div>

    <ol v-if="progress.steps.length" class="space-y-3" aria-label="处理步骤">
      <li v-for="step in progress.steps" :key="step.step_id" class="flex items-start gap-2">
        <LoaderCircle v-if="step.status === 'running' || step.status === 'repairing'" class="mt-0.5 size-3.5 shrink-0 animate-spin text-info" />
        <CircleAlert v-else-if="step.status === 'failed' || step.status === 'warning'" class="mt-0.5 size-3.5 shrink-0" :class="step.status === 'failed' ? 'text-error' : 'text-warning'" />
        <Check v-else-if="step.status === 'success' || step.status === 'repaired'" class="mt-0.5 size-3.5 shrink-0 text-success" />
        <span v-else class="mt-1 size-3.5 shrink-0 rounded-full border border-line" />
        <details class="group/recipe-step min-w-0 flex-1 leading-5">
          <summary class="flex cursor-pointer list-none items-start gap-1.5 rounded focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink [&::-webkit-details-marker]:hidden">
            <ChevronRight class="mt-1 size-3 shrink-0 transition group-open/recipe-step:rotate-90" />
            <span class="flex min-w-0 flex-1 flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
              <span class="font-medium">{{ userFacingText(step.name, '处理数据', 100) }}</span>
              <span class="text-[11px] text-faint">{{ STEP_LABELS[step.status] }}</span>
            </span>
          </summary>
          <div class="mt-1.5 space-y-1 pl-4.5">
            <p v-if="step.description" class="whitespace-pre-line break-words text-mute">{{ userFacingSummary(step.description, '', 2400) }}</p>
            <dl v-if="recipeStepFields(progress, step.step_id).length" class="space-y-1 text-xs text-mute">
              <div v-for="field in recipeStepFields(progress, step.step_id)" :key="field.label">
                <dt class="font-medium">{{ field.label }}</dt>
                <dd class="whitespace-pre-line break-words [overflow-wrap:anywhere]">{{ field.text }}</dd>
              </div>
            </dl>
            <p v-if="stepExplanation(step)" class="whitespace-pre-line break-words text-xs text-mute">处理依据：{{ stepExplanation(step) }}</p>
            <p v-if="step.report?.rows_out !== undefined" class="text-xs text-mute">整理得到 {{ step.report.rows_out }} 条记录</p>
            <ol v-if="repairs(step).length" class="space-y-2 border-l border-line pl-3 text-xs text-mute" aria-label="调整记录">
              <li v-for="(repair, index) in repairs(step)" :key="index" class="space-y-0.5">
                <p class="font-medium">{{ repair.label }}</p>
                <p v-if="repair.status === 'trying'" class="whitespace-pre-line break-words">正在核对问题并制定修正方案，尚未收到具体修改内容。</p>
                <dl v-else class="space-y-1">
                  <div v-for="field in repair.fields" :key="field.label">
                    <dt class="font-medium">{{ field.label }}</dt>
                    <dd class="whitespace-pre-line break-words [overflow-wrap:anywhere]">{{ field.text }}</dd>
                  </div>
                </dl>
              </li>
            </ol>
            <ul v-if="step.report?.checks?.length" class="space-y-1 text-xs text-mute" aria-label="步骤校验">
              <li v-for="(check, index) in step.report.checks" :key="index" :class="{ 'text-warning': !check.ok }">{{ check.ok ? '通过' : '需核对' }}：{{ userFacingText(check.message, '', 1200) }}</li>
            </ul>
            <p v-if="step.status === 'failed' && step.error" class="text-xs text-error">{{ userFacingText(step.error, '这一步未能完成，需调整处理方式后重试。') }}</p>
          </div>
        </details>
      </li>
    </ol>

    <div v-if="checks.length || progress.verify || progress.validation" class="space-y-2 border-t border-line/70 pt-3">
      <p v-if="progress.validation" class="flex items-start gap-2 text-mute">
        <ShieldCheck class="mt-0.5 size-3.5 shrink-0" :class="progress.validation.status === 'fail' ? 'text-error' : 'text-success'" />
        <span>结果校验：{{ userFacingText(progress.validation.summary, '已完成检查') }}</span>
      </p>
      <ul v-if="checks.length" class="space-y-1 pl-5 text-xs leading-5 text-mute">
        <li v-for="(check, index) in checks" :key="index">{{ userFacingText(check.description, '检查处理结果') }} · {{ userFacingText(check.message, check.status === 'pass' ? '通过' : check.status === 'fail' ? '未通过' : '需核对') }}</li>
      </ul>
      <p v-if="progress.verify" class="pl-5 text-xs leading-5 text-mute">样例比对：{{ userFacingText(progress.verify.summary, progress.verify.status === 'pass' ? '与样例一致' : '存在差异，需要核对') }}</p>
    </div>
  </div>
</template>
