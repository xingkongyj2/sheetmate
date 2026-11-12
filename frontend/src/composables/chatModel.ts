/**
 * 对话消息的界面模型 + 事件归并（纯函数）。
 *
 * 后端推来的事件（文本、工具调用、结果表、Recipe 步骤…）在这里归并成：
 * - content   最终回答（Markdown）
 * - timeline  过程：中间说明、工具调用、计划步骤、多表专员…
 * - artifacts 产物：结果表、文件、处理流程卡片、澄清问题
 * 刷新页面时用数据库里存的事件重新归并，得到同样的界面。
 */
import type { AgentEvent, DiffCard, ExecutionState, PublicOperationDetail, RecipeFlowData, RecipeJSON, RecipeToolKind, ScriptReference, StepReport, VerifyReport } from '@/api/types'
import { scriptReferences } from './scriptPresentation.ts'
import { recipeProcessDetails, type RecipeProcessDetail } from './recipeProcessDetails'
import { mergeExecutionStop, readExecutionState } from './executionStatus.ts'
import type { FileDeliveryStatus } from './deliveryFiles'

export type ToolStatus = 'running' | 'ok' | 'error'
export type PlanStatus = 'pending' | 'running' | 'done' | 'warning' | 'waiting' | 'error' | 'stopped'
export type AgentStatus = 'running' | 'done' | 'error' | 'incomplete' | 'waiting' | 'cancelled' | 'interrupted' | 'unknown'

export const AGENT_STATUS_LABELS: Record<AgentStatus, string> = {
  running: '执行中', done: '已完成', error: '失败', incomplete: '未完成', waiting: '待确认',
  cancelled: '已停止', interrupted: '已中断', unknown: '状态未知',
}

function agentStatus(event: AgentEvent): AgentStatus {
  if (event.error || event.status === 'failed' || event.status === 'error') return 'error'
  if (event.status == null || event.status === 'completed' || event.status === 'done') return 'done'
  return ['running', 'incomplete', 'waiting', 'cancelled', 'interrupted'].includes(event.status) ? event.status : 'unknown'
}

/** 专员的未完成状态也传递给所属过程块，不能被显示成已完成。 */
export function agentStageStatus(items: TimelineItem[]): PlanStatus | undefined {
  const states = items.flatMap((item) => item.kind === 'agent' ? [item.status] : [])
  if (states.includes('running')) return 'running'
  if (states.includes('error')) return 'error'
  if (states.some((status) => status === 'incomplete' || status === 'unknown')) return 'warning'
  if (states.some((status) => status === 'cancelled' || status === 'interrupted')) return 'stopped'
  if (states.includes('waiting')) return 'waiting'
  return states.length ? 'done' : undefined
}

export type TimelineItem =
  | { kind: 'note'; key: string; text: string; step?: number }
  | { kind: 'thinking'; key: string; text: string; step?: number }
  | { kind: 'script'; key: string; script: ScriptReference; step?: number }
  | {
      kind: 'tool'
      key: string
      id: string
      tool: string
      label: string
      params: Record<string, unknown>
      status: ToolStatus
      /** 参数校验未通过，调用尚未执行；不能作为一次流程执行尝试。 */
      errorKind?: 'protocol' | 'schema'
      summary?: string
      text?: string
      duration_ms?: number
      details?: PublicOperationDetail[]
      step?: number
    }
  | { kind: 'plan'; key: string; num: number; total: number; description: string; status: PlanStatus; summary?: string; detail?: string }
  | { kind: 'agent'; key: string; id: string; table: string; status: AgentStatus; conclusion?: string }
  | { kind: 'info'; key: string; text: string; tone?: 'memory' | 'mapping' | 'compact' | 'reflect' | 'aggregate'; step?: number }

export interface StepState {
  step_id: number
  name: string
  formula: string
  formula_name: string
  kind?: RecipeToolKind
  formula_kind: string
  description: string
  output: string
  status: 'pending' | 'running' | 'success' | 'warning' | 'repaired' | 'repairing' | 'failed'
  report?: StepReport
  error?: string
  repairs: { attempt: number; status: string; explanation?: string }[]
}

export interface RecipeProgress {
  key: string
  toolCallId?: string
  mode: 'learn' | 'revise' | 'replay'
  status: 'thinking' | 'running' | 'verifying' | 'refining' | 'done' | 'failed'
  round: number
  thinking?: string
  evidence?: DiffCard[]
  recipe?: RecipeJSON
  flow?: RecipeFlowData
  questions?: { question: string; options?: string[] }[]
  runId?: string
  recipeId?: string
  recipeName?: string
  recipeVersion?: number
  recipePersisted?: boolean
  deleted?: boolean
  steps: StepState[]
  details?: RecipeProcessDetail[]
  inputs: { alias: string; kind?: 'table' | 'file'; filename: string; sheet: string | null; rows: number; renames: Record<string, string>; warnings: string[] }[]
  outputs?: { sheet: string; rows: number }[]
  validation?: { status: string; summary: string; checks: { status: string; description: string; message: string }[] }
  verify?: VerifyReport
  verifyHistory: VerifyReport[]
  diff?: DiffCard[]
  runStatus?: string
  runSummary?: string
  escalation?: string
  saved?: { recipe_id: string; version: number; name: string; verification?: unknown; flow?: RecipeFlowData }
  file?: FileArtifact
  files?: FileArtifact[]
  failed?: string
}

export interface FileArtifact {
  file_id: string
  toolCallId?: string
  filename: string
  kind?: 'input' | 'expected' | 'output' | 'doc'
  kind_label?: string
  download_url: string
  sheets?: { sheet: string; rows: number }[]
  run_id?: string
  delivery_status?: FileDeliveryStatus
  intermediate?: boolean
  explicit?: boolean
}

/** 查看流程时保存的只读快照，与执行/修订进度分开。 */
export interface RecipeViewArtifact {
  recipe_id: string
  name: string
  description: string
  version: number
  flow: RecipeFlowData
  toolCallId?: string
  deleted?: boolean
}

export type Artifact =
  | { kind: 'table'; key: string; table: TableArtifact }
  | { kind: 'file'; key: string; file: FileArtifact }
  | { kind: 'recipe'; key: string; progress: RecipeProgress }
  | { kind: 'recipe_view'; key: string; view: RecipeViewArtifact }
  | { kind: 'clarify'; key: string; question: string; options: string[] }

