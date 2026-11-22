<script setup lang="ts">
/**
 * 一次运行的执行详情：顶部状态卡（含报错/异常提示）→ 输出文件（查看/下载）→ 处理节点（可点开单步详情或重跑）
 * → 输入文件。全部校验通过时不展示校验明细，仅在存在异常时突出提示。
 */
import { Check, ChevronRight, CircleX, Download, Eye, FileSpreadsheet, LoaderCircle, Sheet, TriangleAlert, X } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { fileApi, runApi } from '@/api'
import { errorText } from '@/api/http'
import type { RunDetail, StepReport } from '@/api/types'
import MascotLoader from '@/components/brand/MascotLoader.vue'
import RecipeIcon from '@/components/brand/RecipeIcon.vue'
import RunStatusMascot from '@/components/brand/RunStatusMascot.vue'
import ExecutionStatusNotice from '@/components/chat/ExecutionStatusNotice.vue'
import BaseModal from '@/components/common/BaseModal.vue'
import StepDetail from '@/components/recipe/StepDetail.vue'
import { duration, type UiMessage } from '@/composables/chatModel'
import { runDeliveryFiles } from '@/composables/deliveryFiles'
import { readExecutionState } from '@/composables/executionStatus'
import { useFilePreview } from '@/composables/useFilePreview'
import { useToast } from '@/composables/useToast'

const props = defineProps<{ runId: string; sequence?: number; elevated?: boolean }>()
const emit = defineEmits<{ close: []; changed: [] }>()
const toast = useToast()

const run = ref<RunDetail | null>(null)
const activeRunId = ref(props.runId)
const selected = ref<StepReport | null>(null)
const rerunning = ref(false)
const log = ref<string[]>([])
const openFilePreview = useFilePreview()
let loadVersion = 0
let mounted = true
onBeforeUnmount(() => { mounted = false; loadVersion++ })

async function load() {
  const version = ++loadVersion
  const runId = activeRunId.value
  try {
    const data = await runApi.get(runId)
    if (!mounted || version !== loadVersion || activeRunId.value !== runId) return
    run.value = data.run
  } catch (err) {
    if (mounted && version === loadVersion) toast.error(errorText(err))
  }
}
onMounted(load)
watch(() => props.runId, (id) => {
  activeRunId.value = id
  selected.value = null
  run.value = null
  void load()
})

// ---- 概况 ----
const pad = (n: number) => String(n).padStart(2, '0')
const startedAt = computed(() => {
  if (!run.value) return ''
  const date = new Date(run.value.created_at)
  if (Number.isNaN(date.getTime())) return run.value.created_at.replace('T', ' ').slice(0, 16)
  return `${date.getMonth() + 1}月${date.getDate()}日 ${pad(date.getHours())}:${pad(date.getMinutes())}`
})
const executionMessage = computed<UiMessage | null>(() => {
  const execution = readExecutionState(run.value?.validation?.execution)
  return run.value && execution ? { key: run.value.run_id, role: 'assistant', content: '', meta: {},
    streaming: execution.status === 'running', timeline: [], artifacts: [], execution } : null
})
const errorMessage = computed(() => (!executionMessage.value && run.value?.error) || '')
const checks = computed(() => run.value?.validation?.checks ?? [])
const failedChecks = computed(() => checks.value.filter((check) => check.status === 'fail' || check.status === 'warn'))

// ---- 处理节点 ----
const STEP_STATUS_TEXT: Record<string, string> = {
  pending: '等待中',
  running: '执行中',
  repairing: '修复中',
  success: '已完成',
  repaired: '已自动修复',
  warning: '有提示',
  failed: '失败',
}
const steps = computed(() => (run.value?.steps ?? []).filter((step) => step.step_id).map((step) => {
  const stepChecks = step.checks ?? []
  const failed = stepChecks.filter((check) => !check.ok)
  const status = step.status || 'success'
  return {
    step,
    status,
    statusText: STEP_STATUS_TEXT[status] ?? status,
    failed,
  }
}))
const outputFiles = computed(() => runDeliveryFiles(run.value))

