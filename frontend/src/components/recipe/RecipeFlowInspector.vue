<script setup lang="ts">
import { ArrowDownToLine, ArrowLeft, Check, ChevronRight, Eye, FileSpreadsheet, LoaderCircle, Pencil, Sparkles, Trash2, TriangleAlert, X } from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { conversationApi, customerApi, fileApi, recipeApi, runApi, scriptApi } from '@/api'
import { errorText } from '@/api/http'
import type { FileInfo, FormulaInfo, RecipeEditRequest, RecipeFlowData, RecipeJSON, ScriptFile, StepReport } from '@/api/types'
import DataTable from '@/components/common/DataTable.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import { duration, type StepState } from '@/composables/chatModel'
import { completeInspectorRecipe, inspectorCustomerFiles, inspectorFileIds, inspectorFileMatches, inspectorStates, inspectorStep } from '@/composables/recipeFlowInspector'
import type { FlowSelection, RecipeFlowContext } from '@/composables/recipeFlowInteraction'
import { recipeToolKind, recipeToolLabel } from '@/composables/recipeTools'
import { formatSql } from '@/composables/sqlFormat'
import { scriptReferences, scriptTitle } from '@/composables/scriptPresentation'

import FlowCodeBlock from './FlowCodeBlock.vue'

const props = withDefaults(defineProps<{
  selection: FlowSelection
  flow: RecipeFlowData
  states?: StepState[]
  context?: RecipeFlowContext
  files?: FileInfo[]
  recipe?: RecipeJSON
  editable?: boolean
}>(), { states: () => [], files: () => [] })
const emit = defineEmits<{ close: []; preview: [file: FileInfo]; edit: [operation: RecipeEditRequest['operation'], options?: { focus?: 'sql' | 'python' | 'formula' }]; renamed: [] }>()

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
const selectionKey = computed(() => props.selection.kind === 'step' ? `step:${props.selection.id}` : `file:${props.selection.file.key}`)
const referencedFileIds = computed(() => fileNode.value ? inspectorFileIds(fileNode.value, effectiveStates.value) : [])
const title = computed(() => step.value?.name || renamedTo.value || fileNode.value?.name || '节点详情')
const scripts = computed(() => step.value ? scriptReferences([step.value.flowStep, step.value.planned, step.value.params, step.value.report?.result]) : [])
const isFormula = computed(() => !!step.value?.formulaName && !!step.value && recipeToolKind(step.value.tool) === 'formula')
const hasInlineCode = computed(() => !!code.value && !loadedScripts.value.some((script) => script.code.trim() === code.value.trim()))
/** 「AI 修改」只放在这一步主要的代码区上：公式步骤放公式，SQL 步骤放 SQL，脚本步骤放脚本 */
const aiSection = computed<'formula' | 'sql' | 'script' | null>(() => {
  if (!props.editable || !step.value) return null
  if (isFormula.value) return 'formula'
  if (sql.value) return 'sql'
  if (hasInlineCode.value || scripts.value.length) return 'script'
  return null
})
const aiFocus = computed<'sql' | 'python' | 'formula' | undefined>(() => (aiSection.value === 'formula' ? 'formula' : aiSection.value === 'sql' ? 'sql' : aiSection.value === 'script' ? 'python' : undefined))
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
const sql = computed(() => formatSql(step.value?.sql || formulaDefinition.value?.sql || ''))
const lineCount = (text: string) => text ? text.split('\n').length : 0
const code = computed(() => step.value?.code || '')
const inputRows = computed(() => Object.entries(step.value?.report?.rows_in ?? {}))
/** 这一步用的数据：原始输入显示来源文件名，前一步的结果显示那一步的名称；内部别名一律不展示。 */
const friendlyInputs = computed(() => {
  const current = step.value
  if (!current) return []
  const names = new Set<string>()
  for (const name of current.inputNames) {
    const input = fullRecipe.value?.inputs.find((item) => item.alias === name)
    if (input) { names.add(input.source_file || input.description || ''); continue }
    const previous = props.flow.steps.find((item) => item.output === name)
    if (previous) names.add(previous.name)
  }
  return [...names].filter(Boolean)
})
const rowsIn = computed(() => inputRows.value.reduce((total, [, rows]) => total + rows, 0))
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
const stepFiles = computed<FileInfo[]>(() => {
  const files = new Map<string, FileInfo>()
  for (const artifact of step.value?.report?.output_files ?? []) {
    const id = artifact.file_id
    if (!id) continue
    files.set(id, availableFiles.value.find((file) => file.file_id === id) ?? {
      file_id: id, filename: artifact.filename || artifact.name || '过程文件', kind: 'output', kind_label: '过程文件',
      size: 0, sheets: [], created_at: null, download_url: artifact.download_url || fileApi.downloadUrl(id),
      customer_id: props.context?.customerId ?? null, conversation_id: props.context?.conversationId ?? null,
      run_id: props.context?.runId ?? null, intermediate: true, step_id: step.value?.id,
    })
  }
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

watch([() => props.context?.customerId, () => props.context?.conversationId, () => props.context?.recipeId, () => props.context?.runId], () => { resourceFiles.value = []; resolvedFiles.value = [] })
watch([
  () => selectionKey.value,
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
  const recipeId = props.context.recipeId
  // Customer workflow resources include their conversation inputs and retained step files.
  if (props.context.conversationId && !(props.context.customerId && recipeId)) requests.push(conversationApi.get(props.context.conversationId).then((detail) => detail.files))
  if (props.context.customerId) requests.push(customerApi.get(props.context.customerId).then((customer) => inspectorCustomerFiles(customer, recipeId)))
  const results = await Promise.allSettled(requests)
  if (!active) return
  resourceFiles.value = [...resourceFiles.value, ...results.flatMap((result) => result.status === 'fulfilled' ? result.value : [])]
  if (results.every((result) => result.status === 'rejected')) {
    const failure = results.find((result) => result.status === 'rejected')
    if (failure?.status === 'rejected') fileError.value = errorText(failure.reason)
  }
  loadingFiles.value = false
}, { immediate: true })

// 输入 / 输出文件：在标题处直接改文件名，并同步引用它的流程
const renaming = ref(false)
const renamedTo = ref('')
const draftName = ref('')
const savingName = ref(false)
const renameError = ref('')
const boundFile = computed(() => matchedFiles.value[0])
/** 输入文件的字段：优先必需列，没有时用录制时看到的列 */
const fieldColumns = computed(() => [...new Set(inputSpecs.value.flatMap((input) => input.required_columns?.length ? input.required_columns : input.expected_columns ?? []))])
watch(selectionKey, () => { renaming.value = false; renamedTo.value = ''; renameError.value = '' })

function startRename() {
  draftName.value = renamedTo.value || boundFile.value?.filename || fileNode.value?.name || ''
  renameError.value = ''
  renaming.value = true
}
async function saveName() {
  const file = boundFile.value
  const name = draftName.value.trim()
  if (savingName.value) return
  if (!file || !name || name === (renamedTo.value || file.filename)) { renaming.value = false; return }
  savingName.value = true
  renameError.value = ''
  try {
    const result = await recipeApi.renameFiles([{ file_id: file.file_id, new_name: name }])
    if (!mounted) return
    renamedTo.value = result.files[0]?.new ?? name
    renaming.value = false
    emit('renamed')
  } catch (error) { if (mounted) renameError.value = errorText(error) }
  finally { if (mounted) savingName.value = false }
}

async function preview(file: FileInfo) {
  if (openingFile.value) return
  const key = selectionKey.value
  const generation = previewGeneration
  openingFile.value = file.file_id
  fileError.value = ''
  try {
    const full = fullFiles.value.find((item) => item.file_id === file.file_id) ?? await fileApi.get(file.file_id)
    if (!mounted || generation !== previewGeneration || selectionKey.value !== key) return
    resolvedFiles.value = [...resolvedFiles.value.filter((item) => item.file_id !== full.file_id), full]
    emit('preview', full)
  } catch (error) { if (mounted && generation === previewGeneration && selectionKey.value === key) fileError.value = errorText(error) }
  finally { if (mounted && generation === previewGeneration && selectionKey.value === key) openingFile.value = null }
}

/** 字符串、数字组成的列表：拆成一个个小标签，不显示成多行 JSON */
function paramItems(value: unknown): string[] | null {
  if (!Array.isArray(value) || !value.length || value.length > 40) return null
  return value.every((item) => ['string', 'number', 'boolean'].includes(typeof item)) ? value.map(String) : null
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
        <form v-if="renaming" class="flex items-center gap-1.5" @submit.prevent="saveName">
          <input v-model="draftName" class="input input-sm min-w-0 flex-1 rounded-xl" aria-label="文件名" autofocus @keydown.esc="renaming = false" />
          <button type="submit" class="icon-btn shrink-0 !text-[#6265dc]" :disabled="savingName" aria-label="保存文件名" title="保存"><LoaderCircle v-if="savingName" class="size-4 animate-spin" /><Check v-else class="size-4" /></button>
          <button type="button" class="icon-btn shrink-0" aria-label="取消" title="取消" @click="renaming = false"><X class="size-4" /></button>
        </form>
        <div v-else class="flex items-center gap-1">
          <h3 class="min-w-0 break-words text-base font-bold leading-snug">{{ step ? `步骤 ${step.id} · ${title}` : title }}</h3>
          <span v-if="step" class="inline-flex shrink-0 items-center rounded-md bg-[#6265DC]/10 px-2 py-0.5 text-[11px] font-semibold text-[#6265DC]">{{ recipeToolLabel(step.tool) }}</span>
          <button v-if="fileNode && boundFile" type="button" class="icon-btn size-7 shrink-0" aria-label="修改文件名" title="修改文件名" @click="startRename"><Pencil class="size-3.5" /></button>
        </div>
        <p v-if="renameError" class="mt-1 text-xs text-error" role="alert">{{ renameError }}</p>
      </div>
      <StatusPill v-if="step?.state" class="shrink-0" :status="step.state.status === 'repairing' ? 'running' : step.state.status" :label="statusLabel" />
    </header>

    <div class="flow-inspector-body">

      <!-- 步骤 -->
      <template v-if="step">
        <p v-if="step.description" class="whitespace-pre-wrap break-words text-[14px] leading-[1.8] text-[#2a2a38]">{{ step.description }}</p>

        <p v-if="step.report" class="fi-stats">
          <span v-if="inputRows.length"><b>{{ rowsIn.toLocaleString() }}</b> 行输入</span>
          <span v-if="step.report.rows_out !== undefined"><b>{{ step.report.rows_out.toLocaleString() }}</b> 行输出</span>
          <span v-if="step.report.duration_ms !== undefined">耗时 <b>{{ duration(step.report.duration_ms) || '<1ms' }}</b></span>
        </p>

        <p v-if="stepError" class="rounded-xl bg-error/5 px-3 py-2.5 text-xs leading-6 text-error" role="alert">{{ stepError }}</p>
        <ul v-if="step.report?.warnings?.length" class="space-y-2">
          <li v-for="(warning, index) in step.report.warnings" :key="index" class="flex gap-2 rounded-xl bg-warning/10 px-3 py-2 text-xs leading-6 text-[#8a5a00]"><TriangleAlert class="mt-1 size-3.5 shrink-0" />{{ warning }}</li>
        </ul>
        <ul v-if="step.report?.checks?.length" class="space-y-1.5">
          <li v-for="(check, index) in step.report.checks" :key="index" class="flex gap-2 text-xs leading-6" :class="check.ok ? 'text-success' : 'text-error'"><Check v-if="check.ok" class="mt-1 size-3.5 shrink-0" /><TriangleAlert v-else class="mt-1 size-3.5 shrink-0" />{{ check.message }}</li>
        </ul>

        <section v-if="friendlyInputs.length" class="flow-inspector-section">
          <h4>使用的数据</h4>
          <ul class="fi-list">
            <li v-for="name in friendlyInputs" :key="name">{{ name }}</li>
          </ul>
        </section>

        <section v-if="step.report?.preview?.columns?.length" class="flow-inspector-section">
          <h4>结果预览<small>{{ step.report.preview.total.toLocaleString() }} 行</small></h4>
          <DataTable :columns="step.report.preview.columns" :rows="step.report.preview.rows" max-height="260px" dense />
        </section>

        <section v-if="stepFiles.length" class="flow-inspector-section">
          <h4>过程文件</h4>
          <div v-for="file in stepFiles" :key="file.file_id" class="flex items-center gap-2 rounded-xl bg-paper px-3 py-2.5">
            <FileSpreadsheet class="size-4 shrink-0 text-fun-green" />
            <span class="min-w-0 flex-1 truncate text-xs" :title="file.filename">{{ file.filename }}</span>
            <button type="button" class="shrink-0 text-xs text-[#6265dc] disabled:opacity-50" :disabled="!!openingFile" :aria-label="`查看过程文件 ${file.filename}`" @click="preview(file)"><LoaderCircle v-if="openingFile === file.file_id" class="size-3.5 animate-spin" /><template v-else>查看</template></button>
            <a :href="file.download_url" class="shrink-0 text-mute hover:text-[#6265dc]" :aria-label="`下载过程文件 ${file.filename}`" title="下载"><ArrowDownToLine class="size-3.5" /></a>
          </div>
          <p v-if="fileError" class="mt-2 text-xs leading-6 text-error" role="alert">{{ fileError }}</p>
        </section>

        <!-- 处理方式：公式 / SQL / 脚本默认展开；参数、日志按需展开 -->
        <section v-if="isFormula || sql || hasInlineCode || scripts.length || (Object.keys(params).length && !isFormula) || step.report?.stdout || step.report?.stderr" class="fi-more-group">
          <details v-if="isFormula" class="fi-more" open>
            <summary><ChevronRight class="size-3.5" />公式<LoaderCircle v-if="loadingFormula" class="size-3.5 animate-spin text-[#6265dc]" /><button v-if="aiSection === 'formula'" type="button" class="fi-ai" title="用 AI 修改这一步" @click.prevent.stop="emit('edit', 'modify', { focus: aiFocus })"><Sparkles class="size-3.5" />AI 修改</button></summary>
            <div class="fi-more-body">
              <div class="fi-formula">
                <p class="fi-formula-name">{{ formulaTitle }}</p>
                <p v-if="formulaDefinition?.intent && formulaDefinition.intent !== step.description" class="fi-formula-intent">{{ formulaDefinition.intent }}</p>
                <dl v-if="Object.keys(params).length" class="fi-kv">
                  <template v-for="(value, key) in params" :key="key">
                    <dt>{{ formulaDefinition?.params[key]?.desc || key }}</dt>
                    <dd>
                      <template v-if="paramItems(value)"><code v-for="item in paramItems(value)" :key="item">{{ item }}</code></template>
                      <code v-else>{{ paramValue(value) }}</code>
                    </dd>
                  </template>
                </dl>
              </div>
            </div>
          </details>
          <details v-if="Object.keys(params).length && !isFormula" class="fi-more">
            <summary><ChevronRight class="size-3.5" />{{ isPython ? '脚本参数' : '处理参数' }}</summary>
            <div class="fi-more-body">
              <dl v-if="Object.keys(params).length <= 8" class="fi-kv">
                <template v-for="(value, key) in params" :key="key"><dt>{{ key }}</dt><dd><code>{{ paramValue(value) }}</code></dd></template>
              </dl>
              <pre v-else class="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-paper p-3 font-mono text-[11px] leading-6">{{ paramsText }}</pre>
            </div>
          </details>
          <details v-if="sql" class="fi-more" open>
            <summary><ChevronRight class="size-3.5" />SQL<small>{{ lineCount(sql) }} 行</small><button v-if="aiSection === 'sql'" type="button" class="fi-ai" title="用 AI 修改这一步" @click.prevent.stop="emit('edit', 'modify', { focus: aiFocus })"><Sparkles class="size-3.5" />AI 修改</button></summary>
            <div class="fi-more-body"><FlowCodeBlock :code="sql" language="sql" /></div>
          </details>
          <details v-if="hasInlineCode" class="fi-more" open>
            <summary><ChevronRight class="size-3.5" />Python 脚本<small>{{ lineCount(code) }} 行</small><button v-if="aiSection === 'script'" type="button" class="fi-ai" title="用 AI 修改这一步" @click.prevent.stop="emit('edit', 'modify', { focus: aiFocus })"><Sparkles class="size-3.5" />AI 修改</button></summary>
            <div class="fi-more-body"><FlowCodeBlock :code="code" language="python" /></div>
          </details>
          <details v-if="scripts.length" class="fi-more" open>
            <summary><ChevronRight class="size-3.5" />Python 脚本<small v-if="loadedScripts.length">{{ loadedScripts.reduce((total, script) => total + lineCount(script.code), 0) }} 行</small><button v-if="aiSection === 'script' && !hasInlineCode" type="button" class="fi-ai" title="用 AI 修改这一步" @click.prevent.stop="emit('edit', 'modify', { focus: aiFocus })"><Sparkles class="size-3.5" />AI 修改</button></summary>
            <div class="fi-more-body">
              <p v-if="loadingScripts" class="flex items-center gap-2 py-3 text-xs text-mute" role="status"><LoaderCircle class="size-3.5 animate-spin text-[#6265dc]" />正在加载脚本…</p>
              <div v-for="script in loadedScripts" :key="script.script_id" class="mb-3"><FlowCodeBlock :code="script.code" language="python" :title="scriptTitle(script.script_title)" /></div>
              <div v-if="scriptError" class="rounded-xl bg-error/5 p-3 text-xs leading-6 text-error" role="alert"><p>{{ scriptError }}</p><button type="button" class="mt-1 font-medium underline underline-offset-4" @click="scriptRetry++">重新加载</button></div>
            </div>
          </details>
          <details v-if="step.report?.stdout || step.report?.stderr" class="fi-more">
            <summary><ChevronRight class="size-3.5" />运行日志</summary>
            <div class="fi-more-body"><pre v-if="step.report.stdout" class="max-h-52 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-paper p-3 font-mono text-[11px] leading-6">{{ step.report.stdout }}</pre><pre v-if="step.report.stderr" class="mt-2 max-h-52 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-error/5 p-3 font-mono text-[11px] leading-6 text-error">{{ step.report.stderr }}</pre></div>
          </details>
        </section>
        <p v-if="loadingRecipe" class="flex items-center gap-2 text-xs text-mute" role="status"><LoaderCircle class="size-3.5 animate-spin text-[#6265dc]" />正在加载流程详情…</p>
        <p v-else-if="recipeError && !sql && !code && !scripts.length" class="text-xs leading-6 text-mute">{{ recipeError }}</p>
      </template>

      <!-- 输入 / 输出文件：查看文件、字段、改文件名 -->
      <template v-else-if="fileNode">
        <p v-if="loadingFiles" class="flex items-center gap-2 py-4 text-xs text-mute" role="status"><LoaderCircle class="size-3.5 animate-spin text-[#6265dc]" />正在查找文件…</p>
        <div v-for="file in matchedFiles" :key="file.file_id" class="fi-file">
          <span class="fi-file-icon"><FileSpreadsheet class="size-[18px] text-fun-green" /></span>
          <ul class="fi-sheets">
            <li v-for="sheet in file.sheets" :key="sheet.sheet"><span class="min-w-0 break-words font-medium text-ink">{{ sheet.sheet }}</span><span class="shrink-0 tabular-nums"><b>{{ sheet.rows.toLocaleString() }}</b> 行<template v-if="sheet.cols"> · <b>{{ sheet.cols }}</b> 列</template></span></li>
            <li v-if="!file.sheets.length"><span class="min-w-0 break-words font-medium text-ink">{{ file.filename }}</span></li>
          </ul>
          <button type="button" class="icon-btn shrink-0" :disabled="!!openingFile" title="查看文件" aria-label="查看文件" @click="preview(file)"><LoaderCircle v-if="openingFile === file.file_id" class="size-4 animate-spin" /><Eye v-else class="size-4" /></button>
          <a v-if="fileNode.direction === 'output' && file.download_url" :href="file.download_url" class="icon-btn shrink-0" title="下载" aria-label="下载文件"><ArrowDownToLine class="size-4" /></a>
        </div>
        <p v-if="!loadingFiles && !matchedFiles.length && !fileError" class="py-6 text-center text-sm text-faint">{{ fileNode.direction === 'input' ? '还没有文件' : context?.running ? '流程完成后可在这里查看' : '还没有生成文件' }}</p>
        <div v-if="fileError" class="rounded-xl bg-error/5 p-3 text-xs leading-6 text-error" role="alert"><p>{{ fileError }}</p><button type="button" class="mt-1 font-medium underline underline-offset-4" @click="fileRetry++">重新加载</button></div>

        <section v-if="fieldColumns.length" class="flow-inspector-section">
          <h4>字段<small>{{ fieldColumns.length }}</small></h4>
          <div class="flex flex-wrap gap-1.5"><span v-for="column in fieldColumns" :key="column" class="flow-inspector-chip">{{ column }}</span></div>
        </section>

        <div v-if="editable" class="fi-footer">
          <button type="button" class="btn-text gap-1.5 px-2 text-[13px] !text-error" title="从流程中删除" @click="emit('edit', 'remove')"><Trash2 class="size-4" />删除</button>
        </div>
      </template>
    </div>
  </section>
</template>

<style scoped>
.flow-inspector { display: flex; width: 100%; min-width: 0; height: 100%; min-height: 0; flex: 1 1 auto; flex-direction: column; background: #fff; }
.flow-inspector-header { display: flex; flex: none; align-items: center; gap: 11px; border-bottom: 1px solid var(--color-line); padding: 17px 18px; }
.flow-inspector-back { display: flex; width: 32px; height: 32px; flex: none; align-items: center; justify-content: center; border-radius: 10px; color: var(--color-mute); transition: background .15s, color .15s; }
.flow-inspector-back:hover { background: var(--color-paper); color: var(--color-ink); }
.flow-inspector-back:focus-visible, .flow-inspector-preview:focus-visible { outline: 2px solid #6265dc; outline-offset: 3px; }
.flow-inspector-body { display: flex; min-height: 0; flex: 1; flex-direction: column; gap: 22px; overflow-y: auto; padding: 20px; overscroll-behavior: contain; }
.fi-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 2px; margin: -6px -8px -6px; }

.flow-inspector-section { min-width: 0; }
.flow-inspector-section h4 { display: flex; align-items: baseline; gap: 8px; margin: 0 0 10px; color: #14141c; font-size: 13px; font-weight: 700; line-height: 1.6; }
.flow-inspector-section h4 small { color: #a4a4b2; font-size: 11px; font-weight: 500; }
.flow-inspector-chip { display: inline-block; max-width: 100%; overflow-wrap: anywhere; border-radius: 7px; background: #f4f4f8; padding: 3px 9px; color: #4a4a58; font-size: 12px; line-height: 1.6; }

/* 一行执行概况 */
.fi-stats { display: flex; flex-wrap: wrap; gap: 4px 18px; margin: 0; color: #7a7a88; font-size: 12.5px; }
.fi-stats b { color: #14141c; font-size: 15px; font-weight: 700; font-variant-numeric: tabular-nums; }

/* 使用的数据：朴素的列表 */
.fi-list { margin: 0; padding: 0; list-style: none; }
.fi-list li { position: relative; padding-left: 16px; color: #3a3a48; font-size: 13.5px; line-height: 1.8; overflow-wrap: anywhere; }
.fi-list li + li { margin-top: 4px; }
.fi-list li::before { position: absolute; top: 11px; left: 3px; width: 5px; height: 5px; border-radius: 50%; background: #b7b8ec; content: ''; }

/* 文件行：图标 + 每张工作表的行列数 + 右侧小图标按钮 */
.fi-file { display: flex; align-items: center; gap: 12px; border-radius: 16px; background: #f7f7f9; padding: 14px 10px 14px 14px; }
.fi-file-icon { display: grid; width: 38px; height: 38px; flex: none; place-items: center; border-radius: 12px; background: #fff; box-shadow: 0 0 0 1px #ececf1; }
.fi-sheets { display: flex; min-width: 0; flex: 1; flex-direction: column; gap: 2px; margin: 0; padding: 0; list-style: none; }
.fi-sheets li { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; color: #7a7a88; font-size: 12.5px; line-height: 1.7; }
.fi-sheets b { color: #14141c; font-size: 14px; font-weight: 700; }
.fi-footer { display: flex; gap: 4px; margin: auto -8px -4px; padding: 10px 0 0; border-top: 1px solid var(--color-line); }

/* 折叠的实现细节 */
.fi-more-group { display: flex; flex-direction: column; gap: 6px; }
.fi-more { }
.fi-more > summary { display: flex; cursor: pointer; align-items: center; gap: 6px; padding: 6px 0; color: #4a4a58; font-size: 13px; font-weight: 600; list-style: none; }
.fi-more > summary::-webkit-details-marker { display: none; }
.fi-more > summary:hover { color: #14141c; }
.fi-more > summary svg { color: #a4a4b2; transition: transform .15s; }
.fi-more[open] > summary svg:first-child { transform: rotate(90deg); }
.fi-more > summary small { color: #a4a4b2; font-size: 11px; font-weight: 500; font-variant-numeric: tabular-nums; }
.fi-more-body { padding: 6px 0 4px; }
.fi-ai { display: inline-flex; align-items: center; gap: 4px; margin-left: auto; border-radius: 8px; padding: 3px 9px; color: #6265dc; font-size: 12px; font-weight: 500; transition: background .15s; }
.fi-ai:hover { background: #6265dc14; }
.fi-ai:focus-visible { outline: 2px solid #6265dc; outline-offset: 2px; }

/* 公式：名称 + 用途 + 键值参数 */
.fi-formula { display: flex; flex-direction: column; gap: 6px; border-radius: 14px; background: #f7f7f9; padding: 14px 16px; }
.fi-formula-name { margin: 0; color: #14141c; font-size: 14px; font-weight: 700; line-height: 1.6; overflow-wrap: anywhere; }
.fi-formula-intent { margin: 0; color: #666675; font-size: 12.5px; line-height: 1.7; }
.fi-kv { display: grid; grid-template-columns: minmax(0, max-content) minmax(0, 1fr); gap: 8px 14px; margin: 6px 0 0; padding-top: 12px; border-top: 1px solid #ececf1; font-size: 12.5px; }
.fi-kv dt { color: #7a7a88; line-height: 1.7; overflow-wrap: anywhere; }
.fi-kv dd { display: flex; flex-wrap: wrap; gap: 5px; margin: 0; min-width: 0; }
.fi-kv code { display: inline-block; max-width: 100%; border-radius: 7px; background: #fff; box-shadow: 0 0 0 1px #ececf1; padding: 1px 8px; color: #14141c; font: 12px/1.7 var(--font-mono, monospace); white-space: pre-wrap; overflow-wrap: anywhere; }

.flow-inspector-preview { display: inline-flex; align-items: center; justify-content: center; gap: 6px; border-radius: 9px; background: #6265dc; padding: 7px 14px; color: white; font-size: 12px; transition: background .15s; }
.flow-inspector-preview:hover { background: #5356cc; }
.flow-inspector-preview:disabled { cursor: wait; opacity: .6; }
</style>
