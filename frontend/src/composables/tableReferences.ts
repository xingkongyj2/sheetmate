/** A selected worksheet keeps its file identity when sent to the assistant. */
export interface TableReference {
  filename: string
  sheet: string
  tableName?: string
}

export interface FileReference {
  filename: string
  sheet?: never
  tableName?: never
}

export type ComposerReference = TableReference | FileReference

export function isFileReference(reference: ComposerReference): reference is FileReference {
  return !reference.sheet
}

export type TableReferenceSegment =
  | { type: 'text'; text: string }
  | { type: 'table'; text: string; reference: TableReference }
  | { type: 'file'; text: string; reference: FileReference }

export type TableReferenceRange = {
  start: number
  end: number
  explicit: boolean
} & (
  | { type: 'table'; reference: TableReference }
  | { type: 'file'; reference: FileReference }
)

function escapeName(value: string): string {
  return value.replace(/[\\/【】]/g, '\\$&')
}

export function formatTableReference(reference: TableReference): string {
  return `【表格：${escapeName(reference.filename)} / ${escapeName(reference.sheet)}】`
}

export function formatFileReference(reference: FileReference): string {
  return `【文件：${escapeName(reference.filename)}】`
}

function unescapeName(value: string): string {
  return value.replace(/\\([\\/【】])/g, '$1')
}

function isExplicitReference(text: string): boolean {
  return text.startsWith('【表格：') || text.startsWith('【文件：') || text.startsWith('<table-ref>')
}

function readHumanReference(body: string): TableReference | null {
  for (let index = 0; index < body.length; index++) {
    if (body[index] === '\\') {
      index++
      continue
    }
    if (body.slice(index, index + 3) !== ' / ') continue
    const filename = unescapeName(body.slice(0, index))
    const sheet = unescapeName(body.slice(index + 3))
    return filename && sheet ? { filename, sheet } : null
  }
  return null
}

function readWireReference(json: string): TableReference | null {
  try {
    const value: unknown = JSON.parse(json)
    if (!value || typeof value !== 'object') return null
    const candidate = value as Record<string, unknown>
    if (typeof candidate.filename !== 'string' || !candidate.filename || typeof candidate.sheet !== 'string' || !candidate.sheet) return null
    return {
      filename: candidate.filename,
      sheet: candidate.sheet,
      ...(typeof candidate.tableName === 'string' && candidate.tableName ? { tableName: candidate.tableName } : {}),
    }
  } catch {
    return null
  }
}

function resolveReference(reference: TableReference, available: TableReference[]): TableReference {
  const matches = available.filter((item) => item.filename === reference.filename && item.sheet === reference.sheet)
  const exact = reference.tableName ? matches.find((item) => item.tableName === reference.tableName) : undefined
  const names = new Set(matches.map((item) => item.tableName).filter((name): name is string => !!name))
  const tableName = exact?.tableName ?? (names.size === 1 ? [...names][0] : reference.tableName)
  return { filename: reference.filename, sheet: reference.sheet, ...(tableName ? { tableName } : {}) }
}

function availableAliases(available: TableReference[], availableFiles: FileReference[]): Map<string, ComposerReference> {
  const candidates = new Map<string, Map<string, ComposerReference>>()
  const files: FileReference[] = [...availableFiles, ...available.map(({ filename }) => ({ filename }))]
  for (const reference of [...available, ...files]) {
    const aliases = isFileReference(reference) ? [reference.filename] : [reference.tableName, reference.sheet]
    for (const alias of new Set(aliases)) {
      if (!alias || alias.length < 2) continue
      const matches = candidates.get(alias) ?? new Map<string, ComposerReference>()
      matches.set(JSON.stringify([isFileReference(reference) ? 'file' : 'table', reference.filename, reference.sheet, reference.tableName]), reference)
      candidates.set(alias, matches)
    }
  }
  return new Map([...candidates].flatMap(([alias, matches]) => matches.size === 1 ? [[alias, [...matches.values()][0]!] as const] : []))
}

