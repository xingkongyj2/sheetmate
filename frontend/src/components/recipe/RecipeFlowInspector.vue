<script setup lang="ts">
import { ArrowDownToLine, ArrowLeft, Check, FileSpreadsheet, LoaderCircle, TriangleAlert } from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { conversationApi, customerApi, fileApi, recipeApi, runApi, scriptApi } from '@/api'
import { errorText } from '@/api/http'
import type { FileInfo, FormulaInfo, RecipeFlowData, RecipeJSON, ScriptFile, StepReport } from '@/api/types'
import DataTable from '@/components/common/DataTable.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import { duration, type StepState } from '@/composables/chatModel'
import { executionFileSize } from '@/composables/executionPresentation'
import { completeInspectorRecipe, inspectorFileIds, inspectorFileMatches, inspectorStates, inspectorStep } from '@/composables/recipeFlowInspector'
import type { FlowSelection, RecipeFlowContext } from '@/composables/recipeFlowInteraction'
import { recipeToolKind, recipeToolLabel } from '@/composables/recipeTools'
import { scriptReferences, scriptTitle } from '@/composables/scriptPresentation'

import FlowCodeBlock from './FlowCodeBlock.vue'

const props = withDefaults(defineProps<{
  selection: FlowSelection
  flow: RecipeFlowData
  states?: StepState[]
  context?: RecipeFlowContext
  files?: FileInfo[]
  recipe?: RecipeJSON
}>(), { states: () => [], files: () => [] })
const emit = defineEmits<{ close: []; preview: [file: FileInfo] }>()

const fetchedRecipe = ref<RecipeJSON>()
const runRecipe = ref<RecipeJSON>()
const runReports = ref<StepReport[]>([])
const loadingRecipe = ref(false)
const recipeError = ref('')
const formulaCatalog = ref<{ builtin: FormulaInfo[]; customer: FormulaInfo[] }>({ builtin: [], customer: [] })
const formulaScope = ref<string>()
const loadingFormula = ref(false)
const loadedScripts = ref<ScriptFile[]>([])
const loadingScripts = ref(false)
const scriptError = ref('')
const scriptRetry = ref(0)
const resourceFiles = ref<FileInfo[]>([])
const resolvedFiles = ref<FileInfo[]>([])
const loadingFiles = ref(false)
const fileError = ref('')
const openingFile = ref<string | null>(null)
const fileRetry = ref(0)
let previewGeneration = 0
let mounted = true
onBeforeUnmount(() => { mounted = false; previewGeneration++ })

const suppliedRecipe = computed(() => props.recipe ?? props.context?.recipe)
const fullRecipe = computed(() => runRecipe.value ?? (completeInspectorRecipe(suppliedRecipe.value) ? suppliedRecipe.value : fetchedRecipe.value ?? suppliedRecipe.value))
const effectiveStates = computed(() => inspectorStates(props.states, runReports.value))
const step = computed(() => props.selection.kind === 'step' ? inspectorStep(props.flow, effectiveStates.value, fullRecipe.value, props.selection.id) : null)
const fileNode = computed(() => props.selection.kind === 'file' ? props.selection.file : null)
const referencedFileIds = computed(() => fileNode.value ? inspectorFileIds(fileNode.value, effectiveStates.value) : [])
const title = computed(() => step.value?.name || fileNode.value?.name || '节点详情')
const scripts = computed(() => step.value ? scriptReferences([step.value.flowStep, step.value.planned, step.value.params, step.value.report?.result]) : [])
const isPython = computed(() => !!step.value && recipeToolKind(step.value.tool) === 'python')
const formulaDefinition = computed(() => {
  if (!step.value || recipeToolKind(step.value.tool) !== 'formula') return undefined
  const catalog = formulaCatalog.value
  const scoped = step.value.tool.formula_kind === 'customer' ? catalog.customer : catalog.builtin
  return scoped.find((formula) => formula.id === step.value!.tool.formula) ?? catalog.customer.find((formula) => formula.id === step.value!.tool.formula)
})
const formulaTitle = computed(() => {
  if (!step.value) return ''
  return step.value.formulaName && step.value.formulaName !== step.value.tool.formula
    ? step.value.formulaName : formulaDefinition.value?.name || recipeToolLabel(step.value.tool)
})
const statusLabel = computed(() => step.value?.state?.status === 'repairing' ? '修复中' : step.value?.state?.status === 'pending' ? '等待执行' : undefined)
const params = computed(() => {
  const values = { ...(step.value?.params ?? {}) }
  for (const key of ['code', 'sql', 'script_id', 'script_title', 'script_filename']) delete values[key]
  return values
})
const paramsText = computed(() => JSON.stringify(params.value, null, 2))
const sql = computed(() => step.value?.sql || formulaDefinition.value?.sql || '')
const code = computed(() => step.value?.code || '')
const inputRows = computed(() => Object.entries(step.value?.report?.rows_in ?? {}))
const stepError = computed(() => step.value?.state?.error || step.value?.report?.error || '')