export interface TableArtifact {
  name: string | null
  label: string
  rows: number
  cols: number
  columns: string[]
  preview: unknown[][]
  source?: string
  file_id?: string | null
  filename?: string | null
  sheet?: string | null
  toolCallId?: string
  role?: string
  ephemeral?: boolean
  description?: string
}

/** 思考模式：回答发出前对照执行结果核对的结果（回答下面显示成一行小标识） */
export interface AuditResult {
  ok: boolean | null
  issues: string[]
  skipped: boolean
  reason: string
  corrected: boolean
}

export interface UiMessage {
  key: string
  id?: number
  role: 'user' | 'assistant'
  content: string
  meta: Record<string, unknown>
  streaming: boolean
  /** 本轮回复的本地起止时间；最终耗时优先使用服务端统计。 */
  replyStartedAt?: number
  replyFinishedAt?: number
  /** 恢复快照后递增：已收到的文字立即显示，随后新增文字继续流式展示。 */
  replayRevision?: number
  timeline: TimelineItem[]
  artifacts: Artifact[]
  blocks?: AssistantBlock[]
  /** 开发者日志保留诊断事件；主聊天仍使用 timeline / artifacts 的用户视图。 */
  events?: AgentEvent[]
  /** 兼容旧事件：未通过完成检查后紧接的 note 是内部候选，不能作为正文展示。 */
  pendingCompletionDraft?: boolean
  /** Terminal selection survives later file events and stored-event replay. */
  deliveryStatuses?: Record<string, FileDeliveryStatus>
  /** 此刻在做什么；整轮执行中的通用回复提示由消息组件兜底。 */
  activity?: string
  /** 思考模式：后端事件启用本轮进度卡，与步骤数无关。 */
  checklist?: boolean
  /** 思考模式：回答核对的结果。 */
  audit?: AuditResult
  /** 整体完成状态由后端汇总，流结束本身不代表业务已完成。 */
  execution?: ExecutionState
  error?: string
  createdAt?: string
}

export type AssistantBlock =
  | { kind: 'process'; key: string; title: string; itemKeys: string[]; recipeKeys: string[]; closed: boolean }
  | { kind: 'text'; key: string; text: string; source: 'note' | 'reply'; closed: boolean }

let seq = 0
export const uid = (prefix = 'k') => `${prefix}_${Date.now().toString(36)}_${(seq++).toString(36)}`

export function newAssistant(): UiMessage {
  return { key: uid('m'), role: 'assistant', content: '', meta: {}, streaming: true,
    replyStartedAt: Date.now(), timeline: [], artifacts: [], blocks: [] }
}

export function newUser(content: string, meta: Record<string, unknown> = {}): UiMessage {
  return { key: uid('m'), role: 'user', content, meta, streaming: false, timeline: [], artifacts: [] }
}

// ---------------------------------------------------------------------------
// Recipe 进度归并
// ---------------------------------------------------------------------------

export function newProgress(mode: RecipeProgress['mode'] = 'learn', toolCallId?: string): RecipeProgress {
  return { key: uid('r'), toolCallId, mode, status: 'thinking', round: 1, steps: [], inputs: [], verifyHistory: [] }
}

/** Keep every delivered file for this run, retaining the legacy primary result. */
export function addProgressFile(progress: RecipeProgress, file: FileArtifact): void {
  const files = progress.files ?? (progress.file ? [progress.file] : [])
  progress.files = [...files.filter((item) => item.file_id !== file.file_id), file]
  progress.file = file
}

function stepOf(p: RecipeProgress, id: number): StepState | undefined {
  return p.steps.find((s) => s.step_id === id)
}

/** 旧消息中的流程可能带有已移除的启停字段，恢复时不再保留。 */
function recipeSnapshot(recipe: RecipeJSON | undefined, previous?: RecipeJSON): RecipeJSON | undefined {
  if (!recipe) return undefined
  const snapshot = { ...previous, ...recipe, steps: recipe.steps?.map((step) => {
    const prior = previous?.steps.find((item) => item.id === step.id)
    return { ...prior, ...step, params: { ...(step.params ?? prior?.params) } }
  }) } as RecipeJSON & { status?: unknown; status_label?: unknown }
  delete snapshot.status
  delete snapshot.status_label
  return snapshot
}

/** 把一个 recipe_* 事件归并进进度对象，返回 true 表示已处理 */
export function applyRecipeEvent(p: RecipeProgress, e: AgentEvent): boolean {
  const handled = mergeRecipeEvent(p, e)
  if (handled) (p.details ??= []).push(...recipeProcessDetails(p, e))
  return handled
}

