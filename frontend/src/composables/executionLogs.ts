import type { AgentEvent, ScriptReference } from '../api/types.ts'
import type { TimelineItem, UiMessage } from './chatModel.ts'
import { scriptReferences, withoutScriptCode } from './scriptPresentation.ts'

export type LogFilter = 'all' | 'tools' | 'errors'
export type LogStatus = 'running' | 'success' | 'error' | 'warning' | 'info'

export interface LogSection {
  label: string
  language: string
  text: string
}

export interface ExecutionLog {
  key: string
  sequence: number
  category: 'tool' | 'event'
  title: string
  type: string
  status: LogStatus
  summary: string
  callId?: string
  duration?: number
  sections: LogSection[]
  scripts: ScriptReference[]
  raw: unknown
}

const EVENT_LABELS: Record<string, string> = {
  note: '处理说明', error: '执行错误', start: '开始处理', done: '处理结束',
  plan_start: '执行计划', step_start: '计划步骤开始', step_done: '计划步骤结束',
  table: '数据产物', file: '交付文件', script: '脚本文件', clarify: '待确认问题',
  memory_saved: '记忆已保存', field_mapping_saved: '字段映射已保存', compacted: '上下文已压缩',
  reflect_start: '自检开始', reflect_done: '自检结束', agent_pool_start: '并行任务开始',
  agent_start: '专员开始', agent_done: '专员结束', aggregate_start: '汇总开始',
  recipe_learn_start: '生成流程', recipe_evidence: '流程输入比对', recipe_generated: '流程已生成',
  recipe_run_start: '流程运行开始', recipe_input: '流程输入', recipe_step_start: '流程步骤开始',
  recipe_step_done: '流程步骤结束', recipe_step_error: '流程步骤错误', recipe_repair: '流程自动修复',
  recipe_output: '流程输出', recipe_validation: '运行校验', recipe_run_done: '流程运行结束',
  recipe_verify: '结果比对', recipe_refine: '调整流程', recipe_diff: '流程差异',
  recipe_completed: '流程处理完成', recipe_saved: '流程已保存', recipe_deleted: '流程已删除',
  recipe_failed: '流程执行失败', recipe_view: '查看流程', recipes_changed: '流程列表已更新',
}

export function formatLogValue(value: unknown): string {
  if (typeof value !== 'string') return JSON.stringify(value, null, 2) ?? String(value)
  try { return JSON.stringify(JSON.parse(value), null, 2) } catch { /* 普通日志 / 代码 */ }
  // 脚本结果是单行 JSON 后跟工具建议，保留建议并单独格式化报告。
  const newline = value.indexOf('\n')
  if (newline > 0) {
    try { return `${JSON.stringify(JSON.parse(value.slice(0, newline)), null, 2)}${value.slice(newline)}` } catch { /* 普通多行文本 */ }
  }
  return value
}

function section(label: string, value: unknown, language = 'JSON'): LogSection {
  return { label, text: formatLogValue(value), language }
}

function parameterSections(params: unknown, tool = ''): LogSection[] {
  if (!params || typeof params !== 'object' || Array.isArray(params)) return [section('参数', params)]
  const rest = { ...params as Record<string, unknown> }
  const sections: LogSection[] = []
  for (const field of ['code', 'sql']) {
    if (typeof rest[field] !== 'string' || !rest[field]) continue
    const language = field === 'sql' ? 'SQL' : tool.includes('python') ? 'Python' : 'Code'
    sections.push(section(field === 'sql' ? '执行 SQL' : '执行代码', rest[field], language))
    delete rest[field]
  }
  if (Object.keys(rest).length || !sections.length) sections.push(section('参数', rest))
  return sections
}

function eventStatus(event: AgentEvent): LogStatus {
  const status = String(event.status ?? event.report?.status ?? '')
  if (event.ok === false || event.type === 'error' || event.type.endsWith('_error') || event.type.endsWith('_failed') ||
      event.error || ['error', 'failed', 'fail', 'escalated', 'gave_up'].includes(status)) return 'error'
  if (['warning', 'warn', 'stopped', 'waiting'].includes(status)) return 'warning'
  if (['done', 'success', 'pass', 'repaired'].includes(status) || event.type.endsWith('_saved')) return 'success'
  return 'info'
}

function eventSummary(event: AgentEvent): string {
  return String(event.error || event.summary || event.description || event.content || event.text ||
    event.report?.summary || event.filename || event.name || event.table?.name || '')
}