/** Explicit mentions and unambiguous, delimited workspace table names. */
export function parseTableReferences(text: string, available: TableReference[] = [], availableFiles: FileReference[] = []): TableReferenceSegment[] {
  const segments: TableReferenceSegment[] = []
  const aliases = availableAliases(available, availableFiles)
  const names = [...aliases.keys()].sort((left, right) => right.length - left.length)
  const aliasPattern = names.length ? new RegExp(names.map((name) => name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|'), 'gu') : null

  function appendText(value: string) {
    if (!value) return
    const previous = segments.at(-1)
    if (previous?.type === 'text') previous.text += value
    else segments.push({ type: 'text', text: value })
  }

  function appendPlain(value: string) {
    if (!aliasPattern) return appendText(value)
    aliasPattern.lastIndex = 0
    let offset = 0
    for (const match of value.matchAll(aliasPattern)) {
      const start = match.index
      const end = start + match[0].length
      const before = value.slice(0, start).match(/.$/u)?.[0] ?? ''
      const after = value.slice(end).match(/^./u)?.[0] ?? ''
      // Do not turn a name embedded in another word into a reference.
      if ((/^[\p{L}\p{N}_]/u.test(match[0]) && /[\p{L}\p{N}_]/u.test(before)) ||
          (/[\p{L}\p{N}_]$/u.test(match[0]) && /[\p{L}\p{N}_]/u.test(after))) continue
      appendText(value.slice(offset, start))
      const reference = aliases.get(match[0])!
      if (isFileReference(reference)) segments.push({ type: 'file', text: match[0], reference })
      else segments.push({ type: 'table', text: match[0], reference: resolveReference(reference, available) })
      offset = end
    }
    appendText(value.slice(offset))
  }

  const explicitPattern = /【表格：((?:\\[\s\S]|[^\\】])*)】|<table-ref>([\s\S]*?)<\/table-ref>|【文件：((?:\\[\s\S]|[^\\】])*)】/gu
  let offset = 0
  for (const match of text.matchAll(explicitPattern)) {
    appendPlain(text.slice(offset, match.index))
    if (match[3] !== undefined) {
      const filename = unescapeName(match[3])
      if (filename) segments.push({ type: 'file', text: match[0], reference: { filename } })
      else appendText(match[0])
    } else {
      const reference = match[1] !== undefined ? readHumanReference(match[1]) : readWireReference(match[2]!)
      if (reference) segments.push({ type: 'table', text: match[0], reference: resolveReference(reference, available) })
      else appendText(match[0])
    }
    offset = match.index + match[0].length
  }
  appendPlain(text.slice(offset))
  return segments
}

/** Names remain ordinary quoted text in messages sent to the assistant. */
export function serializeTableReferences(text: string, available: TableReference[] = [], availableFiles: FileReference[] = []): string {
  let offset = 0
  const quoted = quotedSpans(text)
  return parseTableReferences(text, available, availableFiles).map((segment) => {
    const start = offset
    offset += segment.text.length
    if (segment.type === 'text') return segment.text
    const quotedName = JSON.stringify(segment.type === 'file' ? segment.reference.filename : segment.reference.tableName || segment.reference.sheet)
    const explicit = isExplicitReference(segment.text)
    if (!explicit) {
      const containing = quoted.find((span) => span.start < start && span.end >= offset)
      if (containing) {
        // A partial name inside an existing string is text, not another reference.
        return start === containing.start + 1 && offset === containing.end ? quotedName.slice(1, -1) : segment.text
      }
    }
    // Preserve a user's existing pair of quotes rather than nesting another pair.
    if (text[start - 1] === '"' && text[offset] === '"' && !isEscaped(text, start - 1)) {
      return quotedName.slice(1, -1)
    }
    return quotedName
  }).join('')
}

function quotedSpans(text: string): { start: number; end: number }[] {
  const spans: { start: number; end: number }[] = []
  let start = -1
  for (let index = 0; index < text.length; index++) {
    if (text[index] !== '"' || isEscaped(text, index)) continue
    if (start < 0) start = index
    else {
      spans.push({ start, end: index })
      start = -1
    }
  }
  return spans
}

function isEscaped(text: string, index: number): boolean {
  let slashes = 0
  for (let cursor = index - 1; cursor >= 0 && text[cursor] === '\\'; cursor--) slashes++
  return slashes % 2 === 1
}

function projectMessage(message: string, available: TableReference[], availableFiles: FileReference[]) {
  let text = ''
  const ranges: TableReferenceRange[] = []
  const encoded: { range: TableReferenceRange; source: string }[] = []
  for (const segment of parseTableReferences(message, available, availableFiles)) {
    if (segment.type === 'text') {
      text += segment.text
      continue
    }
    const explicit = isExplicitReference(segment.text)
    const visible = explicit ? (segment.type === 'file' ? segment.reference.filename : segment.reference.sheet) : segment.text
    const base = { start: text.length, end: text.length + visible.length, explicit }
    const range: TableReferenceRange = segment.type === 'file'
      ? { ...base, type: 'file', reference: segment.reference }
      : { ...base, type: 'table', reference: segment.reference }
    ranges.push(range)
    if (explicit) encoded.push({ range, source: segment.text })
    text += visible
  }
  return { text, ranges, encoded }
}

/** Show only the worksheet name while keeping its identity in the draft value. */
export function projectTableReferences(message: string, available: TableReference[] = [], availableFiles: FileReference[] = []) {
  const { text, ranges } = projectMessage(message, available, availableFiles)
  return { text, ranges }
}

/** Apply an edit in visible textarea coordinates without damaging other references. */
export function updateTableReferenceText(
  message: string,
  nextText: string,
  available: TableReference[] = [],
  edit?: { start: number; end: number },
  availableFiles: FileReference[] = [],
): string {
  const previous = projectMessage(message, available, availableFiles)
  if (previous.text === nextText) return message
  let start = edit?.start ?? -1
  let end = edit?.end ?? -1
  const insertedLength = nextText.length - previous.text.length + end - start
  const validHint = Number.isInteger(start) && Number.isInteger(end) && start >= 0 && end >= start &&
    end <= previous.text.length && insertedLength >= 0 &&
    previous.text.slice(0, start) === nextText.slice(0, start) &&
    previous.text.slice(end) === nextText.slice(start + insertedLength)
  if (!validHint) {
    start = 0
    while (start < previous.text.length && start < nextText.length && previous.text[start] === nextText[start]) start++
    let suffix = 0
    while (previous.text.length - suffix > start && nextText.length - suffix > start &&
           previous.text[previous.text.length - suffix - 1] === nextText[nextText.length - suffix - 1]) suffix++
    end = previous.text.length - suffix
  }
  const delta = nextText.length - previous.text.length
  let result = ''
  let offset = 0
  for (const { range, source } of previous.encoded) {
    let nextStart: number
    let nextEnd: number
    if (range.end <= start) {
      nextStart = range.start
      nextEnd = range.end
    } else if (range.start >= end) {
      nextStart = range.start + delta
      nextEnd = range.end + delta
    } else {
      // An edit inside a table name turns the touched label into ordinary text.
      continue
    }
    result += nextText.slice(offset, nextStart) + source
    offset = nextEnd
  }
  return result + nextText.slice(offset)
}