function mergeRecipeEvent(p: RecipeProgress, e: AgentEvent): boolean {
  switch (e.type) {
    case 'recipe_learn_start':
      p.mode = e.mode === 'revise' ? 'revise' : 'learn'
      p.status = 'thinking'
      return true
    case 'recipe_evidence':
      p.evidence = e.diff?.cards ?? []
      return true
    case 'recipe_thinking':
      p.thinking = e.message
      return true
    case 'recipe_generated':
      p.recipePersisted = false
      p.saved = undefined
      p.recipe = recipeSnapshot(e.recipe)
      p.recipeId = e.recipe?.id
      p.flow = e.flow
      p.questions = e.questions
      p.round = e.round ?? p.round
      p.recipeName = e.recipe?.name
      p.status = 'running'
      return true
    case 'recipe_run_start':
      if (e.recipe) p.recipe = recipeSnapshot(e.recipe, p.recipe)
      p.runId = e.run_id
      p.recipeId = e.recipe?.id ?? e.recipe_id ?? p.recipeId
      p.recipeName = e.recipe?.name ?? p.recipeName
      p.recipeVersion = e.recipe?.version
      p.recipePersisted = e.recipe_persisted === true
      p.saved = undefined
      p.round = e.round ?? p.round
      p.inputs = []
      p.validation = undefined
      p.failed = undefined
      p.escalation = undefined
      p.runStatus = undefined
      p.runSummary = undefined
      p.verify = undefined
      p.outputs = undefined
      p.file = undefined
      p.files = undefined
      p.steps = (e.recipe?.steps ?? []).map((s: StepState) => ({
        step_id: (s as unknown as { id: number }).id,
        name: s.name,
        formula: s.formula,
        kind: s.kind,
        formula_name: s.formula_name,
        formula_kind: s.formula_kind,
        description: s.description,
        output: s.output,
        status: (e.start_step && (s as unknown as { id: number }).id < e.start_step ? 'success' : 'pending') as StepState['status'],
        repairs: [],
      }))
      p.status = 'running'
      return true
    case 'recipe_input':
      p.inputs.push({ alias: e.alias, kind: e.kind, filename: e.filename, sheet: e.sheet, rows: e.rows, renames: e.renames ?? {}, warnings: e.warnings ?? [] })
      return true
    case 'recipe_step_start': {
      const s = stepOf(p, e.step_id)
      if (s) s.status = 'running'
      return true
    }
    case 'recipe_step_error': {
      const s = stepOf(p, e.step_id)
      if (s) {
        s.error = e.error
        s.status = 'repairing'
      }
      return true
    }
    case 'recipe_repair': {
      if (e.status === 'retry') {
        p.recipePersisted = false
        p.saved = undefined
      }
      const s = stepOf(p, e.step_id)
      const planned = p.recipe?.steps.find((item) => item.id === e.step_id)
      if (planned && e.status === 'retry') {
        if (e.params && typeof e.params === 'object' && !Array.isArray(e.params)) planned.params = { ...e.params }
        if (typeof e.formula === 'string') planned.formula = e.formula
        if (typeof e.explanation === 'string') planned.explanation = e.explanation
      }
      if (s) {
        s.repairs.push({ attempt: e.attempt, status: e.status, explanation: e.explanation })
        if (e.status === 'gave_up') s.status = 'failed'
      }
      return true
    }
    case 'recipe_step_done': {
      const s = stepOf(p, e.step_id)
      const planned = p.recipe?.steps.find((item) => item.id === e.step_id)
      if (planned) {
        if (e.params && typeof e.params === 'object' && !Array.isArray(e.params)) planned.params = { ...e.params }
        if (typeof e.formula === 'string') planned.formula = e.formula
      }
      if (s) {
        s.status = e.status
        s.report = e as unknown as StepReport
        s.formula = e.formula
        s.kind = e.kind ?? s.kind
        s.formula_name = e.formula_name
        s.formula_kind = e.formula_kind ?? s.formula_kind
      }
      return true
    }
    case 'recipe_output':
      p.outputs = e.sheets
      return true
    case 'recipe_validation':
      p.validation = e.report
      return true
    case 'recipe_run_done':
      if (typeof e.recipe_persisted === 'boolean') {
        p.recipePersisted = e.recipe_persisted
        if (!e.recipe_persisted) p.saved = undefined
      }
      p.runStatus = e.status
      p.runSummary = e.summary
      p.escalation = e.report?.escalation
      if (p.mode === 'replay') p.status = e.status === 'failed' || e.status === 'escalated' ? 'failed' : 'done'
      for (const s of p.steps) if (s.status === 'running' || s.status === 'repairing') s.status = 'failed'
      return true
    case 'recipe_verify':
      p.verify = e.report
      p.verifyHistory.push(e.report)
      p.status = 'verifying'
      return true
    case 'recipe_refine':
      p.status = 'refining'
      p.round = e.round
      return true
    case 'recipe_diff':
      p.diff = e.diff?.cards
      return true
    case 'recipe_completed':
    case 'recipe_saved':
      p.saved = e.type === 'recipe_saved'
        ? { recipe_id: e.recipe_id, version: e.version, name: e.name, verification: e.verification, flow: e.flow }
        : undefined
      if (e.type === 'recipe_saved') {
        p.deleted = false
        p.recipePersisted = true
      }
      p.recipeId = e.recipe_id ?? p.recipeId
      if (e.flow) p.flow = e.flow
      if (e.selected_run) {
        const selected = e.selected_run
        const report = selected.report
        const reports = report.steps as StepReport[]
        p.runId = selected.run_id
        p.round = selected.round
        p.recipe = recipeSnapshot(selected.recipe)
        p.recipeId = selected.recipe?.id ?? p.recipeId
        p.recipeName = selected.recipe?.name ?? p.recipeName
        p.recipeVersion = selected.recipe?.version ?? e.version
        p.runStatus = report.status
        p.runSummary = selected.summary
        p.failed = report.error || undefined
        p.escalation = report.escalation || undefined
        p.validation = report.validation || undefined
        p.verify = selected.verification || undefined
        p.inputs = report.inputs ?? []
        p.outputs = report.outputs ?? []
        p.steps = (p.recipe?.steps ?? []).map((step) => {
          const result = reports.find((item) => item.step_id === step.id)
          return {
            step_id: step.id, name: step.name, formula: step.formula, kind: result?.kind ?? step.kind,
            formula_name: result?.formula_name ?? step.formula,
            formula_kind: result?.formula_kind ?? step.formula_kind ?? 'builtin',
            description: step.description, output: step.output,
            status: result?.status ?? 'pending', report: result, error: result?.error,
            repairs: (result?.attempts ?? []).map((attempt) => ({
              attempt: attempt.attempt, status: 'recorded', explanation: attempt.explanation,
            })),
          }
        })
      }
      if (p.mode !== 'replay' || p.status !== 'failed') p.status = p.runStatus === 'failed' || p.runStatus === 'escalated' ? 'failed' : 'done'
      return true
    case 'recipe_failed':
      p.failed = e.error
      p.status = 'failed'
      return true
    default:
      return false
  }
}

// ---------------------------------------------------------------------------
// 消息归并
// ---------------------------------------------------------------------------

const RECIPE_TOOLS = new Set(['build_recipe', 'revise_recipe', 'run_recipe'])

function argumentErrorKind(e: AgentEvent): 'protocol' | 'schema' | undefined {
  return e.error_kind === 'protocol' || e.error_kind === 'schema' ? e.error_kind : undefined
}