function resultError(value: unknown): string {
  if (typeof value !== 'string') return ''
  try {
    const report = JSON.parse(value.split('\n')[0] ?? '')
    return typeof report?.error === 'string' ? report.error : ''
  } catch { return '' }
}

function timelineEvents(items: TimelineItem[]): AgentEvent[] {
  return items.flatMap((item): AgentEvent[] => {
    if (item.kind === 'thinking') return []
    if (item.kind === 'tool') return [
      { type: 'tool_call', id: item.id, tool: item.tool, label: item.label, params: item.params },
      ...(item.status === 'running' ? [] : [{ type: 'tool_result', id: item.id, tool: item.tool, ok: item.status === 'ok', summary: item.summary, text: item.text, duration_ms: item.duration_ms }]),
    ]
    return [{ type: item.kind, ...item }]
  })
}

/** 按调用开始顺序合并请求与返回，保留其他诊断事件的顺序与完整内容。 */
export function buildExecutionLogs(message: UiMessage | null): ExecutionLog[] {
  if (!message) return []
  const events = message.events?.length ? message.events : timelineEvents(message.timeline)
  const entries: ExecutionLog[] = []
  const calls = new Map<string, ExecutionLog>()
  for (const [index, original] of events.entries()) {
    const event = withoutScriptCode(original) as AgentEvent
    if (['text_chunk', 'thinking', 'recipe_thinking', 'final_text'].includes(event.type)) continue
    if (event.type === 'tool_call') {
      const entry: ExecutionLog = {
        key: `tool-${event.id}-${index}`, sequence: index + 1, category: 'tool',
        title: String(event.tool || event.label || '工具调用'), type: event.type,
        status: message.streaming ? 'running' : 'error', summary: String(event.label || ''),
        callId: event.id, sections: parameterSections(event.debug_params ?? event.params ?? {}, event.tool), raw: { request: event },
        scripts: scriptReferences(event),
      }
      calls.set(event.id, entry)
      entries.push(entry)
      continue
    }
    if (event.type === 'tool_result') {
      const call = calls.get(event.id)
      if (call) {
        call.status = event.ok ? 'success' : 'error'
        call.summary = (!event.ok && resultError(event.debug_result)) || String(event.summary || call.summary)
        call.duration = event.duration_ms
        call.scripts = scriptReferences([call.scripts, event])
        if (event.debug_result !== undefined || event.text !== undefined) {
          const value = event.debug_result ?? event.text
          call.sections.push(section('返回结果', value, typeof value === 'string' && !value.trimStart().startsWith('{') && !value.trimStart().startsWith('[') ? 'Text' : 'JSON'))
        }
        call.raw = { request: (call.raw as { request: AgentEvent }).request, response: event }
        continue
      }
    }
    const parts: LogSection[] = []
    if (event.params) parts.push(...parameterSections(event.params))
    if (event.sql) parts.push(section('执行 SQL', event.sql, 'SQL'))
    entries.push({
      key: `event-${index}`, sequence: index + 1, category: 'event',
      title: EVENT_LABELS[event.type] ?? event.type, type: event.type, status: eventStatus(event),
      summary: eventSummary(event), callId: event.tool_call_id ?? event.id, duration: event.duration_ms,
      sections: parts, raw: event,
      scripts: scriptReferences(event),
    })
  }
  if (message.error && !entries.some((entry) => entry.status === 'error' && entry.summary === message.error)) {
    entries.push({ key: 'message-error', sequence: events.length + 1, category: 'event', title: '执行错误', type: 'error',
      status: 'error', summary: message.error, sections: [], scripts: [], raw: { type: 'error', content: message.error } })
  }
  return entries
}

export function filterExecutionLogs(entries: ExecutionLog[], filter: LogFilter, query: string): ExecutionLog[] {
  const needle = query.trim().toLocaleLowerCase()
  return entries.filter((entry) =>
    (filter !== 'tools' || entry.category === 'tool') && (filter !== 'errors' || entry.status === 'error') &&
    (!needle || `${entry.title}\n${entry.summary}\n${entry.callId ?? ''}\n${formatLogValue(entry.raw)}`.toLocaleLowerCase().includes(needle)),
  )
}

export function exportExecutionLogs(message: UiMessage, entries: ExecutionLog[]): string {
  return JSON.stringify({ message_id: message.id ?? message.key, created_at: message.createdAt ?? null,
    status: message.streaming ? 'running' : message.error ? 'error' : 'done', logs: entries.map((entry) => entry.raw) }, null, 2)
}
