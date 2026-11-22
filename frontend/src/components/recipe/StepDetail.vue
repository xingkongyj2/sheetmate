<script setup lang="ts">
/**
 * 单个处理节点的详情：概况（行数、耗时）→ 提示 / 出错 → 检查 → 中间结果 → 结果预览 → 执行内容（脚本 / SQL / 参数 / 日志）。
 * 可以修改参数后从这一步重跑（断点续跑）。
 */
import { ChevronDown, CircleCheck, Download, Eye, FileSpreadsheet, RotateCcw, Table2, TriangleAlert, Wrench } from '@lucide/vue'
import { computed, ref } from 'vue'

import { fileApi, runApi } from '@/api'
import type { RecipeArtifact, RecipeToolKind, StepReport } from '@/api/types'
import BaseModal from '@/components/common/BaseModal.vue'
import DataTable from '@/components/common/DataTable.vue'
import ScriptFile from '@/components/common/ScriptFile.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import TableViewer from '@/components/common/TableViewer.vue'
import { duration, percent } from '@/composables/chatModel'
import { recipeToolKind, recipeToolLabel } from '@/composables/recipeTools'
import { scriptReferences } from '@/composables/scriptPresentation'
import { useFilePreview } from '@/composables/useFilePreview'

const props = defineProps<{
  stepId: number
  name: string
  formulaName?: string
  formula?: string
  kind?: RecipeToolKind
  formulaKind?: string
  description?: string
  explanation?: string
  status?: string
  report?: StepReport | null
  params?: Record<string, unknown>
  runId?: string | null
  error?: string
  repairs?: { attempt: number; status: string; explanation?: string }[]
  canRerun?: boolean
  elevated?: boolean
}>()
const emit = defineEmits<{ close: []; rerun: [params: Record<string, unknown>] }>()

const openFilePreview = useFilePreview()
const viewing = ref(false)
const editing = ref(false)
const showChecks = ref(false)
const panel = ref<'script' | 'sql' | 'params' | 'logs' | null>(null)
const paramsText = ref(JSON.stringify(props.report?.params ?? props.params ?? {}, null, 2))
const paramsError = ref('')

const tool = computed(() => ({ kind: props.report?.kind ?? props.kind, formula: props.report?.formula ?? props.formula, formula_kind: props.report?.formula_kind ?? props.formulaKind }))
const isPython = computed(() => recipeToolKind(tool.value) === 'python')
const script = computed(() => scriptReferences(props.report?.params ?? props.params)[0])
const code = computed(() => {
  const value = (props.report?.params ?? props.params)?.code
  return typeof value === 'string' ? value : ''
})
const visibleParams = computed(() => {
  const params = { ...(props.report?.params ?? props.params ?? {}) }
  if (isPython.value) delete params.code
  return params
})

// ---- 概况 ----
const rowsIn = computed(() => Object.entries(props.report?.rows_in ?? {}))
const stats = computed(() => {
  const report = props.report
  if (!report) return []
  const list: { label: string; value: string; hint?: string }[] = []
  if (rowsIn.value.length) {
    list.push({ label: '输入', value: `${rowsIn.value.reduce((sum, [, n]) => sum + n, 0).toLocaleString()} 行`, hint: rowsIn.value.map(([table, n]) => `${table}：${n} 行`).join('\n') })
  }
  if (report.rows_out !== undefined) {
    const cols = report.columns?.length
    list.push({ label: '输出', value: `${report.rows_out.toLocaleString()} 行${cols ? ` · ${cols} 列` : ''}`, hint: report.columns?.join('、') })
  }
  if (report.metrics?.match_rate !== undefined) list.push({ label: '匹配率', value: percent(report.metrics.match_rate as number) })
  list.push({ label: '耗时', value: duration(report.duration_ms) || '<1ms' })
  return list
})

// ---- 检查：文字描述的是「可能出的问题」，通过的收起来，没通过的直接列出 ----
const checks = computed(() => props.report?.checks ?? [])
const failedChecks = computed(() => checks.value.filter((check) => !check.ok))
const warnings = computed(() => props.report?.warnings ?? [])

