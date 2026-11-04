import { referenceSourceKey, type FileReference, type TableReference } from './tableReferences.ts'

export type ReferenceTrigger = '@' | '#'

export type ReferenceSuggestion = {
  key: string
  label: string
} & (
  | { type: 'file'; reference: FileReference }
  | { type: 'table'; reference: TableReference }
)

export interface ReferenceQuery {
  trigger: ReferenceTrigger
  query: string
  start: number
  end: number
}

/** Keep upload order stable while searching either a filename or its worksheets. */
export function referenceSuggestions(
  trigger: ReferenceTrigger,
  query: string,
  tables: TableReference[] = [],
  files: FileReference[] = [],
): ReferenceSuggestion[] {
  const terms = query.trim().toLowerCase().split(/\s+/u).filter(Boolean)
  const seen = new Set<string>()
  const candidates: ReferenceSuggestion[] = trigger === '@'
    ? files.map((reference) => ({
      key: JSON.stringify(['file', referenceSourceKey(reference)]),
      type: 'file' as const,
      label: reference.filename,
      reference,
    }))
    : tables.map((reference) => ({
      key: JSON.stringify(['table', referenceSourceKey(reference), reference.sheet]),
      type: 'table' as const,
      label: `${reference.filename} - ${reference.sheet}`,
      reference,
    }))

  return candidates.filter((candidate) => {
    if (seen.has(candidate.key)) return false
    seen.add(candidate.key)
    const label = candidate.label.toLowerCase()
    return terms.every((term) => label.includes(term))
  })
}

/** Find the unfinished mention immediately before the caret in visible editor text. */
export function findReferenceQuery(
  text: string,
  caret: number,
  ranges: { start: number; end: number }[] = [],
): ReferenceQuery | null {
  if (!Number.isInteger(caret) || caret < 0 || caret > text.length) return null
  const beforeCaret = text.slice(0, caret)
  const start = Math.max(beforeCaret.lastIndexOf('@'), beforeCaret.lastIndexOf('#'))
  if (start < 0) return null

  if (ranges.some((range) => range.start < caret && range.end > start)) return null

  // Persisted references can contain literal @/# in their filenames. They are
  // already complete even when a caller has not supplied their visible ranges.
  const encoded = /【(?:文件|表格)：(?:\\[\s\S]|[^\\】])*】|<table-ref>[\s\S]*?<\/table-ref>/gu
  for (const reference of text.matchAll(encoded)) {
    if (reference.index < caret && reference.index + reference[0].length > start) return null
  }

  const query = text.slice(start + 1, caret)
  if (/[\r\n，。；！？,;!?【】\uFFFC]/u.test(query)) return null
  return { trigger: text[start] as ReferenceTrigger, query, start, end: caret }
}
