<script setup lang="ts">
/**
 * 一次运行的详情：概况一行（版本、耗时、比对、校验）→ 输入文件 → 处理节点（每步的状态、行数、检查、中间结果）
 * → 输出文件 → 校验。点一个节点打开单步详情：看中间表、参数，可以改参数后从这一步重跑。
 */
import { ChevronRight, CircleCheck, CircleX, Download, Eye, FileSpreadsheet, LoaderCircle, TriangleAlert } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { fileApi, runApi } from '@/api'
import { errorText } from '@/api/http'
import type { RunDetail, StepReport } from '@/api/types'
import ExecutionStatusNotice from '@/components/chat/ExecutionStatusNotice.vue'
import BaseModal from '@/components/common/BaseModal.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import StepDetail from '@/components/recipe/StepDetail.vue'
import { duration, percent, type UiMessage } from '@/composables/chatModel'
import { runDeliveryFiles } from '@/composables/deliveryFiles'
import { readExecutionState } from '@/composables/executionStatus'
import { recipeToolLabel } from '@/composables/recipeTools'
import { useFilePreview } from '@/composables/useFilePreview'
import { useToast } from '@/composables/useToast'

const props = defineProps<{ runId: string }>()
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
const checkSummary = computed(() => {
  if (!checks.value.length) return ''
  return failedChecks.value.length ? `${failedChecks.value.length} 项校验未通过` : `校验全部通过`
})

