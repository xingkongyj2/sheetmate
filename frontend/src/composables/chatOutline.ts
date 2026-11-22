/**
 * 聊天页左侧对话目录：从消息流提炼每轮提问摘要、时间范围与当前阅读位置。
 * 纯函数实现，便于单元测试，不依赖 DOM。
 */
import type { FileInfo } from '../api/types.ts'
import { isImageFile } from './attachmentFiles.ts'
import type { UiMessage } from './chatModel.ts'
import { recipeEditLabel, storedRecipeEdit } from './recipeEditing.ts'
import { parseTableReferences } from './tableReferences.ts'

export interface ChatOutlineItem {
  key: string
  index: number
  timeLabel: string
  timestamp: number | null
  text: string
  attachmentNote: string
  fullLabel: string
  running: boolean
  adopted: boolean
  /** 视觉权重：包含较多步骤、产物或长内容的轮次在竖条中显示为短竖线，简短提问显示为圆点。 */
  weight: 'dot' | 'bar'
}

export interface OutlineMetric {
  key: string
  top: number
}

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'] as const

function parseTimestamp(value: string | number | null | undefined): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (!value) return null
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : null
}

export function formatOutlineClock(timestamp: number | null): string {
  if (timestamp === null) return ''
  const date = new Date(timestamp)
  if (Number.isNaN(date.getTime())) return ''
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

function summarizePrompt(message: UiMessage, attachmentCount: number): string {
  const plain = parseTableReferences(message.content)
    .map((segment) => (segment.type === 'table'
      ? segment.reference.sheet
      : segment.type === 'file'
        ? segment.reference.filename
        : segment.text))
    .join('')

  const cleaned = plain
    .replace(/```[^\n]*\n?([\s\S]*?)```/g, '$1')
    .replace(/^\s*(?:[-*+]\s*)?\[[ xX]\]\s*/gm, '')
    .replace(/^\s*(?:#{1,6}|>+)\s+/gm, '')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/(`+)([^`]*?)\1/g, '$2')
    .replace(/\*\*|__|~~/g, '')
    .replace(/\s+/g, ' ')
    .trim()

  if (cleaned) return cleaned
  const edit = storedRecipeEdit(message.meta.recipe_edit)
  if (edit) return recipeEditLabel(edit)
  return attachmentCount > 0 ? '发送附件' : '空消息'
}

function attachmentSummary(message: UiMessage, fileMap: Map<string, FileInfo>): { count: number; note: string } {
  const ids = Array.isArray(message.meta.attachments)
    ? message.meta.attachments.filter((id): id is string => typeof id === 'string' && !!id)
    : []
  if (!ids.length) return { count: 0, note: '' }

  let images = 0
  let files = 0
  for (const id of ids) {
    const file = fileMap.get(id)
    if (file && isImageFile(file.filename, file.media_type)) images++
    else files++
  }

  const parts: string[] = []
  if (files > 0) parts.push(`${files} 个文件`)
  if (images > 0) parts.push(`${images} 张图片`)
  return { count: ids.length, note: parts.join('、') }
}

function computeWeight(user: UiMessage, replies: UiMessage[], attachmentCount: number): 'dot' | 'bar' {
  if (attachmentCount > 0 || user.content.trim().length >= 120) return 'bar'
  for (const reply of replies) {
    if (reply.artifacts.length > 0 || reply.timeline.length >= 3 || reply.content.trim().length >= 260) {
      return 'bar'
    }
  }
  return 'dot'
}

function isCommittedSteer(message: UiMessage): boolean {
  const steering = message.meta.steering
  return !!steering && typeof steering === 'object' && (steering as Record<string, unknown>).status === 'committed'
}

/** 从对话消息流提炼左侧目录条目（按用户发言划分轮次）。 */
export function buildChatOutline(messages: UiMessage[], files: FileInfo[] = []): ChatOutlineItem[] {
  const fileMap = new Map(files.map((file) => [file.file_id, file]))
  const items: ChatOutlineItem[] = []

  for (let i = 0; i < messages.length; i++) {
    const message = messages[i]!
    if (message.role !== 'user') continue

    const replies: UiMessage[] = []
    for (let j = i + 1; j < messages.length && messages[j]!.role === 'assistant'; j++) {
      replies.push(messages[j]!)
    }

    const timestamp = parseTimestamp(message.createdAt)
    const { count: attachmentCount, note: attachmentNote } = attachmentSummary(message, fileMap)
    const text = summarizePrompt(message, attachmentCount)
    const fullLabel = attachmentNote ? `${text}（${attachmentNote}）` : text

    items.push({
      key: message.key,
      index: items.length + 1,
      timeLabel: formatOutlineClock(timestamp),
      timestamp,
      text,
      attachmentNote,
      fullLabel,
      running: replies.some((reply) => reply.streaming),
      adopted: isCommittedSteer(message),
      weight: computeWeight(message, replies, attachmentCount),
    })
  }

  return items
}

/** 格式化目录浮层顶部的日期、时间跨度与轮数统计。 */
export function formatOutlineMeta(
  items: ChatOutlineItem[],
  options: { fallbackStart?: string | null; fallbackEnd?: string | null; now?: Date } = {},
): string {
  const count = items.length
  if (!count) return ''

  const timestamps = items.map((item) => item.timestamp).filter((ts): ts is number => ts !== null)
  const fallbackStart = parseTimestamp(options.fallbackStart)
  const fallbackEnd = parseTimestamp(options.fallbackEnd)
  const start = timestamps[0] ?? fallbackStart
  const end = timestamps.length
    ? Math.max(timestamps[timestamps.length - 1]!, fallbackEnd ?? 0)
    : (fallbackEnd ?? start)

  if (start === null) return `${count} 轮对话`

  const startDate = new Date(start)
  const endDate = new Date(end ?? start)
  const now = options.now ?? new Date()
  const sameDay = startDate.getFullYear() === endDate.getFullYear()
    && startDate.getMonth() === endDate.getMonth()
    && startDate.getDate() === endDate.getDate()

  if (sameDay) {
    const yearPrefix = startDate.getFullYear() === now.getFullYear() ? '' : `${startDate.getFullYear()}年`
    const dateLabel = `${yearPrefix}${startDate.getMonth() + 1}月${startDate.getDate()}日${WEEKDAYS[startDate.getDay()]}`
    const startClock = formatOutlineClock(start)
    const endClock = formatOutlineClock(end ?? start)
    const range = startClock && endClock && startClock !== endClock ? `${startClock} – ${endClock}` : startClock
    return range ? `${dateLabel} · ${range} · ${count} 轮` : `${dateLabel} · ${count} 轮`
  }

  const startYearPrefix = startDate.getFullYear() === now.getFullYear() ? '' : `${startDate.getFullYear()}年`
  const endYearPrefix = endDate.getFullYear() === startDate.getFullYear() ? '' : `${endDate.getFullYear()}年`
  const startDay = `${startYearPrefix}${startDate.getMonth() + 1}月${startDate.getDate()}日`
  const endDay = `${endYearPrefix}${endDate.getMonth() + 1}月${endDate.getDate()}日`
  return `${startDay} – ${endDay} · ${count} 轮`
}

/**
 * 根据滚动容器当前位置解析处于阅读焦点的轮次 key。
 * 滚到顶部时固定第一轮，滚到底部时固定最后一轮，中间按视口上方约 28% 阅读锚线匹配。
 */
export function resolveActiveOutlineKey(
  metrics: OutlineMetric[],
  scrollTop: number,
  clientHeight: number,
  scrollHeight: number,
  options: { anchorRatio?: number; edgeThreshold?: number } = {},
): string | null {
  if (!metrics.length) return null
  if (metrics.length === 1) return metrics[0]!.key

  const edgeThreshold = options.edgeThreshold ?? 24
  const maxScroll = Math.max(0, scrollHeight - clientHeight)

  if (scrollTop <= edgeThreshold) return metrics[0]!.key
  if (maxScroll > 0 && maxScroll - scrollTop <= edgeThreshold) return metrics[metrics.length - 1]!.key

  const anchorRatio = options.anchorRatio ?? 0.28
  const anchorOffset = Math.min(220, Math.max(72, clientHeight * anchorRatio))
  const anchor = scrollTop + anchorOffset

  let active = metrics[0]!.key
  for (const metric of metrics) {
    if (metric.top <= anchor) active = metric.key
    else break
  }
  return active
}
