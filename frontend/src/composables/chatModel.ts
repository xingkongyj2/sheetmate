/**
 * 对话消息的界面模型 + 事件归并（纯函数）。
 *
 * 后端推来的事件（文本、工具调用、结果表、Recipe 步骤…）在这里归并成：
 * - content   最终回答（Markdown）
 * - timeline  过程：中间说明、工具调用、计划步骤、多表专员…
 * - artifacts 产物：结果表、文件、处理流程卡片、澄清问题
 * 刷新页面时用数据库里存的事件重新归并，得到同样的界面。
 */
import type { AgentEvent, DiffCard, RecipeFlowData, RecipeJSON, RecipeToolKind, ScriptReference, StepReport, VerifyReport } from '@/api/types'
import { scriptReferences } from './scriptPresentation.ts'

export type ToolStatus = 'running' | 'ok' | 'error'
export type PlanStatus = 'pending' | 'running' | 'done' | 'warning' | 'waiting' | 'error' | 'stopped'

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
      summary?: string
      text?: string
      duration_ms?: number
      step?: number
    }
  | { kind: 'plan'; key: string; num: number; total: number; description: string; status: PlanStatus; summary?: string }
  | { kind: 'agent'; key: string; id: string; table: string; status: 'running' | 'done' | 'error'; conclusion?: string }
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
  deleted?: boolean
  steps: StepState[]
  inputs: { alias: string; kind?: 'table' | 'file'; filename: string; sheet: string | null; rows: number; renames: Record<string, string>; warnings: string[] }[]
  outputs?: { sheet: string; rows: number }[]
  validation?: { status: string; summary: string; checks: { status: string; description: string; message: string }[] }
  verify?: VerifyReport
  verifyHistory: VerifyReport[]
  diff?: DiffCard[]
  runStatus?: string
  runSummary?: string
  escalation?: string
  saved?: { recipe_id: string; version: number; name: string; status: string; status_label: string; verification?: unknown; flow?: RecipeFlowData }
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
}