// ---- 处理节点 ----
const steps = computed(() => (run.value?.steps ?? []).filter((step) => step.step_id).map((step) => {
  const files = (step.output_files ?? []).filter((file) => file.file_id)
  const stepChecks = step.checks ?? []
  const failed = stepChecks.filter((check) => !check.ok)
  return {
    step,
    files,
    failed,
    checkText: stepChecks.length ? (failed.length ? `${failed.length}/${stepChecks.length} 项检查未通过` : `${stepChecks.length} 项检查通过`) : '',
    meta: [step.rows_out !== undefined ? `${step.rows_out.toLocaleString()} 行` : '', duration(step.duration_ms), files.length ? `${files.length} 个中间表` : ''].filter(Boolean).join(' · '),
  }
}))
const outputFiles = computed(() => runDeliveryFiles(run.value))

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
  <BaseModal width="max-w-3xl" @close="emit('close')">
    <template #title>
      <div v-if="run" class="flex items-center gap-2.5">
        <h3 class="text-base font-semibold">{{ startedAt }}</h3>
        <StatusPill :status="run.status" :label="run.status_label" />
      </div>
    </template>

    <div v-if="!run" class="flex h-40 items-center justify-center"><LoaderCircle class="size-5 animate-spin text-mute" /></div>
    <div v-else class="space-y-8">
      <!-- 概况：一行重点 -->
      <div class="space-y-3">
        <div class="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[13px] text-mute">
          <span v-if="run.recipe_version" class="rounded-md bg-ink px-1.5 py-0.5 text-[11px] font-semibold text-white">v{{ run.recipe_version }}</span>
          <span>{{ run.kind_label }}</span>
          <span v-if="run.duration_ms">耗时 {{ duration(run.duration_ms) }}</span>
          <span v-if="run.verification" class="font-medium" :class="run.verification.status === 'pass' ? 'text-success' : 'text-[#b7770c]'">比对 {{ percent(run.verification.overall_match_rate) }}</span>
          <span v-if="checkSummary" class="inline-flex items-center gap-1 font-medium" :class="failedChecks.length ? 'text-[#b7770c]' : 'text-success'">
            <TriangleAlert v-if="failedChecks.length" class="size-3.5" /><CircleCheck v-else class="size-3.5" />{{ checkSummary }}
          </span>
        </div>
        <ExecutionStatusNotice v-if="executionMessage" :message="executionMessage" />
        <p v-if="errorMessage" class="whitespace-pre-wrap rounded-xl bg-error/5 px-4 py-3 text-[13px] leading-6 text-error" role="alert">{{ errorMessage }}</p>
        <p v-else-if="run.summary" class="whitespace-pre-wrap text-[13px] leading-6 text-mute">{{ run.summary }}</p>
      </div>

      <!-- 输入文件 -->
      <section v-if="run.input_files?.length">
        <h4 class="sec-title">输入文件<small>{{ run.input_files.length }}</small></h4>
        <ul class="card">
          <li v-for="file in run.input_files" :key="file.file_id" class="file-row">
            <FileSpreadsheet class="size-5 shrink-0 text-ink" />
            <button type="button" class="file-name" :title="`查看 ${file.filename}`" @click="openFilePreview(file.file_id)">{{ file.filename }}</button>
            <span class="flex shrink-0 items-center">
              <button type="button" class="icon-btn" title="查看" :aria-label="`查看 ${file.filename}`" @click="openFilePreview(file.file_id)"><Eye class="size-4" /></button>
              <a :href="fileApi.downloadUrl(file.file_id)" class="icon-btn" title="下载" :aria-label="`下载 ${file.filename}`"><Download class="size-4" /></a>
            </span>
          </li>
        </ul>
      </section>

      <!-- 处理节点：每一步的状态、行数、检查、中间结果 -->
      <section v-if="steps.length">
        <h4 class="sec-title">处理节点<small>{{ steps.length }}</small></h4>
        <ol class="card">
          <li v-for="{ step, failed, checkText, meta } in steps" :key="step.step_id" class="step">
            <button type="button" class="step-head" :aria-label="`查看第 ${step.step_id} 步的详情`" @click="selected = step">
              <span class="step-no" :class="{ 'is-failed': step.status === 'failed' }">{{ step.step_id }}</span>
              <span class="min-w-0 flex-1">
                <span class="flex flex-wrap items-center gap-2">
                  <span class="text-sm font-semibold leading-6" :title="step.name">{{ step.name }}</span>
                  <span class="kind-tag">{{ recipeToolLabel(step) }}</span>
                  <StatusPill v-if="step.status && step.status !== 'success'" :status="step.status" />
                </span>
                <span v-if="meta || checkText" class="mt-0.5 flex flex-wrap items-center gap-x-3 text-xs text-faint">
                  <span v-if="meta" class="tabular-nums">{{ meta }}</span>
                  <span v-if="checkText" class="inline-flex items-center gap-1" :class="failed.length ? 'text-[#b7770c]' : 'text-success'">
                    <TriangleAlert v-if="failed.length" class="size-3" /><CircleCheck v-else class="size-3" />{{ checkText }}
                  </span>
                </span>
              </span>
              <ChevronRight class="size-4 shrink-0 text-faint" />
            </button>

            <!-- 出错、未通过的检查、提示：直接列出来 -->
            <ul v-if="step.error || failed.length || step.warnings?.length" class="notes">
              <li v-if="step.error" class="is-error"><CircleX class="mt-0.5 size-3.5 shrink-0" />{{ step.error }}</li>
              <li v-for="(check, i) in failed" :key="`c${i}`" class="is-warn"><TriangleAlert class="mt-0.5 size-3.5 shrink-0" />{{ check.message }}</li>
              <li v-for="(warning, i) in step.warnings ?? []" :key="`w${i}`" class="is-warn"><TriangleAlert class="mt-0.5 size-3.5 shrink-0" />{{ warning }}</li>
            </ul>
          </li>
        </ol>
      </section>

      <div v-if="log.length" class="rounded-xl bg-paper p-3 font-mono text-xs leading-relaxed text-mute">
        <div v-for="(line, i) in log" :key="i">{{ line }}</div>
        <LoaderCircle v-if="rerunning" class="mt-1 size-3.5 animate-spin" />
      </div>

      <!-- 输出文件 -->
      <section>
        <h4 class="sec-title">输出文件<small v-if="outputFiles.length">{{ outputFiles.length }}</small></h4>
        <ul v-if="outputFiles.length" class="card">
          <li v-for="file in outputFiles" :key="file.id" class="file-row">
            <FileSpreadsheet class="size-5 shrink-0 text-ink" />
            <button type="button" class="file-name" :title="`查看 ${file.name}`" @click="openFilePreview(file.id)">{{ file.name }}</button>
            <span class="flex shrink-0 items-center">
              <button type="button" class="icon-btn" title="查看" :aria-label="`查看 ${file.name}`" @click="openFilePreview(file.id)"><Eye class="size-4" /></button>
              <a :href="fileApi.downloadUrl(file.id)" class="icon-btn" title="下载" :aria-label="`下载 ${file.name}`"><Download class="size-4" /></a>
            </span>
          </li>
        </ul>
        <p v-else class="empty">{{ run.status === 'running' ? '运行完成后在这里查看' : '这次没有生成结果文件' }}</p>
      </section>

      <!-- 校验 -->
      <section v-if="checks.length">
        <h4 class="sec-title">校验<small>{{ checks.length }}</small></h4>
        <ul class="card">
          <li v-for="check in checks" :key="check.rule_id" class="check-row">
            <CircleCheck v-if="check.status === 'pass' || check.status === 'info'" class="mt-0.5 size-4 shrink-0 text-success" />
            <TriangleAlert v-else-if="check.status === 'warn'" class="mt-0.5 size-4 shrink-0 text-[#b7770c]" />
            <CircleX v-else class="mt-0.5 size-4 shrink-0 text-error" />
            <span class="min-w-0 flex-1">
              <span class="block text-sm font-medium leading-6">{{ check.description || check.rule_type }}</span>
              <span v-if="check.message && check.message !== check.description" class="block text-xs leading-5 text-mute">{{ check.message }}</span>
            </span>
          </li>
        </ul>
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
      @close="selected = null"
      @rerun="(params) => rerun(selected!, params)"
    />
  </BaseModal>
