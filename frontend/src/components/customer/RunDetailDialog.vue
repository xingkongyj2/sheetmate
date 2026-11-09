<script setup lang="ts">
/** 一次运行的详情：每一步的输入输出、提示、修复记录；可以查看中间表、修改参数后从某一步重跑（断点续跑） */
import { Download, Eye, LoaderCircle } from '@lucide/vue'
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { fileApi, runApi } from '@/api'
import { errorText } from '@/api/http'
import type { FileInfo, RunDetail, StepReport } from '@/api/types'
import BaseModal from '@/components/common/BaseModal.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import ScriptFile from '@/components/common/ScriptFile.vue'
import StepDetail from '@/components/recipe/StepDetail.vue'
import { duration, percent, type UiMessage } from '@/composables/chatModel'
import { readExecutionState } from '@/composables/executionStatus'
import { scriptReferences } from '@/composables/scriptPresentation'
import { useToast } from '@/composables/useToast'
import ExecutionStatusNotice from '@/components/chat/ExecutionStatusNotice.vue'

const WorkbookPreview = defineAsyncComponent(() => import('@/features/workbook-preview/WorkbookPreview.vue'))

const props = defineProps<{ runId: string }>()
const emit = defineEmits<{ close: []; changed: [] }>()
const toast = useToast()

const run = ref<RunDetail | null>(null)
const activeRunId = ref(props.runId)
const tables = ref<{ name: string; rows: number }[]>([])
const selected = ref<StepReport | null>(null)
const rerunning = ref(false)
const log = ref<string[]>([])
const preview = ref<FileInfo | null>(null)
const loadingPreview = ref(false)
let loadVersion = 0
let mounted = true
onBeforeUnmount(() => { mounted = false; loadVersion++ })

async function previewOutput(fileId: string) {
  if (loadingPreview.value) return
  loadingPreview.value = true
  try {
    preview.value = await fileApi.get(fileId)
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    loadingPreview.value = false
  }
}

