<script setup lang="ts">
import { ArrowDownToLine, ChevronRight, Eye, Sheet, LoaderCircle, Pencil, Trash2, TriangleAlert, X } from '@lucide/vue'
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'

import { conversationApi, customerApi, fileApi, recipeApi, runApi, scriptApi } from '@/api'
import { errorText } from '@/api/http'
import type { FileInfo, FormulaInfo, PrivacyColumns, RecipeEditRequest, RecipeFlowData, RecipeJSON, ScriptFile, StepReport } from '@/api/types'
import DataTable from '@/components/common/DataTable.vue'
import PrivacyStatusIcon from '@/components/chat/PrivacyStatusIcon.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import { duration, type StepState } from '@/composables/chatModel'
import { executionFileSize, executionTime } from '@/composables/executionPresentation'
import { formatCount, privacyStatusShort, privacyStatusText } from '@/composables/privacyPresentation'
import { usePrivacyStatus } from '@/composables/usePrivacyStatus'
import { completeInspectorRecipe, inspectorCustomerFiles, inspectorFileIds, inspectorFileMatches, inspectorStates, inspectorStep } from '@/composables/recipeFlowInspector'
import type { FlowSelection, RecipeFlowContext } from '@/composables/recipeFlowInteraction'
import { recipeStepTitle, recipeToolKind, recipeToolLabel } from '@/composables/recipeTools'
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
const emit = defineEmits<{ close: []; preview: [file: FileInfo]; edit: [operation: RecipeEditRequest['operation'], options?: { focus?: 'sql' | 'python' | 'formula' | 'name' }] }>()

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
const title = computed(() => step.value?.name || fileNode.value?.name || '节点详情')
const headerTitle = computed(() => step.value ? `步骤 ${step.value.id} · ${recipeStepTitle({ ...step.value.tool, name: title.value })}` : title.value)
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
/** 校验：通过的只数个数；没通过的才单独列出来（脚本里的校验信息写的是「出问题时的提示」，通过时逐条打勾反而读不懂） */
const failedChecks = computed(() => (step.value?.report?.checks ?? []).filter((check) => !check.ok))
/** 「概况」里的几项重点：输入 / 输出行数、耗时、校验结果 */
const stepFacts = computed(() => {
  const report = step.value?.report
  if (!report) return []
  const facts: { label: string; value: string; tone?: 'ok' | 'bad' }[] = []
  if (inputRows.value.length) facts.push({ label: '输入', value: `${rowsIn.value.toLocaleString()} 行` })
  if (report.rows_out !== undefined) facts.push({ label: '输出', value: `${report.rows_out.toLocaleString()} 行` })
  if (report.duration_ms !== undefined) facts.push({ label: '耗时', value: duration(report.duration_ms) || '<1ms' })
  const total = report.checks?.length ?? 0
  if (total) facts.push(failedChecks.value.length ? { label: '校验', value: `${failedChecks.value.length} 项未通过`, tone: 'bad' } : { label: '校验', value: `${total} 项通过`, tone: 'ok' })
  return facts
})
/** 说明和步骤名一样时不重复显示 */
const showDescription = computed(() => {
  const bare = (value: string) => value.trim().replace(/[\s。.,，；;：:!！?？]+$/, '')
  const text = bare(step.value?.description ?? '')
  return !!text && text !== bare(step.value?.name ?? '')
})
const hasMethod = computed(() => !!step.value && (isFormula.value || !!sql.value || hasInlineCode.value || scripts.value.length > 0 ||
  (Object.keys(params.value).length > 0 && !isFormula.value) || !!step.value.report?.stdout || !!step.value.report?.stderr))

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
/** 文件节点按文件分组渲染；没有匹配到文件但有字段时放一个空位，让「字段」这块仍能显示 */
const fileGroups = computed<(FileInfo | null)[]>(() => (matchedFiles.value.length ? matchedFiles.value : fieldColumns.value.length ? [null] : []))
// 文件的脱敏情况：只在看文件节点时才去取（每个文件只请求一次）
const privacy = usePrivacyStatus(() => (fileNode.value ? matchedFiles.value.map((file) => file.file_id) : []))
// 脱敏明细（哪些列被保护、什么类型）：只给已经识别过的文件取，没识别过的文件取明细会触发识别，这里不做
const privacyColumns = reactive<Record<string, PrivacyColumns>>({})
const privacyLoading = new Set<string>()
async function loadPrivacyColumns(id: string, force = false) {
  if (!force && (privacyColumns[id] || privacyLoading.has(id))) return
  privacyLoading.add(id)
  try {
    const data = await fileApi.privacyColumns(id, props.context?.conversationId ?? undefined)
    if (mounted) privacyColumns[id] = data
  } catch { /* 拿不到明细就只显示概况 */ } finally { privacyLoading.delete(id) }
}
watch(() => matchedFiles.value.filter((file) => privacy[file.file_id]?.state === 'ready').map((file) => file.file_id).join(','), (ids) => {
  for (const id of ids ? ids.split(',') : []) void loadPrivacyColumns(id)
}, { immediate: true })
async function refreshPrivacy(id: string) {   // 在脱敏维护里改过之后：状态和明细都重新取
  try {
    const status = (await fileApi.privacyStatus([id]))[id]
    if (status) privacy[id] = status
  } catch { /* 保持原来的 */ }
  await loadPrivacyColumns(id, true)
}
/** 被脱敏的列，按工作表分组；脱敏值数：文件里真的换成令牌的格子数，数不出来时（比如旧格式没法原地改）用行数，和聊天页一致 */
const maskedGroups = (id: string) => (privacyColumns[id]?.sheets ?? [])
  .map((sheet) => ({
    sheet: sheet.sheet,
    columns: sheet.columns.filter((column) => column.masked).map((column) => ({ ...column, values: column.tokens || sheet.rows })),
  }))
  .filter((group) => group.columns.length)