// ---- 中间结果 ----
type StepFile = RecipeArtifact & { table?: string; sheets?: { rows: number; cols: number }[] }
const files = computed(() => ((props.report?.output_files ?? []) as StepFile[]).filter((file) => file.file_id))
const fileLabel = (file: StepFile) => file.table || file.name || file.filename || '中间结果'
const fileMeta = (file: StepFile) => {
  const sheet = file.sheets?.[0]
  if (!sheet) return ''
  return file.sheets!.length > 1 ? `${file.sheets!.length} 张表` : `${sheet.rows.toLocaleString()} 行 · ${sheet.cols} 列`
}

// ---- 修复记录 ----
const attempts = computed(() => props.report?.attempts ?? [])

// ---- 执行内容 ----
const tabs = computed(() => {
  const list: { key: NonNullable<typeof panel.value>; label: string }[] = []
  if (isPython.value && (script.value || code.value)) list.push({ key: 'script', label: 'Python 脚本' })
  if (props.report?.sql) list.push({ key: 'sql', label: 'SQL' })
  list.push({ key: 'params', label: isPython.value ? '输入输出绑定' : '参数' })
  if (props.report?.stdout || props.report?.stderr) list.push({ key: 'logs', label: '运行日志' })
  return list
})
const openPanel = computed(() => (editing.value ? 'params' : panel.value))
const toggle = (key: NonNullable<typeof panel.value>) => { editing.value = false; panel.value = panel.value === key ? null : key }

function submitRerun() {
  try {
    const parsed = JSON.parse(paramsText.value)
    paramsError.value = ''
    emit('rerun', parsed)
  } catch {
    paramsError.value = '参数不是合法的 JSON'
  }
}

async function loadFull(offset: number, limit: number) {
  const page = await runApi.tablePage(props.runId!, props.report!.output, offset, limit)
  return { columns: page.columns.map((c) => c.name), rows: page.rows, total: page.total }
}
</script>

