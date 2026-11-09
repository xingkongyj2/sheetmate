/**
 * 思考模式的展示：右侧任务进度卡、回答下面的核对小标识。
 *
 * 都是纯函数：后端通过 plan_start.checklist 控制进度卡；这里只整理简短展示文字，保留原始执行记录。
 */
import type { AuditResult, PlanStatus, TimelineItem, UiMessage } from './chatModel'
import { userFacingText } from './timelinePresentation'

export interface ChecklistItem {
  key: string
  num: number
  description: string
  summary: string
  status: PlanStatus
}

export type ChecklistTone = 'running' | 'ok' | 'warn' | 'error' | 'stopped' | 'waiting'

export interface ChecklistSummary {
  done: number
  total: number
  /** 折叠成一行时显示的文字：进行中是正在做的那一项，结束后是结果。 */
  label: string
  tone: ChecklistTone
}

export const STEP_TEXT_LIMIT = 30

/** Compact display text only; the original plan and execution records stay intact. */
export function shortStepText(value: string | undefined, fallback = ''): string {
  const source = (value ?? '')
    .replace(/^\s*(?:[-*+]\s*)?\[[ xX]\]\s*/gm, '')
    .replace(/^\s*(?:#{1,6}|>+|[-*+]|\d+[.)、])\s+/gm, '')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
  const text = userFacingText(source, fallback, source.length)
    .replace(/(`+)([^`]*?)\1/g, '$2')
    .replace(/\*\*|__|~~|\*|_/g, '')
    .replace(/<[^>]*>/g, '')
    .replace(/\s+/g, ' ').trim() || fallback
  const characters = Array.from(text)
  return characters.length > STEP_TEXT_LIMIT
    ? `${characters.slice(0, STEP_TEXT_LIMIT - 1).join('')}…`
    : text
}

const STEP_SUMMARY_FALLBACK: Record<PlanStatus, string> = {
  pending: '等待执行', running: '正在处理', done: '本步骤已完成', warning: '已处理，仍需核对',
  waiting: '等待确认后继续', error: '本步骤未完成', stopped: '本步骤已停止',
}

/** Old completed steps may only have a note; other states must not inherit a success note. */
export function stepSummary(message: UiMessage, item: Extract<TimelineItem, { kind: 'plan' }>): string {
  const fallback = STEP_SUMMARY_FALLBACK[item.status]
  if (item.status === 'pending' || item.status === 'running') return fallback
  const summary = shortStepText(item.summary)
  if (summary) return summary
  if (item.status === 'done') {
    const note = [...message.timeline].reverse().find((entry) => entry.kind === 'note' && entry.step === item.num)
    if (note?.kind === 'note') return shortStepText(note.text, fallback)
  }
  return fallback
}

/** 当前助手消息的进度项；旧记录没有启用清单时返回空数组。 */
export function checklistItems(message: UiMessage | undefined): ChecklistItem[] {
  if (!message || message.role !== 'assistant' || !message.checklist) return []
  return message.timeline.flatMap((item): ChecklistItem[] => item.kind === 'plan'
    ? [{ key: item.key, num: item.num, description: shortStepText(item.description, `第 ${item.num} 项`),
      summary: stepSummary(message, item), status: item.status }]
    : []).sort((left, right) => left.num - right.num)
}

const FINISHED: ReadonlySet<PlanStatus> = new Set(['done', 'warning'])

export function checklistSummary(items: ChecklistItem[], running: boolean): ChecklistSummary {
  const total = items.length
  const done = items.filter((item) => FINISHED.has(item.status)).length
  if (running) {
    const current = items.find((item) => item.status === 'running')
    return { done, total, label: current?.description ?? '正在收尾', tone: 'running' }
  }
  const has = (status: PlanStatus) => items.some((item) => item.status === status)
  if (has('error')) return { done, total, label: '处理出错了', tone: 'error' }
  if (has('stopped')) return { done, total, label: '已停止', tone: 'stopped' }
  if (has('waiting')) return { done, total, label: '等你确认后继续', tone: 'waiting' }
  if (has('pending')) return { done, total, label: `完成了 ${done} 项，其余没有执行`, tone: 'warn' }
  const careful = items.filter((item) => item.status === 'warning').length
  return careful
    ? { done, total, label: `已完成，有 ${careful} 项需要留意`, tone: 'warn' }
    : { done, total, label: '已全部完成', tone: 'ok' }
}

export type AuditTone = 'ok' | 'warn' | 'skip'

/** 回答下面的核对小标识。没有核对结果（普通模式、闲聊）时返回 null。 */
export function auditLine(audit: AuditResult | undefined): { text: string; tone: AuditTone } | null {
  if (!audit) return null
  if (audit.skipped) {
    const reason = userFacingText(audit.reason, '', 100)
    return { tone: 'skip', text: `回答核对没有完成${reason ? `（${reason}）` : ''}，请自行核对关键数字` }
  }
  if (audit.ok) return { tone: 'ok', text: '已对照执行结果核对回答里的数字和结论，没有发现出入' }
  const issue = userFacingText(audit.issues[0], '内容与执行结果不符', 120)
  const detail = `${issue}${audit.issues.length > 1 ? ` 等 ${audit.issues.length} 处` : ''}`
  return audit.corrected
    ? { tone: 'ok', text: `核对时发现出入（${detail}），已据此更正回答` }
    : { tone: 'warn', text: `核对时发现出入（${detail}），没能完成更正，请自行核对` }
}
