<script setup lang="ts">
/**
 * 对话里的处理卡片：实时展示规划、执行（含自动修复）、校验和比对，按请求展示流程保存结果。
 * 执行已有流程（重复执行）也用这张卡片。
 */
import { ChevronRight, CircleAlert, Download, ExternalLink, Eye, FileSpreadsheet, LoaderCircle, MessageCircleQuestion, ShieldCheck } from '@lucide/vue'
import { computed, ref } from 'vue'

import { fileApi } from '@/api'
import { errorText } from '@/api/http'
import type { FileInfo } from '@/api/types'
import FunObject from '@/components/brand/FunObject.vue'
import Mascot from '@/components/brand/Mascot.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import type { RecipeProgress, StepState } from '@/composables/chatModel'
import { recipeFailure } from '@/composables/messagePresentation'
import { userFacingText } from '@/composables/timelinePresentation'
import { useToast } from '@/composables/useToast'

import DiffCards from './DiffCards.vue'
import RecipeFlow from './RecipeFlow.vue'
import StepDetail from './StepDetail.vue'
import VerifyBadge from './VerifyBadge.vue'

const props = defineProps<{ progress: RecipeProgress; customerId?: string | null; hideDetails?: boolean }>()
const emit = defineEmits<{ previewFile: [file: FileInfo] }>()
const toast = useToast()
const openingFile = ref(false)

async function previewFile() {
  const file = props.progress.file
  if (!file || openingFile.value) return
  openingFile.value = true
  try {
    emit('previewFile', await fileApi.get(file.file_id))
  } catch (error) {
    toast.error(errorText(error))
  } finally {
    openingFile.value = false
  }
}

const selected = ref<StepState | null>(null)
const showEvidence = ref(false)
const showDetail = ref(false) // 流程图、校验、比对默认收起，财务只需要看到结果和下载

const MODE_LABEL = { learn: '规划处理步骤', revise: '调整处理步骤', replay: '执行已有流程' }
const STATUS_LABEL: Record<string, string> = {
  thinking: '规划中', running: '执行中', verifying: '比对中', refining: '修正中', done: '完成', failed: '没有完成',
}

const flow = computed(() => {
  if (props.progress.flow) return props.progress.flow
  if (!props.progress.steps.length) return null
  return {
    inputs: props.progress.inputs.map((i) => ({ alias: i.alias, description: i.filename, file_pattern: '' })),
    steps: props.progress.steps.map((s) => ({
      id: s.step_id, name: s.name, action: '', formula: s.formula, formula_kind: s.formula_kind as 'builtin' | 'customer',
      description: s.description, output: s.output, inputs: [],
    })),
    outputs: (props.progress.outputs ?? []).map((o) => ({ name: o.sheet, source: '' })),
  }
})

const busy = computed(() => ['thinking', 'running', 'verifying', 'refining'].includes(props.progress.status))
const failure = computed(() => {
  const value = recipeFailure(props.progress)
  return value && { ...value, summary: userFacingText(value.summary, '本次处理未完成，需要调整处理方式后重试。', 400) }
})
const renames = computed(() => props.progress.inputs.filter((i) => Object.keys(i.renames).length || i.warnings.length))
const pillStatus = computed(() => {
  if (busy.value) return 'running'
  if (props.progress.status === 'failed') return 'failed'
  return props.progress.runStatus === 'warning' ? 'warning' : 'success'
})
const doneCount = computed(() => props.progress.steps.filter((s) => ['success', 'warning', 'repaired'].includes(s.status)).length)
const currentStep = computed(() => props.progress.steps.find((s) => s.status === 'running' || s.status === 'repairing'))
const busyText = computed(() => {
  const p = props.progress
  if (p.status === 'verifying') return '正在和你给的结果样例比对…'
  if (p.status === 'refining') return `发现有出入，正在修正（第 ${p.round} 轮）…`
  if (currentStep.value) return `第 ${doneCount.value + 1}/${p.steps.length} 步 · ${currentStep.value.name}`
  return '正在理解需求与整理处理步骤…'
})
const recipeStep = (id: number) => props.progress.recipe?.steps.find((s) => s.id === id)
</script>

