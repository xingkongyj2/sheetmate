import type { ICellData, IColumnData, IRange, IRowData, IStyleData, IWorkbookData, IWorksheetData } from '@univerjs/core'
import type { Cell, CellValue, Row, Workbook, Worksheet } from 'exceljs'

import { colorToHex, columnWidthToPixels, convertStyle, readThemeColors } from './style.ts'
import { PREVIEW_LIMITS, WorkbookPreviewError } from './types.ts'
import type { ParsedWorkbook } from './types.ts'

// ExcelJS 4.4.0's model getters rebuild every cell model (Workbook.model even
// does this twice). Read only its stored sparse collections instead: the public
// includeEmpty iterators allocate missing cells and eachCell omits styled blanks.
// Keep these version-specific fields in this adapter and cover them with real
// ExcelJS workbooks in workbookSnapshot.test.mjs.
type PreviewWorksheet = Worksheet & {
  _rows: Array<Row | undefined>
  _merges: Record<string, { range: string }>
  conditionalFormattings?: unknown[]
}
type PreviewRow = Row & { style: Cell['style']; _cells: Array<Cell | undefined> }

function tooLarge(message: string): never {
  throw new WorkbookPreviewError(`${message}，请下载原始文件查看。`)
}

function decodeAddress(address: string): { row: number; column: number } | undefined {
  const match = /^\$?([A-Z]+)\$?(\d+)$/i.exec(address)
  if (!match) return undefined
  let column = 0
  for (const letter of match[1]!.toUpperCase()) column = column * 26 + letter.charCodeAt(0) - 64
  return { row: Number(match[2]), column }
}

function convertRange(address: string): IRange | undefined {
  const [startAddress, endAddress = startAddress] = address.split(':')
  const start = decodeAddress(startAddress!), end = decodeAddress(endAddress!)
  if (!start || !end) return undefined
  return { startRow: start.row - 1, startColumn: start.column - 1, endRow: end.row - 1, endColumn: end.column - 1 }
}

function storedValue(value: CellValue | undefined, date1904: boolean, warnings: Set<string>): Pick<ICellData, 'v' | 't'> {
  if (value === null || value === undefined) return {}
  if (typeof value === 'number') return Number.isFinite(value) ? { v: value, t: 2 } : { v: String(value), t: 1 }
  if (typeof value === 'boolean') return { v: value, t: 3 }
  if (typeof value === 'string') return { v: value, t: 1 }
  if (value instanceof Date) return { v: 25569 + value.getTime() / 86_400_000 - (date1904 ? 1462 : 0), t: 2 }
  if ('richText' in value) {
    warnings.add('部分单元格含有富文本，预览使用整格字体显示。')
    return { v: value.richText.map((part) => part.text).join(''), t: 1 }
  }
  if ('error' in value) return { v: value.error, t: 1 }
  if ('text' in value) return { v: value.text, t: 1 }
  return {}
}

function cellValue(cell: Cell, date1904: boolean, warnings: Set<string>, formulaStats: { count: number; missing: number; examples: string[] }): Pick<ICellData, 'v' | 't' | 'custom'> {
  // ExcelJS exposes a merged slave's master's value; keep that value only once.
  if (cell.isMerged && cell.master.address !== cell.address) return {}
  const value = cell.value
  if (value && typeof value === 'object' && !(value instanceof Date) && ('formula' in value || 'sharedFormula' in value)) {
    formulaStats.count++
    const formula = cell.formula || ('formula' in value ? value.formula : '')
    const custom = formula ? { excelFormula: `=${formula.replace(/^=/, '')}` } : undefined
    // ExcelJS 4.4's value getter omits falsy formula results (0, false, '').
    // Its public cell model retains the exact cached value.
    const result = cell.model.result as CellValue | undefined
    if (result === undefined || result === null) {
      formulaStats.missing++
      if (formulaStats.examples.length < 3) formulaStats.examples.push(`${cell.worksheet.name}!${cell.address}`)
      return { v: '（公式结果未缓存）', t: 1, custom }
    }
    // Do not emit f: a read-only preview must show the saved result rather than
    // recalculate it with a potentially different formula engine.
    return { ...storedValue(result, date1904, warnings), custom }
  }
  return storedValue(value, date1904, warnings)
}