watch([() => !!step.value && recipeToolKind(step.value.tool) === 'formula', () => props.context?.customerId], async ([needed, customerId], _, onCleanup) => {
  let active = true
  onCleanup(() => { active = false })
  loadingFormula.value = false
  const scope = customerId || 'builtin'
  if (!needed || formulaScope.value === scope) return
  formulaCatalog.value = { builtin: [], customer: [] }
  loadingFormula.value = true
  try {
    const catalog = await recipeApi.formulas(customerId || undefined)
    if (active) { formulaCatalog.value = catalog; formulaScope.value = scope }
  } catch { /* The snapshot's formula name and parameters remain available. */ }
  finally { if (active) loadingFormula.value = false }
}, { immediate: true })

watch([() => props.context?.runId, () => props.context?.running], async (_, __, onCleanup) => {
  let active = true
  onCleanup(() => { active = false })
  runRecipe.value = undefined
  runReports.value = []
  if (!props.context?.runId) return
  try {
    const result = await runApi.get(props.context.runId)
    if (!active) return
    runReports.value = result.run.steps
    const snapshot = result.run.validation.recipe_snapshot
    if (snapshot && typeof snapshot === 'object' && Array.isArray((snapshot as RecipeJSON).steps)) runRecipe.value = snapshot as RecipeJSON
  } catch { /* A live run may not have a persisted report yet; streamed state remains available. */ }
}, { immediate: true })

watch([
  () => props.context?.recipeId ?? suppliedRecipe.value?.id,
  () => props.context?.version ?? suppliedRecipe.value?.version,
  () => completeInspectorRecipe(suppliedRecipe.value),
  () => props.context?.deleted,
], async ([recipeId, version], _, onCleanup) => {
  let active = true
  onCleanup(() => { active = false })
  fetchedRecipe.value = undefined
  loadingRecipe.value = false
  recipeError.value = ''
  if (completeInspectorRecipe(suppliedRecipe.value) || !recipeId || props.context?.deleted) return
  loadingRecipe.value = true
  try {
    const result = await recipeApi.get(recipeId, version)
    if (active) fetchedRecipe.value = result.recipe
  } catch (error) {
    if (active) recipeError.value = errorText(error)
  } finally { if (active) loadingRecipe.value = false }
}, { immediate: true })

watch([() => scripts.value.map((script) => script.script_id).join('|'), () => scriptRetry.value], async (_, __, onCleanup) => {
  let active = true
  onCleanup(() => { active = false })
  loadedScripts.value = []
  scriptError.value = ''
  loadingScripts.value = scripts.value.length > 0
  if (!scripts.value.length) return
  const results = await Promise.allSettled(scripts.value.map((script) => scriptApi.get(script.script_id)))
  if (!active) return
  loadedScripts.value = results.flatMap((result) => result.status === 'fulfilled' ? [result.value] : [])
  const failure = results.find((result) => result.status === 'rejected')
  if (failure?.status === 'rejected') scriptError.value = errorText(failure.reason)
  loadingScripts.value = false
}, { immediate: true })

