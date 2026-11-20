import { parseCsvWorkbook } from '../workbook-preview/csv'
import { openWorkbook } from '../workbook-preview/excel'
import type { ParsedWorkbook, SheetMeta, WorkbookSession } from '../workbook-preview/excel'
import { computeSheetDiffSummary, diffSheetSnapshots } from './diff'
import type { AlignMode, DiffOptions } from './types'

type Side = 'left' | 'right'

type Request =
  | { id: number; type: 'open'; side: Side; buffer: ArrayBuffer; filename: string }
  | { id: number; type: 'clear'; side: Side }
  | { id: number; type: 'swap' }
  | { id: number; type: 'loadSingle'; side: Side; index: number }
  | { id: number; type: 'diffSheet'; leftIndex: number | null; rightIndex: number | null; options: DiffOptions; sheetTitle: string }
  | { id: number; type: 'diffSummary'; leftIndex: number | null; rightIndex: number | null; alignMode: AlignMode }

interface SideState {
  session: WorkbookSession | null
  cache: Map<number, ParsedWorkbook>
}

const KEEP = 6

let sides: Record<Side, SideState> = {
  left: { session: null, cache: new Map() },
  right: { session: null, cache: new Map() },
}

function csvSession(buffer: ArrayBuffer, filename: string): WorkbookSession {
  const csv = parseCsvWorkbook(buffer, filename)
  return {
    sheets: csv.workbook.sheetOrder.map((id, index): SheetMeta => ({
      index,
      name: csv.workbook.sheets[id]?.name ?? filename,
      hidden: 0,
    })),
    active: 0,
    warnings: csv.warnings,
    readSheet: async () => csv,
  }
}

async function readCachedSheet(side: Side, index: number | null): Promise<ParsedWorkbook | null> {
  if (index === null) return null
  const state = sides[side]
  if (!state.session) throw new Error(side === 'left' ? '左侧基准文件尚未加载。' : '右侧目标文件尚未加载。')
  let parsed = state.cache.get(index)
  if (parsed) {
    // 刷新 LRU 顺序
    state.cache.delete(index)
    state.cache.set(index, parsed)
    return parsed
  }
  parsed = await state.session.readSheet(index)
  state.cache.set(index, parsed)
  if (state.cache.size > KEEP) {
    const oldest = state.cache.keys().next().value
    if (oldest !== undefined) state.cache.delete(oldest)
  }
  return parsed
}

self.onmessage = async (event: MessageEvent<Request>) => {
  const message = event.data
  try {
    if (message.type === 'open') {
      const session = /\.csv$/i.test(message.filename)
        ? csvSession(message.buffer, message.filename)
        : await openWorkbook(message.buffer, message.filename)
      sides[message.side] = { session, cache: new Map() }
      self.postMessage({
        id: message.id,
        ok: true,
        opened: { sheets: session.sheets, active: session.active, warnings: session.warnings },
      })
    } else if (message.type === 'clear') {
      sides[message.side] = { session: null, cache: new Map() }
      self.postMessage({ id: message.id, ok: true })
    } else if (message.type === 'swap') {
      const prevLeft = sides.left
      sides = { left: sides.right, right: prevLeft }
      self.postMessage({ id: message.id, ok: true })
    } else if (message.type === 'loadSingle') {
      const parsed = await readCachedSheet(message.side, message.index)
      if (!parsed) throw new Error('没有找到对应的工作表。')
      self.postMessage({ id: message.id, ok: true, parsed })
    } else if (message.type === 'diffSheet') {
      const [leftParsed, rightParsed] = await Promise.all([
        readCachedSheet('left', message.leftIndex),
        readCachedSheet('right', message.rightIndex),
      ])
      const result = diffSheetSnapshots(
        leftParsed?.workbook ?? null,
        rightParsed?.workbook ?? null,
        message.options,
        message.sheetTitle,
      )
      self.postMessage({
        id: message.id,
        ok: true,
        result,
        leftWarnings: leftParsed?.warnings ?? [],
        rightWarnings: rightParsed?.warnings ?? [],
      })
    } else if (message.type === 'diffSummary') {
      const [leftParsed, rightParsed] = await Promise.all([
        readCachedSheet('left', message.leftIndex),
        readCachedSheet('right', message.rightIndex),
      ])
      const stats = computeSheetDiffSummary(
        leftParsed?.workbook ?? null,
        rightParsed?.workbook ?? null,
        message.alignMode,
      )
      self.postMessage({ id: message.id, ok: true, stats })
    }
  } catch (error) {
    self.postMessage({
      id: message.id,
      ok: false,
      error: error instanceof Error ? error.message : '表格解析或比对失败',
    })
  }
}
