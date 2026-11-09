import type { ExecutionState, ExecutionStatus } from '../api/types.ts'
import type { UiMessage } from './chatModel.ts'
import { userFacingText } from './timelinePresentation.ts'

const STATUSES = new Set<ExecutionStatus>(['running', 'completed', 'incomplete', 'waiting', 'failed', 'cancelled', 'interrupted', 'unknown'])
const SEVERITY: Record<ExecutionStatus, number> = { running: 0, completed: 0, unknown: 1, waiting: 2, incomplete: 3, interrupted: 4, cancelled: 4, failed: 5 }

export function readExecutionState(value: unknown): ExecutionState | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const state = value as Record<string, unknown>
  if (typeof state.status !== 'string' || !STATUSES.has(state.status as ExecutionStatus)) return undefined
  const fields = { ...state }
  delete fields.type
  return { ...fields, status: state.status as ExecutionStatus,
    code: typeof state.code === 'string' ? state.code : undefined,
    summary: typeof state.summary === 'string' ? state.summary : undefined }
}

/** 子步骤结束不能让此前未完成的任务重新变为成功；总体事件由后端单独汇总。 */
export function mergeExecutionStop(previous: ExecutionState | undefined, next: ExecutionState): ExecutionState {
  return previous && SEVERITY[previous.status] > SEVERITY[next.status] ? previous : next
}

export function messageExecution(message: UiMessage): ExecutionState | undefined {
  const state = message.execution ?? readExecutionState(message.meta.execution)
  if (state) return state
  if (message.error) return { status: /已停止|用户停止/.test(message.error) ? 'cancelled' : 'failed', summary: message.error }
  return undefined
}

export function executionExportStatus(message: UiMessage): ExecutionStatus {
  if (message.streaming) return 'running'
  const status = messageExecution(message)?.status
  return status === 'running' ? 'incomplete' : status ?? 'unknown'
}

export interface ExecutionNotice {
  title: string
  summary: string
  lastStep: string
  tone: 'error' | 'warning' | 'info'
}

const REASONS: Record<string, string> = {
  timeout: '模型响应超时，本次处理未完成。',
  connection_error: '与模型服务的连接中断，本次处理未完成。',
  stream_incomplete: '模型返回中途结束，没有收到完整的结束信号。',
  empty_response: '模型没有返回可执行的操作或有效回答。',
  output_limit: '模型输出达到长度上限，本次响应不完整。',
  content_filter: '模型服务中止了本次回复，本次处理未完成。',
  unexpected_finish_reason: '模型服务返回了异常的结束状态，本次处理未完成。',
  tool_protocol: '模型返回的操作不完整，相关操作未执行。',
  premature_stop: '模型只返回了后续计划，没有继续完成处理。',
  max_iterations: '本次处理已达到执行轮数上限，剩余工作尚未完成。',
  tool_budget_exhausted: '本次处理已达到操作次数上限，剩余工作尚未完成。',
  recovery_exhausted: '多次调整处理方式后仍未解决问题，已停止本次处理。',
  user_cancelled: '用户停止了本次处理，尚未确认任务全部完成。',
  user_stop: '用户停止了本次处理，尚未确认任务全部完成。',
  system_cancelled: '本次处理被系统中断，尚未确认任务全部完成。',
  process_interrupted: '服务重启前的处理未正常结束，无法确认当时的具体中断原因。',
  process_restart: '服务重启前的处理未正常结束，无法确认当时的具体中断原因。',
  missing_execution_status: '处理连接已经结束，但没有收到任务完成确认。',
}

/** 普通聊天只显示停止事实；模型原始原因和预算留在执行日志中。 */
export function executionNotice(message: UiMessage): ExecutionNotice | null {
  if (message.streaming) return null
  const state = messageExecution(message)
  if (!state || state.status === 'completed' || state.status === 'unknown') return null
  const titles: Record<ExecutionStatus, string> = { running: '未完成', completed: '已完成', incomplete: '未完成', waiting: '等待确认', failed: '执行失败', cancelled: '已停止', interrupted: '已中断', unknown: '完成状态未知' }
  const fallback = state.status === 'waiting' ? '需要确认后才能继续处理。' : state.status === 'cancelled' ? '本次处理已停止。' : '本次处理未完成，请查看执行记录中的停止原因。'
  const lastStep = typeof state.last_step === 'string' ? state.last_step
    : state.last_step && typeof state.last_step === 'object' ? state.last_step.label ?? state.last_step.summary : ''
  const publicStep = typeof lastStep === 'string' && !/\b(?:table_info|run_sql|run_python_script|preview_table|build_recipe|ask_user)\b/.test(lastStep)
    ? userFacingText(lastStep, '', 160) : ''
  return { title: titles[state.status],
    summary: REASONS[state.code ?? ''] ?? userFacingText(state.summary, fallback, 400),
    lastStep: publicStep,
    tone: state.status === 'failed' ? 'error' : state.status === 'waiting' ? 'info' : 'warning' }
}