type BannerTone = 'running' | 'success' | 'warning' | 'error'
const banner = computed<{ tone: BannerTone; title: string; desc: string }>(() => {
  const current = run.value
  if (!current) return { tone: 'running', title: '', desc: '' }
  const status = current.status
  if (status === 'running') {
    return { tone: 'running', title: '正在执行…', desc: steps.value.length ? `共 ${steps.value.length} 个处理节点` : '' }
  }
  if (['failed', 'escalated'].includes(status)) {
    const failedStep = steps.value.find((item) => item.status === 'failed')
    return {
      tone: 'error',
      title: status === 'escalated' ? '执行失败，需人工处理' : '执行失败',
      desc: failedStep ? `节点「${failedStep.step.name}」执行出错` : '请查看下方报错信息',
    }
  }
  if (['cancelled', 'interrupted', 'incomplete'].includes(status)) {
    const title = status === 'cancelled' ? '已停止执行' : status === 'interrupted' ? '执行已中断' : '执行未完成'
    return { tone: 'warning', title, desc: '' }
  }
  const summaryParts = [
    steps.value.length ? `${steps.value.length} 个节点全部完成` : '',
    outputFiles.value.length ? `已生成 ${outputFiles.value.length} 个结果文件` : '',
  ].filter(Boolean)
  if (status === 'warning' || failedChecks.value.length) {
    return {
      tone: 'warning',
      title: '执行完成，存在提示',
      desc: summaryParts.join(' · ') || '请留意下方提示内容',
    }
  }
  return {
    tone: 'success',
    title: status === 'repaired' ? '执行成功（已自动修复）' : '执行成功',
    desc: summaryParts.join(' · ') || '流程已执行完成',
  }
})

async function rerun(step: StepReport, params: Record<string, unknown>) {
  if (rerunning.value) return
  const sourceRunId = run.value?.run_id ?? activeRunId.value
  selected.value = null
  rerunning.value = true
  log.value = [`从第 ${step.step_id} 步重跑…`]
  const handle = runApi.rerun(sourceRunId, step.step_id, params, (e) => {
    if (e.type === 'recipe_run_start') activeRunId.value = e.run_id
    if (e.type === 'recipe_step_done') log.value.push(`第 ${e.step_id} 步完成：${e.rows_out} 行`)
    if (e.type === 'recipe_step_error') log.value.push(`第 ${e.step_id} 步出错：${e.error}`)
    if (e.type === 'recipe_run_done') log.value.push(`结束：${e.summary}`)
    if (e.type === 'recipe_saved') log.value.push(`参数修改已保存为流程 v${e.version}`)
  })
  try {
    await handle.done
  } catch (err) {
    log.value.push(errorText(err))
  } finally {
    rerunning.value = false
    await load()
    emit('changed')
  }
}
</script>

