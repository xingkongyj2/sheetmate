import type { TimelineItem } from './chatModel'

type ToolItem = Extract<TimelineItem, { kind: 'tool' }>

/** Keep progress feedback readable without leaking payloads, code or execution logs. */
export function userFacingText(value: string | undefined, fallback = '', maxLength = 200): string {
  let text = value?.trim() ?? ''
  if (!text) return fallback

  if (/^[{[]/.test(text)) {
    try {
      const payload: unknown = JSON.parse(text)
      if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return fallback
      const fields = payload as Record<string, unknown>
      text = [fields.summary, fields.message, fields.error].find((field): field is string => typeof field === 'string' && !!field.trim()) ?? ''
    } catch {
      return fallback
    }
  }

  if (/PermissionError|Permission denied|文件.*(?:占用|锁定)/i.test(text)) return '文件暂时无法读写，请检查是否被占用或缺少权限。'
  if (/TimeoutError|ConnectionError|timed out|连接中断|连接失败|超时/i.test(text)) return '处理过程中发生超时或连接错误。'
  const missingField = text.match(/(?:KeyError|ColumnNotFoundError):\s*['"]?([^'"\n]{1,60})/)
  if (missingField) return `未找到需要的字段“${missingField[1]?.trim()}”。`
  if (/TemplatePreservationError|原合并单元格|无法.*保留.*格式/i.test(text)) return '写入结果时遇到格式或合并单元格冲突。'
  if (/(?:参数|响应|返回值).{0,20}JSON|JSON.{0,20}(?:参数|格式|对象)/i.test(text)) return '处理步骤的设置需要调整。'

  // An error summary can contain an entire stack trace or a truncated JSON response.
  text = text.split(/Traceback \(most recent call last\):|\n\s*File "/)[0] ?? ''
  text = text.replace(/```[\s\S]*?(?:```|$)/g, '')
    .replace(/\b(?:recipe_id|run_id|file_id|tool_call_id)\s*[=:]\s*[A-Za-z0-9_-]+/g, '')
    .replace(/\b(?:rcp|run|script|call)_[A-Za-z0-9_-]+\b/g, '')
    .replace(/\[(\s*['"][^\]\n]+['"]\s*)\]/g, (_, values: string) => values.replace(/['"]/g, '').replace(/\s*,\s*/g, '、'))
    .replace(/^[\w.]+(?:Error|Exception):\s*/, '')
    .replace(/\s+/g, ' ').trim()

  if (/[{[]\s*["'][\w-]+["']\s*:|\b(?:SELECT\b[\s\S]*\bFROM|INSERT\s+INTO|import\s+\w+|def\s+\w+\(|pd\.|np\.|openpyxl\.)/i.test(text)) return fallback
  if (!text || /^(?:脚本|工具)?\s*[：:·-]*$/.test(text)) return fallback
  return text.length > maxLength ? `${text.slice(0, maxLength)}…` : text
}

export function toolPresentation(item: ToolItem): { label: string; summary: string } {
  const label = userFacingText(item.label, '处理数据', 80)
    .replace(/^SQL\s*查询/i, '查询数据')
  const fallback = item.status === 'running' ? '正在处理…' : item.status === 'error' ? '本次处理未完成。' : '已完成'
  return { label, summary: userFacingText(item.summary, fallback) }
}

/** Preserve paragraphs and lists in public feedback when no technical content was removed. */
export function userFacingSummary(value: string | undefined, fallback = '', maxLength = 800): string {
  const text = userFacingText(value, fallback, maxLength)
  const source = value?.trim() ?? ''
  return source && source.replace(/\s+/g, ' ') === text ? source : text
}
