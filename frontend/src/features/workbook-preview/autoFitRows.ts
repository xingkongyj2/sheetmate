/**
 * 表格预览 / 比对的行高自适应工具：
 * 1. 等待 Univer 进入 Rendered 生命周期（AutoHeightController 就绪后首张表才能正确计算行高）；
 * 2. 对所有有内容的可见行执行 Univer 原生文本测量并自动展开行高；
 * 3. 保留原本在 Excel 中特意拉高的行（如大标题栏），只撑高、不压矮。
 */
import type { createUniver, ICellData, IRange, IWorkbookData } from '@univerjs/presets'

type UniverFacade = ReturnType<typeof createUniver>['univerAPI']
type FacadeWorkbook = ReturnType<UniverFacade['createWorkbook']>

export interface RenderedGate {
  wait(): Promise<void>
  dispose(): void
}

/** 跟踪 Univer 实例是否已进入 Rendered 阶段；首张表等待一次（约 300ms），切表时立即返回。 */
export function createRenderedGate(univerAPI: UniverFacade): RenderedGate {
  let rendered = false
  let disposed = false
  let resolveReady: (() => void) | null = null
  const promise = new Promise<void>((resolve) => {
    resolveReady = resolve
  })

  const finish = () => {
    rendered = true
    clearTimeout(fallbackTimer)
    resolveReady?.()
    resolveReady = null
  }

  const sub = univerAPI.addEvent(univerAPI.Event.LifeCycleChanged, ({ stage }) => {
    if (stage >= univerAPI.Enum.LifecycleStages.Rendered) finish()
  })
  // 兜底超时，避免极端情况下阻塞表格打开
  const fallbackTimer = setTimeout(finish, 1500)

  return {
    wait() {
      if (rendered || disposed) return Promise.resolve()
      return promise
    },
    dispose() {
      disposed = true
      clearTimeout(fallbackTimer)
      sub.dispose()
      resolveReady?.()
      resolveReady = null
    },
  }
}

/** 将有序行号合并成最少的连续范围，减少 Univer 命令调用次数。 */
function toContiguousRanges(rows: number[], lastColumn: number): IRange[] {
  const ranges: IRange[] = []
  let start = rows[0]!
  let end = start
  for (let i = 1; i < rows.length; i++) {
    const row = rows[i]!
    if (row === end + 1) {
      end = row
    } else {
      ranges.push({ startRow: start, endRow: end, startColumn: 0, endColumn: lastColumn })
      start = end = row
    }
  }
  ranges.push({ startRow: start, endRow: end, startColumn: 0, endColumn: lastColumn })
  return ranges
}

/** 检查快照中是否存在未缓存结果的公式单元格（需由 Univer 公式引擎实时计算）。 */
export function hasPendingFormulas(snapshot: IWorkbookData): boolean {
  for (const sheet of Object.values(snapshot.sheets)) {
    const cellData = sheet?.cellData
    if (!cellData) continue
    for (const rowCells of Object.values(cellData)) {
      if (!rowCells) continue
      for (const cell of Object.values(rowCells as Record<string, ICellData | null | undefined>)) {
        if (cell && typeof cell.f === 'string' && cell.f.length > 0 && (cell.v === undefined || cell.v === null)) {
          return true
        }
      }
    }
  }
  return false
}

/**
 * 订阅一次公式计算完成事件：在 createWorkbook 之前调用，避免错过快速计算；
 * 若快照不含未缓存公式，则立即返回已完成状态，零额外等待。
 */
export function prepareFormulaWaiter(univerAPI: UniverFacade, snapshot: IWorkbookData): { wait(): Promise<void>; dispose(): void } {
  if (!hasPendingFormulas(snapshot)) {
    return { wait: () => Promise.resolve(), dispose: () => undefined }
  }
  const formulaEngine = (univerAPI as unknown as { getFormula?: () => { calculationEnd?: (cb: () => void) => { dispose(): void } } }).getFormula?.()
  if (!formulaEngine?.calculationEnd) {
    return { wait: () => Promise.resolve(), dispose: () => undefined }
  }
  let done = false
  let resolveWait!: () => void
  const promise = new Promise<void>((resolve) => {
    resolveWait = resolve
  })
  const finish = () => {
    if (done) return
    done = true
    clearTimeout(timer)
    sub.dispose()
    resolveWait()
  }
  const sub = formulaEngine.calculationEnd(() => finish())
  const timer = setTimeout(finish, 1500)
  return {
    wait: () => promise,
    dispose: finish,
  }
}

/** 对当前活动工作表中有内容的可见行按内容自适应行高，且不低于原始行高。 */
export function autoFitSheetRows(workbook: FacadeWorkbook, snapshot: IWorkbookData): void {
  const sheet = workbook.getActiveSheet()
  if (!sheet) return
  const sheetSnap = snapshot.sheets[sheet.getSheetId()] ?? Object.values(snapshot.sheets)[0]
  const cellData = sheetSnap?.cellData
  if (!cellData) return

  const rowData = sheetSnap.rowData ?? {}
  const defaultRowHeight = sheetSnap.defaultRowHeight ?? 20
  const contentRows: number[] = []
  const minHeights = new Map<number, number>()

  for (const [key, rowCells] of Object.entries(cellData)) {
    const row = Number(key)
    if (!Number.isInteger(row) || row < 0 || !rowCells) continue
    if (rowData[row]?.hd === 1) continue
    const cells = Object.values(rowCells as Record<string, ICellData | null | undefined>)
    const hasContent = cells.some(
      (cell) => cell && ((cell.v !== undefined && cell.v !== null && cell.v !== '') || Boolean(cell.p) || Boolean(cell.f)),
    )
    if (!hasContent) continue
    contentRows.push(row)
    const explicit = rowData[row]?.h
    minHeights.set(row, Math.max(defaultRowHeight, explicit ?? defaultRowHeight))
  }

  if (!contentRows.length) return
  contentRows.sort((a, b) => a - b)

  const lastColumn = Math.max(0, sheet.getMaxColumns() - 1)
  const ranges = toContiguousRanges(contentRows, lastColumn)
  sheet.setRangesAutoHeight(ranges)

  // 保护原本显式设置了更高行高的行（例如合并的大标题行），确保只展开、不压矮
  let runStart = -1
  let runCount = 0
  let runHeight = 0
  const flushPreserve = () => {
    if (runCount > 0) {
      sheet.setRowHeightsForced(runStart, runCount, runHeight)
      runStart = -1
      runCount = 0
    }
  }
  for (const row of contentRows) {
    const minH = minHeights.get(row)!
    const currentH = sheet.getRowHeight(row)
    if (currentH < minH - 0.5) {
      if (runCount > 0 && row === runStart + runCount && Math.abs(minH - runHeight) < 0.5) {
        runCount += 1
      } else {
        flushPreserve()
        runStart = row
        runCount = 1
        runHeight = minH
      }
    } else {
      flushPreserve()
    }
  }
  flushPreserve()
}
