export type ReferenceFileKind = 'input' | 'expected' | 'output'

interface ReferenceSource {
  sourceId?: string
  kind?: ReferenceFileKind
}

/** A selected worksheet keeps its file identity when sent to the assistant. */
export interface TableReference extends ReferenceSource {
  filename: string
  sheet: string
  tableName?: string
}

export interface FileReference extends ReferenceSource {
  filename: string
  resolvedFilename?: string
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

export function referenceSourceKey(reference: Pick<ComposerReference, 'filename' | 'sourceId' | 'kind'>): string {
  return JSON.stringify(reference.sourceId ? ['id', reference.sourceId] : ['name', reference.kind ?? '', reference.filename])
}

function sourceMetadata(reference: ReferenceSource): ReferenceSource {
  return {
    ...(typeof reference.sourceId === 'string' && reference.sourceId ? { sourceId: reference.sourceId } : {}),
    ...(reference.kind === 'input' || reference.kind === 'expected' || reference.kind === 'output' ? { kind: reference.kind } : {}),
  }
}

function formatSource(reference: ReferenceSource): string {
  const metadata = sourceMetadata(reference)
  return Object.keys(metadata).length ? ` / ${escapeName(JSON.stringify(metadata))}` : ''
}

export function formatTableReference(reference: TableReference): string {
  return `【表格：${escapeName(reference.filename)} / ${escapeName(reference.sheet)}${formatSource(reference)}】`
}

export function formatFileReference(reference: FileReference): string {
  return `【文件：${escapeName(reference.filename)}${formatSource(reference)}】`
}

function unescapeName(value: string): string {
  return value.replace(/\\([\\/【】])/g, '$1')
}

function isExplicitReference(text: string): boolean {
  return text.startsWith('【表格：') || text.startsWith('【文件：') || text.startsWith('<table-ref>')
}

function referenceParts(body: string): string[] {
  const parts: string[] = []
  let offset = 0
  for (let index = 0; index < body.length; index++) {
    if (body[index] === '\\') {
      index++
      continue
    }
    if (body.slice(index, index + 3) !== ' / ') continue
    parts.push(unescapeName(body.slice(offset, index)))
    offset = index + 3
    index += 2
  }
  parts.push(unescapeName(body.slice(offset)))
  return parts
}

function readSource(json: string): ReferenceSource | null {
  try {
    const value: unknown = JSON.parse(json)
    if (!value || typeof value !== 'object') return null
    const candidate = value as Record<string, unknown>
    if (candidate.sourceId !== undefined && (typeof candidate.sourceId !== 'string' || !candidate.sourceId)) return null
    if (candidate.kind !== undefined && candidate.kind !== 'input' && candidate.kind !== 'expected' && candidate.kind !== 'output') return null
    const source = sourceMetadata(candidate as ReferenceSource)
    return Object.keys(source).length ? source : null
  } catch {
    return null
  }
}

function readHumanReference(body: string): TableReference | null {
  const parts = referenceParts(body)
  if (parts.length !== 2 && parts.length !== 3) return null
  const [filename, sheet, metadata] = parts
  const source = metadata === undefined ? {} : readSource(metadata)
  return filename && sheet && source ? { filename, sheet, ...source } : null
}

function readFileReference(body: string): FileReference | null {
  const parts = referenceParts(body)
  if (parts.length !== 1 && parts.length !== 2) return null
  const [filename, metadata] = parts
  const source = metadata === undefined ? {} : readSource(metadata)
  return filename && source ? { filename, ...source } : null
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
      ...sourceMetadata(candidate as ReferenceSource),
      ...(typeof candidate.tableName === 'string' && candidate.tableName ? { tableName: candidate.tableName } : {}),
    }
  } catch {
    return null
  }
}