<template>
  <BaseModal width="max-w-3xl" :elevated="elevated" @close="emit('close')">
    <template #title>
      <div class="flex min-w-0 items-center gap-2.5">
        <RunStatusMascot v-if="run" :status="run.status" :size="30" />
        <RecipeIcon v-else kind="workflow" :size="28" />
        <h3 class="flex min-w-0 items-baseline gap-1.5 text-[15px] font-semibold leading-snug text-ink">
          <span class="shrink-0">{{ sequence ? `第 ${sequence} 次执行` : '执行详情' }}</span>
          <template v-if="run">
            <span class="shrink-0" aria-hidden="true">·</span>
            <span class="min-w-0 truncate tabular-nums">
              {{ startedAt }}<template v-if="run.recipe_version"> · v{{ run.recipe_version }}</template><template v-if="run.duration_ms"> · 耗时 {{ duration(run.duration_ms) }}</template>
            </span>
          </template>
        </h3>
      </div>
    </template>

    <MascotLoader v-if="!run" size="sm" class="h-44" />
    <div v-else class="space-y-5">
      <!-- 1. 顶部执行结果总览（状态 + 快捷结果文件 + 报错/提示） -->
      <div class="status-banner" :class="`is-${banner.tone}`" role="status">
        <div class="flex items-center gap-3.5">
          <span class="status-badge" :class="`is-${banner.tone}`" aria-hidden="true">
            <LoaderCircle v-if="banner.tone === 'running'" class="size-4.5 animate-spin" />
            <Check v-else-if="banner.tone === 'success'" class="size-4.5 stroke-[2.4]" />
            <TriangleAlert v-else-if="banner.tone === 'warning'" class="size-4" />
            <X v-else class="size-4.5 stroke-[2.4]" />
          </span>
          <div class="min-w-0 flex-1">
            <div class="text-[15px] font-semibold leading-snug text-ink">{{ banner.title }}</div>
            <p v-if="banner.desc" class="mt-0.5 text-xs leading-5 text-mute">{{ banner.desc }}</p>
          </div>
        </div>

        <!-- 快捷结果文件：置顶直接查看与下载 -->
        <ul v-if="outputFiles.length" class="quick-results">
          <li v-for="file in outputFiles" :key="file.id" class="flex items-center gap-3">
            <span class="output-icon is-sm" aria-hidden="true">
              <FileSpreadsheet class="size-4" />
            </span>
            <button type="button" class="file-name" :title="`查看 ${file.name}`" @click="openFilePreview(file.id)">{{ file.name }}</button>
            <div class="flex shrink-0 items-center gap-2">
              <button type="button" class="btn-line h-8 gap-1.5 px-3 py-0 text-xs" @click="openFilePreview(file.id)">
                <Eye class="size-3.5" />查看
              </button>
              <a :href="fileApi.downloadUrl(file.id)" class="btn-ink h-8 gap-1.5 px-3.5 py-0 text-xs">
                <Download class="size-3.5" />下载
              </a>
            </div>
          </li>
        </ul>

        <div v-if="executionMessage" class="mt-3">
          <ExecutionStatusNotice :message="executionMessage" />
        </div>
        <div
          v-if="errorMessage"
          class="mt-3 whitespace-pre-wrap rounded-xl border border-error/15 bg-white/90 px-3.5 py-2.5 text-[13px] leading-6 text-error"
          role="alert"
        >{{ errorMessage }}</div>
        <ul
          v-if="failedChecks.length"
          class="mt-2.5 space-y-1 rounded-xl border border-[#f2d6a2] bg-white/85 px-3.5 py-2.5 text-xs leading-5 text-[#8a5a00]"
        >
          <li v-for="check in failedChecks" :key="check.rule_id" class="flex items-start gap-1.5">
            <TriangleAlert class="mt-0.5 size-3.5 shrink-0" /><span>{{ check.message || check.description }}</span>
          </li>
        </ul>
      </div>

      <!-- 2. 输入文件 -->
      <section v-if="run.input_files?.length">
        <h4 class="sec-title">输入文件<small>{{ run.input_files.length }}</small></h4>
        <ul class="card is-compact">
          <li v-for="file in run.input_files" :key="file.file_id" class="flex items-center gap-2.5">
            <Sheet class="size-4 shrink-0 text-mute" />
            <button type="button" class="input-name" :title="`查看 ${file.filename}`" @click="openFilePreview(file.file_id)">{{ file.filename }}</button>
            <span class="flex shrink-0 items-center">
              <button type="button" class="icon-btn size-7" title="查看" :aria-label="`查看 ${file.filename}`" @click="openFilePreview(file.file_id)"><Eye class="size-3.5" /></button>
              <a :href="fileApi.downloadUrl(file.file_id)" class="icon-btn size-7" title="下载" :aria-label="`下载 ${file.filename}`"><Download class="size-3.5" /></a>
            </span>
          </li>
        </ul>
      </section>

      <!-- 3. 处理节点：可点击查看单步详情或从该步重跑 -->
      <section v-if="steps.length">
        <h4 class="sec-title">处理节点<small>{{ steps.length }}</small></h4>
        <ol class="card">
          <li v-for="{ step, status, statusText, failed } in steps" :key="step.step_id" class="step">
            <button type="button" class="step-head" :aria-label="`查看第 ${step.step_id} 步的详情`" @click="selected = step">
              <span class="step-no" :class="`is-${status}`">
                <Check v-if="status === 'success' || status === 'repaired'" class="size-3.5" />
                <TriangleAlert v-else-if="status === 'warning'" class="size-3.5" />
                <X v-else-if="status === 'failed'" class="size-3.5" />
                <template v-else>{{ step.step_id }}</template>
              </span>
              <span class="min-w-0 flex-1 truncate text-sm font-medium text-ink" :title="step.name">{{ step.name }}</span>
              <span class="step-status" :class="`is-${status}`">{{ statusText }}</span>
              <ChevronRight class="size-4 shrink-0 text-faint" />
            </button>

            <ul v-if="step.error || failed.length || step.warnings?.length" class="notes">
              <li v-if="step.error" class="is-error"><CircleX class="mt-0.5 size-3.5 shrink-0" /><span>{{ step.error }}</span></li>
              <li v-for="(check, i) in failed" :key="`c${i}`" class="is-warn"><TriangleAlert class="mt-0.5 size-3.5 shrink-0" /><span>{{ check.message }}</span></li>
              <li v-for="(warning, i) in step.warnings ?? []" :key="`w${i}`" class="is-warn"><TriangleAlert class="mt-0.5 size-3.5 shrink-0" /><span>{{ warning }}</span></li>
            </ul>
          </li>
        </ol>
      </section>

      <div v-if="log.length" class="rounded-xl bg-paper p-3 font-mono text-xs leading-relaxed text-mute">
        <div v-for="(line, i) in log" :key="i">{{ line }}</div>
        <LoaderCircle v-if="rerunning" class="mt-1 size-3.5 animate-spin" />
      </div>

      <!-- 4. 输出文件 -->
      <section>
        <h4 class="sec-title">输出文件<small v-if="outputFiles.length">{{ outputFiles.length }}</small></h4>
        <ul v-if="outputFiles.length" class="card is-compact">
          <li v-for="file in outputFiles" :key="file.id" class="flex items-center gap-2.5">
            <span class="output-icon is-xs" aria-hidden="true">
              <FileSpreadsheet class="size-3.5" />
            </span>
            <button type="button" class="input-name font-semibold" :title="`查看 ${file.name}`" @click="openFilePreview(file.id)">{{ file.name }}</button>
            <span class="flex shrink-0 items-center gap-0.5">
              <button type="button" class="icon-btn size-7" title="查看" :aria-label="`查看 ${file.name}`" @click="openFilePreview(file.id)"><Eye class="size-3.5" /></button>
              <a :href="fileApi.downloadUrl(file.id)" class="icon-btn size-7" title="下载" :aria-label="`下载 ${file.name}`"><Download class="size-3.5" /></a>
            </span>
          </li>
        </ul>
        <p v-else class="empty">{{ run.status === 'running' ? '运行完成后在这里查看' : '本次执行未生成结果文件' }}</p>
      </section>
    </div>

    <StepDetail
      v-if="selected"
      :step-id="selected.step_id"
      :name="selected.name"
      :formula-name="selected.formula_name"
      :formula="selected.formula"
      :kind="selected.kind"
      :formula-kind="selected.formula_kind"
      :description="selected.description"
      :status="selected.status"
      :report="selected"
      :params="selected.params"
      :run-id="run?.run_id ?? activeRunId"
      :error="selected.error"
      :can-rerun="!!run?.recipe_id && !rerunning"
      :elevated="elevated"
      @close="selected = null"
      @rerun="(params) => rerun(selected!, params)"
    />
  </BaseModal>