/** 把一个事件归并进助手消息（recipe_* 事件交给流程进度对象处理）。 */
export function applyEvent(msg: UiMessage, e: AgentEvent): void {
  if (e.type === 'stream_snapshot') {
    if (Array.isArray(e.events)) restoreStreamSnapshot(msg, e.events)
    return
  }
  const previous = msg.events?.at(-1)
  const suppressCompletionDraft = e.type === 'note' && msg.pendingCompletionDraft === true &&
    previous?.type === 'completion_check' && previous.agent_id === e.agent_id && previous.step_num === e.step_num
  msg.pendingCompletionDraft = e.type === 'completion_check' && e.status === 'incomplete' && e.can_continue === true
  const timelineStart = msg.timeline.length
  const artifactStart = msg.artifacts.length
  mergeEvent(msg, e, suppressCompletionDraft)
  appendBlocks(msg, e, timelineStart, artifactStart, suppressCompletionDraft)
}

/** 一次恢复到服务端快照的完整状态；随后只追加快照边界之后的新事件。 */
function restoreStreamSnapshot(msg: UiMessage, events: AgentEvent[]): void {
  const restored = newAssistant()
  restored.key = msg.key
  restored.id = msg.id
  restored.createdAt = msg.createdAt
  restored.replyStartedAt = msg.replyStartedAt
  restored.replyFinishedAt = msg.replyFinishedAt
  restored.meta = { ...msg.meta }
  const savedExecution = readExecutionState(msg.meta.execution)
  restored.execution = { status: 'running', code: 'running', summary: '正在处理',
    llm_usage: savedExecution?.llm_usage, llm_usage_pending: savedExecution?.llm_usage_pending }
  restored.meta.execution = { ...restored.execution }
  for (const event of events) {
    if (event && typeof event.type === 'string' && event.type !== 'stream_snapshot') applyEvent(restored, event)
  }
  Object.assign(msg, restored, {
    events: restored.events ?? [],
    deliveryStatuses: restored.deliveryStatuses,
    activity: restored.activity,
    checklist: restored.checklist,
    audit: restored.audit,
    error: restored.error,
    pendingCompletionDraft: restored.pendingCompletionDraft,
    replayRevision: (msg.replayRevision ?? 0) + 1,
  })
}