const pickedSheet = reactive<Record<string, string>>({})    // 每个文件当前看的工作表
const activeGroup = (id: string) => maskedGroups(id).find((group) => group.sheet === pickedSheet[id]) ?? maskedGroups(id)[0]
/** 一句话汇总：已脱敏 N 列 · M 个值（明细还没取到时先只写列数） */
function privacySummary(id: string): string {
  const status = privacy[id]
  if (!status) return ''
  if (status.state !== 'ready' || !privacyColumns[id]) return privacyStatusShort(status)
  const columns = maskedGroups(id).flatMap((group) => group.columns)
  return columns.length ? `已脱敏 ${columns.length} 列 · ${formatCount(columns.reduce((total, column) => total + column.values, 0))} 个值` : '无需脱敏'
}
function privacyTone(id: string): string {
  const status = privacy[id]
  if (status?.state === 'failed') return 'is-warn'
  return maskedGroups(id).length || (status?.state === 'ready' && !privacyColumns[id] && status.columns > 0) ? 'is-ok' : 'is-mute'
}
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

// 输入 / 输出文件：文件名不能在这里直接改，要用底部的「AI 改名」
/** 输入文件的字段：优先必需列，没有时用录制时看到的列 */
const fieldColumns = computed(() => [...new Set(inputSpecs.value.flatMap((input) => input.required_columns?.length ? input.required_columns : input.expected_columns ?? []))])

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
      <h3 class="min-w-0 flex-1 break-words text-base font-bold leading-snug" :title="step ? title : undefined">{{ headerTitle }}</h3>
      <button type="button" class="flow-inspector-close" aria-label="关闭" title="关闭" @click="emit('close')"><X class="size-4" /></button>
    </header>

    <div class="flow-inspector-body">

      <!-- 步骤：每个模块 = 大标题（在卡片外面）+ 一张包着内容的卡片；校验明细、代码、日志都收进「处理方式」，默认不展开 -->
      <template v-if="step">
        <div v-if="stepError || failedChecks.length || step.report?.warnings?.length" class="fi-alerts">
          <p v-if="stepError" class="rounded-xl bg-error/5 px-3 py-2.5 text-xs leading-6 text-error" role="alert">{{ stepError }}</p>
          <p v-for="(check, index) in failedChecks" :key="`check:${index}`" class="flex gap-2 rounded-xl bg-error/5 px-3 py-2 text-xs leading-6 text-error" role="alert"><TriangleAlert class="mt-1 size-3.5 shrink-0" />{{ check.message }}</p>
          <p v-for="(warning, index) in step.report?.warnings ?? []" :key="`warning:${index}`" class="flex gap-2 rounded-xl bg-warning/10 px-3 py-2 text-xs leading-6 text-[#8a5a00]"><TriangleAlert class="mt-1 size-3.5 shrink-0" />{{ warning }}</p>
        </div>

        <section class="fi-module" data-testid="step-result">
          <div class="fi-module-head"><h4>概况</h4></div>
          <div class="fi-card">
            <div class="fi-tags">
              <span class="inline-flex shrink-0 items-center rounded-lg bg-[#6265DC]/10 px-2.5 py-1 text-xs font-semibold text-[#6265DC]">{{ recipeToolLabel(step.tool) }}</span>
              <StatusPill v-if="step.state" tag :status="step.state.status === 'repairing' ? 'running' : step.state.status" :label="statusLabel" />
            </div>
            <dl v-if="stepFacts.length" class="fi-facts fi-facts--below">
              <div v-for="fact in stepFacts" :key="fact.label"><dt>{{ fact.label }}</dt><dd class="tabular-nums" :class="fact.tone ? `is-${fact.tone}` : ''">{{ fact.value }}</dd></div>
            </dl>
          </div>
        </section>

        <section v-if="showDescription" class="fi-module" data-testid="step-description">
          <div class="fi-module-head"><h4>说明</h4></div>
          <div class="fi-card"><p class="fi-text">{{ step.description }}</p></div>
        </section>

        <section v-if="friendlyInputs.length" class="fi-module" data-testid="step-inputs">
          <div class="fi-module-head"><h4>使用的数据</h4></div>
          <div class="fi-card">
            <ul class="fi-plain"><li v-for="name in friendlyInputs" :key="name">{{ name }}</li></ul>
          </div>
        </section>

        <section v-if="step.report?.preview?.columns?.length" class="fi-module" data-testid="step-preview">
          <div class="fi-module-head"><h4>结果预览<small>{{ step.report.preview.total.toLocaleString() }} 行</small></h4></div>
          <div class="fi-card is-flush"><DataTable flat :columns="step.report.preview.columns" :rows="step.report.preview.rows" max-height="260px" dense /></div>
        </section>

        <section v-if="stepFiles.length" class="fi-module" data-testid="step-files">
          <div class="fi-module-head"><h4>过程文件<small>{{ stepFiles.length }}</small></h4></div>
          <div class="fi-card">
            <ul class="fi-tiles">
              <li v-for="file in stepFiles" :key="file.file_id">
                <span class="flex min-w-0 items-center gap-2"><Sheet class="size-4 shrink-0 text-faint" /><span class="fi-tile-name truncate" :title="file.filename">{{ file.filename }}</span></span>
                <span class="fi-tile-tools">
                  <button type="button" class="icon-btn size-7" :disabled="!!openingFile" title="查看文件" :aria-label="`查看过程文件 ${file.filename}`" @click="preview(file)"><LoaderCircle v-if="openingFile === file.file_id" class="size-4 animate-spin" /><Eye v-else class="size-4" /></button>
                  <a :href="file.download_url" class="icon-btn size-7" title="下载" :aria-label="`下载过程文件 ${file.filename}`"><ArrowDownToLine class="size-4" /></a>
                </span>
              </li>
            </ul>
            <p v-if="fileError" class="mt-2 text-xs leading-6 text-error" role="alert">{{ fileError }}</p>
          </div>
        </section>

        <!-- 处理方式：每一项一行，点开才看；只有公式（几句话 + 参数）默认展开，代码和日志默认收起 -->
        <section v-if="hasMethod" class="fi-module" data-testid="step-method">
          <div class="fi-module-head"><h4>处理方式</h4></div>
          <div class="fi-card fi-card--list">
            <details v-if="isFormula" class="fi-more" open>
              <summary><ChevronRight class="size-3.5" />公式<LoaderCircle v-if="loadingFormula" class="size-3.5 animate-spin text-[#6265dc]" /></summary>
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
              <summary><ChevronRight class="size-3.5" />{{ isPython ? '脚本参数' : '处理参数' }}<small>{{ Object.keys(params).length }}</small></summary>
              <div class="fi-more-body">
                <dl v-if="Object.keys(params).length <= 8" class="fi-kv">
                  <template v-for="(value, key) in params" :key="key"><dt>{{ key }}</dt><dd><code>{{ paramValue(value) }}</code></dd></template>
                </dl>
                <pre v-else class="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-white p-3 font-mono text-[11px] leading-6">{{ paramsText }}</pre>
              </div>
            </details>
            <details v-if="sql" class="fi-more">
              <summary><ChevronRight class="size-3.5" />SQL<small>{{ lineCount(sql) }} 行</small></summary>
              <div class="fi-more-body"><FlowCodeBlock :code="sql" language="sql" /></div>
            </details>
            <details v-if="hasInlineCode" class="fi-more">
              <summary><ChevronRight class="size-3.5" />Python 脚本<small>{{ lineCount(code) }} 行</small></summary>
              <div class="fi-more-body"><FlowCodeBlock :code="code" language="python" /></div>
            </details>
            <details v-if="scripts.length" class="fi-more">
              <summary><ChevronRight class="size-3.5" />Python 脚本<small v-if="loadedScripts.length">{{ loadedScripts.reduce((total, script) => total + lineCount(script.code), 0) }} 行</small></summary>
              <div class="fi-more-body">
                <p v-if="loadingScripts" class="flex items-center gap-2 py-3 text-xs text-mute" role="status"><LoaderCircle class="size-3.5 animate-spin text-[#6265dc]" />正在加载脚本…</p>
                <div v-for="script in loadedScripts" :key="script.script_id" class="mb-3 last:mb-0"><FlowCodeBlock :code="script.code" language="python" :title="scriptTitle(script.script_title)" /></div>
                <div v-if="scriptError" class="rounded-xl bg-error/5 p-3 text-xs leading-6 text-error" role="alert"><p>{{ scriptError }}</p><button type="button" class="mt-1 font-medium underline underline-offset-4" @click="scriptRetry++">重新加载</button></div>
              </div>
            </details>
            <details v-if="step.report?.stdout || step.report?.stderr" class="fi-more">
              <summary><ChevronRight class="size-3.5" />运行日志</summary>
              <div class="fi-more-body"><pre v-if="step.report.stdout" class="max-h-52 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-white p-3 font-mono text-[11px] leading-6">{{ step.report.stdout }}</pre><pre v-if="step.report.stderr" class="mt-2 max-h-52 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-error/5 p-3 font-mono text-[11px] leading-6 text-error">{{ step.report.stderr }}</pre></div>
            </details>
          </div>
        </section>
        <p v-if="loadingRecipe" class="flex items-center gap-2 text-xs text-mute" role="status"><LoaderCircle class="size-3.5 animate-spin text-[#6265dc]" />正在加载流程详情…</p>
        <p v-else-if="recipeError && !sql && !code && !scripts.length" class="text-xs leading-6 text-mute">{{ recipeError }}</p>
      </template>

      <!-- 输入 / 输出文件：每个模块 = 大标题（在卡片外面）+ 一张包着内容的卡片，模块之间留出间距 -->
      <template v-else-if="fileNode">
        <p v-if="loadingFiles" class="flex items-center gap-2 py-4 text-xs text-mute" role="status"><LoaderCircle class="size-3.5 animate-spin text-[#6265dc]" />正在查找文件…</p>
        <p v-if="!loadingFiles && !matchedFiles.length && !fileError" class="py-6 text-center text-sm text-faint">{{ fileNode.direction === 'input' ? '还没有文件' : context?.running ? '流程完成后可在这里查看' : '还没有生成文件' }}</p>
        <div v-if="fileError" class="rounded-xl bg-error/5 p-3 text-xs leading-6 text-error" role="alert"><p>{{ fileError }}</p><button type="button" class="mt-1 font-medium underline underline-offset-4" @click="fileRetry++">重新加载</button></div>

        <div v-for="(file, index) in fileGroups" :key="file?.file_id ?? 'fields'" class="fi-file">
          <p v-if="file && matchedFiles.length > 1" class="fi-file-name" :title="file.filename">{{ file.filename }}</p>
          <template v-if="file">
            <section class="fi-module" data-testid="file-info">
              <div class="fi-module-head">
                <h4>文件信息</h4>
                <span class="fi-module-tools">
                  <button type="button" class="icon-btn size-7" :disabled="!!openingFile" title="查看文件" aria-label="查看文件" @click="preview(file)"><LoaderCircle v-if="openingFile === file.file_id" class="size-4 animate-spin" /><Eye v-else class="size-4" /></button>
                  <a v-if="fileNode.direction === 'output' && file.download_url" :href="file.download_url" class="icon-btn size-7" title="下载" aria-label="下载文件"><ArrowDownToLine class="size-4" /></a>
                </span>
              </div>
              <div class="fi-card">
                <dl class="fi-facts">
                  <div><dt>类型</dt><dd>{{ file.kind_label || (fileNode.direction === 'input' ? '输入文件' : '输出文件') }}</dd></div>
                  <div v-if="file.size > 0"><dt>大小</dt><dd class="tabular-nums">{{ executionFileSize(file.size) }}</dd></div>
                  <div v-if="file.created_at"><dt>{{ file.kind === 'output' ? '生成时间' : '上传时间' }}</dt><dd class="tabular-nums">{{ executionTime(file.created_at) }}</dd></div>
                </dl>
              </div>
            </section>

            <section v-if="file.sheets.length" class="fi-module" data-testid="file-sheets">
              <div class="fi-module-head"><h4>工作表<small>{{ file.sheets.length }}</small></h4></div>
              <div class="fi-card">
                <ul class="fi-tiles">
                  <li v-for="(sheet, sheetIndex) in file.sheets" :key="sheet.sheet">
                    <span class="min-w-0"><small class="fi-tile-no">Sheet {{ sheetIndex + 1 }}</small><span class="fi-tile-name">{{ sheet.sheet }}</span></span>
                    <span class="fi-tile-meta"><b>{{ sheet.rows.toLocaleString() }}</b> 行<template v-if="sheet.cols"> · <b>{{ sheet.cols }}</b> 列</template></span>
                  </li>
                </ul>
              </div>
            </section>
          </template>

          <section v-if="index === 0 && fieldColumns.length" class="fi-module" data-testid="file-fields">
            <div class="fi-module-head"><h4>字段<small>{{ fieldColumns.length }}</small></h4></div>
            <div class="fi-card"><div class="flex flex-wrap gap-1.5"><span v-for="column in fieldColumns" :key="column" class="flow-inspector-chip">{{ column }}</span></div></div>
          </section>

          <section v-if="file && privacy[file.file_id]" class="fi-module" data-testid="file-privacy">
            <div class="fi-module-head">
              <h4>脱敏</h4>
              <span class="fi-module-tools"><PrivacyStatusIcon :status="privacy[file.file_id]" :file="file" :conversation-id="context?.conversationId ?? undefined" elevated @changed="refreshPrivacy(file.file_id)" /></span>
            </div>
            <div class="fi-card">
              <p class="fi-privacy-summary" :class="privacyTone(file.file_id)" :title="privacyStatusText(privacy[file.file_id])">{{ privacySummary(file.file_id) }}</p>
              <template v-if="activeGroup(file.file_id)">
                <div v-if="maskedGroups(file.file_id).length > 1" class="fi-tabs" role="tablist" :aria-label="`${file.filename} 的工作表`">
                  <button
                    v-for="group in maskedGroups(file.file_id)" :key="group.sheet" type="button" role="tab" class="fi-tab"
                    :class="{ 'is-active': activeGroup(file.file_id)!.sheet === group.sheet }" :aria-selected="activeGroup(file.file_id)!.sheet === group.sheet" :title="group.sheet"
                    @click="pickedSheet[file.file_id] = group.sheet"
                  ><span class="max-w-40 truncate">{{ group.sheet }}</span></button>
                </div>
                <table class="fi-table" role="tabpanel">
                  <thead><tr><th>字段</th><th>类型</th><th>脱敏值数</th></tr></thead>
                  <tbody>
                    <tr v-for="column in activeGroup(file.file_id)!.columns" :key="column.name">
                      <td class="break-all font-medium text-ink">{{ column.name }}</td>
                      <td class="text-mute">{{ column.kind_label || '自定义' }}</td>
                      <td class="tabular-nums text-ink">{{ formatCount(column.values) }}</td>
                    </tr>
                  </tbody>
                </table>
              </template>
            </div>
          </section>
        </div>
      </template>
    </div>

    <footer v-if="(fileNode && editable) || (step && aiSection)" class="flow-inspector-footer">
      <template v-if="fileNode">
        <button type="button" class="btn-ink h-9 gap-1.5 px-4 py-0 text-[13px]" title="用 AI 修改文件名" @click="emit('edit', 'modify', { focus: 'name' })"><Pencil class="size-4" />AI 改名</button>
        <button type="button" class="btn-ink h-9 gap-1.5 px-4 py-0 text-[13px]" title="从流程中删除" @click="emit('edit', 'remove')"><Trash2 class="size-4" />删除</button>
      </template>
      <button v-else type="button" class="btn-ink h-9 gap-1.5 px-4 py-0 text-[13px]" title="用 AI 修改这一步" @click="emit('edit', 'modify', { focus: aiFocus })"><Pencil class="size-4" />AI 修改</button>
    </footer>
  </section>
