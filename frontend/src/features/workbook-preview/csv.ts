import type { ICellData, IWorkbookData } from '@univerjs/core'

export interface ParsedCsvWorkbook {
  workbook: IWorkbookData
  warnings: string[]
  stats: { sheetCount: number; cellCount: number; formulaCount: number }
}

const MAX_BYTES = 20 * 1024 * 1024
const MAX_ROWS = 100_000
const MAX_COLUMNS = 16_384
const MAX_CELLS = 250_000
const MAX_FIELD_LENGTH = 32_767

function decodeCsv(buffer: ArrayBuffer): string {
  if (buffer.byteLength > MAX_BYTES) throw new Error('CSV 文件超过 20 MB，暂无法预览，请下载查看。')
  const bytes = new Uint8Array(buffer)
  if (bytes[0] === 0xff && bytes[1] === 0xfe) return new TextDecoder('utf-16le', { fatal: true }).decode(bytes)
  if (bytes[0] === 0xfe && bytes[1] === 0xff) return new TextDecoder('utf-16be', { fatal: true }).decode(bytes)
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes)
  } catch {
    try {
      return new TextDecoder('gb18030', { fatal: true }).decode(bytes)
    } catch {
      throw new Error('无法识别 CSV 文件的文字编码，请另存为 UTF-8 CSV 后重试。')
    }
  }
}

/** Inspect separators only outside quoted fields, including multiline fields. */
function detectDelimiter(text: string): string {
  const candidates = [',', '\t', ';']
  const counts = candidates.map(() => [0])
  let row = 0
  let quoted = false
  let fieldStart = true
  for (let index = 0; index < Math.min(text.length, 65_536) && row < 20; index += 1) {
    const char = text[index]!
    if (quoted) {
      if (char === '"') {
        if (text[index + 1] === '"') index += 1
        else quoted = false
      }
      continue
    }
    if (char === '"' && fieldStart) {
      quoted = true
      fieldStart = false
      continue
    }
    if (char === '\r' || char === '\n') {
      if (char === '\r' && text[index + 1] === '\n') index += 1
      row += 1
      counts.forEach((item) => item.push(0))
      fieldStart = true
      continue
    }
    const candidate = candidates.indexOf(char)
    if (candidate !== -1) {
      counts[candidate]![row]! += 1
      fieldStart = true
    } else {
      fieldStart = false
    }
  }
  let best = 0
  let bestScore = 0
  counts.forEach((item, index) => {
    const frequencies = new Map<number, number>()
    item.forEach((count) => {
      if (count > 0) frequencies.set(count, (frequencies.get(count) ?? 0) + 1)
    })
    let score = 0
    frequencies.forEach((frequency, count) => {
      // Prefer a consistent shape, then the separator producing the wider table.
      score = Math.max(score, frequency * 100_000 + count)
    })
    if (score > bestScore) {
      best = index
      bestScore = score
    }
  })
  return candidates[best]!
}

function parseRows(text: string, delimiter: string): string[][] {
  const rows: string[][] = []
  let current: string[] = []
  let field = ''
  let quoted = false
  let closedQuote = false
  let cellCount = 0

  function finishField() {
    if (field.length > MAX_FIELD_LENGTH) throw new Error('CSV 中有字段超过 32,767 个字符，暂无法预览，请下载查看。')
    cellCount += 1
    if (cellCount > MAX_CELLS) throw new Error('CSV 超过 250,000 个单元格，暂无法预览，请下载查看。')
    current.push(field)
    if (current.length > MAX_COLUMNS) throw new Error('CSV 列数过多，暂无法预览，请下载查看。')
    field = ''
    closedQuote = false
  }
  function finishRow() {
    finishField()
    rows.push(current)
    if (rows.length > MAX_ROWS) throw new Error('CSV 行数过多，暂无法预览，请下载查看。')
    current = []
  }

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index]!
    if (quoted) {
      if (char === '"') {
        if (text[index + 1] === '"') {
          field += '"'
          index += 1
        } else {
          quoted = false
          closedQuote = true
        }
      } else {
        field += char
      }
    } else if (char === delimiter) {
      finishField()
    } else if (char === '\r' || char === '\n') {
      finishRow()
      if (char === '\r' && text[index + 1] === '\n') index += 1
    } else if (char === '"' && field === '' && !closedQuote) {
      quoted = true
    } else {
      if (closedQuote) throw new Error('CSV 的引号或分隔符格式不正确，暂无法预览，请下载查看。')
      field += char
    }
    if (field.length > MAX_FIELD_LENGTH) throw new Error('CSV 中有字段超过 32,767 个字符，暂无法预览，请下载查看。')
  }
  if (quoted) throw new Error('CSV 中有未闭合的引号，暂无法预览，请下载查看。')
  if (field !== '' || current.length > 0 || closedQuote || !/[\r\n]$/.test(text)) finishRow()
  return rows
}

/** CSV has no cell formats; keep every field as text to preserve IDs and formula-like input. */
export function parseCsvWorkbook(buffer: ArrayBuffer, filename: string): ParsedCsvWorkbook {
  let text: string
  try {
    text = decodeCsv(buffer).replace(/^\uFEFF/, '')
  } catch (error) {
    if (error instanceof TypeError) throw new Error('CSV 文件的文字编码不完整，请另存为 UTF-8 CSV 后重试。')
    throw error
  }
  if (text.length === 0) throw new Error('CSV 文件没有内容。')
  const rows = parseRows(text, detectDelimiter(text))
  const cellData: Record<number, Record<number, ICellData>> = {}
  let columnCount = 0
  let cellCount = 0
  rows.forEach((row, rowIndex) => {
    columnCount = Math.max(columnCount, row.length)
    row.forEach((value, columnIndex) => {
      if (value === '') return
      const cells = cellData[rowIndex] ?? (cellData[rowIndex] = {})
      cells[columnIndex] = { v: value, t: 1 }
      cellCount += 1
    })
  })
  const sheetId = 'csv-sheet-1'
  return {
    workbook: {
      id: `csv-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
      name: filename,
      appVersion: '1.3.1',
      locale: 'zhCN' as IWorkbookData['locale'],
      styles: {},
      sheetOrder: [sheetId],
      sheets: {
        [sheetId]: {
          id: sheetId,
          name: filename.replace(/\.csv$/i, '') || 'CSV',
          rowCount: Math.max(40, rows.length),
          columnCount: Math.max(10, columnCount),
          cellData,
          defaultColumnWidth: 120,
          defaultRowHeight: 24,
          showGridlines: 1,
        },
      },
    },
    warnings: ['CSV 文件不包含单元格样式，按原始文本内容展示。'],
    stats: { sheetCount: 1, cellCount, formulaCount: 0 },
  }
}