</template>

<style scoped>
.status-banner {
  border: 1px solid var(--color-line);
  border-radius: 16px;
  padding: 14px 16px;
  background: var(--color-paper);
}
.status-banner.is-success {
  border-color: #cdecd9;
  background: #f3fbf6;
}
.status-banner.is-warning {
  border-color: #f3ddb2;
  background: #fffaf0;
}
.status-banner.is-error {
  border-color: #f7cfcf;
  background: #fef5f5;
}

.status-badge {
  display: inline-flex;
  width: 36px;
  height: 36px;
  flex: none;
  align-items: center;
  justify-content: center;
  border-radius: 9999px;
}
.status-badge.is-running {
  border: 1px solid var(--color-line);
  background: #fff;
  color: var(--color-ink);
}
.status-badge.is-success {
  background: #22b573;
  color: #fff;
}
.status-badge.is-warning {
  background: #f2a21b;
  color: #fff;
}
.status-badge.is-error {
  background: var(--color-error);
  color: #fff;
}

.sec-title {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin: 0 2px 8px;
  color: var(--color-ink);
  font-size: 13.5px;
  font-weight: 600;
  line-height: 1.4;
}
.sec-title small {
  color: var(--color-faint);
  font-size: 12px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}

.card {
  margin: 0;
  border: 1px solid var(--color-line);
  border-radius: 16px;
  background: #fff;
  padding: 2px 16px;
  list-style: none;
}
.card > li {
  padding: 11px 0;
}
.card > li + li {
  border-top: 1px solid #f2f1ee;
}
.card.is-result > li {
  padding: 12px 0;
}
.card.is-compact {
  padding: 2px 14px;
}
.card.is-compact > li {
  padding: 8px 0;
}