const fullFiles = computed(() => {
  const files = new Map<string, FileInfo>()
  for (const file of [...resourceFiles.value, ...(props.context?.files ?? []), ...props.files, ...resolvedFiles.value]) files.set(file.file_id, file)
  return [...files.values()]
})
const availableFiles = computed(() => {
  const files = new Map<string, FileInfo>()
  for (const file of props.context?.resultFiles ?? []) {
    files.set(file.file_id, {
      file_id: file.file_id, filename: file.filename, kind: file.kind ?? 'output', kind_label: file.kind_label ?? '输出文件',
      download_url: file.download_url, size: 0, customer_id: props.context?.customerId ?? null,
      conversation_id: props.context?.conversationId ?? null, run_id: file.run_id ?? props.context?.runId ?? null,
      created_at: null, sheets: (file.sheets ?? []).map((sheet) => ({ ...sheet, cols: 0, columns: [] })),
    })
  }
  for (const file of fullFiles.value) files.set(file.file_id, file)
  return [...files.values()]
})
const matchedFiles = computed(() => {
  const node = fileNode.value
  if (!node) return []
  let candidates = availableFiles.value
  if (node.direction === 'output' && props.context?.runId && !node.fileId) {
    const deliveredIds = new Set([
      ...(props.context.resultFiles ?? []).map((file) => file.file_id),
      ...effectiveStates.value.flatMap((state) => (state.report?.output_files ?? []).map((file) => file.file_id)),
    ])
    candidates = candidates.filter((file) => file.run_id === props.context?.runId || deliveredIds.has(file.file_id))
  }
  return inspectorFileMatches(node, props.flow, candidates, effectiveStates.value)
})
const inputSpecs = computed(() => {
  const node = fileNode.value
  if (!node || node.direction !== 'input') return []
  return (fullRecipe.value?.inputs ?? []).filter((input) => node.aliases.includes(input.alias) || input.alias === node.name || input.source_file === node.name || (!!node.pattern && input.file_pattern === node.pattern))
})
const inputDescriptions = computed(() => {
  const node = fileNode.value
  if (!node || node.direction !== 'input') return []
  return props.flow.inputs.filter((input) => node.aliases.includes(input.alias) || input.alias === node.name || input.source_file === node.name || (!!node.pattern && input.file_pattern === node.pattern))
})

watch([() => props.context?.customerId, () => props.context?.conversationId], () => { resourceFiles.value = []; resolvedFiles.value = [] })
watch([
  () => fileNode.value?.key,
  () => referencedFileIds.value.join('|'),
  () => props.context?.customerId,
  () => props.context?.conversationId,
  () => props.context?.runId,
  () => props.context?.recipeId,
  () => props.context?.version,
  () => fileRetry.value,
], async (_, __, onCleanup) => {
  let active = true
  onCleanup(() => { active = false })
  previewGeneration++
  fileError.value = ''
  loadingFiles.value = false
  openingFile.value = null
  const node = fileNode.value
  if (!node) return
  const unknownIds = referencedFileIds.value.filter((id) => !fullFiles.value.some((file) => file.file_id === id))
  if (unknownIds.length) {
    loadingFiles.value = true
    const results = await Promise.allSettled(unknownIds.map((id) => fileApi.get(id)))
    if (!active) return
    resolvedFiles.value = [...resolvedFiles.value, ...results.flatMap((result) => result.status === 'fulfilled' ? [result.value] : [])]
    const failure = results.find((result) => result.status === 'rejected')
    if (failure?.status === 'rejected') fileError.value = errorText(failure.reason)
    loadingFiles.value = false
    return
  }
  if (matchedFiles.value.length || (!props.context?.conversationId && !props.context?.customerId)) return
  loadingFiles.value = true
  const requests: Promise<FileInfo[]>[] = []
  if (props.context.conversationId) requests.push(conversationApi.get(props.context.conversationId).then((detail) => detail.files))
  if (props.context.customerId) requests.push(customerApi.get(props.context.customerId).then((customer) => [...customer.files, ...customer.outputs]))
  const results = await Promise.allSettled(requests)
  if (!active) return
  resourceFiles.value = [...resourceFiles.value, ...results.flatMap((result) => result.status === 'fulfilled' ? result.value : [])]
  if (results.every((result) => result.status === 'rejected')) {
    const failure = results.find((result) => result.status === 'rejected')
    if (failure?.status === 'rejected') fileError.value = errorText(failure.reason)
  }
  loadingFiles.value = false
}, { immediate: true })