function mergeEvent(msg: UiMessage, e: AgentEvent, suppressCompletionDraft = false): void {
  // 文本增量、模型思考和临时的活动提示不属于执行诊断，避免日志里堆积流式碎片。
  if (e.type !== 'text_chunk' && e.type !== 'thinking' && e.type !== 'activity') (msg.events ??= []).push({ ...e })
  // 模型开始调用仍属于刚声明的活动；阶段结束或有新内容时才清空具体提示。
  if (!['activity', 'thinking', 'model_call_start'].includes(e.type) && msg.activity) msg.activity = ''
  if (e.type === 'recipe_view') {
    // 查看事件不能落进下方 recipe_* 的执行进度分派，否则会产生一次虚假的失败尝试。
    if (typeof e.recipe_id !== 'string' || !e.recipe_id || !e.flow ||
        !Array.isArray(e.flow.inputs) || !Array.isArray(e.flow.steps) || !Array.isArray(e.flow.outputs)) return
    const view: RecipeViewArtifact = {
      recipe_id: e.recipe_id, name: e.name ?? '处理流程', description: e.description ?? '',
      version: e.version ?? 1,
      toolCallId: e.tool_call_id,
      // 只接收流程图公开字段，快照不依赖后续 API 请求，也不携带执行参数或 SQL。
      flow: {
        inputs: e.flow.inputs.map((input: RecipeFlowData['inputs'][number]) => ({
          alias: input.alias, kind: input.kind, description: input.description, file_pattern: input.file_pattern,
          source_file: input.source_file, sheet: input.sheet,
        })),
        input_files: e.flow.input_files?.map((file: NonNullable<RecipeFlowData['input_files']>[number]) => ({
          name: file.name, aliases: [...file.aliases], file_pattern: file.file_pattern,
        })),
        steps: e.flow.steps.map((step: RecipeFlowData['steps'][number]) => ({
          id: step.id, name: step.name, action: step.action, formula: step.formula, kind: step.kind,
          formula_kind: step.formula_kind, description: step.description, output: step.output,
          inputs: [...(step.inputs ?? [])],
          ...scriptReferences(step)[0],
        })),
        outputs: e.flow.outputs.map((output: RecipeFlowData['outputs'][number]) => ({ name: output.name, source: output.source })),
        output_files: e.flow.output_files?.map((file: NonNullable<RecipeFlowData['output_files']>[number]) => ({
          name: file.name, ref: file.ref, source: file.source, sources: [...file.sources],
        })),
      },
    }
    const previous = msg.artifacts.find((artifact) => artifact.kind === 'recipe_view' &&
      artifact.view.recipe_id === view.recipe_id && artifact.view.version === view.version)
    if (previous?.kind === 'recipe_view') {
      previous.view = { ...view, toolCallId: previous.view.toolCallId ?? view.toolCallId, deleted: previous.view.deleted }
    } else msg.artifacts.push({ kind: 'recipe_view', key: uid('view'), view })
    return
  }
  if (e.type === 'recipe_deleted') {
    if (typeof e.recipe_id === 'string' && e.recipe_id) {
      markRecipeDeleted([msg], e.recipe_id)
      msg.timeline.push({ kind: 'info', key: uid('t'), text: `已删除流程「${e.name || e.recipe_id}」`, step: activePlan(msg)?.num })
    }
    return
  }
  if (e.type.startsWith('recipe_')) {
    applyRecipeEvent(progressFor(msg, e), e)
    return
  }
  switch (e.type) {
    case 'start': {
      if (typeof e.assistant_message_id === 'number') msg.id = e.assistant_message_id
      if (typeof e.request_message_id === 'number') msg.meta.request_message_id = e.request_message_id
      if (typeof e.job_id === 'string') msg.meta.job_id = e.job_id
      break
    }
    case 'execution_status': {
      if (typeof e.assistant_message_id === 'number') msg.id = e.assistant_message_id
      if (typeof e.request_message_id === 'number') msg.meta.request_message_id = e.request_message_id
      if (typeof e.job_id === 'string') msg.meta.job_id = e.job_id
      const state = readExecutionState(e)
      if (state) {
        msg.execution = state
        msg.meta.execution = { ...state }
      }
      break
    }
    case 'execution_stop': {
      const state = readExecutionState(e)
      if (state && !msg.events?.some((event) => event.type === 'execution_status')) {
        msg.execution = mergeExecutionStop(msg.execution, state)
      }
      break
    }
    case 'text_chunk':
      msg.content += e.content
      break
    case 'note':
      if (e.retracted || suppressCompletionDraft) break
      if (typeof e.content === 'string' && e.content.trim()) {
        msg.timeline.push({ kind: 'note', key: uid('t'), text: e.content.trim(), step: e.step_num ?? activePlan(msg)?.num })
      }
      break
    case 'thinking': {
      const last = msg.timeline[msg.timeline.length - 1]
      const step = activePlan(msg)?.num
      if (last?.kind === 'thinking' && last.step === step) last.text += e.content
      else msg.timeline.push({ kind: 'thinking', key: uid('t'), text: e.content, step })
      break
    }
    case 'tool_call': {
      const errorKind = argumentErrorKind(e)
      const params = errorKind ? {} : { ...(e.params ?? {}) }
      // Older recovery events kept declared explanations only in diagnostic parameters.
      // Recover these explicit business fields without rendering arbitrary debug payloads.
      if (!errorKind && e.tool === 'reconsider_approach' && e.debug_params && typeof e.debug_params === 'object') {
        for (const key of ['action', 'method', 'target', 'before', 'after', 'evidence', 'change', 'validation']) {
          if (!params[key] && typeof e.debug_params[key] === 'string') params[key] = e.debug_params[key]
        }
      }
      msg.timeline.push({ kind: 'tool', key: uid('t'), id: e.id, tool: e.tool,
        label: errorKind ? '核对处理参数' : e.label, params, status: 'running', errorKind, step: activePlan(msg)?.num })
      if (RECIPE_TOOLS.has(e.tool) && !errorKind) {
        const progress = newProgress(e.tool === 'run_recipe' ? 'replay' : e.tool === 'revise_recipe' ? 'revise' : 'learn', e.id)
        msg.artifacts.push({ kind: 'recipe', key: progress.key, progress })
      }
      break
    }
    case 'tool_result': {
      const item = msg.timeline.find((t) => t.kind === 'tool' && t.id === e.id)
      const errorKind = argumentErrorKind(e) ?? (item?.kind === 'tool' ? item.errorKind : undefined)
      if (item && item.kind === 'tool') {
        item.status = e.ok ? 'ok' : 'error'
        item.summary = e.summary
        item.text = e.text
        item.duration_ms = e.duration_ms
        if (errorKind) {
          item.errorKind = errorKind
          item.label = '核对处理参数'
          item.params = {}
        }
        if (Array.isArray(e.details)) item.details = e.details.filter((detail) => detail &&
          typeof detail.label === 'string' && Array.isArray(detail.items)).map((detail) => ({
          label: detail.label,
          items: detail.items.filter((text): text is string => typeof text === 'string'),
          ...(detail.ordered ? { ordered: true } : {}),
        }))
      }
      if (errorKind) {
        // Older stored events may only mark the result. Remove the speculative
        // placeholder and its anchors so SSE replay also shows no execution.
        const rejected = new Set(msg.artifacts.flatMap((artifact) => artifact.kind === 'recipe' &&
          artifact.progress.toolCallId === e.id ? [artifact.key] : []))
        if (rejected.size) {
          msg.artifacts = msg.artifacts.filter((artifact) => !rejected.has(artifact.key))
          for (const block of msg.blocks ?? []) {
            if (block.kind === 'process') block.recipeKeys = block.recipeKeys.filter((key) => !rejected.has(key))
          }
        }
        break
      }
      const progress = findProgress(msg, e.id)
      if (progress && progress.status !== 'done' && progress.status !== 'failed') progress.status = e.ok ? 'done' : 'failed'
      if (progress && !e.ok && !progress.failed && !progress.escalation && !progress.runSummary) {
        progress.failed = e.summary || e.text
      }
      break
    }
    case 'table':
      msg.artifacts.push({ kind: 'table', key: uid('a'), table: { ...e.table, toolCallId: e.tool_call_id } })
      break
    case 'script': {
      const script = scriptReferences(e)[0]
      if (!script) break
      const item = msg.timeline.find((entry) => entry.kind === 'tool' && entry.id === e.tool_call_id)
      if (item?.kind === 'tool') item.params = { ...item.params, ...script }
      else if (!msg.timeline.some((entry) => entry.kind === 'script' && entry.script.script_id === script.script_id)) {
        msg.timeline.push({ kind: 'script', key: uid('t'), script, step: activePlan(msg)?.num })
      }
      break
    }
    case 'file': {
      const status = e.delivery_status === 'superseded' ? 'superseded' : msg.deliveryStatuses?.[e.file_id] ?? e.delivery_status
      const file: FileArtifact = { file_id: e.file_id, toolCallId: e.tool_call_id, filename: e.filename, kind: e.kind, kind_label: e.kind_label, download_url: e.download_url, sheets: e.sheets, run_id: e.run_id,
        delivery_status: status, intermediate: status ? status === 'intermediate' || status === 'superseded' : e.intermediate, explicit: e.explicit === true }
      const progress = e.tool_call_id ? findProgress(msg, e.tool_call_id) : undefined
      if (progress) addProgressFile(progress, file)
      else msg.artifacts.push({ kind: 'file', key: uid('a'), file })
      break
    }
    case 'delivery_manifest': {
      applyDeliveryManifest([msg], e)
      break
    }
    case 'clarify':
      msg.artifacts.push({ kind: 'clarify', key: uid('a'), question: e.question, options: e.options ?? [] })
      break
    case 'memory_saved':
      msg.timeline.push({ kind: 'info', key: uid('t'), tone: 'memory', text: `记住了：${(e.items ?? []).map((i: { key: string; value: string }) => `${i.key}（${i.value}）`).join('；')}`, step: activePlan(msg)?.num })
      break
    case 'field_mapping_saved':
      msg.timeline.push({ kind: 'info', key: uid('t'), tone: 'mapping', text: `字段映射：${e.standard} ← ${(e.aliases ?? []).join('、')}`, step: activePlan(msg)?.num })
      break
    case 'compacted':
      msg.timeline.push({ kind: 'info', key: uid('t'), tone: 'compact', text: `对话较长，已把前 ${e.old_count} 条消息压缩成摘要`, step: activePlan(msg)?.num })
      break
    case 'plan_start':
      msg.checklist = e.checklist === true
      for (const step of e.steps ?? []) {
        msg.timeline.push({ kind: 'plan', key: uid('t'), num: step.id, total: e.steps.length, description: step.description, status: 'pending' })
      }
      break
    case 'step_start': {
      const item = msg.timeline.find((entry) => entry.kind === 'plan' && entry.num === e.step_num)
      if (item?.kind === 'plan') {
        item.status = 'running'
        item.summary = undefined
        item.detail = undefined
      }
      else msg.timeline.push({ kind: 'plan', key: uid('t'), num: e.step_num, total: e.total, description: e.description, status: 'running' })
      break
    }
    case 'step_done': {
      const item = [...msg.timeline].reverse().find((t) => t.kind === 'plan' && t.num === e.step_num)
      if (item && item.kind === 'plan') {
        item.status = ['done', 'warning', 'waiting', 'error', 'stopped'].includes(e.status) ? e.status : 'done'
        item.summary = typeof e.summary === 'string' ? e.summary : undefined
        item.detail = typeof e.detail === 'string' ? e.detail : undefined
      }
      break
    }
    case 'reflect_start':
      if (e.quiet) break
      msg.timeline.push({ kind: 'info', key: uid('t'), tone: 'reflect', text: '自检：检查需求是否都完成了', step: activePlan(msg)?.num })
      break
    // 思考模式：think_start / plan_review / step_check / audit_start 只出现在开发者日志里——
    // 计划审查和每一项的核对结果已经写在正文里（引用块），不再另起一行折叠记录。
    case 'activity':
      msg.activity = typeof e.text === 'string' ? e.text : ''
      break
    case 'completion_check_start':
      msg.activity = '正在确认任务完成情况'
      break
    case 'audit_done':
      msg.audit = {
        ok: typeof e.ok === 'boolean' ? e.ok : null,
        issues: textList(e.issues),
        skipped: e.skipped === true,
        reason: typeof e.reason === 'string' ? e.reason.trim() : '',
        corrected: e.corrected === true,
      }
      break
    case 'agent_pool_start':
      msg.timeline.push({ kind: 'info', key: uid('t'), tone: 'aggregate', text: `多表并行分析：${(e.agents ?? []).length} 位专员同时工作`, step: activePlan(msg)?.num })
      break
    case 'agent_start':
      msg.timeline.push({ kind: 'agent', key: uid('t'), id: e.agent_id, table: e.table_name, status: 'running' })
      break
    case 'agent_done': {
      const item = msg.timeline.find((t) => t.kind === 'agent' && t.id === e.agent_id)
      if (item && item.kind === 'agent') {
        item.status = agentStatus(e)
        item.conclusion = e.conclusion
      }
      break
    }
    case 'aggregate_start':
      msg.timeline.push({ kind: 'info', key: uid('t'), tone: 'aggregate', text: '汇总各专员的结论，做跨表对比', step: activePlan(msg)?.num })
      break
    case 'error': {
      msg.error = e.content
      const step = activePlan(msg)
      if (step) {
        step.status = e.content?.includes('已停止') ? 'stopped' : 'error'
        if (typeof e.content === 'string') step.detail = e.content
      }
      break
    }
    case 'done':
      finishMessage(msg)
      break
  }
}