</template>

<style scoped>
/* 模块 = 卡片外的标题 + 一张卡片，卡片里一行一项、细线分隔 */
.sec-title { display: flex; align-items: baseline; gap: 8px; margin: 0 2px 10px; color: #14141c; font-size: 16px; font-weight: 700; line-height: 1.35; }
.sec-title small { color: #a4a4b2; font-size: 13px; font-weight: 500; font-variant-numeric: tabular-nums; }
.card { margin: 0; border: 1px solid var(--color-line); border-radius: 16px; background: #fff; padding: 4px 18px; list-style: none; }
.card > li { padding: 12px 0; }
.card > li + li { border-top: 1px solid #f0f0f4; }

.file-row { display: flex; align-items: center; gap: 12px; }
.file-name { min-width: 0; flex: 1; overflow: hidden; color: #14141c; font-size: 14px; font-weight: 600; text-align: left; text-overflow: ellipsis; white-space: nowrap; text-underline-offset: 4px; }
.file-name:hover { text-decoration: underline; }

.step { padding: 6px 0 !important; }
.step + .step { padding-top: 6px !important; }
.step-head { display: flex; width: calc(100% + 16px); align-items: center; gap: 12px; margin: 0 -8px; border-radius: 12px; padding: 8px; text-align: left; transition: background-color .15s; }
.step-head:hover { background: var(--color-paper); }
.step-head:focus-visible { outline: 2px solid var(--color-ink); outline-offset: 2px; }
.step-no { display: inline-flex; width: 24px; height: 24px; flex: none; align-items: center; justify-content: center; border-radius: 9999px; background: #6265dc14; color: #6265dc; font-size: 12px; font-weight: 700; font-variant-numeric: tabular-nums; }
.step-no.is-failed { background: color-mix(in srgb, var(--color-error) 10%, transparent); color: var(--color-error); }
.kind-tag { display: inline-flex; align-items: center; border-radius: 8px; background: #6265dc1a; padding: 1px 8px; color: #6265dc; font-size: 12px; font-weight: 600; line-height: 1.6; }

.notes { margin: 2px 0 8px 36px; padding: 0; list-style: none; }
.notes li { display: flex; gap: 6px; padding: 2px 0; font-size: 12.5px; line-height: 1.6; overflow-wrap: anywhere; }
.notes .is-error { color: var(--color-error); }
.notes .is-warn { color: #8a5a00; }


.check-row { display: flex; align-items: flex-start; gap: 10px; }
.empty { margin: 0; border: 1px dashed var(--color-line); border-radius: 16px; padding: 20px; color: #a4a4b2; font-size: 13px; text-align: center; }
</style>
