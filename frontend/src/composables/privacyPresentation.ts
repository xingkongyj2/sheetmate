/**
 * 脱敏的展示辅助：把后端事件规范成界面用的结构（字段缺失时给默认值，不让界面崩），
 * 以及聊天卡片和详情面板共用的文案和数字格式。
 */
import type { AgentEvent, PrivacyColumn, PrivacyFile, PrivacyFileStatus, PrivacyReport, PrivacySheet, PrivacySource } from '@/api/types'

const counter = new Intl.NumberFormat('en-US')
export const formatCount = (value: number) => counter.format(value)

export const PRIVACY_SOURCE_LABELS: Record<PrivacySource, string> = { rules: '规则', model: '模型', both: '规则+模型', manual: '手动' }

const KINDS = ['phone', 'landline', 'email', 'im', 'address', 'mixed', 'custom'] as const
const ACTIONS = ['tokenize', 'address_detail', 'cell_scan'] as const
const SOURCES = ['rules', 'model', 'both'] as const

const num = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) ? value : 0)
const str = (value: unknown) => (typeof value === 'string' ? value : '')
const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : [])

function readColumn(value: unknown): PrivacyColumn | null {
  if (!isRecord(value) || !str(value.name)) return null
  const kind = KINDS.find((item) => item === value.kind) ?? 'mixed'
  const action = ACTIONS.find((item) => item === value.action) ?? 'cell_scan'
  const source = SOURCES.find((item) => item === value.source) ?? 'rules'
  return {
    name: str(value.name), kind, action, source,
    kind_label: str(value.kind_label) || '敏感信息',
    action_label: str(value.action_label) || '已替换',
    reason: str(value.reason), values: num(value.values),
  }
}

function readSheet(value: unknown): PrivacySheet | null {
  if (!isRecord(value)) return null
  return {
    sheet: str(value.sheet), rows: num(value.rows),
    columns: list(value.columns).flatMap((item) => readColumn(item) ?? []),
  }
}

export function readPrivacyFiles(value: unknown): PrivacyFile[] {
  return list(value).flatMap((item): PrivacyFile[] => {
    if (!isRecord(item)) return []
    return [{
      file_id: str(item.file_id), filename: str(item.filename) || '未命名文件', cached: item.cached === true,
      source: item.source === 'upload' || item.source === 'chat' ? item.source : null,
      sheets: list(item.sheets).flatMap((sheet) => readSheet(sheet) ?? []),
    }]
  })
}

/** 文件行里脱敏小图标的提示文字 */
export function privacyStatusText(status: PrivacyFileStatus): string {
  const when = status.source === 'upload' ? '上传时识别' : status.source === 'chat' ? '对话时识别' : ''
  if (status.state === 'running') return '正在识别敏感列…'
  if (status.state === 'failed') return `识别没有完成，对话里用到这个文件时会再试${status.error ? `（${status.error}）` : ''}`
  if (status.state === 'none') return '还没识别，对话里用到这个文件时会自动识别'
  if (status.columns > 0) return `已识别敏感列：保护 ${status.columns} 列${when ? `（${when}）` : ''}`
  return '已检查：没有发现联系方式或地址'
}

/** 文件详情里脱敏的一句话汇总（用词和聊天页一致；完整说明在 privacyStatusText，放在提示里） */
export function privacyStatusShort(status: PrivacyFileStatus): string {
  if (status.state === 'running') return '识别中…'
  if (status.state === 'failed') return '识别未完成'
  if (status.state === 'none') return '未识别'
  return status.columns > 0 ? `已脱敏 ${status.columns} 列` : '无需替换'
}

/** privacy_report 事件 → 卡片数据；事件不合法返回 null */
export function readPrivacyReport(event: AgentEvent): PrivacyReport | null {
  if (event.type !== 'privacy_report') return null
  const files = readPrivacyFiles(event.files)
  const totals = isRecord(event.totals) ? event.totals : {}
  return {
    status: event.status === 'blocked' ? 'blocked' : 'ok',
    mode: event.mode === 'rules' ? 'rules' : 'model_rules',
    model: typeof event.model === 'string' && event.model ? event.model : null,
    headline: str(event.headline) || (event.status === 'blocked' ? '脱敏没有完成，已停止处理' : '已完成脱敏'),
    files,
    totals: { files: num(totals.files) || files.length, columns: num(totals.columns), values: num(totals.values) },
    notes: list(event.notes).filter((item): item is string => typeof item === 'string' && !!item.trim()),
    reason: typeof event.reason === 'string' && event.reason.trim() ? event.reason.trim() : null,
  }
}

/** privacy_start 事件 → 正在脱敏的文件名 */
export function readPrivacyStart(event: AgentEvent): string[] {
  return list(event.files).filter((item): item is string => typeof item === 'string' && !!item)
}

/** 助手回复里要不要显示脱敏这一块：被中止、有被保护的列、或有需要说明的事才显示；没有任何东西被脱敏就什么都不说 */
export function showsPrivacyReport(report: PrivacyReport): boolean {
  return report.status === 'blocked' || report.notes.length > 0 || report.files.some((file) => protectedColumnCount(file) > 0)
}

/** 一个文件被保护的列数 */
export function protectedColumnCount(file: PrivacyFile): number {
  return file.sheets.reduce((sum, sheet) => sum + sheet.columns.length, 0)
}

export function runningPrivacyText(filenames: string[]): string {
  return filenames.length ? `正在对 ${filenames.length} 个文件脱敏` : '正在脱敏'
}