function matchesSource(reference: ComposerReference, candidate: ComposerReference): boolean {
  if (reference.kind && candidate.kind && reference.kind !== candidate.kind) return false
  if (reference.sourceId) return candidate.sourceId === reference.sourceId
  if (reference.kind && candidate.kind !== reference.kind) return false
  return candidate.filename === reference.filename
}

function resolveReference(reference: TableReference, available: TableReference[]): TableReference {
  let matches = available.filter((item) => matchesSource(reference, item) && item.sheet === reference.sheet)
  if (reference.tableName) {
    const exact = matches.filter((item) => item.tableName === reference.tableName)
    if (exact.length) matches = exact
  }
  const sources = new Set(matches.map(referenceSourceKey))
  if (sources.size !== 1) return reference
  const names = new Set(matches.map((item) => item.tableName).filter((name): name is string => !!name))
  const tableName = names.size === 1 ? [...names][0] : reference.tableName
  return {
    filename: reference.filename,
    sheet: reference.sheet,
    ...sourceMetadata(matches[0]!),
    ...sourceMetadata(reference),
    ...(tableName ? { tableName } : {}),
  }
}

function availableFileReferences(available: TableReference[], availableFiles: FileReference[]): FileReference[] {
  const files = new Map<string, FileReference>()
  for (const reference of [...available.map(({ filename, sourceId, kind }) => ({ filename, ...sourceMetadata({ sourceId, kind }) })), ...availableFiles]) {
    const key = referenceSourceKey(reference)
    files.set(key, { ...files.get(key), ...reference })
  }
  const candidates = [...files.values()]
  return candidates.filter((file) => file.sourceId || !candidates.some((other) => other !== file && other.filename === file.filename &&
    (!file.kind || file.kind === other.kind) && (other.sourceId || (!file.kind && other.kind))))
}

function resolveFileReference(reference: FileReference, available: FileReference[]): FileReference {
  const matches = available.filter((item) => matchesSource(reference, item))
  if (new Set(matches.map(referenceSourceKey)).size !== 1) return reference
  const match = matches[0]!
  const resolvedFilename = match.resolvedFilename || (match.filename !== reference.filename ? match.filename : undefined)
  return {
    filename: reference.filename,
    ...sourceMetadata(match),
    ...sourceMetadata(reference),
    ...(resolvedFilename ? { resolvedFilename } : {}),
  }
}

function availableAliases(available: TableReference[], availableFiles: FileReference[]): Map<string, ComposerReference> {
  const candidates = new Map<string, Map<string, ComposerReference>>()
  const files = availableFileReferences(available, availableFiles)
  for (const reference of [...available, ...files]) {
    const aliases = isFileReference(reference) ? [reference.filename] : [reference.tableName, reference.sheet]
    for (const alias of new Set(aliases)) {
      if (!alias || alias.length < 2) continue
      const matches = candidates.get(alias) ?? new Map<string, ComposerReference>()
      matches.set(JSON.stringify([isFileReference(reference) ? 'file' : 'table', referenceSourceKey(reference), reference.sheet, reference.tableName]), reference)
      candidates.set(alias, matches)
    }
  }
  return new Map([...candidates].flatMap(([alias, matches]) => matches.size === 1 ? [[alias, [...matches.values()][0]!] as const] : []))
}

/** Explicit mentions and unambiguous, delimited workspace table names. */
export function parseTableReferences(text: string, available: TableReference[] = [], availableFiles: FileReference[] = []): TableReferenceSegment[] {
  const segments: TableReferenceSegment[] = []
  const files = availableFileReferences(available, availableFiles)
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
      if (isFileReference(reference)) segments.push({ type: 'file', text: match[0], reference: resolveFileReference(reference, files) })
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
      const reference = readFileReference(match[3])
      if (reference) segments.push({ type: 'file', text: match[0], reference: resolveFileReference(reference, files) })
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
    const quotedName = JSON.stringify(segment.type === 'file' ? segment.reference.resolvedFilename || segment.reference.filename : segment.reference.tableName || segment.reference.sheet)
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