/** 查看流程时保存的只读快照，与执行/修订进度分开。 */
export interface RecipeViewArtifact {
  recipe_id: string
  name: string
  description: string
  version: number
  status: string
  status_label: string
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

export interface UiMessage {
  key: string
  id?: number
  role: 'user' | 'assistant'
  content: string
  meta: Record<string, unknown>
  streaming: boolean
  timeline: TimelineItem[]
  artifacts: Artifact[]
  /** 开发者日志保留诊断事件；主聊天仍使用 timeline / artifacts 的用户视图。 */
  events?: AgentEvent[]
  error?: string
  createdAt?: string
}

let seq = 0
export const uid = (prefix = 'k') => `${prefix}_${Date.now().toString(36)}_${(seq++).toString(36)}`

export function newAssistant(): UiMessage {
  return { key: uid('m'), role: 'assistant', content: '', meta: {}, streaming: true, timeline: [], artifacts: [] }
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

/** 把一个 recipe_* 事件归并进进度对象，返回 true 表示已处理 */
export function applyRecipeEvent(p: RecipeProgress, e: AgentEvent): boolean {
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
      p.recipe = e.recipe
      p.recipeId = e.recipe?.id
      p.flow = e.flow
      p.questions = e.questions
      p.round = e.round ?? p.round
      p.recipeName = e.recipe?.name
      p.status = 'running'
      return true
    case 'recipe_run_start':
      p.runId = e.run_id
      p.recipeId = e.recipe?.id ?? e.recipe_id ?? p.recipeId
      p.recipeName = e.recipe?.name ?? p.recipeName
      p.recipeVersion = e.recipe?.version
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
      const s = stepOf(p, e.step_id)
      if (s) {
        s.repairs.push({ attempt: e.attempt, status: e.status, explanation: e.explanation })
        if (e.status === 'gave_up') s.status = 'failed'
      }
      return true
    }
    case 'recipe_step_done': {
      const s = stepOf(p, e.step_id)
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
      p.saved = e.type === 'recipe_saved' ? e as unknown as RecipeProgress['saved'] : undefined
      if (e.type === 'recipe_saved') p.deleted = false
      p.recipeId = e.recipe_id ?? p.recipeId
      if (e.flow) p.flow = e.flow
      if (e.selected_run) {
        const selected = e.selected_run
        const report = selected.report
        const reports = report.steps as StepReport[]
        p.runId = selected.run_id
        p.round = selected.round
        p.recipe = selected.recipe
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

/** 把一个事件归并进助手消息（recipe_* 事件交给流程进度对象处理）。 */
export function applyEvent(msg: UiMessage, e: AgentEvent): void {
  // 文本增量与模型思考不属于执行诊断，避免日志里堆积流式碎片。
  if (e.type !== 'text_chunk' && e.type !== 'thinking') (msg.events ??= []).push({ ...e })
  if (e.type === 'recipe_view') {
    // 查看事件不能落进下方 recipe_* 的执行进度分派，否则会产生一次虚假的失败尝试。
    if (typeof e.recipe_id !== 'string' || !e.recipe_id || !e.flow ||
        !Array.isArray(e.flow.inputs) || !Array.isArray(e.flow.steps) || !Array.isArray(e.flow.outputs)) return
    const view: RecipeViewArtifact = {
      recipe_id: e.recipe_id, name: e.name ?? '处理流程', description: e.description ?? '',
      version: e.version ?? 1, status: e.status ?? 'draft', status_label: e.status_label ?? '',
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
    case 'text_chunk':
      msg.content += e.content
      break
    case 'note':
      // 后端判定：刚才这一轮文字后面跟着工具调用，是过程说明——撤回已经实时输出的部分，挪进「执行过程」
      msg.content = ''
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
    case 'tool_call':
      flushNote(msg)
      msg.timeline.push({ kind: 'tool', key: uid('t'), id: e.id, tool: e.tool, label: e.label, params: e.params ?? {}, status: 'running', step: activePlan(msg)?.num })
      if (RECIPE_TOOLS.has(e.tool)) {
        const progress = newProgress(e.tool === 'run_recipe' ? 'replay' : e.tool === 'revise_recipe' ? 'revise' : 'learn', e.id)
        msg.artifacts.push({ kind: 'recipe', key: progress.key, progress })
      }
      break
    case 'tool_result': {
      const item = msg.timeline.find((t) => t.kind === 'tool' && t.id === e.id)
      if (item && item.kind === 'tool') {
        item.status = e.ok ? 'ok' : 'error'
        item.summary = e.summary
        item.text = e.text
        item.duration_ms = e.duration_ms
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
      const file: FileArtifact = { file_id: e.file_id, toolCallId: e.tool_call_id, filename: e.filename, kind: e.kind, kind_label: e.kind_label, download_url: e.download_url, sheets: e.sheets, run_id: e.run_id }
      const progress = e.tool_call_id ? findProgress(msg, e.tool_call_id) : undefined
      if (progress) addProgressFile(progress, file)
      else msg.artifacts.push({ kind: 'file', key: uid('a'), file })
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
      for (const step of e.steps ?? []) {
        msg.timeline.push({ kind: 'plan', key: uid('t'), num: step.id, total: e.steps.length, description: step.description, status: 'pending' })
      }
      break
    case 'step_start': {
      flushNote(msg)
      const item = msg.timeline.find((entry) => entry.kind === 'plan' && entry.num === e.step_num)
      if (item?.kind === 'plan') item.status = 'running'
      else msg.timeline.push({ kind: 'plan', key: uid('t'), num: e.step_num, total: e.total, description: e.description, status: 'running' })
      break
    }
    case 'step_done': {
      const item = [...msg.timeline].reverse().find((t) => t.kind === 'plan' && t.num === e.step_num)
      flushNote(msg)
      if (item && item.kind === 'plan') {
        item.status = ['done', 'warning', 'waiting', 'error', 'stopped'].includes(e.status) ? e.status : 'done'
        if (typeof e.summary === 'string') item.summary = e.summary
      }
      break
    }
    case 'reflect_start':
      msg.timeline.push({ kind: 'info', key: uid('t'), tone: 'reflect', text: '自检：检查需求是否都完成了', step: activePlan(msg)?.num })
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
        item.status = e.error ? 'error' : 'done'
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
      if (step) step.status = e.content?.includes('已停止') ? 'stopped' : 'error'
      break
    }
    case 'done':
      finishMessage(msg)
      break
  }
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

/** 工具调用前已经输出的文字，挪到过程里作为「中间说明」 */
function flushNote(msg: UiMessage) {
  const text = msg.content.trim()
  if (text) msg.timeline.push({ kind: 'note', key: uid('t'), text, step: activePlan(msg)?.num })
  msg.content = ''
}

function activePlan(msg: UiMessage) {
  return msg.timeline.find((item): item is Extract<TimelineItem, { kind: 'plan' }> => item.kind === 'plan' && item.status === 'running')
}

export function finishMessage(msg: UiMessage): void {
  msg.streaming = false
  for (const item of msg.timeline) {
    if (item.kind === 'tool' && item.status === 'running') item.status = 'error'
    if (item.kind === 'plan' && item.status === 'running') {
      item.status = msg.error && !msg.error.includes('已停止') ? 'error' : 'stopped'
    }
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
export function fromStored(stored: StoredMessage): UiMessage {
  const msg: UiMessage = {
    key: `db_${stored.id}`, id: stored.id, role: stored.role, content: '', meta: stored.meta ?? {}, streaming: false,
    timeline: [], artifacts: [], createdAt: stored.created_at,
  }
  if (stored.role === 'assistant') {
    for (const e of stored.events ?? []) applyEvent(msg, e)
  }
  msg.content = stored.content
  finishMessage(msg)
  return msg
}

/** 按消息顺序还原删除状态；之后重新保存的流程保持可用。 */
export function fromStoredMessages(stored: StoredMessage[]): UiMessage[] {
  const messages: UiMessage[] = []
  for (const item of stored) {
    const message = fromStored(item)
    for (const event of item.role === 'assistant' ? item.events ?? [] : []) {
      if (event.type === 'recipe_deleted' && typeof event.recipe_id === 'string') {
        markRecipeDeleted(messages, event.recipe_id)
      }
    }
    messages.push(message)
  }
  return messages
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