<template>
  <BaseModal width="max-w-3xl" :elevated="elevated" @close="emit('close')">
    <template #title>
      <div class="flex min-w-0 items-center gap-2.5">
        <span class="step-no">{{ stepId }}</span>
        <h3 class="truncate text-base font-semibold" :title="name">{{ name }}</h3>
        <span class="kind-tag shrink-0">{{ recipeToolLabel(tool) }}</span>
        <StatusPill v-if="status" class="shrink-0" :status="status" />
      </div>
    </template>

    <div class="space-y-8 text-sm">
      <!-- 这一步做什么 + 概况 -->
      <div class="space-y-4">
        <p v-if="description" class="leading-6 text-mute">{{ description }}</p>
        <p v-if="explanation" class="text-xs leading-relaxed text-faint">依据：{{ explanation }}</p>
        <dl v-if="stats.length" class="stats">
          <div v-for="item in stats" :key="item.label" :title="item.hint">
            <dt>{{ item.label }}</dt>
            <dd>{{ item.value }}</dd>
          </div>
        </dl>
        <p v-if="error" class="whitespace-pre-wrap rounded-xl bg-error/5 px-4 py-3 text-[13px] leading-6 text-error" role="alert">{{ error }}</p>
      </div>

      <!-- 需要留意的：没通过的检查、脚本提示 -->
      <ul v-if="failedChecks.length || warnings.length" class="space-y-1.5">
        <li v-for="(check, i) in failedChecks" :key="`c${i}`" class="note"><TriangleAlert class="mt-0.5 size-4 shrink-0" />{{ check.message }}</li>
        <li v-for="(w, i) in warnings" :key="`w${i}`" class="note"><TriangleAlert class="mt-0.5 size-4 shrink-0" />{{ w }}</li>
      </ul>

      <!-- 中间结果 -->
      <section v-if="files.length">
        <h4 class="sec-title">中间结果<small>{{ files.length }}</small></h4>
        <ul class="card">
          <li v-for="file in files" :key="file.file_id" class="flex items-center gap-3">
            <FileSpreadsheet class="size-5 shrink-0 text-ink" />
            <button type="button" class="file-name" :title="file.filename || file.name" @click="openFilePreview(file.file_id!)">{{ fileLabel(file) }}</button>
            <span v-if="fileMeta(file)" class="hidden shrink-0 text-xs tabular-nums text-faint sm:inline">{{ fileMeta(file) }}</span>
            <span class="flex shrink-0 items-center">
              <button type="button" class="icon-btn" title="查看" :aria-label="`查看 ${fileLabel(file)}`" @click="openFilePreview(file.file_id!)"><Eye class="size-4" /></button>
              <a :href="file.download_url || fileApi.downloadUrl(file.file_id!)" class="icon-btn" title="下载" :aria-label="`下载 ${fileLabel(file)}`"><Download class="size-4" /></a>
            </span>
          </li>
        </ul>
      </section>

      <!-- 结果预览 -->
      <section v-if="report?.preview?.columns?.length">
        <div class="mb-2.5 flex items-center justify-between gap-3">
          <h4 class="sec-title !mb-0">结果预览<small>前 {{ report.preview.rows.length }} 行 / 共 {{ report.preview.total.toLocaleString() }} 行</small></h4>
          <button v-if="runId" type="button" class="btn-text h-7 px-2 py-0 text-xs" @click="viewing = true"><Table2 class="size-3.5" />查看完整表</button>
        </div>
        <DataTable :columns="report.preview.columns" :rows="report.preview.rows" max-height="260px" dense />
      </section>

      <!-- 检查（通过的默认收起） -->
      <section v-if="checks.length && !failedChecks.length">
        <button type="button" class="fold" :aria-expanded="showChecks" @click="showChecks = !showChecks">
          <CircleCheck class="size-4 text-success" />
          <span class="font-medium text-success">{{ checks.length }} 项检查全部通过</span>
          <ChevronDown class="size-4 text-faint transition" :class="showChecks ? 'rotate-180' : ''" />
        </button>
        <ul v-if="showChecks" class="card mt-2.5">
          <li v-for="(check, i) in checks" :key="i" class="flex items-start gap-2.5 text-[13px] text-mute">
            <CircleCheck class="mt-0.5 size-4 shrink-0 text-success" />{{ check.message }}
          </li>
        </ul>
      </section>

      <!-- 自动修复记录 -->
      <section v-if="repairs?.length || attempts.length">
        <h4 class="sec-title">自动修复<small>{{ attempts.length || repairs?.length }}</small></h4>
        <ul class="card">
          <template v-if="attempts.length">
            <li v-for="(a, i) in attempts" :key="`a${i}`" class="flex gap-2.5 text-[13px]">
              <Wrench class="mt-0.5 size-4 shrink-0 text-fun-purple" />
              <span class="min-w-0"><span class="block text-error">第 {{ a.attempt }} 次出错：{{ a.error }}</span><span v-if="a.explanation" class="block text-fun-purple">{{ a.explanation }}</span></span>
            </li>
          </template>
          <template v-else>
            <li v-for="r in repairs" :key="`r${r.attempt}${r.status}`" class="flex gap-2.5 text-[13px] text-fun-purple">
              <Wrench class="mt-0.5 size-4 shrink-0" />第 {{ r.attempt }} 次：{{ r.status === 'gave_up' ? '没能修好' : r.status === 'retry' ? r.explanation : '修复中…' }}
            </li>
          </template>
        </ul>
      </section>

      <!-- 执行内容：脚本 / SQL / 参数 / 日志，点哪个看哪个 -->
      <section>
        <h4 class="sec-title">执行内容</h4>
        <div class="tabs" role="tablist">
          <button v-for="tab in tabs" :key="tab.key" type="button" role="tab" :aria-selected="openPanel === tab.key" :class="{ 'is-on': openPanel === tab.key }" @click="toggle(tab.key)">{{ tab.label }}</button>
        </div>

        <div v-if="openPanel === 'script'" class="mt-3">
          <ScriptFile v-if="script" :script="script" />
          <pre v-else class="code">{{ code }}</pre>
        </div>
        <pre v-else-if="openPanel === 'sql'" class="code mt-3 whitespace-pre-wrap">{{ report?.sql }}</pre>
        <div v-else-if="openPanel === 'params'" class="mt-3">
          <textarea v-if="editing" v-model="paramsText" class="field h-44 font-mono text-xs" spellcheck="false" aria-label="节点参数（JSON）" />
          <pre v-else class="max-h-60 overflow-auto rounded-xl bg-paper p-3.5 font-mono text-xs leading-relaxed">{{ JSON.stringify(visibleParams, null, 2) }}</pre>
          <p v-if="paramsError" class="mt-1.5 text-xs text-error">{{ paramsError }}</p>
          <button v-if="canRerun && runId" type="button" class="btn-text mt-2 h-7 px-2 py-0 text-xs" @click="editing = !editing">{{ editing ? '取消修改' : '修改参数后重跑' }}</button>
        </div>
        <div v-else-if="openPanel === 'logs'" class="mt-3 space-y-2">
          <pre v-if="report?.stdout" class="max-h-52 overflow-auto whitespace-pre-wrap rounded-xl bg-paper p-3.5 font-mono text-xs">{{ report.stdout }}</pre>
          <pre v-if="report?.stderr" class="max-h-52 overflow-auto whitespace-pre-wrap rounded-xl bg-error/5 p-3.5 font-mono text-xs text-error">{{ report.stderr }}</pre>
        </div>
      </section>
    </div>

    <template v-if="editing" #footer>
      <button class="btn-line" @click="editing = false">取消</button>
      <button class="btn-ink" @click="submitRerun"><RotateCcw class="size-4" />从第 {{ stepId }} 步重跑</button>
    </template>
    <template v-else-if="canRerun && runId" #footer>
      <button class="btn-line" @click="emit('rerun', report?.params ?? params ?? {})"><RotateCcw class="size-4" />从这一步重跑</button>
    </template>
  </BaseModal>

  <TableViewer v-if="viewing && runId && report" :title="report.output" :subtitle="`第 ${stepId} 步的结果（中间表）`" :loader="loadFull" @close="viewing = false" />
