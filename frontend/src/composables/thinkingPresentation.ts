/**
 * 思考模式的展示：输入框上方的任务清单、回答下面的核对小标识。
 *
 * 都是纯函数：AI 根据展示整体进度的价值决定要不要列清单，后端把决定写在 plan_start.checklist 里，与步骤数无关；这里只负责整理界面数据。
 */
import type { AuditResult, PlanStatus, UiMessage } from './chatModel'
import { userFacingText } from './timelinePresentation'

export interface ChecklistItem {
  key: string
  num: number
  description: string
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

/** 要在输入框上方列任务清单的那条助手消息的各项；后端没有要求展示时返回空数组。 */
export function checklistItems(message: UiMessage | undefined): ChecklistItem[] {
  if (!message || message.role !== 'assistant' || !message.checklist) return []
  return message.timeline.flatMap((item): ChecklistItem[] => item.kind === 'plan'
    ? [{ key: item.key, num: item.num, description: userFacingText(item.description, `第 ${item.num} 项`, 120), status: item.status }]
    : [])
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