</template>

<style scoped>
.flow-inspector { display: flex; width: 100%; min-width: 0; height: 100%; min-height: 0; flex: 1 1 auto; flex-direction: column; background: #fff; }
.flow-inspector-header { display: flex; flex: none; align-items: flex-start; gap: 11px; border-bottom: 1px solid var(--color-line); padding: 15px 18px; }
.flow-inspector-header h3 { padding: 5px 0; }
.flow-inspector-close { display: flex; width: 32px; height: 32px; flex: none; align-items: center; justify-content: center; border-radius: 10px; color: var(--color-mute); transition: background .15s, color .15s; }
.flow-inspector-close:hover { background: var(--color-paper); color: var(--color-ink); }
.flow-inspector-close:focus-visible { outline: 2px solid #6265dc; outline-offset: 3px; }
.flow-inspector-body { display: flex; min-height: 0; flex: 1; flex-direction: column; gap: 28px; overflow-y: auto; padding: 22px 18px; overscroll-behavior: contain; }
/* 底部操作条：固定在面板底部，不跟着内容滚动 */
.flow-inspector-footer { display: flex; flex: none; gap: 8px; border-top: 1px solid var(--color-line); padding: 12px 16px; }

/* 每个模块 = 大标题（在卡片外面，右边放操作图标）+ 一张包着内容的卡片；模块之间留出较大的间距，标题离自己的卡片更近 */
.fi-module { min-width: 0; }
.fi-module-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-height: 32px; margin: 0 2px 10px; }
.fi-module-head h4 { display: flex; min-width: 0; align-items: baseline; gap: 8px; margin: 0; color: #14141c; font-size: 18px; font-weight: 700; line-height: 1.35; }
.fi-module-head h4 small { color: #a4a4b2; font-size: 13px; font-weight: 500; }
.fi-module-tools { display: flex; flex: none; align-items: center; gap: 2px; margin-right: -6px; }
.fi-card { min-width: 0; border: 1px solid var(--color-line); border-radius: 16px; background: #fff; padding: 16px 18px; }
.fi-card.is-flush { overflow: hidden; padding: 0; }
.fi-card--list { padding: 12px; }
.fi-alerts { display: flex; flex-direction: column; gap: 8px; }
.fi-text { margin: 0; color: #2a2a38; font-size: 14px; line-height: 1.75; overflow-wrap: anywhere; white-space: pre-wrap; }
.flow-inspector-chip { display: inline-block; max-width: 100%; overflow-wrap: anywhere; border-radius: 7px; background: #f4f4f8; padding: 3px 9px; color: #4a4a58; font-size: 12px; line-height: 1.6; }

/* 几项重点并排，标签在上、内容在下，没有分隔线 */
.fi-facts { display: flex; flex-wrap: wrap; gap: 14px 36px; margin: 0; }
.fi-facts dt { color: #8a8a98; font-size: 12px; line-height: 1.4; }
.fi-facts dd { margin: 4px 0 0; color: #14141c; font-size: 14px; font-weight: 500; line-height: 1.5; overflow-wrap: anywhere; }
.fi-tags { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.fi-facts--below { margin-top: 16px; }
.fi-facts dd.is-ok { color: #318b76; }
.fi-facts dd.is-bad { color: var(--color-error); }
/* 子项：每一项一块浅色底（工作表、使用的数据、过程文件） */
.fi-tiles { display: flex; min-width: 0; flex-direction: column; gap: 8px; margin: 0; padding: 0; list-style: none; }
.fi-tiles li { display: flex; align-items: center; justify-content: space-between; gap: 12px; border-radius: 12px; background: #f7f7f9; padding: 10px 14px; color: #7a7a88; font-size: 12.5px; line-height: 1.5; }
.fi-tile-no { display: block; color: #9a9aa6; font-size: 11px; letter-spacing: .02em; line-height: 1.4; }
.fi-tile-name { display: block; min-width: 0; color: #14141c; font-size: 14px; font-weight: 600; overflow-wrap: anywhere; }
.fi-tile-meta { flex: none; font-variant-numeric: tabular-nums; }
.fi-tile-meta b { color: #14141c; font-size: 14px; font-weight: 700; }
.fi-plain { display: flex; flex-direction: column; gap: 8px; margin: 0; padding: 0; list-style: none; }
.fi-plain li { color: #14141c; font-size: 14px; font-weight: 500; line-height: 1.6; overflow-wrap: anywhere; }
.fi-tile-tools { display: flex; flex: none; align-items: center; gap: 2px; margin-right: -6px; }
.fi-tile-tools .icon-btn:hover { background: #fff; }

/* 文件节点：多个文件时，每个文件一条灰色文件名条，下面各自一套模块 */
.fi-file { display: flex; min-width: 0; flex-direction: column; gap: 28px; }
.fi-file + .fi-file { margin-top: 12px; }
.fi-file-name { min-width: 0; overflow: hidden; border-radius: 10px; background: #f4f4f8; padding: 8px 12px; color: #4a4a58; font-size: 13px; font-weight: 600; line-height: 1.5; text-overflow: ellipsis; white-space: nowrap; }

/* 脱敏：一句话汇总 + 和聊天页同款的表（字段 / 类型 / 脱敏值数），多张工作表用 tab 切换 */
.fi-privacy-summary { margin: 0; font-size: 14px; font-weight: 600; line-height: 1.5; }
.fi-privacy-summary.is-ok { color: #318b76; }
.fi-privacy-summary.is-warn { color: #b7770c; }
.fi-privacy-summary.is-mute { color: #8a8a98; font-weight: 500; }
.fi-tabs { display: flex; gap: 4px; margin: 12px 0 0; overflow-x: auto; }
.fi-tab { display: inline-flex; flex: none; align-items: center; border-radius: 9999px; padding: 2px 10px; color: var(--color-mute); font-size: 12px; line-height: 20px; transition: color .15s, background-color .15s; }
.fi-tab:hover { background: #f4f4f8; color: var(--color-ink); }
.fi-tab.is-active { background: var(--color-ink); color: #fff; }
.fi-tab:focus-visible { outline: 2px solid #6265dc; outline-offset: 2px; }
.fi-table { width: 100%; margin-top: 12px; border-collapse: separate; border-spacing: 0; font-size: 13px; line-height: 20px; }
.fi-table th { background: #f7f7f9; padding: 7px 14px; color: #8a8a98; font-size: 12px; font-weight: 400; text-align: left; }
.fi-table th:first-child { border-radius: 10px 0 0 10px; }
.fi-table th:last-child { border-radius: 0 10px 10px 0; }
.fi-table td { padding: 10px 14px; }
.fi-table tbody tr + tr td { border-top: 1px solid #f0f0f4; }
.fi-table tbody tr:last-child td { padding-bottom: 0; }
.fi-table th:last-child, .fi-table td:last-child { text-align: right; }
.fi-table th:not(:first-child), .fi-table td:not(:first-child) { white-space: nowrap; }     /* 字段名太长时只让第一列换行，类型和数量不折断 */

/* 处理方式：每一项一行，点开才看 */
.fi-more { border-radius: 12px; background: #f7f7f9; }
.fi-more + .fi-more { margin-top: 8px; }
.fi-more > summary { display: flex; cursor: pointer; align-items: center; gap: 8px; border-radius: 12px; padding: 11px 14px; color: #14141c; font-size: 14px; font-weight: 600; list-style: none; }
.fi-more > summary::-webkit-details-marker { display: none; }
.fi-more > summary:focus-visible { outline: 2px solid #6265dc; outline-offset: 2px; }
.fi-more > summary svg { flex: none; color: #a4a4b2; transition: transform .15s; }
.fi-more[open] > summary svg:first-child { transform: rotate(90deg); }
.fi-more > summary small { color: #a4a4b2; font-size: 12px; font-weight: 500; font-variant-numeric: tabular-nums; }
.fi-more-body { padding: 2px 12px 12px; }

/* 公式：名称 + 用途 + 键值参数 */
.fi-formula { display: flex; flex-direction: column; gap: 6px; padding: 0 2px; }
.fi-formula-name { margin: 0; color: #14141c; font-size: 14px; font-weight: 700; line-height: 1.6; overflow-wrap: anywhere; }
.fi-formula-intent { margin: 0; color: #666675; font-size: 12.5px; line-height: 1.7; }
.fi-kv { display: grid; grid-template-columns: minmax(0, max-content) minmax(0, 1fr); gap: 8px 14px; margin: 0; font-size: 12.5px; }
.fi-formula .fi-kv { margin-top: 8px; }
.fi-kv dt { color: #7a7a88; line-height: 1.7; overflow-wrap: anywhere; }
.fi-kv dd { display: flex; flex-wrap: wrap; gap: 5px; margin: 0; min-width: 0; }
.fi-kv code { display: inline-block; max-width: 100%; border-radius: 7px; background: #fff; box-shadow: 0 0 0 1px #ececf1; padding: 1px 8px; color: #14141c; font: 12px/1.7 var(--font-mono, monospace); white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