async function preview(file: FileInfo) {
  if (openingFile.value) return
  const key = fileNode.value?.key
  const generation = previewGeneration
  openingFile.value = file.file_id
  fileError.value = ''
  try {
    const full = fullFiles.value.find((item) => item.file_id === file.file_id) ?? await fileApi.get(file.file_id)
    if (!mounted || generation !== previewGeneration || fileNode.value?.key !== key) return
    resolvedFiles.value = [...resolvedFiles.value.filter((item) => item.file_id !== full.file_id), full]
    emit('preview', full)
  } catch (error) { if (mounted && generation === previewGeneration && fileNode.value?.key === key) fileError.value = errorText(error) }
  finally { if (mounted && generation === previewGeneration && fileNode.value?.key === key) openingFile.value = null }
}

function paramValue(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2) ?? ''
}
</script>

<template>
  <section class="flow-inspector" aria-label="节点详情">
    <header class="flow-inspector-header">
      <button type="button" class="flow-inspector-back" aria-label="返回流程" @click="emit('close')"><ArrowLeft class="size-4" /></button>
      <div class="min-w-0 flex-1">
        <p class="mb-1 text-[10px] font-medium tracking-wide text-[#6265dc]">{{ step ? `步骤 ${step.id} · ${recipeToolLabel(step.tool)}` : fileNode?.direction === 'input' ? '输入文件' : '输出文件' }}</p>
        <h3 class="break-words text-[15px] font-semibold leading-snug">{{ title }}</h3>
      </div>
      <StatusPill v-if="step?.state" class="shrink-0" :status="step.state.status === 'repairing' ? 'running' : step.state.status" :label="statusLabel" />
    </header>

    <div class="flow-inspector-body">
      <template v-if="step">
        <p v-if="step.description" class="whitespace-pre-wrap break-words text-[13px] leading-7 text-ink">{{ step.description }}</p>
        <p v-if="step.explanation && step.explanation !== step.description" class="rounded-xl bg-[#6265dc]/[0.06] px-3 py-2.5 text-xs leading-6 text-mute">{{ step.explanation }}</p>

        <section v-if="step.inputNames.length || step.output" class="flow-inspector-section">
          <h4>输入与输出</h4>
          <dl class="flow-inspector-bindings">
            <template v-if="step.inputNames.length"><dt>输入</dt><dd><span v-for="name in step.inputNames" :key="name" class="flow-inspector-chip">{{ name }}</span></dd></template>
            <template v-if="step.output"><dt>输出</dt><dd><span class="flow-inspector-chip">{{ step.output }}</span></dd></template>
          </dl>
        </section>

        <section v-if="step.report" class="flow-inspector-section">
          <h4>执行结果</h4>
          <div class="grid grid-cols-2 gap-2">
            <div v-for="[name, rows] in inputRows" :key="name" class="flow-inspector-stat"><span class="truncate" :title="name">{{ name }}</span><strong>{{ rows.toLocaleString() }} <small>行输入</small></strong></div>
            <div v-if="step.report.rows_out !== undefined" class="flow-inspector-stat"><span>输出</span><strong>{{ step.report.rows_out.toLocaleString() }} <small>行</small></strong></div>
            <div v-if="step.report.duration_ms !== undefined" class="flow-inspector-stat"><span>耗时</span><strong>{{ duration(step.report.duration_ms) || '<1ms' }}</strong></div>
          </div>
        </section>

        <p v-if="stepError" class="rounded-xl bg-error/5 px-3 py-2.5 text-xs leading-6 text-error" role="alert">{{ stepError }}</p>
        <ul v-if="step.report?.warnings?.length" class="space-y-2">
          <li v-for="(warning, index) in step.report.warnings" :key="index" class="flex gap-2 rounded-xl bg-warning/10 px-3 py-2 text-xs leading-6 text-[#8a5a00]"><TriangleAlert class="mt-1 size-3.5 shrink-0" />{{ warning }}</li>
        </ul>
        <ul v-if="step.report?.checks?.length" class="space-y-1.5">
          <li v-for="(check, index) in step.report.checks" :key="index" class="flex gap-2 text-xs leading-6" :class="check.ok ? 'text-success' : 'text-error'"><Check v-if="check.ok" class="mt-1 size-3.5 shrink-0" /><TriangleAlert v-else class="mt-1 size-3.5 shrink-0" />{{ check.message }}</li>
        </ul>

        <section v-if="step.formulaName && recipeToolKind(step.tool) === 'formula'" class="flow-inspector-section">
          <h4>公式 <LoaderCircle v-if="loadingFormula" class="size-3.5 animate-spin text-[#6265dc]" /></h4>
          <p class="break-words text-[13px] font-medium leading-6">{{ formulaTitle }}</p>
          <p v-if="formulaDefinition?.intent && formulaDefinition.intent !== step.description" class="mt-1 text-xs leading-6 text-mute">{{ formulaDefinition.intent }}</p>
          <p v-if="step.tool.formula" class="mt-1 break-all text-[10px] text-faint">公式标识：<span class="font-mono">{{ step.tool.formula }}</span></p>
        </section>
        <section v-if="Object.keys(params).length" class="flow-inspector-section">
          <h4>{{ isPython ? '脚本参数' : '处理参数' }}</h4>
          <dl v-if="Object.keys(params).length <= 8" class="space-y-3">
            <div v-for="(value, key) in params" :key="key"><dt class="mb-1 break-words text-[11px] text-[#6265dc]">{{ formulaDefinition?.params[key]?.desc || key }}</dt><dd class="whitespace-pre-wrap break-words rounded-lg bg-paper px-3 py-2 font-mono text-xs leading-6">{{ paramValue(value) }}</dd></div>
          </dl>
          <pre v-else class="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-paper p-3 font-mono text-[11px] leading-6">{{ paramsText }}</pre>
        </section>

        <section v-if="sql" class="flow-inspector-section"><h4>SQL</h4><FlowCodeBlock :code="sql" language="sql" /></section>
        <section v-if="code && !loadedScripts.some((script) => script.code.trim() === code.trim())" class="flow-inspector-section"><h4>Python 脚本</h4><FlowCodeBlock :code="code" language="python" /></section>
        <section v-if="scripts.length" class="flow-inspector-section">
          <h4>Python 脚本</h4>
          <p v-if="loadingScripts" class="flex items-center gap-2 py-3 text-xs text-mute" role="status"><LoaderCircle class="size-3.5 animate-spin text-[#6265dc]" />正在加载脚本…</p>
          <div v-for="script in loadedScripts" :key="script.script_id" class="mb-3"><FlowCodeBlock :code="script.code" language="python" :title="scriptTitle(script.script_title)" /></div>
          <div v-if="scriptError" class="rounded-xl bg-error/5 p-3 text-xs leading-6 text-error" role="alert"><p>{{ scriptError }}</p><button type="button" class="mt-1 font-medium underline underline-offset-4" @click="scriptRetry++">重新加载</button></div>
        </section>
        <p v-if="loadingRecipe" class="flex items-center gap-2 text-xs text-mute" role="status"><LoaderCircle class="size-3.5 animate-spin text-[#6265dc]" />正在加载流程详情…</p>
        <p v-else-if="recipeError && !sql && !code && !scripts.length" class="text-xs leading-6 text-mute">{{ recipeError }}</p>

        <section v-if="step.report?.preview?.columns?.length" class="flow-inspector-section">
          <h4>结果预览 <span class="font-normal text-faint">{{ step.report.preview.total.toLocaleString() }} 行</span></h4>
          <DataTable :columns="step.report.preview.columns" :rows="step.report.preview.rows" max-height="260px" dense />
        </section>
        <section v-if="step.report?.stdout || step.report?.stderr" class="flow-inspector-section"><h4>运行日志</h4><pre v-if="step.report.stdout" class="max-h-52 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-paper p-3 font-mono text-[11px] leading-6">{{ step.report.stdout }}</pre><pre v-if="step.report.stderr" class="mt-2 max-h-52 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-error/5 p-3 font-mono text-[11px] leading-6 text-error">{{ step.report.stderr }}</pre></section>
      </template>

      <template v-else-if="fileNode">
        <section v-if="fileNode.pattern || fileNode.aliases.length || fileNode.source || fileNode.sources?.length" class="flow-inspector-section">
          <h4>{{ fileNode.direction === 'input' ? '文件要求' : '输出内容' }}</h4>
          <dl class="flow-inspector-bindings">
            <template v-if="fileNode.pattern"><dt>文件名</dt><dd class="break-all font-mono text-xs">{{ fileNode.pattern }}</dd></template>
            <template v-if="fileNode.aliases.length"><dt>{{ fileNode.direction === 'input' ? '数据表' : '工作表' }}</dt><dd><span v-for="alias in fileNode.aliases" :key="alias" class="flow-inspector-chip">{{ alias }}</span></dd></template>
            <template v-if="fileNode.source || fileNode.sources?.length"><dt>来源</dt><dd><span v-for="source in fileNode.sources?.length ? fileNode.sources : [fileNode.source]" :key="source" class="flow-inspector-chip">{{ source }}</span></dd></template>
          </dl>
          <p v-for="input in inputDescriptions.filter((input) => input.description)" :key="input.alias" class="mt-3 text-xs leading-6 text-mute">{{ input.description }}</p>
          <div v-for="input in inputSpecs.filter((input) => input.required_columns?.length)" :key="input.alias" class="mt-3"><p class="mb-2 text-[11px] text-faint">{{ input.alias }} · 必需列</p><div class="flex flex-wrap gap-1.5"><span v-for="column in input.required_columns" :key="column" class="flow-inspector-chip">{{ column }}</span></div></div>
        </section>

        <section class="flow-inspector-section">
          <h4>{{ fileNode.direction === 'input' ? '已绑定文件' : '生成文件' }}<span v-if="matchedFiles.length > 1" class="font-normal text-faint">{{ matchedFiles.length }} 个匹配文件</span></h4>
          <p v-if="loadingFiles" class="flex items-center gap-2 py-4 text-xs text-mute" role="status"><LoaderCircle class="size-3.5 animate-spin text-[#6265dc]" />正在查找文件…</p>
          <div v-for="file in matchedFiles" :key="file.file_id" class="flow-inspector-file">
            <div class="flex items-start gap-2.5"><FileSpreadsheet class="mt-0.5 size-5 shrink-0 text-[#6265dc]" /><div class="min-w-0 flex-1"><p class="break-words text-[13px] font-medium leading-6">{{ file.filename }}</p><p class="mt-0.5 text-[11px] text-faint">{{ [file.kind_label, file.size ? executionFileSize(file.size) : '', file.sheets.length ? `${file.sheets.length} 个工作表` : ''].filter(Boolean).join(' · ') }}</p></div></div>
            <div v-if="file.sheets.length" class="mt-3 space-y-1"><div v-for="sheet in file.sheets" :key="sheet.sheet" class="flex items-start justify-between gap-2 text-xs leading-6 text-mute"><span class="break-words">{{ sheet.sheet }}</span><span class="shrink-0 tabular-nums text-faint">{{ sheet.rows.toLocaleString() }} 行<template v-if="sheet.cols"> × {{ sheet.cols }} 列</template></span></div></div>
            <div class="mt-3 flex items-center gap-3"><button type="button" class="flow-inspector-preview" :disabled="!!openingFile" @click="preview(file)"><LoaderCircle v-if="openingFile === file.file_id" class="size-3.5 animate-spin" />查看文件</button><a v-if="file.download_url" :href="file.download_url" class="inline-flex items-center gap-1 text-xs text-mute hover:text-[#6265dc]"><ArrowDownToLine class="size-3.5" />下载</a></div>
          </div>
          <p v-if="!loadingFiles && !matchedFiles.length && !fileError" class="rounded-xl bg-paper px-3 py-4 text-xs leading-6 text-mute">{{ fileNode.direction === 'input' ? '上传符合要求的文件后，即可在这里查看。' : context?.running ? '流程完成后，可在这里查看生成文件。' : '尚未生成或绑定此输出文件。' }}</p>
          <div v-if="fileError" class="rounded-xl bg-error/5 p-3 text-xs leading-6 text-error" role="alert"><p>{{ fileError }}</p><button type="button" class="mt-1 font-medium underline underline-offset-4" @click="fileRetry++">重新加载</button></div>
        </section>
      </template>
    </div>
  </section>
