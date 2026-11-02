<script setup lang="ts">
/** 单步详情：这一步做了什么、参数、SQL、行数变化、提示、修复记录、结果预览（可看完整中间表、可从这一步重跑） */
import { Check, Code, Play, Table2, TriangleAlert, Wrench } from '@lucide/vue'
import { computed, ref } from 'vue'

import { runApi } from '@/api'
import type { RecipeToolKind, StepReport } from '@/api/types'
import BaseModal from '@/components/common/BaseModal.vue'
import DataTable from '@/components/common/DataTable.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import TableViewer from '@/components/common/TableViewer.vue'
import { duration, percent } from '@/composables/chatModel'
import { recipeToolKind, recipeToolLabel } from '@/composables/recipeTools'

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
}>()
const emit = defineEmits<{ close: []; rerun: [params: Record<string, unknown>] }>()

const showSql = ref(false)
const showCode = ref(false)
const showLogs = ref(false)
const viewing = ref(false)
const editing = ref(false)
const paramsText = ref(JSON.stringify(props.report?.params ?? props.params ?? {}, null, 2))
const paramsError = ref('')

const rowsIn = computed(() => Object.entries(props.report?.rows_in ?? {}))
const tool = computed(() => ({ kind: props.report?.kind ?? props.kind, formula: props.report?.formula ?? props.formula, formula_kind: props.report?.formula_kind ?? props.formulaKind }))
const isPython = computed(() => recipeToolKind(tool.value) === 'python')
const code = computed(() => {
  const value = (props.report?.params ?? props.params)?.code
  return typeof value === 'string' ? value : ''
})
const visibleParams = computed(() => {
  const params = { ...(props.report?.params ?? props.params ?? {}) }
  if (isPython.value) delete params.code
  return params
})

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
  <BaseModal width="max-w-4xl" @close="emit('close')">
    <template #title>
      <div class="flex items-center gap-2">
        <span class="flex size-6 items-center justify-center rounded-full bg-ink text-xs font-semibold text-white">{{ stepId }}</span>
        <h3 class="text-base font-semibold">{{ name }}</h3>
        <span class="tag">{{ recipeToolLabel(tool) }}</span>
        <span v-if="formulaName && !isPython && recipeToolKind(tool) === 'formula'" class="text-xs text-mute">{{ formulaName }}</span>
        <StatusPill v-if="status" :status="status" />
      </div>
    </template>

    <div class="space-y-5 text-sm">
      <p v-if="description" class="leading-relaxed text-ink">{{ description }}</p>
      <p v-if="explanation" class="rounded-xl bg-paper px-3 py-2 text-xs leading-relaxed text-mute">依据：{{ explanation }}</p>

      <div v-if="report" class="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div v-for="[table, n] in rowsIn" :key="table" class="rounded-xl border border-line px-3 py-2">
          <div class="text-[11px] text-faint">输入 · {{ table }}</div>
          <div class="font-mono text-lg">{{ n }}<span class="ml-1 text-xs text-faint">行</span></div>
        </div>
        <div v-if="!isPython || report.columns?.length" class="rounded-xl border border-line px-3 py-2">
          <div class="text-[11px] text-faint">输出 · {{ report.output }}</div>
          <div class="font-mono text-lg">{{ report.rows_out }}<span class="ml-1 text-xs text-faint">行 × {{ report.columns?.length }} 列</span></div>
        </div>
        <div v-if="report.metrics?.match_rate !== undefined" class="rounded-xl border border-line px-3 py-2">
          <div class="text-[11px] text-faint">匹配率</div>
          <div class="font-mono text-lg">{{ percent(report.metrics.match_rate as number) }}</div>
        </div>
        <div class="rounded-xl border border-line px-3 py-2">
          <div class="text-[11px] text-faint">耗时</div>
          <div class="font-mono text-lg">{{ duration(report.duration_ms) || '<1ms' }}</div>
        </div>
      </div>

      <div v-if="error" class="rounded-xl bg-error/5 px-3 py-2 text-error">{{ error }}</div>
      <ul v-if="report?.warnings?.length" class="space-y-1">
        <li v-for="(w, i) in report.warnings" :key="i" class="flex gap-2 rounded-xl bg-warning/10 px-3 py-2 text-[13px] text-[#8a5a00]">
          <TriangleAlert class="mt-0.5 size-4 shrink-0" />{{ w }}
        </li>
      </ul>

      <div v-if="report?.checks?.length" class="space-y-1.5">
        <div class="label-mini">脚本校验</div>
        <div v-for="(check, i) in report.checks" :key="i" class="flex items-start gap-2 rounded-xl px-3 py-2 text-xs" :class="check.ok ? 'bg-success/5 text-success' : 'bg-error/5 text-error'">
          <Check v-if="check.ok" class="size-3.5 shrink-0" /><TriangleAlert v-else class="size-3.5 shrink-0" />{{ check.message }}
        </div>
      </div>

      <div v-if="report?.output_files?.length" class="space-y-1.5">
        <div class="label-mini">生成文件</div>
        <div v-for="file in report.output_files" :key="file.ref" class="rounded-xl border border-line px-3 py-2 text-xs">{{ file.name }}</div>
      </div>

      <div v-if="repairs?.length || report?.attempts?.length" class="space-y-1.5">
        <div class="label-mini">自动修复记录</div>
        <div v-for="(a, i) in report?.attempts ?? []" :key="`a${i}`" class="flex gap-2 text-xs">
          <Wrench class="mt-0.5 size-3.5 shrink-0 text-fun-purple" />
          <div><span class="text-error">第 {{ a.attempt }} 次出错：{{ a.error }}</span><br v-if="a.explanation" /><span v-if="a.explanation" class="text-fun-purple">{{ a.explanation }}</span></div>
        </div>
        <div v-if="!report?.attempts?.length">
          <div v-for="r in repairs" :key="`r${r.attempt}${r.status}`" class="flex gap-2 text-xs text-fun-purple">
            <Wrench class="size-3.5" />第 {{ r.attempt }} 次：{{ r.status === 'gave_up' ? '没能修好' : r.status === 'retry' ? r.explanation : '修复中…' }}
          </div>
        </div>
      </div>

      <div>
        <div class="mb-1.5 flex items-center justify-between">
          <span class="label-mini">{{ isPython ? '输入与输出绑定' : '参数' }}</span>
          <button v-if="canRerun && runId" class="btn-text" @click="editing = !editing">{{ editing ? '取消修改' : '修改参数后重跑' }}</button>
        </div>
        <textarea v-if="editing" v-model="paramsText" class="field h-44 font-mono text-xs" spellcheck="false" />
        <pre v-else class="max-h-52 overflow-auto rounded-xl bg-paper p-3 font-mono text-xs leading-relaxed">{{ JSON.stringify(visibleParams, null, 2) }}</pre>
        <p v-if="paramsError" class="mt-1 text-xs text-error">{{ paramsError }}</p>
      </div>

      <div v-if="isPython && code">
        <button class="btn-text -ml-2" @click="showCode = !showCode"><Code class="size-3.5" />{{ showCode ? '收起' : '查看' }}执行的 Python 脚本</button>
        <pre v-if="showCode" class="mt-1 max-h-80 overflow-auto rounded-xl bg-[#0f0f11] p-3 font-mono text-[11.5px] leading-relaxed text-[#d7f5e3]">{{ code }}</pre>
      </div>

      <div v-if="report?.stdout || report?.stderr">
        <button class="btn-text -ml-2" @click="showLogs = !showLogs"><Code class="size-3.5" />{{ showLogs ? '收起' : '查看' }}脚本日志</button>
        <div v-if="showLogs" class="mt-1 space-y-2">
          <pre v-if="report.stdout" class="max-h-52 overflow-auto whitespace-pre-wrap rounded-xl bg-paper p-3 font-mono text-xs">{{ report.stdout }}</pre>
          <pre v-if="report.stderr" class="max-h-52 overflow-auto whitespace-pre-wrap rounded-xl bg-error/5 p-3 font-mono text-xs text-error">{{ report.stderr }}</pre>
        </div>
      </div>

      <div v-if="report?.sql">
        <button class="btn-text -ml-2" @click="showSql = !showSql"><Code class="size-3.5" />{{ showSql ? '收起' : '查看' }}执行的 DuckDB SQL</button>
        <pre v-if="showSql" class="mt-1 max-h-60 overflow-auto whitespace-pre-wrap rounded-xl bg-[#0f0f11] p-3 font-mono text-[11.5px] leading-relaxed text-[#d7f5e3]">{{ report.sql }}</pre>
      </div>

      <div v-if="report?.preview?.columns?.length">
        <div class="mb-1.5 flex items-center justify-between">
          <span class="label-mini">结果预览（前 {{ report.preview.rows.length }} 行，共 {{ report.preview.total }} 行）</span>
          <button v-if="runId" class="btn-text" @click="viewing = true"><Table2 class="size-3.5" />查看完整中间表</button>
        </div>
        <DataTable :columns="report.preview.columns" :rows="report.preview.rows" max-height="260px" dense />
      </div>
    </div>

    <template v-if="editing" #footer>
      <button class="btn-line" @click="editing = false">取消</button>
      <button class="btn-ink" @click="submitRerun"><Play class="size-4" />从第 {{ stepId }} 步重跑</button>
    </template>
    <template v-else-if="canRerun && runId" #footer>
      <button class="btn-line" @click="emit('rerun', report?.params ?? params ?? {})"><Check class="size-4" />从这一步重跑</button>
    </template>
  </BaseModal>

  <TableViewer v-if="viewing && runId && report" :title="report.output" :subtitle="`第 ${stepId} 步的结果（中间表）`" :loader="loadFull" @close="viewing = false" />
</template>
