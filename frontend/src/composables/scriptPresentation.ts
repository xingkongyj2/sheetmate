import type { ScriptReference } from '../api/types.ts'

/** Titles count Unicode characters, including a whole supplementary character. */
export function scriptTitle(value: unknown): string {
  const title = typeof value === 'string' ? value.trim() : ''
  return Array.from(title || 'Python 脚本').slice(0, 20).join('')
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