/** 事件里的字符串列表字段：只取非空字符串 */
function textList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string' && !!item.trim()).map((item) => item.trim()) : []
}

function closeBlock(msg: UiMessage) {
  const last = msg.blocks?.at(-1)
  if (last) last.closed = true
}

function processBlock(msg: UiMessage, title = '处理过程'): Extract<AssistantBlock, { kind: 'process' }> {
  const blocks = msg.blocks ??= []
  const last = blocks.at(-1)
  if (last?.kind === 'process' && !last.closed) return last
  closeBlock(msg)
  const block: Extract<AssistantBlock, { kind: 'process' }> = {
    kind: 'process', key: uid('b'), title, itemKeys: [], recipeKeys: [], closed: false,
  }
  blocks.push(block)
  // Read through the reactive array: synchronous consumers can observe the block
  // before its item/recipe anchors are appended, so those pushes must notify Vue.
  return blocks.at(-1)! as typeof block
}

function appendText(msg: UiMessage, text: string, source: 'note' | 'reply', closed: boolean) {
  if (!text) return
  const blocks = msg.blocks ??= []
  const last = blocks.at(-1)
  if (source === 'reply' && last?.kind === 'text' && last.source === source && !last.closed) {
    last.text += text
    last.closed = closed
    return
  }
  closeBlock(msg)
  blocks.push({ kind: 'text', key: uid('b'), text, source, closed })
}

/** Render anchors are created once at arrival time; later events only extend the active block. */
function appendBlocks(msg: UiMessage, e: AgentEvent, timelineStart: number, artifactStart: number, suppressCompletionDraft = false) {
  if (e.type === 'text_chunk') {
    appendText(msg, e.content, 'reply', false)
    return
  }
  if (e.type === 'reply') {
    const last = msg.blocks?.at(-1)
    if (last?.kind === 'text' && last.source === 'reply' && last.text === e.content) last.closed = true
    else if (e.content?.trim() && !(last?.kind === 'text' && last.text.trim() === e.content.trim())) {
      appendText(msg, e.content, 'reply', true)
      msg.content += e.content
    }
    return
  }
  if (e.type === 'note') {
    if (e.retracted || suppressCompletionDraft) return
    const last = msg.blocks?.at(-1)
    // Older servers may have streamed a paragraph before marking it as a note.
    // Keep that paragraph at its original anchor instead of moving or retyping it.
    if (last?.kind === 'text' && last.text.trim() === e.content?.trim()) last.closed = true
    else appendText(msg, e.content?.trim(), 'note', true)
    return
  }
  if (e.type === 'done') {
    closeBlock(msg)
    return
  }
  if (e.type === 'step_start') {
    closeBlock(msg)
    if (e.quiet) return // 思考模式：本项由正文汇报，不再另起一行折叠记录
    const plan = msg.timeline.find((item) => item.kind === 'plan' && item.num === e.step_num)
    const block = processBlock(msg, e.description || (plan?.kind === 'plan' ? plan.description : '处理过程'))
    if (plan) block.itemKeys.push(plan.key)
    return
  }
  if (e.type === 'step_done') {
    if (e.quiet) {
      closeBlock(msg)
      return
    }
    const summary = e.summary?.trim()
    const lastText = [...(msg.blocks ?? [])].reverse().find((block) => block.kind === 'text')
    if (summary && lastText?.kind === 'text' && lastText.text.trim() === summary) closeBlock(msg)
    else if (summary && e.status !== 'waiting') appendText(msg, summary, 'note', true)
    else closeBlock(msg)
    return
  }
  const items = msg.timeline.slice(timelineStart).filter((item) => !['note', 'plan', 'thinking', 'script'].includes(item.kind))
  const recipes = msg.artifacts.slice(artifactStart).flatMap((artifact) => artifact.kind === 'recipe' ? [artifact.key] : [])
  if (!items.length && !recipes.length) return
  // Model reasoning only marks a phase. Public detail comes from actual operations and results.
  const firstTool = items.find((item) => item.kind === 'tool')
  const last = msg.blocks?.at(-1)
  if (last?.kind === 'process' && (firstTool && (last.title === '理解需求' || last.itemKeys.length || last.recipeKeys.length) ||
      items.length && last.recipeKeys.length)) {
    closeBlock(msg)
  }
  const block = processBlock(msg, firstTool?.kind === 'tool' ? firstTool.label : '处理过程')
  block.itemKeys.push(...items.map((item) => item.key))
  block.recipeKeys.push(...recipes)
}

