<script setup lang="ts">
import { Check, CircleAlert, FileSpreadsheet, LoaderCircle, ShieldCheck } from '@lucide/vue'
import { computed } from 'vue'

import type { RecipeProgress, StepState } from '@/composables/chatModel'
import { userFacingText } from '@/composables/timelinePresentation'

const props = defineProps<{ progress: RecipeProgress }>()
const checks = computed(() => props.progress.validation?.checks ?? [])
const STEP_LABELS: Record<StepState['status'], string> = {
  pending: '待处理', running: '处理中', success: '已完成', warning: '需核对',
  repaired: '已修正', repairing: '正在调整', failed: '未完成',
}
</script>

<template>
  <div class="space-y-3 text-[13px]">
    <div v-if="progress.inputs.length" class="space-y-1.5">
      <p v-for="input in progress.inputs" :key="`${input.alias}:${input.sheet}`" class="flex items-start gap-2 leading-5 text-mute">
        <FileSpreadsheet class="mt-0.5 size-3.5 shrink-0 text-fun-green" />
        <span class="min-w-0 break-words">已读取 {{ input.filename }} · {{ input.sheet }} · {{ input.rows }} 行</span>
      </p>
    </div>

    <ol v-if="progress.steps.length" class="space-y-3" aria-label="处理步骤">
      <li v-for="(step, index) in progress.steps" :key="step.step_id" class="flex items-start gap-2">
        <LoaderCircle v-if="step.status === 'running' || step.status === 'repairing'" class="mt-0.5 size-3.5 shrink-0 animate-spin text-info" />
        <CircleAlert v-else-if="step.status === 'failed' || step.status === 'warning'" class="mt-0.5 size-3.5 shrink-0" :class="step.status === 'failed' ? 'text-error' : 'text-warning'" />
        <Check v-else-if="step.status === 'success' || step.status === 'repaired'" class="mt-0.5 size-3.5 shrink-0 text-success" />
        <span v-else class="mt-1 size-3.5 shrink-0 rounded-full border border-line" />
        <div class="min-w-0 flex-1 space-y-1 leading-5">
          <div class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <p class="font-medium">{{ index + 1 }}. {{ userFacingText(step.name, '处理数据', 100) }}</p>
            <span class="text-[11px] text-faint">{{ STEP_LABELS[step.status] }}</span>
          </div>
          <p v-if="step.description" class="break-words text-mute">{{ userFacingText(step.description) }}</p>
          <p v-if="step.formula_name && step.formula_name !== step.formula" class="text-xs text-faint">使用工具：{{ userFacingText(step.formula_name, '数据处理工具', 80) }}</p>
          <p v-if="step.report?.rows_out !== undefined" class="text-xs text-mute">输出 {{ step.report.rows_out }} 行<template v-if="step.output"> · {{ step.output }}</template></p>
          <p v-if="step.repairs.length" class="text-xs text-mute">调整了 {{ step.repairs.length }} 次{{ step.status === 'repaired' ? '，已完成修正' : '' }}</p>
          <p v-if="step.status === 'failed' && step.error" class="text-xs text-error">{{ userFacingText(step.error, '这一步未能完成，需调整处理方式后重试。') }}</p>
        </div>
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
