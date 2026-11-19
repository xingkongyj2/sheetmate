import type { ScriptReference } from '../api/types.ts'

/** 脚本标题（以及 Python 步骤的名字）最多 15 个字，和后端 MAX_TITLE_LENGTH 一致 */
export const SCRIPT_TITLE_LIMIT = 15
const TITLE_BREAKS = '，,、；;。：:（('
const TITLE_BRACKETS: Record<string, string> = { '（': '）', '(': ')', '「': '」', '《': '》', '【': '】', '[': ']' }

/** 截断后括号 / 引号没写完时，退回到第一个没配对的开括号前面 */
function closeBrackets(chars: string[]): string[] {
  const stack: { index: number; close: string }[] = []
  chars.forEach((char, index) => {
    if (TITLE_BRACKETS[char]) stack.push({ index, close: TITLE_BRACKETS[char] })
    else if (stack.length && char === stack[stack.length - 1]!.close) stack.pop()
  })
  return stack.length ? chars.slice(0, stack[0]!.index) : chars
}

/** 收短到 limit 个字以内：优先在最后一个标点处断开（不留半句话），没有合适的标点再按字数截断；按 Unicode 字符计数 */
export function shortenTitle(value: unknown, limit = SCRIPT_TITLE_LIMIT, fallback = ''): string {
  const text = (typeof value === 'string' ? value : '').split(/\s+/).filter(Boolean).join(' ')
  const chars = Array.from(text)
  if (chars.length <= limit) return text || fallback
  const window = chars.slice(0, limit + 1)
  const cut = Math.max(...Array.from(TITLE_BREAKS, (mark) => window.lastIndexOf(mark)))
  const kept = closeBrackets(cut >= Math.floor(limit / 2) ? window.slice(0, cut) : chars.slice(0, limit)).join('').replace(/[\s，,、；;。：:（(\-—_]+$/u, '')
  return kept || chars.slice(0, limit).join('')
}

export function scriptTitle(value: unknown): string {
  return shortenTitle(value, SCRIPT_TITLE_LIMIT, 'Python 脚本')
}

/** Find file references in parameters, reports and JSON tool results without loading source. */
export function scriptReferences(value: unknown): ScriptReference[] {
  const scripts = new Map<string, ScriptReference>()
  const seen = new Set<object>()
  function visit(item: unknown, depth: number) {
    if (depth > 10) return
    if (typeof item === 'string') {
      const text = item.trimStart()
      if (!text.startsWith('{') && !text.startsWith('[')) return
      try { visit(JSON.parse(text), depth + 1) } catch {
        try { visit(JSON.parse(text.split('\n')[0] ?? ''), depth + 1) } catch { /* Plain tool output. */ }
      }
      return
    }
    if (!item || typeof item !== 'object' || seen.has(item)) return
    seen.add(item)
    if (Array.isArray(item)) { item.forEach((child) => visit(child, depth + 1)); return }
    const record = item as Record<string, unknown>
    if (typeof record.script_id === 'string' && record.script_id.trim()) {
      const id = record.script_id.trim()
      const existing = scripts.get(id)
      scripts.set(id, {
        script_id: id,
        script_title: scriptTitle(record.script_title || existing?.script_title),
        script_filename: `${id}.py`,
      })
    }
    for (const [key, child] of Object.entries(record)) {
      if (key !== 'code') visit(child, depth + 1)
    }
  }
  visit(value, 0)
  return [...scripts.values()]
}

/** A referenced script's source is fetched from its file, rather than repeated in logs. */
export function withoutScriptCode(value: unknown): unknown {
  if (typeof value === 'string') {
    const newline = value.indexOf('\n')
    try { return JSON.stringify(withoutScriptCode(JSON.parse(value))) } catch { /* JSON with a tool hint follows. */ }
    if (newline > 0) {
      try { return `${JSON.stringify(withoutScriptCode(JSON.parse(value.slice(0, newline))))}${value.slice(newline)}` } catch { /* Plain text. */ }
    }
    return value
  }
  if (Array.isArray(value)) return value.map(withoutScriptCode)
  if (!value || typeof value !== 'object') return value
  const record = value as Record<string, unknown>
  return Object.fromEntries(Object.entries(record)
    .filter(([key]) => key !== 'code' || typeof record.script_id !== 'string')
    .map(([key, child]) => [key, withoutScriptCode(child)]))
}