/** 删除流程后保留历史执行和文件，撤下已保存标记与流程入口。 */
export function markRecipeDeleted(messages: UiMessage[], recipeId: string): void {
  for (const message of messages) {
    for (const artifact of message.artifacts) {
      if (artifact.kind === 'recipe_view') {
        if (artifact.view.recipe_id === recipeId) artifact.view.deleted = true
        continue
      }
      if (artifact.kind !== 'recipe') continue
      const progress = artifact.progress
      if ((progress.recipeId ?? progress.saved?.recipe_id ?? progress.recipe?.id) !== recipeId) continue
      progress.deleted = true
      progress.saved = undefined
    }
  }
}

/** A confirmed replacement retires earlier deliveries across the conversation. */
export function applyDeliveryManifest(messages: UiMessage[], event: AgentEvent): void {
  if (event.type !== 'delivery_manifest') return
  const candidates = new Set(textList(event.candidate_file_ids))
  const finals = new Set(event.status === 'confirmed' ? textList(event.final_file_ids).filter((id) => candidates.has(id)) : [])
  const replaced = new Set(finals.size ? textList(event.replaced_file_ids).filter((id) => !candidates.has(id)) : [])
  const metadata = new Map<string, Record<string, unknown>>((Array.isArray(event.files) ? event.files : [])
    .filter((file): file is Record<string, unknown> => !!file && typeof file === 'object' &&
      typeof file.file_id === 'string' && finals.has(file.file_id))
    .map((file) => [file.file_id as string, file]))
  for (const message of messages) {
    const statuses = message.deliveryStatuses ??= {}
    for (const id of candidates) {
      if (statuses[id] !== 'superseded') statuses[id] = finals.has(id) ? 'final' : 'intermediate'
    }
    for (const id of replaced) statuses[id] = 'superseded'
    for (const artifact of message.artifacts) {
      const files = artifact.kind === 'file' ? [artifact.file] : artifact.kind === 'recipe'
        ? [...(artifact.progress.files ?? []), ...(artifact.progress.file ? [artifact.progress.file] : [])] : []
      for (const file of files) {
        // Database state can already know about a later turn during history replay.
        if (file.delivery_status === 'superseded') statuses[file.file_id] = 'superseded'
        const status = statuses[file.file_id]
        if (!status) continue
        file.delivery_status = status
        file.intermediate = status === 'intermediate' || status === 'superseded'
        const current = metadata.get(file.file_id)
        if (current && status === 'final') {
          if (typeof current.filename === 'string') file.filename = current.filename
          if (typeof current.download_url === 'string') file.download_url = current.download_url
          if (Array.isArray(current.sheets)) file.sheets = current.sheets as FileArtifact['sheets']
        }
      }
    }
  }
}

function activePlan(msg: UiMessage) {
  return msg.timeline.find((item): item is Extract<TimelineItem, { kind: 'plan' }> => item.kind === 'plan' && item.status === 'running')
}

export function finishMessage(msg: UiMessage): void {
  if (msg.streaming && msg.role === 'assistant' && msg.replyStartedAt !== undefined && msg.replyFinishedAt === undefined) {
    msg.replyFinishedAt = Date.now()
  }
  msg.streaming = false
  msg.activity = ''
  if (msg.execution?.status === 'running') {
    msg.execution = { ...msg.execution, status: 'incomplete', code: 'missing_execution_status',
      summary: '处理连接已经结束，但没有收到任务完成确认。' }
    msg.meta.execution = { ...msg.execution }
  }
  closeBlock(msg)
  for (const item of msg.timeline) {
    if (item.kind === 'tool' && item.status === 'running') item.status = 'error'
    if (item.kind === 'plan' && item.status === 'running') {
      item.status = msg.execution?.status === 'waiting' ? 'waiting'
        : msg.execution?.status === 'incomplete' ? 'warning'
          : msg.execution?.status === 'failed' || msg.error && !msg.error.includes('已停止') ? 'error' : 'stopped'
      item.detail = msg.error || msg.execution?.summary || item.summary
    }
    if (item.kind === 'agent' && item.status === 'running') item.status = 'error'
  }
  for (const artifact of msg.artifacts) {
    if (artifact.kind === 'recipe' && !['done', 'failed'].includes(artifact.progress.status)) {
      artifact.progress.status = 'failed'
      artifact.progress.failed ||= msg.error || '处理已结束，但没有收到这次尝试的完成结果。'
    }
  }
}

function findProgress(msg: UiMessage, toolCallId: string): RecipeProgress | undefined {
  for (const a of msg.artifacts) if (a.kind === 'recipe' && a.progress.toolCallId === toolCallId) return a.progress
  return undefined
}

/** 找到事件所属的流程进度（按 tool_call_id；找不到就用最后一个，或者新建一个） */
function progressFor(msg: UiMessage, e: AgentEvent): RecipeProgress {
  if (e.tool_call_id) {
    const found = findProgress(msg, e.tool_call_id)
    if (found) return found
  }
  const last = [...msg.artifacts].reverse().find((a) => a.kind === 'recipe')
  if (last && last.kind === 'recipe') return last.progress
  const progress = newProgress(e.type === 'recipe_run_start' ? 'replay' : 'learn', e.tool_call_id)
  msg.artifacts.push({ kind: 'recipe', key: progress.key, progress })
  return progress
}