/**
 * Map a loaded ExcelJS workbook into a sparse, read-only Univer snapshot.
 * only：只转换这一张表（按表加载时，其余工作表是空壳）；快照里只有这一张表，并且一定显示出来。
 */
export function buildWorkbookSnapshot(source: Workbook, filename: string, options: { only?: Worksheet } = {}): ParsedWorkbook {
  const worksheets = options.only ? [options.only] : source.worksheets
  if (!worksheets.length) throw new WorkbookPreviewError('这个文件没有可预览的工作表。')
  if (worksheets.length > PREVIEW_LIMITS.sheets) tooLarge(`工作表超过 ${PREVIEW_LIMITS.sheets} 张的预览范围`)
  const warnings = new Set<string>()
  const theme = readThemeColors((source as Workbook & { _themes?: unknown })._themes)
  const date1904 = !!source.properties.date1904
  const workbook: IWorkbookData = {
    id: `preview-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    name: filename,
    appVersion: '1.3.1',
    locale: 'zhCN' as IWorkbookData['locale'],
    dateSystem: (date1904 ? 'date1904' : 'date1900') as IWorkbookData['dateSystem'],
    defaultStyle: { ff: 'Arial', fs: 11, vt: 3 },
    styles: {},
    sheetOrder: [],
    sheets: {},
  }
  const styleIds = new Map<string, string>()
  const sourceStyles = new WeakMap<object, string | undefined>()
  function registerStyle(style: IStyleData): string {
    const key = JSON.stringify(style)
    let id = styleIds.get(key)
    if (!id) {
      id = `s${styleIds.size}`
      styleIds.set(key, id)
      workbook.styles[id] = style
    }
    return id
  }
  function styleId(sourceStyle: Cell['style']): string | undefined {
    if (sourceStyles.has(sourceStyle)) return sourceStyles.get(sourceStyle)
    const style: IStyleData = convertStyle(sourceStyle, theme, warnings)
    if (!Object.keys(style).length) {
      sourceStyles.set(sourceStyle, undefined)
      return undefined
    }
    const id = registerStyle(style)
    sourceStyles.set(sourceStyle, id)
    return id
  }
  let cellCount = 0
  const formulas = { count: 0, missing: 0, examples: [] as string[] }
  for (const worksheet of worksheets) {
    const id = `sheet-${worksheet.id}`
    const storedSheet = worksheet as PreviewWorksheet
    const cellData: IWorksheetData['cellData'] = {}
    const rowData: IWorksheetData['rowData'] = {}
    const columnData: IWorksheetData['columnData'] = {}
    let maxRow = 0, maxColumn = 0, omittedFormatting = false
    const mergeData = Object.values(storedSheet._merges).map((range) => convertRange(range.range)).filter((range): range is IRange => !!range)
    for (const range of mergeData) {
      if (range.endRow >= PREVIEW_LIMITS.rows || range.endColumn >= PREVIEW_LIMITS.columns) tooLarge(`「${worksheet.name}」合并区域超出预览范围`)
      maxRow = Math.max(maxRow, range.endRow + 1)
      maxColumn = Math.max(maxColumn, range.endColumn + 1)
    }

    // Object.values visits stored entries without copying a million-slot sparse
    // row range or walking all missing cells up to the last formatted column.
    for (const row of Object.values(storedSheet._rows)) {
      if (!row) continue
      const rowInRange = row.number <= PREVIEW_LIMITS.rows
      const storedRow = row as PreviewRow
      const rowStyle = styleId(storedRow.style)
      const rowInfo: Partial<IRowData> = {}
      if (row.height !== undefined) rowInfo.h = Math.max(1, row.height * 4 / 3)
      if (row.hidden) rowInfo.hd = 1
      if (rowStyle) rowInfo.s = rowStyle
      if (Object.keys(rowInfo).length) {
        if (rowInRange) {
          rowData[row.number - 1] = rowInfo
          maxRow = Math.max(maxRow, row.number)
        } else omittedFormatting = true
      }
      for (const cell of Object.values(storedRow._cells)) {
        if (!cell) continue
        // ExcelJS 4.4's declaration says string, but its getter returns number.
        const column = Number(cell.col)
        if (!rowInRange || column > PREVIEW_LIMITS.columns) {
          // Formatting at the bottom/right edge is common in otherwise small
          // Excel files. Omit only blank formatting; never silently omit data.
          if (cell.value !== null && cell.value !== undefined && !(cell.isMerged && cell.master.address !== cell.address)) {
            if (!rowInRange) tooLarge(`「${worksheet.name}」行数超过 ${PREVIEW_LIMITS.rows.toLocaleString()} 行的预览范围`)
            tooLarge(`「${worksheet.name}」列数超过 ${PREVIEW_LIMITS.columns} 列的预览范围`)
          }
          if (styleId(cell.style)) omittedFormatting = true
          continue
        }
        const value = cellValue(cell, date1904, warnings, formulas)
        let cellStyle = styleId(cell.style)
        const raw = cell.value
        const date = raw instanceof Date || (raw && typeof raw === 'object' && 'result' in raw && raw.result instanceof Date)
        if (date && !cell.numFmt) {
          const defaultDateStyle = { ...(cellStyle ? workbook.styles[cellStyle] : {}), n: { pattern: 'yyyy-mm-dd' } }
          cellStyle = registerStyle(defaultDateStyle)
        }
        if (value.v === undefined && !cellStyle) continue
        if (++cellCount > PREVIEW_LIMITS.cells) tooLarge(`工作簿有效单元格超过 ${PREVIEW_LIMITS.cells.toLocaleString()} 个的预览范围`)
        const cells = cellData[row.number - 1] ??= {}
        cells[column - 1] = { ...value, ...(cellStyle ? { s: cellStyle } : {}) }
        maxRow = Math.max(maxRow, row.number)
        maxColumn = Math.max(maxColumn, column)
      }
    }
    worksheet.columns?.forEach((column, index) => {
      const columnStyle = column.style && styleId(column.style)
      const columnInfo: Partial<IColumnData> = {}
      if (column.width !== undefined) columnInfo.w = columnWidthToPixels(column.width)
      if (column.hidden) columnInfo.hd = 1
      if (columnStyle) columnInfo.s = columnStyle
      if (Object.keys(columnInfo).length) {
        if (index >= PREVIEW_LIMITS.columns) {
          omittedFormatting = true
          return
        }
        columnData[index] = columnInfo
        maxColumn = Math.max(maxColumn, index + 1)
      }
    })
    if (omittedFormatting) warnings.add(`「${worksheet.name}」超出预览范围的空白行列格式已省略。`)
    const view = worksheet.views?.[0]
    const xSplit = view?.state === 'frozen' ? view.xSplit ?? 0 : 0
    const ySplit = view?.state === 'frozen' ? view.ySplit ?? 0 : 0
    const firstScrollable = view?.state === 'frozen' && view.topLeftCell ? decodeAddress(view.topLeftCell) : undefined
    const defaultColumnWidth = worksheet.properties.defaultColWidth
    const defaultColPx = defaultColumnWidth ? columnWidthToPixels(defaultColumnWidth) : 64

    // 含显式换行符或被右侧非空单元格截断的长文本，若原表未显式关闭自动换行，则在预览中自动折行以便行高自适应完整展开
    const mergeEndCol = new Map<string, number>()
    for (const range of mergeData) {
      mergeEndCol.set(`${range.startRow}:${range.startColumn}`, range.endColumn)
    }
    const resolveStyleObj = (ref: IStyleData | string | null | undefined | void): IStyleData | undefined =>
      typeof ref === 'string' ? workbook.styles[ref] ?? undefined : ref ?? undefined
    const estimateWidth = (text: string, fontSize: number): number => {
      let width = 0
      for (let i = 0; i < text.length; i++) {
        width += text.charCodeAt(i) > 0x2e80 ? fontSize * 1.35 : fontSize * 0.72
      }
      return width
    }
    for (const [rKey, rawRowCells] of Object.entries(cellData)) {
      if (!rawRowCells) continue
      const rowCells = rawRowCells as Record<number, ICellData | null | undefined>
      const r = Number(rKey)
      const rowStyleObj = resolveStyleObj(rowData[r]?.s)
      for (const [cKey, cell] of Object.entries(rowCells)) {
        if (!cell || typeof cell.v !== 'string' || !cell.v) continue
        const c = Number(cKey)
        const cellStyleObj = resolveStyleObj(cell.s)
        const colStyleObj = resolveStyleObj(columnData[c]?.s)
        const existingWrap = cellStyleObj?.tb ?? rowStyleObj?.tb ?? colStyleObj?.tb
        if (existingWrap !== undefined) continue
        if (cellStyleObj?.stf === 1 || cellStyleObj?.tr) continue
        let needsWrap = cell.v.includes('\n')
        if (!needsWrap) {
          const endCol = mergeEndCol.get(`${r}:${c}`) ?? c
          const rightCell = rowCells[endCol + 1]
          const rightOccupied = Boolean(rightCell && rightCell.v !== undefined && rightCell.v !== null && rightCell.v !== '')
          if (rightOccupied) {
            let colWidth = 0
            for (let k = c; k <= endCol; k++) colWidth += columnData[k]?.w ?? defaultColPx
            const fs = cellStyleObj?.fs ?? rowStyleObj?.fs ?? colStyleObj?.fs ?? 11
            needsWrap = estimateWidth(cell.v, fs) > colWidth - 8
          }
        }
        if (needsWrap) {
          const base = cellStyleObj ?? {}
          cell.s = registerStyle({
            ...base,
            tb: 3,
            pd: { l: 2, r: 2, ...base.pd, t: base.pd?.t ?? 3, b: base.pd?.b ?? 3 },
          })
        }
      }
    }

    const sheet: Partial<IWorksheetData> = {
      id, name: worksheet.name,
      tabColor: colorToHex(worksheet.properties.tabColor, theme) ?? '',
      hidden: worksheet.state === 'veryHidden' ? 2 : worksheet.state === 'hidden' ? 1 : 0,
      rowCount: Math.max(50, maxRow),
      columnCount: Math.max(20, maxColumn),
      defaultRowHeight: (worksheet.properties.defaultRowHeight ?? 15) * 4 / 3,
      defaultColumnWidth: defaultColPx,
      zoomRatio: Math.max(0.1, Math.min(4, (view?.zoomScale ?? 100) / 100)),
      scrollTop: 0, scrollLeft: 0,
      freeze: { xSplit, ySplit, startRow: ySplit ? (firstScrollable?.row ?? ySplit + 1) - 1 : -1, startColumn: xSplit ? (firstScrollable?.column ?? xSplit + 1) - 1 : -1 },
      rowHeader: { width: 48, hidden: view?.showRowColHeaders === false ? 1 : 0 },
      columnHeader: { height: 24, hidden: view?.showRowColHeaders === false ? 1 : 0 },
      showGridlines: view?.showGridLines === false ? 0 : 1,
      rightToLeft: view?.rightToLeft ? 1 : 0,
      cellData, rowData, columnData, mergeData,
    }
    workbook.sheetOrder.push(id)
    workbook.sheets[id] = sheet
    if (worksheet.getImages().length) warnings.add('文件中的图片暂不在表格预览中显示，请下载原文件查看。')
    if (storedSheet.conditionalFormattings?.length) warnings.add('条件格式暂未应用，预览显示单元格的基础样式。')
  }
  if (formulas.missing) warnings.add(`${formulas.missing} 个公式单元格没有已保存的计算结果（如 ${formulas.examples.join('、')}），预览已作标记。`)
  if (!worksheets.some((sheet) => sheet.state === 'visible')) {
    workbook.sheets[workbook.sheetOrder[0]!]!.hidden = 0
    if (!options.only) warnings.add('所有工作表均为隐藏状态，预览临时显示第一张工作表。')
  }
  return { workbook, warnings: [...warnings], stats: { sheetCount: worksheets.length, cellCount, formulaCount: formulas.count } }
}
