import { parseCsvWorkbook } from './csv'
import { openWorkbook } from './excel'
import type { ParsedWorkbook, WorkbookSession } from './excel'

// Parsing runs outside the UI thread. No file content leaves the browser.
// 一个 Worker 对应一个打开的文件：先 open 得到工作表目录，再按需 sheet 读取某一张。
type Request =
  | { id: number; type: 'open'; buffer: ArrayBuffer; filename: string }
  | { id: number; type: 'sheet'; index: number }

let session: WorkbookSession | null = null
const parsed = new Map<number, ParsedWorkbook>()      // 最近读过的几张表：来回切换时不用重新解析
const KEEP = 4

function csvSession(buffer: ArrayBuffer, filename: string): WorkbookSession {
  const csv = parseCsvWorkbook(buffer, filename)
  return {
    sheets: csv.workbook.sheetOrder.map((id, index) => ({ index, name: csv.workbook.sheets[id]?.name ?? filename, hidden: 0 as const })),
    active: 0,
    warnings: csv.warnings,
    readSheet: async () => csv,
  }
}

self.onmessage = async (event: MessageEvent<Request>) => {
  const message = event.data
  try {
    if (message.type === 'open') {
      session = /\.csv$/i.test(message.filename) ? csvSession(message.buffer, message.filename) : await openWorkbook(message.buffer, message.filename)
      parsed.clear()
      self.postMessage({ id: message.id, ok: true, opened: { sheets: session.sheets, active: session.active, warnings: session.warnings } })
    } else {
      if (!session) throw new Error('文件还没有打开。')
      let result = parsed.get(message.index)
      if (!result) {
        result = await session.readSheet(message.index)
        parsed.set(message.index, result)
        if (parsed.size > KEEP) parsed.delete(parsed.keys().next().value!)
      }
      self.postMessage({ id: message.id, ok: true, result })
    }
  } catch (error) {
    self.postMessage({ id: message.id, ok: false, error: error instanceof Error ? error.message : '文件解析失败' })
  }
}