type StoredMessage = { id: number; role: 'user' | 'assistant'; content: string; events: AgentEvent[]; meta: Record<string, unknown>; created_at: string }

/** 用数据库里的消息重建界面消息 */
export function fromStored(stored: StoredMessage, options: { streaming?: boolean } = {}): UiMessage {
  const storedExecution = readExecutionState(stored.meta?.execution)
  const msg: UiMessage = {
    key: `db_${stored.id}`, id: stored.id, role: stored.role, content: '', meta: { ...stored.meta }, streaming: options.streaming === true,
    timeline: [], artifacts: [], blocks: stored.role === 'assistant' ? newAssistant().blocks : [], createdAt: stored.created_at,
  }
  if (stored.role === 'assistant' && options.streaming) {
    const startedAt = typeof storedExecution?.started_at === 'number' && Number.isFinite(storedExecution.started_at)
      ? storedExecution.started_at : Date.parse(stored.created_at)
    msg.replyStartedAt = Number.isFinite(startedAt) ? startedAt : Date.now()
  }
  if (stored.role === 'assistant') {
    for (const e of stored.events ?? []) applyEvent(msg, e)
    // 元数据保存的是最终总体状态，优先于回放中的单项结束记录。
    msg.execution = storedExecution ?? msg.execution
    if (msg.execution) msg.meta.execution = { ...msg.execution }
  }
  msg.content = stored.content
  if (stored.role === 'assistant' && stored.content.trim()) {
    const replies = (msg.blocks ?? []).flatMap((block) => block.kind === 'text' && block.source === 'reply' ? [block.text] : [])
    const lastText = [...(msg.blocks ?? [])].reverse().find((block) => block.kind === 'text')
    if (replies.join('').trim() !== stored.content.trim() && replies.join('\n\n').trim() !== stored.content.trim() &&
        (lastText?.kind !== 'text' || lastText.text.trim() !== stored.content.trim())) {
      // 检查点正文包含已落盘的段落和当前增量；只补末尾尚未写成 reply 的文字。
      const prefix = replies.join('').trimStart()
      const missing = options.streaming && stored.content.startsWith(prefix) ? stored.content.slice(prefix.length) : stored.content
      appendText(msg, missing, 'reply', !options.streaming)
    }
  }
  if (!options.streaming) finishMessage(msg)
  return msg
}

/** 按消息顺序还原删除状态；之后重新保存的流程保持可用。 */
export function fromStoredMessages(stored: StoredMessage[], throughMessageId?: number): UiMessage[] {
  const messages: UiMessage[] = []
  for (const item of stored) {
    const message = fromStored(item)
    for (const event of item.role === 'assistant' ? item.events ?? [] : []) {
      if (event.type === 'delivery_manifest') applyDeliveryManifest(messages, event)
      if (event.type === 'recipe_deleted' && typeof event.recipe_id === 'string') {
        markRecipeDeleted(messages, event.recipe_id)
      }
    }
    messages.push(message)
    if (item.role === 'user' && item.id === throughMessageId) break
  }
  return messages
}

/** 先直接展示持久化检查点，再用后台快照补齐全部内容并衔接实时事件。 */
export function restoreConversationMessages(stored: StoredMessage[], job?: { id: string; message_id: number } | null): { messages: UiMessage[]; assistant?: UiMessage } {
  if (!job) return { messages: fromStoredMessages(stored) }
  const checkpoint = [...stored].reverse().find((item) => item.role === 'assistant' &&
    (item.meta?.job_id === job.id || !item.meta?.job_id && item.meta?.request_message_id === job.message_id))
  const requestIndex = stored.findIndex((item) => item.role === 'user' && item.id === job.message_id)
  const history = requestIndex >= 0 ? stored.slice(0, requestIndex + 1) : stored.filter((item) => item !== checkpoint)
  const messages = fromStoredMessages(history)
  const assistant = checkpoint ? fromStored(checkpoint, { streaming: true }) : newAssistant()
  if (!checkpoint && requestIndex >= 0) {
    const startedAt = Date.parse(stored[requestIndex]!.created_at)
    if (Number.isFinite(startedAt)) assistant.replyStartedAt = startedAt
  }
  for (const event of checkpoint?.events ?? []) {
    if (event.type === 'delivery_manifest') applyDeliveryManifest(messages, event)
  }
  assistant.meta.request_message_id = job.message_id
  assistant.meta.job_id = job.id
  assistant.execution = { status: 'running', code: 'reconnecting', summary: '正在恢复执行记录',
    llm_usage: assistant.execution?.llm_usage, llm_usage_pending: assistant.execution?.llm_usage_pending }
  assistant.meta.execution = { ...assistant.execution }
  messages.push(assistant)
  return { messages, assistant }
}

// ---------------------------------------------------------------------------
// 显示辅助
// ---------------------------------------------------------------------------

export const FORMULA_ART: Record<string, string> = {
  select_columns: 'broom', drop_columns: 'broom', rename_columns: 'broom', trim_text: 'broom', cast_columns: 'broom',
  fill_null: 'broom', drop_duplicates: 'broom', filter_rows: 'funnel', sort_rows: 'crown', top_n: 'crown',
  compute_column: 'calc', map_values: 'calc', split_column: 'calc', concat_columns: 'calc', add_serial: 'calc',
  lookup: 'magnet', join_tables: 'puzzle', concat_tables: 'puzzle', group_aggregate: 'sigma', append_total: 'sigma',
  pivot_wide: 'blocks', unpivot_long: 'blocks', custom_sql: 'code', python_script: 'code',
}

export function formulaArt(formula: string, kind?: string): string {
  if (kind === 'customer') return 'code'
  return FORMULA_ART[formula] ?? 'spark'
}

export function percent(value: number | undefined | null, digits = 1): string {
  if (value === undefined || value === null) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

export function duration(ms: number | undefined): string {
  if (!ms) return ''
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return ''
  const diff = (Date.now() - new Date(iso).getTime()) / 1000
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)} 天前`
  return iso.slice(0, 10)
}