.output-icon {
  display: inline-flex;
  width: 36px;
  height: 36px;
  flex: none;
  align-items: center;
  justify-content: center;
  border: 1px solid #d2eedf;
  border-radius: 10px;
  background: #effaf4;
  color: #1a9c5b;
}
.output-icon.is-sm {
  width: 30px;
  height: 30px;
  border-radius: 8px;
}
.output-icon.is-xs {
  width: 22px;
  height: 22px;
  border-radius: 6px;
}

.quick-results {
  margin: 12px 0 0;
  border: 1px solid rgba(20, 20, 23, 0.07);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.92);
  padding: 2px 12px;
  list-style: none;
}
.quick-results > li {
  padding: 9px 0;
}
.quick-results > li + li {
  border-top: 1px solid #f2f1ee;
}

.file-name {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  color: var(--color-ink);
  font-size: 14px;
  font-weight: 600;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-underline-offset: 4px;
}
.file-name:hover {
  text-decoration: underline;
}

.input-name {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  color: var(--color-ink);
  font-size: 13.5px;
  font-weight: 500;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-underline-offset: 4px;
}
.input-name:hover {
  text-decoration: underline;
}

.step {
  padding: 4px 0 !important;
}
.step + .step {
  padding-top: 4px !important;
}
.step-head {
  display: flex;
  width: calc(100% + 16px);
  align-items: center;
  gap: 12px;
  margin: 0 -8px;
  border-radius: 12px;
  padding: 8px;
  text-align: left;
  transition: background-color .15s;
}
.step-head:hover {
  background: var(--color-paper);
}
.step-head:focus-visible {
  outline: 2px solid var(--color-ink);
  outline-offset: 2px;
}

.step-no {
  display: inline-flex;
  width: 22px;
  height: 22px;
  flex: none;
  align-items: center;
  justify-content: center;
  border-radius: 9999px;
  background: var(--color-paper);
  color: var(--color-mute);
  font-size: 11.5px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.step-no.is-running,
.step-no.is-repairing {
  background: color-mix(in srgb, var(--color-info) 12%, transparent);
  color: var(--color-info);
}
.step-no.is-success,
.step-no.is-repaired {
  background: color-mix(in srgb, var(--color-success) 14%, transparent);
  color: var(--color-success);
}
.step-no.is-warning {
  background: color-mix(in srgb, var(--color-warning) 18%, transparent);
  color: #8a5a00;
}
.step-no.is-failed {
  background: color-mix(in srgb, var(--color-error) 12%, transparent);
  color: var(--color-error);
}

.step-status {
  flex: none;
  font-size: 12px;
  font-weight: 500;
  color: var(--color-faint);
}
.step-status.is-running,
.step-status.is-repairing {
  color: var(--color-info);
}
.step-status.is-success,
.step-status.is-repaired {
  color: var(--color-success);
}
.step-status.is-warning {
  color: #8a5a00;
}
.step-status.is-failed {
  color: var(--color-error);
}

.notes {
  margin: 2px 0 8px 34px;
  padding: 0;
  list-style: none;
}
.notes li {
  display: flex;
  gap: 6px;
  padding: 2px 0;
  font-size: 12.5px;
  line-height: 1.55;
  overflow-wrap: anywhere;
}
.notes .is-error {
  color: var(--color-error);
}
.notes .is-warn {
  color: #8a5a00;
}

.empty {
  margin: 0;
  border: 1px dashed var(--color-line);
  border-radius: 14px;
  padding: 16px;
  color: var(--color-faint);
  font-size: 13px;
  text-align: center;
}
</style>