async function load() {
  const version = ++loadVersion
  const runId = activeRunId.value
  try {
    const data = await runApi.get(runId)
    if (!mounted || version !== loadVersion || activeRunId.value !== runId) return
    run.value = data.run
    tables.value = data.tables
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

const hasTables = computed(() => tables.value.length > 0)
const executionMessage = computed<UiMessage | null>(() => {
  const execution = readExecutionState(run.value?.validation?.execution)
  return run.value && execution ? { key: run.value.run_id, role: 'assistant', content: '', meta: {},
    streaming: execution.status === 'running', timeline: [], artifacts: [], execution } : null
})
const outputFiles = computed(() => {
  const files = (run.value?.validation?.files ?? []).flatMap((file) => file.file_id ? [{ id: file.file_id, name: file.filename || file.name || '运行结果' }] : [])
  const primary = run.value?.output_file_id
  if (primary && !files.some((file) => file.id === primary)) files.unshift({ id: primary, name: '运行结果' })
  return files
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
  <BaseModal width="max-w-4xl" @close="emit('close')">
    <template #title>
      <div v-if="run" class="flex items-center gap-2">
        <h3 class="text-base font-semibold">{{ run.kind_label }} · {{ run.created_at.replace('T', ' ').slice(0, 16) }}</h3>
        <StatusPill :status="run.status" :label="run.status_label" />
      </div>
    </template>
    <div v-if="!run" class="flex h-40 items-center justify-center"><LoaderCircle class="size-5 animate-spin text-mute" /></div>
    <div v-else class="space-y-5">
      <div class="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        <div class="rounded-xl bg-paper px-3 py-2"><div class="text-[11px] text-faint">流程版本</div><div>{{ run.recipe_version ? `v${run.recipe_version}` : '—' }}</div></div>
        <div class="rounded-xl bg-paper px-3 py-2"><div class="text-[11px] text-faint">耗时</div><div>{{ duration(run.duration_ms) || '—' }}</div></div>
        <div class="rounded-xl bg-paper px-3 py-2"><div class="text-[11px] text-faint">与人工结果比对</div><div>{{ run.verification ? percent(run.verification.overall_match_rate) : '—' }}</div></div>
        <div class="rounded-xl bg-paper px-3 py-2"><div class="text-[11px] text-faint">运行校验</div><div class="truncate">{{ run.validation?.summary ?? '—' }}</div></div>
      </div>
      <p v-if="run.request_text" class="text-sm text-mute">{{ run.request_text }}</p>
      <ExecutionStatusNotice v-if="executionMessage" :message="executionMessage" />
      <div v-if="run.summary || run.error && !executionMessage" class="whitespace-pre-wrap rounded-xl px-3 py-2.5 text-[13px]" :class="run.error && !executionMessage ? 'bg-error/5 text-error' : 'bg-paper text-mute'">{{ run.summary || run.error }}</div>

      <div v-if="run.steps.length">
        <div class="label-mini mb-2">每一步{{ hasTables ? '（点开可以查看中间表、修改参数后从这一步重跑）' : '' }}</div>
        <div class="space-y-1.5">
          <div v-for="s in run.steps" :key="s.step_id ?? (s as unknown as { tool: string }).tool" class="space-y-1.5">
          <button
            class="flex w-full items-center gap-3 rounded-xl border border-line px-3 py-2.5 text-left hover:bg-paper"
            @click="s.step_id ? (selected = s) : undefined"
          >
            <span class="flex size-6 items-center justify-center rounded-full bg-ink text-[11px] font-semibold text-white">{{ s.step_id ?? '·' }}</span>
            <span class="min-w-0 flex-1">
              <span class="block truncate text-[13px] font-medium">{{ s.name ?? (s as unknown as { label: string }).label }}</span>
              <span class="block truncate text-xs text-mute">{{ s.description ?? (s as unknown as { summary: string }).summary }}</span>
            </span>
            <span v-if="s.rows_out !== undefined" class="font-mono text-[11px] text-faint">{{ s.rows_out }} 行</span>
            <StatusPill v-if="s.status" :status="s.status" />
          </button>
          <ScriptFile v-for="script in scriptReferences(s.params)" :key="script.script_id" :script="script" compact />
          <div v-for="file in (s.output_files ?? []).filter((item) => item.file_id)" :key="file.file_id" class="ml-9 flex items-center gap-2 rounded-lg bg-paper/60 px-3 py-2 text-xs">
            <span class="min-w-0 flex-1 truncate" :title="file.filename || file.name">{{ file.filename || file.name }}</span>
            <button type="button" class="shrink-0 text-[#6265dc] disabled:opacity-50" :disabled="loadingPreview" :aria-label="`查看环节文件 ${file.filename || file.name}`" @click="previewOutput(file.file_id!)">查看</button>
            <a :href="file.download_url || fileApi.downloadUrl(file.file_id!)" class="shrink-0 text-mute hover:text-[#6265dc]" :aria-label="`下载环节文件 ${file.filename || file.name}`"><Download class="size-3.5" /></a>
          </div>
          </div>
        </div>
      </div>

      <div v-if="log.length" class="rounded-xl bg-paper p-3 font-mono text-xs leading-relaxed text-mute">
        <div v-for="(l, i) in log" :key="i">{{ l }}</div>
        <LoaderCircle v-if="rerunning" class="mt-1 size-3.5 animate-spin" />
      </div>
      <div v-if="outputFiles.length" class="space-y-2">
        <div class="label-mini">结果文件</div>
        <div v-for="file in outputFiles" :key="file.id" class="flex items-center gap-2 rounded-xl border border-line px-3 py-2">
          <span class="min-w-0 flex-1 truncate text-sm">{{ file.name }}</span>
          <button class="btn-text text-xs" :disabled="loadingPreview" @click="previewOutput(file.id)"><Eye class="size-3.5" />查看</button>
          <a :href="fileApi.downloadUrl(file.id)" class="btn-text text-xs"><Download class="size-3.5" />下载</a>
        </div>
      </div>
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
    <WorkbookPreview v-if="preview" :key="preview.file_id" :file="preview" @close="preview = null" />
  </BaseModal>
</template>