<template>
  <div class="card-soft animate-rise overflow-hidden">
    <!-- 标题 -->
    <div class="flex items-center gap-3 border-b border-line bg-paper/60 px-4 py-3">
      <FunObject kind="blocks" :size="30" />
      <div class="min-w-0 flex-1">
        <div class="flex items-center gap-2">
          <span class="truncate text-[14px] font-semibold">{{ progress.recipeName || progress.saved?.name || MODE_LABEL[progress.mode] }}</span>
          <span v-if="progress.deleted" class="tag">流程已删除</span>
          <span v-else-if="progress.saved" class="tag">v{{ progress.saved.version }}</span>
          <span v-else-if="progress.mode === 'replay' && progress.recipeVersion" class="tag">v{{ progress.recipeVersion }}</span>
        </div>
        <div class="text-xs text-mute">
          {{ MODE_LABEL[progress.mode] }}<template v-if="progress.round > 1"> · 第 {{ progress.round }} 轮</template>
        </div>
      </div>
      <StatusPill :status="pillStatus" :label="STATUS_LABEL[progress.status]" />
    </div>

    <div v-if="!hideDetails || busy || progress.questions?.length || failure" class="space-y-3 px-4 py-3.5">
      <!-- 进行中：一条细进度，不展开细节 -->
      <div v-if="busy" class="space-y-2">
        <div class="flex items-center gap-3">
          <Mascot v-if="!progress.steps.length" :size="36" mood="thinking" :shadow="false" />
          <div class="min-w-0 flex-1 truncate text-sm text-mute">{{ busyText }}</div>
        </div>
        <div v-if="progress.steps.length" class="h-1.5 overflow-hidden rounded-full bg-paper">
          <div class="h-full rounded-full bg-ink transition-all duration-500" :style="{ width: `${Math.round((doneCount / progress.steps.length) * 100)}%` }" />
        </div>
      </div>

      <!-- 结束：一句话小结 -->
      <p v-else-if="progress.runSummary && !failure" class="text-[13px] leading-relaxed text-mute">{{ userFacingText(progress.runSummary, '流程已执行结束，请查看处理结果。', 600) }}</p>

      <!-- 需要确认的问题 -->
      <div v-if="progress.questions?.length" class="rounded-xl bg-fun-yellow/15 px-3 py-2.5 text-[13px]">
        <div class="mb-1 flex items-center gap-1.5 font-medium text-[#8a6400]"><MessageCircleQuestion class="size-4" />需要你确认</div>
        <ul class="list-disc space-y-0.5 pl-5 text-[#6b5000]">
          <li v-for="q in progress.questions" :key="q.question">{{ q.question }}<span v-if="q.options?.length" class="text-[#a68a3a]">（{{ q.options.join(' / ') }}）</span></li>
        </ul>
      </div>

      <!-- 失败 -->
      <div v-if="failure" class="flex gap-2.5 text-[13px] leading-relaxed">
        <CircleAlert class="mt-0.5 size-4 shrink-0 text-error" />
        <div class="min-w-0 space-y-1">
          <p class="text-ink">{{ failure.summary }}</p>
          <p class="text-mute">{{ failure.nextStep }}</p>
        </div>
      </div>

      <!-- 细节：默认收起 -->
      <div v-if="!hideDetails && (flow || progress.verify || progress.validation || progress.evidence?.length || failure)">
        <button class="flex items-center gap-1 text-xs text-mute hover:text-ink" :aria-expanded="showDetail" @click="showDetail = !showDetail">
          <ChevronRight class="size-3.5 transition" :class="{ 'rotate-90': showDetail }" />{{ showDetail ? '收起处理流程' : failure ? '查看处理流程和错误详情' : '查看处理流程和校验' }}
        </button>
        <div v-if="showDetail" class="mt-3 space-y-3">
          <div v-if="failure?.detail" class="rounded-xl bg-paper px-3 py-2.5 text-xs leading-5">
            <h4 class="mb-1 font-medium">错误详情</h4>
            <p class="whitespace-pre-wrap break-words text-mute [overflow-wrap:anywhere]">{{ failure.detail }}</p>
          </div>
          <!-- 证据：输入 vs 人工结果 -->
          <div v-if="progress.evidence?.length">
            <button class="btn-text -ml-2 text-xs" @click="showEvidence = !showEvidence">
              🔍 比对了输入和人工结果，发现 {{ progress.evidence.reduce((n, c) => n + c.highlights.length, 0) }} 处关键变化
              <span class="text-faint">{{ showEvidence ? '收起' : '展开' }}</span>
            </button>
            <DiffCards v-if="showEvidence" class="mt-2" :cards="progress.evidence" />
          </div>

          <!-- 输入识别 -->
          <div v-if="renames.length" class="space-y-1">
            <div v-for="inp in renames" :key="inp.alias" class="flex flex-wrap items-center gap-1.5 text-xs text-mute">
              <FileSpreadsheet class="size-3.5" />
              <span class="font-medium text-ink">{{ inp.alias }}</span> ← {{ inp.filename }}
              <span v-for="(std, raw) in inp.renames" :key="raw" class="rounded bg-info/10 px-1.5 text-info">{{ raw }} → {{ std }}</span>
              <span v-for="w in inp.warnings" :key="w" class="rounded bg-warning/10 px-1.5 text-[#8a5a00]">{{ w }}</span>
            </div>
          </div>

          <!-- 流程图 -->
          <RecipeFlow v-if="flow" :flow="flow" :states="progress.steps" @select="(id) => (selected = progress.steps.find((s) => s.step_id === id) ?? null)" />

          <!-- 比对 / 校验 -->
          <VerifyBadge v-if="progress.verify" :report="progress.verify" :rounds="progress.verifyHistory.length" />
          <div v-if="progress.validation" class="flex items-center gap-2 text-xs text-mute">
            <ShieldCheck class="size-4" :class="progress.validation.status === 'fail' ? 'text-error' : progress.validation.status === 'warn' ? 'text-warning' : 'text-success'" />
            运行校验：{{ progress.validation.summary }}
          </div>
        </div>
      </div>
    </div>

    <!-- 操作 -->
    <div v-if="progress.file || progress.saved" class="flex flex-wrap items-center gap-2 border-t border-line px-4 py-3">
      <button v-if="progress.file" type="button" class="btn-line py-2" :disabled="openingFile" @click="previewFile"><LoaderCircle v-if="openingFile" class="size-4 animate-spin" /><Eye v-else class="size-4" />查看结果</button>
      <a v-if="progress.file" :href="progress.file.download_url" class="btn-ink py-2"><Download class="size-4" />下载结果 Excel</a>
      <span v-if="progress.saved" class="flex items-center gap-1.5 text-xs text-mute">
        流程已保存为 v{{ progress.saved.version }} · <StatusPill :status="progress.saved.status" />
      </span>
      <RouterLink v-if="progress.saved && customerId" :to="{ name: 'customer', params: { id: customerId }, query: { tab: 'recipes' } }" class="btn-text ml-auto">
        在客户页查看<ExternalLink class="size-3.5" />
      </RouterLink>
    </div>

    <StepDetail
      v-if="selected && !hideDetails"
      :step-id="selected.step_id"
      :name="selected.name"
      :formula-name="selected.formula_name"
      :formula-kind="selected.formula_kind"
      :description="selected.description"
      :explanation="recipeStep(selected.step_id)?.explanation"
      :status="selected.status"
      :report="selected.report"
      :params="recipeStep(selected.step_id)?.params"
      :run-id="progress.runId"
      :error="selected.error"
      :repairs="selected.repairs"
      @close="selected = null"
    />
  </div>
</template>