</template>

<style scoped>
.flow-inspector { display: flex; width: 100%; min-width: 0; height: 100%; min-height: 0; flex-direction: column; background: #fff; }
.flow-inspector-header { display: flex; flex: none; align-items: center; gap: 11px; border-bottom: 1px solid var(--color-line); padding: 17px 18px; }
.flow-inspector-back { display: flex; width: 28px; height: 30px; flex: none; align-items: center; justify-content: center; border: 1px solid #6265dc22; border-radius: 9px; background: #6265dc08; color: #6265dc; transition: background .15s; }
.flow-inspector-back:hover { background: #6265dc16; }
.flow-inspector-back:focus-visible, .flow-inspector-preview:focus-visible { outline: 2px solid #6265dc; outline-offset: 3px; }
.flow-inspector-body { display: flex; min-height: 0; flex: 1; flex-direction: column; gap: 20px; overflow-y: auto; padding: 20px; overscroll-behavior: contain; }
.flow-inspector-section { min-width: 0; }
.flow-inspector-section h4 { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 10px; font-size: 12px; font-weight: 600; line-height: 1.6; }
.flow-inspector-bindings { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 11px 14px; align-items: start; font-size: 12px; line-height: 1.8; }
.flow-inspector-bindings dt { padding-top: 2px; color: var(--color-faint); }
.flow-inspector-bindings dd { display: flex; min-width: 0; flex-wrap: wrap; gap: 5px; }
.flow-inspector-chip { display: inline-block; max-width: 100%; overflow-wrap: anywhere; border-radius: 6px; background: #6265dc0b; padding: 2px 7px; color: #6265dc; font-size: 11px; line-height: 1.8; }
.flow-inspector-stat { display: flex; min-width: 0; flex-direction: column; gap: 5px; border: 1px solid var(--color-line); border-radius: 11px; padding: 10px 12px; }
.flow-inspector-stat > span { font-size: 10px; color: var(--color-faint); }
.flow-inspector-stat strong { font-size: 17px; font-weight: 500; line-height: 1.5; }
.flow-inspector-stat small { font-size: 10px; font-weight: 400; color: var(--color-mute); }
.flow-inspector-file { margin-bottom: 10px; border: 1px solid #6265dc1c; border-radius: 13px; padding: 13px; }
.flow-inspector-preview { display: inline-flex; align-items: center; justify-content: center; gap: 6px; border-radius: 7px; background: #6265dc; padding: 6px 12px; color: white; font-size: 11px; transition: background .15s; }
.flow-inspector-preview:hover { background: #5356cc; }
.flow-inspector-preview:disabled { cursor: wait; opacity: .6; }
</style>