</template>

<style scoped>
.step-no { display: inline-flex; width: 24px; height: 24px; flex: none; align-items: center; justify-content: center; border-radius: 9999px; background: #14141c; color: #fff; font-size: 12px; font-weight: 700; font-variant-numeric: tabular-nums; }
.kind-tag { display: inline-flex; align-items: center; border-radius: 8px; background: #6265dc1a; padding: 1px 8px; color: #6265dc; font-size: 12px; font-weight: 600; line-height: 1.6; }

.sec-title { display: flex; align-items: baseline; gap: 8px; margin: 0 2px 10px; color: #14141c; font-size: 16px; font-weight: 700; line-height: 1.35; }
.sec-title small { color: #a4a4b2; font-size: 13px; font-weight: 500; font-variant-numeric: tabular-nums; }
.card { margin: 0; border: 1px solid var(--color-line); border-radius: 16px; background: #fff; padding: 4px 18px; list-style: none; }
.card > li { padding: 12px 0; }
.card > li + li { border-top: 1px solid #f0f0f4; }
.file-name { min-width: 0; flex: 1; overflow: hidden; color: #14141c; font-size: 14px; font-weight: 600; text-align: left; text-overflow: ellipsis; white-space: nowrap; text-underline-offset: 4px; }
.file-name:hover { text-decoration: underline; }

/* 概况：一条分栏的横条，不是一堆大卡片 */
.stats { display: flex; margin: 0; overflow: hidden; border: 1px solid var(--color-line); border-radius: 16px; }
.stats > div { min-width: 0; flex: 1; padding: 12px 18px; }
.stats > div + div { border-left: 1px solid #f0f0f4; }
.stats dt { color: #a4a4b2; font-size: 12px; }
.stats dd { margin: 2px 0 0; overflow: hidden; color: #14141c; font-size: 15px; font-weight: 600; font-variant-numeric: tabular-nums; text-overflow: ellipsis; white-space: nowrap; }

.note { display: flex; gap: 8px; border-radius: 12px; background: color-mix(in srgb, var(--color-warning) 12%, transparent); padding: 10px 14px; color: #8a5a00; font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }

.fold { display: inline-flex; align-items: center; gap: 6px; border-radius: 8px; font-size: 13px; }
.fold:focus-visible { outline: 2px solid var(--color-ink); outline-offset: 3px; }

.tabs { display: inline-flex; flex-wrap: wrap; gap: 4px; border-radius: 12px; background: var(--color-paper); padding: 3px; }
.tabs button { border-radius: 9px; padding: 5px 12px; color: #6b6b7a; font-size: 13px; font-weight: 500; transition: background-color .15s, color .15s; }
.tabs button:hover { color: #14141c; }
.tabs button.is-on { background: #fff; color: #14141c; font-weight: 600; box-shadow: 0 1px 2px rgb(20 20 28 / 8%); }
.tabs button:focus-visible { outline: 2px solid var(--color-ink); outline-offset: 1px; }

.code { max-height: 20rem; overflow: auto; border-radius: 12px; background: #0f0f11; padding: 14px; color: #d7f5e3; font-family: ui-monospace, monospace; font-size: 11.5px; line-height: 1.6; }
</style>
