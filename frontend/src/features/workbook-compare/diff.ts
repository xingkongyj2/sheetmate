import type { IBorderData, ICellData, IColumnData, IRange, IStyleData, IWorkbookData, IWorksheetData, Nullable } from '@univerjs/core'

import type { SheetMeta } from '../workbook-preview/excel/types.ts'
import type {
  AlignedRowMeta,
  AlignMode,
  CellDiffEntry,
  DiffKind,
  DiffOptions,
  SheetDiffResult,
  SheetDiffStats,
  SheetPair,
} from './types.ts'

const MAX_SMART_ROWS = 10_000
const MAX_MYERS_DISTANCE = 1_500
const MAX_DP_PAIR_PRODUCT = 2_500
const MAX_DIFF_ENTRIES = 5_000
const CONTEXT_ROWS = 2
const FLOAT_EPSILON = 1e-9

interface NormalizedCell {
  empty: boolean
  kind: 'empty' | 'number' | 'string' | 'boolean'
  numValue?: number
  strValue?: string
  boolValue?: boolean
  isDate: boolean
  /** 用于行指纹的稳定键 */
  sig: string
  /** 给人看的可读展示值 */
  displayText: string
  /** 原始公式文本（含前导 =） */
  formula?: string
  /** 规范化公式（用于比对） */
  formulaKey?: string
}

/** 0 -> A, 25 -> Z, 26 -> AA */
export function columnLabel(col: number): string {
  let n = col + 1
  let out = ''
  while (n > 0) {
    const rem = (n - 1) % 26
    out = String.fromCharCode(65 + rem) + out
    n = Math.floor((n - 1) / 26)
  }
  return out
}

/** 规范化公式：引号外去空白、统一大写、去掉前导 =；引号内字面量原样保留 */
export function normalizeFormula(raw: string): string {
  const trimmed = raw.trim().replace(/^=+/, '')
  let out = ''
  let inQuote = false
  for (let i = 0; i < trimmed.length; i++) {
    const ch = trimmed[i]!
    if (ch === '"') {
      inQuote = !inQuote
      out += ch
    } else if (inQuote) {
      out += ch
    } else if (!/\s/.test(ch)) {
      out += ch.toUpperCase()
    }
  }
  return out
}

function isDateFormat(pattern?: string): boolean {
  if (!pattern) return false
  // 去掉双引号内文字和 [...] 颜色/语言标记
  const cleaned = pattern.replace(/"[^"]*"/g, '').replace(/\[[^\]]*\]/g, '').replace(/\\./g, '')
  if (!/[ymdhs]/i.test(cleaned)) return false
  // 纯数值占位符不视为日期
  if (/^[#0.,%?E+\-\s();]+$/i.test(cleaned)) return false
  return /(?:yy|dd|mm|yyyy|年|月|日)/i.test(cleaned) || /h+:mm/i.test(cleaned)
}

function formatExcelDateSerial(serial1900: number): string {
  // Excel 1900 序列号转 UTC 日期（与 snapshot.ts 中 25569 公式互逆）
  const ms = Math.round((serial1900 - 25569) * 86_400_000)
  const date = new Date(ms)
  if (Number.isNaN(date.getTime())) return String(serial1900)
  const y = date.getUTCFullYear()
  const m = String(date.getUTCMonth() + 1).padStart(2, '0')
  const d = String(date.getUTCDate()).padStart(2, '0')
  const hh = date.getUTCHours()
  const mm = date.getUTCMinutes()
  const ss = date.getUTCSeconds()
  if (hh === 0 && mm === 0 && ss === 0) return `${y}-${m}-${d}`
  return `${y}-${m}-${d} ${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}`
}

function resolveStyle(workbook: IWorkbookData | null, styleRef: Nullable<string | IStyleData>): IStyleData | undefined {
  if (!styleRef) return undefined
  if (typeof styleRef === 'string') return workbook?.styles?.[styleRef] ?? undefined
  return styleRef
}

function extractFormula(cell: ICellData | undefined): string | undefined {
  if (!cell) return undefined
  if (typeof cell.f === 'string' && cell.f.trim()) {
    return cell.f.startsWith('=') ? cell.f : `=${cell.f}`
  }
  const custom = cell.custom as { excelFormula?: unknown } | undefined
  if (typeof custom?.excelFormula === 'string' && custom.excelFormula.trim()) {
    return custom.excelFormula
  }
  return undefined
}

function normalizeNumberSig(num: number): string {
  if (Object.is(num, -0) || Math.abs(num) < 1e-12) return '0'
  // 统一有效数字精度，防止 0.30000000000000004 导致行指纹失配
  return String(Number(num.toPrecision(12)))
}

function normalizeCell(
  cell: ICellData | undefined,
  workbook: IWorkbookData | null,
  date1904: boolean,
): NormalizedCell {
  const formula = extractFormula(cell)
  const formulaKey = formula ? normalizeFormula(formula) : undefined
  const raw = cell?.v

  if (raw === undefined || raw === null || raw === '') {
    if (!formula) {
      return { empty: true, kind: 'empty', isDate: false, sig: '', displayText: '' }
    }
    return {
      empty: false,
      kind: 'empty',
      isDate: false,
      sig: `F:${formulaKey}`,
      displayText: '',
      formula,
      formulaKey,
    }
  }

  const style = resolveStyle(workbook, cell?.s)
  const dateFmt = isDateFormat(style?.n?.pattern)

  if (typeof raw === 'boolean' || cell?.t === 3) {
    const b = Boolean(raw)
    return {
      empty: false,
      kind: 'boolean',
      boolValue: b,
      isDate: false,
      sig: `B:${b ? '1' : '0'}${formulaKey ? `|F:${formulaKey}` : ''}`,
      displayText: b ? 'TRUE' : 'FALSE',
      formula,
      formulaKey,
    }
  }

  if (typeof raw === 'number' && Number.isFinite(raw)) {
    const normalizedNum = dateFmt && date1904 ? raw + 1462 : raw
    const numSig = normalizeNumberSig(normalizedNum)
    const displayText = dateFmt && normalizedNum >= 1 && normalizedNum < 2_958_466
      ? formatExcelDateSerial(normalizedNum)
      : String(Object.is(raw, -0) ? 0 : raw)
    return {
      empty: false,
      kind: 'number',
      numValue: normalizedNum,
      isDate: dateFmt,
      sig: `${dateFmt ? 'D' : 'N'}:${numSig}${formulaKey ? `|F:${formulaKey}` : ''}`,
      displayText,
      formula,
      formulaKey,
    }
  }

  const text = String(raw)
  return {
    empty: false,
    kind: 'string',
    strValue: text,
    isDate: false,
    sig: `S:${text}${formulaKey ? `|F:${formulaKey}` : ''}`,
    displayText: text,
    formula,
    formulaKey,
  }
}

function valuesEqual(a: NormalizedCell, b: NormalizedCell): boolean {
  if (a.kind !== b.kind) return false
  if (a.kind === 'empty') return true
  if (a.kind === 'boolean') return a.boolValue === b.boolValue
  if (a.kind === 'string') return a.strValue === b.strValue
  if (a.kind === 'number') {
    const av = a.numValue ?? 0
    const bv = b.numValue ?? 0
    if (av === bv) return true
    const scale = Math.max(1, Math.abs(av), Math.abs(bv))
    return Math.abs(av - bv) <= FLOAT_EPSILON * scale
  }
  return false
}

export function compareNormalizedCells(left: NormalizedCell, right: NormalizedCell): DiffKind | null {
  if (left.empty && right.empty) return null
  if (left.empty && !right.empty) return 'added'
  if (!left.empty && right.empty) return 'deleted'
  if (!valuesEqual(left, right)) return 'modified'
  if ((left.formulaKey ?? '') !== (right.formulaKey ?? '')) return 'formula'
  return null
}

/** 根据左右工作表目录与用户手动配对构建统一 Sheet 标签列表 */
export function buildSheetPairs(
  leftSheets: SheetMeta[],
  rightSheets: SheetMeta[],
  manualPairs: Array<{ leftIndex: number; rightIndex: number }> = [],
): SheetPair[] {
  const leftVisible = leftSheets.filter((s) => s.hidden === 0)
  const rightVisible = rightSheets.filter((s) => s.hidden === 0)
  const useLeft = leftVisible.length ? leftVisible : leftSheets
  const useRight = rightVisible.length ? rightVisible : rightSheets

  const usedLeft = new Set<number>()
  const usedRight = new Set<number>()
  const pairs: SheetPair[] = []

  // 1. 用户手动配对优先
  for (const pair of manualPairs) {
    const l = leftSheets.find((s) => s.index === pair.leftIndex)
    const r = rightSheets.find((s) => s.index === pair.rightIndex)
    if (!l || !r || usedLeft.has(l.index) || usedRight.has(r.index)) continue
    usedLeft.add(l.index)
    usedRight.add(r.index)
    pairs.push({
      key: `manual:${l.index}:${r.index}`,
      kind: 'manual',
      name: l.name === r.name ? l.name : `${l.name} → ${r.name}`,
      leftIndex: l.index,
      rightIndex: r.index,
      leftName: l.name,
      rightName: r.name,
    })
  }

  // 2. 同名工作表自动配对，左表独有标为已删除
  for (const l of useLeft) {
    if (usedLeft.has(l.index)) continue
    const match = rightSheets.find((r) => r.name === l.name && !usedRight.has(r.index))
    if (match) {
      usedLeft.add(l.index)
      usedRight.add(match.index)
      pairs.push({
        key: `matched:${l.index}:${match.index}`,
        kind: 'matched',
        name: l.name,
        leftIndex: l.index,
        rightIndex: match.index,
        leftName: l.name,
        rightName: match.name,
      })
    } else {
      usedLeft.add(l.index)
      pairs.push({
        key: `deleted:${l.index}`,
        kind: 'deleted',
        name: l.name,
        leftIndex: l.index,
        rightIndex: null,
        leftName: l.name,
        rightName: null,
      })
    }
  }

  // 3. 右表独有标为新增
  for (const r of useRight) {
    if (usedRight.has(r.index)) continue
    usedRight.add(r.index)
    pairs.push({
      key: `added:${r.index}`,
      kind: 'added',
      name: r.name,
      leftIndex: null,
      rightIndex: r.index,
      leftName: null,
      rightName: r.name,
    })
  }

  return pairs
}

interface ExtractedRow {
  row: number
  cells: Map<number, NormalizedCell>
  sig: string
  firstNonEmptyCol: number
  firstNonEmptyKey: string
}

interface ExtractedSheet {
  workbook: IWorkbookData | null
  sheet: Partial<IWorksheetData> | null
  date1904: boolean
  rows: ExtractedRow[]
  maxRow: number
  maxCol: number
}

function getSingleSheet(workbook: IWorkbookData | null): Partial<IWorksheetData> | null {
  if (!workbook) return null
  const firstId = workbook.sheetOrder[0]
  return (firstId ? workbook.sheets[firstId] : Object.values(workbook.sheets)[0]) ?? null
}

function extractSheet(workbook: IWorkbookData | null): ExtractedSheet {
  const sheet = getSingleSheet(workbook)
  if (!workbook || !sheet) {
    return { workbook: null, sheet: null, date1904: false, rows: [], maxRow: 0, maxCol: 0 }
  }
  const date1904 = workbook.dateSystem === 'date1904'
  const cellData = sheet.cellData ?? {}
  const parsedRows = new Map<number, Map<number, NormalizedCell>>()
  let maxRow = 0
  let maxCol = 0

  for (const [rKey, rowCells] of Object.entries(cellData)) {
    if (!rowCells) continue
    const r = Number(rKey)
    if (!Number.isInteger(r) || r < 0) continue
    let map: Map<number, NormalizedCell> | undefined
    for (const [cKey, rawCell] of Object.entries(rowCells)) {
      if (!rawCell) continue
      const c = Number(cKey)
      if (!Number.isInteger(c) || c < 0) continue
      const norm = normalizeCell(rawCell, workbook, date1904)
      if (norm.empty) continue
      if (!map) {
        map = new Map()
        parsedRows.set(r, map)
      }
      map.set(c, norm)
      if (r + 1 > maxRow) maxRow = r + 1
      if (c + 1 > maxCol) maxCol = c + 1
    }
  }

  const rows: ExtractedRow[] = new Array(maxRow)
  for (let r = 0; r < maxRow; r++) {
    const map = parsedRows.get(r)
    if (!map || map.size === 0) {
      rows[r] = { row: r, cells: new Map(), sig: '', firstNonEmptyCol: -1, firstNonEmptyKey: '' }
      continue
    }
    const cols = [...map.keys()].sort((a, b) => a - b)
    const parts: string[] = new Array(cols.length)
    for (let i = 0; i < cols.length; i++) {
      const c = cols[i]!
      parts[i] = `${c}=${map.get(c)!.sig}`
    }
    const firstCol = cols[0]!
    const firstCell = map.get(firstCol)!
    rows[r] = {
      row: r,
      cells: map,
      sig: parts.join('\u001f'),
      firstNonEmptyCol: firstCol,
      firstNonEmptyKey: `${firstCol}:${firstCell.kind}:${firstCell.displayText}`,
    }
  }

  return { workbook, sheet, date1904, rows, maxRow, maxCol }
}

type DiffOp =
  | { type: 'equal'; left: number; right: number }
  | { type: 'delete'; left: number }
  | { type: 'insert'; right: number }

/**
 * Myers O(ND) 序列 Diff（带公共前后缀裁剪与最大编辑距离保护）。
 * 当编辑距离超过 maxD 时返回 null，调用方回退到坐标对齐，保证大表不卡顿。
 */
function myersDiff(left: string[], right: string[], maxD = MAX_MYERS_DISTANCE): DiffOp[] | null {
  const nTotal = left.length
  const mTotal = right.length

  let prefix = 0
  while (prefix < nTotal && prefix < mTotal && left[prefix] === right[prefix]) prefix++

  let suffix = 0
  while (
    suffix < nTotal - prefix
    && suffix < mTotal - prefix
    && left[nTotal - 1 - suffix] === right[mTotal - 1 - suffix]
  ) {
    suffix++
  }

  const n = nTotal - prefix - suffix
  const m = mTotal - prefix - suffix

  const result: DiffOp[] = []
  for (let i = 0; i < prefix; i++) result.push({ type: 'equal', left: i, right: i })

  if (n === 0 && m === 0) {
    // all equal
  } else if (n === 0) {
    for (let j = 0; j < m; j++) result.push({ type: 'insert', right: prefix + j })
  } else if (m === 0) {
    for (let i = 0; i < n; i++) result.push({ type: 'delete', left: prefix + i })
  } else {
    const limit = Math.min(maxD, n + m)
    const offset = limit
    const v = new Int32Array(2 * limit + 1)
    const trace: Int32Array[] = []
    let foundD = -1

    for (let d = 0; d <= limit; d++) {
      // 只保存当前步用到的 [-d, d] 范围
      const snap = new Int32Array(2 * d + 1)
      for (let k = -d; k <= d; k += 2) {
        let x: number
        if (k === -d || (k !== d && v[offset + k - 1]! < v[offset + k + 1]!)) {
          x = v[offset + k + 1]!
        } else {
          x = v[offset + k - 1]! + 1
        }
        let y = x - k
        while (x < n && y < m && left[prefix + x] === right[prefix + y]) {
          x++
          y++
        }
        v[offset + k] = x
        snap[k + d] = x
        if (x >= n && y >= m) {
          foundD = d
          break
        }
      }
      trace.push(snap)
      if (foundD >= 0) break
    }

    if (foundD < 0) return null

    // 回溯路径
    const midOps: DiffOp[] = []
    let x = n
    let y = m
    for (let d = foundD; d > 0; d--) {
      const prevSnap = trace[d - 1]!
      const prevD = d - 1
      const k = x - y
      const prevK = (k === -d || (k !== d && (prevSnap[k - 1 + prevD] ?? -1) < (prevSnap[k + 1 + prevD] ?? -1)))
        ? k + 1
        : k - 1
      const prevX = prevSnap[prevK + prevD]!
      const prevY = prevX - prevK

      while (x > prevX && y > prevY) {
        x--
        y--
        midOps.push({ type: 'equal', left: prefix + x, right: prefix + y })
      }
      if (x === prevX) {
        y--
        midOps.push({ type: 'insert', right: prefix + y })
      } else {
        x--
        midOps.push({ type: 'delete', left: prefix + x })
      }
    }
    while (x > 0 && y > 0) {
      x--
      y--
      midOps.push({ type: 'equal', left: prefix + x, right: prefix + y })
    }
    midOps.reverse()
    for (const op of midOps) result.push(op)
  }

  for (let i = 0; i < suffix; i++) {
    result.push({ type: 'equal', left: nTotal - suffix + i, right: mTotal - suffix + i })
  }

  return result
}

/** 计算两行的内容相似度（0 ~ 1），前两非空列权重更高（常为主键/编号列） */
function rowSimilarity(left: ExtractedRow, right: ExtractedRow): number {
  if (left.cells.size === 0 && right.cells.size === 0) return 1
  if (left.cells.size === 0 || right.cells.size === 0) return 0

  const cols = new Set<number>()
  for (const c of left.cells.keys()) cols.add(c)
  for (const c of right.cells.keys()) cols.add(c)

  const sortedCols = [...cols].sort((a, b) => a - b)
  let matched = 0
  let total = 0

  for (let idx = 0; idx < sortedCols.length; idx++) {
    const col = sortedCols[idx]!
    const weight = idx < 2 ? 1.5 : 1
    total += weight
    const lc = left.cells.get(col)
    const rc = right.cells.get(col)
    if (!lc || !rc) continue
    if (valuesEqual(lc, rc)) {
      matched += weight
    } else if (lc.formulaKey && rc.formulaKey && lc.formulaKey === rc.formulaKey) {
      matched += weight * 0.5
    }
  }
  return total > 0 ? matched / total : 0
}

interface RawAlignedRow {
  kind: 'same' | 'modified' | 'added' | 'deleted'
  leftRow: number | null
  rightRow: number | null
}

/** 将同一变更块内的 delete 行和 insert 行做单调相似度配对，识别“修改行” */
function pairChangeHunk(
  delIndices: number[],
  insIndices: number[],
  leftRows: ExtractedRow[],
  rightRows: ExtractedRow[],
): RawAlignedRow[] {
  if (!delIndices.length) return insIndices.map((r) => ({ kind: 'added', leftRow: null, rightRow: r }))
  if (!insIndices.length) return delIndices.map((l) => ({ kind: 'deleted', leftRow: l, rightRow: null }))

  // 单行对单行：同一位置改写 1 行，只要两边都非空，直接配成修改行以便直观高亮改动的单元格
  if (delIndices.length === 1 && insIndices.length === 1) {
    const l = delIndices[0]!
    const r = insIndices[0]!
    const lr = leftRows[l]!
    const rr = rightRows[r]!
    if (lr.cells.size > 0 && rr.cells.size > 0) {
      return [{ kind: 'modified', leftRow: l, rightRow: r }]
    }
  }

  const L = delIndices.length
  const R = insIndices.length
  const pairs: Array<[number, number]> = []

  if (L * R <= MAX_DP_PAIR_PRODUCT) {
    const THRESHOLD = 0.35
    const scores = new Float64Array(L * R)
    for (let i = 0; i < L; i++) {
      const lr = leftRows[delIndices[i]!]!
      for (let j = 0; j < R; j++) {
        scores[i * R + j] = rowSimilarity(lr, rightRows[insIndices[j]!]!)
      }
    }
    const cols = R + 1
    const dp = new Float64Array((L + 1) * cols)
    const dir = new Uint8Array((L + 1) * cols) // 1 = skip L, 2 = skip R, 3 = match

    for (let i = 1; i <= L; i++) {
      for (let j = 1; j <= R; j++) {
        let best = dp[(i - 1) * cols + j]!
        let d = 1
        const skipR = dp[i * cols + (j - 1)]!
        if (skipR > best) {
          best = skipR
          d = 2
        }
        const sim = scores[(i - 1) * R + (j - 1)]!
        if (sim >= THRESHOLD) {
          const take = dp[(i - 1) * cols + (j - 1)]! + sim + 0.001
          if (take > best) {
            best = take
            d = 3
          }
        }
        dp[i * cols + j] = best
        dir[i * cols + j] = d
      }
    }

    let i = L
    let j = R
    while (i > 0 && j > 0) {
      const d = dir[i * cols + j]
      if (d === 3) {
        pairs.push([i - 1, j - 1])
        i--
        j--
      } else if (d === 1) {
        i--
      } else {
        j--
      }
    }
    pairs.reverse()
  } else {
    // 大变更块降级：按第一非空列文本做单调 1:1 匹配（O(L + R)）
    const rightByKey = new Map<string, number[]>()
    for (let j = 0; j < R; j++) {
      const key = rightRows[insIndices[j]!]!.firstNonEmptyKey
      if (!key) continue
      const list = rightByKey.get(key)
      if (list) list.push(j)
      else rightByKey.set(key, [j])
    }
    let lastRight = -1
    for (let i = 0; i < L; i++) {
      const key = leftRows[delIndices[i]!]!.firstNonEmptyKey
      if (!key) continue
      const list = rightByKey.get(key)
      if (!list) continue
      const found = list.find((idx) => idx > lastRight)
      if (found !== undefined) {
        pairs.push([i, found])
        lastRight = found
      }
    }
  }

  const out: RawAlignedRow[] = []
  let prevI = 0
  let prevJ = 0
  for (const [pi, pj] of pairs) {
    for (let i = prevI; i < pi; i++) out.push({ kind: 'deleted', leftRow: delIndices[i]!, rightRow: null })
    for (let j = prevJ; j < pj; j++) out.push({ kind: 'added', leftRow: null, rightRow: insIndices[j]! })
    out.push({ kind: 'modified', leftRow: delIndices[pi]!, rightRow: insIndices[pj]! })
    prevI = pi + 1
    prevJ = pj + 1
  }
  for (let i = prevI; i < L; i++) out.push({ kind: 'deleted', leftRow: delIndices[i]!, rightRow: null })
  for (let j = prevJ; j < R; j++) out.push({ kind: 'added', leftRow: null, rightRow: insIndices[j]! })
  return out
}

function alignRows(
  left: ExtractedSheet,
  right: ExtractedSheet,
  requestedMode: AlignMode,
  notices: string[],
): { rows: RawAlignedRow[]; effectiveMode: AlignMode } {
  // 单侧表（新增 Sheet / 删除 Sheet）
  if (!left.sheet && right.sheet) {
    return {
      rows: right.rows.map((r) => ({ kind: 'added', leftRow: null, rightRow: r.row })),
      effectiveMode: requestedMode,
    }
  }
  if (left.sheet && !right.sheet) {
    return {
      rows: left.rows.map((l) => ({ kind: 'deleted', leftRow: l.row, rightRow: null })),
      effectiveMode: requestedMode,
    }
  }

  const maxRow = Math.max(left.maxRow, right.maxRow)
  if (requestedMode === 'coordinate') {
    return { rows: coordinateAlign(left, right, maxRow), effectiveMode: 'coordinate' }
  }

  if (left.maxRow > MAX_SMART_ROWS || right.maxRow > MAX_SMART_ROWS) {
    notices.push(`当前工作表超过 ${MAX_SMART_ROWS.toLocaleString()} 行，已自动切换为「按坐标对齐」以保持流畅。`)
    return { rows: coordinateAlign(left, right, maxRow), effectiveMode: 'coordinate' }
  }

  const leftSigs = left.rows.map((r) => r.sig)
  const rightSigs = right.rows.map((r) => r.sig)
  const ops = myersDiff(leftSigs, rightSigs, MAX_MYERS_DISTANCE)
  if (!ops) {
    notices.push('两张表的数据结构差异较大，已自动切换为「按坐标对齐」展示。')
    return { rows: coordinateAlign(left, right, maxRow), effectiveMode: 'coordinate' }
  }

  const aligned: RawAlignedRow[] = []
  let delBuf: number[] = []
  let insBuf: number[] = []

  const flush = () => {
    if (!delBuf.length && !insBuf.length) return
    aligned.push(...pairChangeHunk(delBuf, insBuf, left.rows, right.rows))
    delBuf = []
    insBuf = []
  }

  for (const op of ops) {
    if (op.type === 'equal') {
      flush()
      aligned.push({ kind: 'same', leftRow: op.left, rightRow: op.right })
    } else if (op.type === 'delete') {
      delBuf.push(op.left)
    } else {
      insBuf.push(op.right)
    }
  }
  flush()

  // 尾部纯新增/纯删除（如空白模板在末尾填满数据）：若对侧超出有效数据区且未发生行号错位，
  // 直接对应原行号以保留空白模板的原始网格线与合并单元格，避免用灰色占位行覆盖。
  for (let i = aligned.length - 1; i >= 0; i--) {
    const row = aligned[i]!
    if (row.kind === 'added' && row.leftRow === null && row.rightRow !== null && row.rightRow >= left.maxRow && i === row.rightRow) {
      row.leftRow = row.rightRow
    } else if (row.kind === 'deleted' && row.rightRow === null && row.leftRow !== null && row.leftRow >= right.maxRow && i === row.leftRow) {
      row.rightRow = row.leftRow
    } else {
      break
    }
  }

  return { rows: aligned, effectiveMode: 'smart' }
}

function coordinateAlign(left: ExtractedSheet, right: ExtractedSheet, maxRow: number): RawAlignedRow[] {
  const aligned: RawAlignedRow[] = []
  for (let r = 0; r < maxRow; r++) {
    const lr = left.rows[r]
    const rr = right.rows[r]
    const lEmpty = !lr || lr.cells.size === 0
    const rEmpty = !rr || rr.cells.size === 0
    if (lEmpty && rEmpty) {
      aligned.push({ kind: 'same', leftRow: r, rightRow: r })
    } else if (lEmpty && !rEmpty) {
      aligned.push({ kind: 'added', leftRow: r, rightRow: r })
    } else if (!lEmpty && rEmpty) {
      aligned.push({ kind: 'deleted', leftRow: r, rightRow: r })
    } else if (lr!.sig === rr!.sig) {
      aligned.push({ kind: 'same', leftRow: r, rightRow: r })
    } else {
      aligned.push({ kind: 'modified', leftRow: r, rightRow: r })
    }
  }
  return aligned
}

/** 过滤「仅看差异行」，在被折叠区间插入 fold 行 */
function buildDisplayRows(aligned: RawAlignedRow[], onlyDiffRows: boolean): AlignedRowMeta[] {
  if (!onlyDiffRows) {
    return aligned.map((row) => ({ leftRow: row.leftRow, rightRow: row.rightRow, kind: row.kind }))
  }

  const hasAnyDiff = aligned.some((r) => r.kind !== 'same')
  if (!hasAnyDiff) {
    return aligned.slice(0, Math.min(aligned.length, 30)).map((r) => ({ leftRow: r.leftRow, rightRow: r.rightRow, kind: r.kind }))
  }

  const keep = new Uint8Array(aligned.length)
  // 保留第 0 行（通常是表头）
  if (aligned.length > 0) keep[0] = 1

  for (let i = 0; i < aligned.length; i++) {
    if (aligned[i]!.kind !== 'same') {
      const start = Math.max(0, i - CONTEXT_ROWS)
      const end = Math.min(aligned.length - 1, i + CONTEXT_ROWS)
      for (let k = start; k <= end; k++) keep[k] = 1
    }
  }

  // 如果两个保留块之间只夹了 1 行未变化行，直接展示这 1 行（不折）
  for (let i = 1; i < aligned.length - 1; i++) {
    if (!keep[i] && keep[i - 1] && keep[i + 1]) keep[i] = 1
  }

  const out: AlignedRowMeta[] = []
  let i = 0
  while (i < aligned.length) {
    if (keep[i]) {
      const row = aligned[i]!
      out.push({ leftRow: row.leftRow, rightRow: row.rightRow, kind: row.kind })
      i++
    } else {
      let count = 0
      while (i < aligned.length && !keep[i]) {
        count++
        i++
      }
      out.push({ leftRow: null, rightRow: null, kind: 'fold', omittedCount: count })
    }
  }
  return out
}

const EMPTY_NORM: NormalizedCell = { empty: true, kind: 'empty', isDate: false, sig: '', displayText: '' }

// ---- Diff 视觉调色板（高可读、柔和克制） ----
const DIFF_PALETTE: Record<DiffKind, { bg: string; fg: string; border: string; rowBg: string }> = {
  added: { bg: '#DCFCE7', fg: '#14532D', border: '#22C55E', rowBg: '#F0FDF4' },
  deleted: { bg: '#FEE2E2', fg: '#7F1D1D', border: '#EF4444', rowBg: '#FEF2F2' },
  modified: { bg: '#FEF3C7', fg: '#78350F', border: '#F59E0B', rowBg: '#FFFBEB' },
  formula: { bg: '#DBEAFE', fg: '#1E3A8A', border: '#3B82F6', rowBg: '#EFF6FF' },
}

const PLACEHOLDER_BG = '#F3F4F6'
const FOLD_STYLE: IStyleData = {
  bg: { rgb: '#F8FAFC' },
  cl: { rgb: '#64748B' },
  it: 1,
  fs: 10,
  ht: 1,
  vt: 2,
}

function makeDiffBorder(rgb: string): IBorderData {
  const edge = { s: 8 as const, cl: { rgb } }
  return { t: edge, b: edge, l: edge, r: edge }
}

/** 构造带样式去重的快照构建器 */
function createStylePool(styles: Record<string, IStyleData>) {
  const byKey = new Map<string, string>()
  return function intern(style: IStyleData | undefined): string | undefined {
    if (!style || !Object.keys(style).length) return undefined
    const key = JSON.stringify(style)
    let id = byKey.get(key)
    if (!id) {
      id = `d${byKey.size}`
      byKey.set(key, id)
      styles[id] = style
    }
    return id
  }
}

/** 剥离杂乱原色，仅保留排版与数字格式，让 Diff 高亮一目了然 */
function stripToCleanStyle(base: IStyleData | undefined): IStyleData {
  if (!base) return {}
  const clean: IStyleData = {}
  if (base.ff) clean.ff = base.ff
  if (base.fs) clean.fs = base.fs
  if (base.bl !== undefined) clean.bl = base.bl
  if (base.it !== undefined) clean.it = base.it
  if (base.ht !== undefined) clean.ht = base.ht
  if (base.vt !== undefined) clean.vt = base.vt
  if (base.tb !== undefined) clean.tb = base.tb
  if (base.n) clean.n = base.n
  if (base.pd) clean.pd = base.pd
  return clean
}

function composeCellStyle(
  base: IStyleData | undefined,
  highlight: DiffKind | 'row-added' | 'row-deleted' | 'placeholder' | null,
  preserveStyles: boolean,
  showFormulaText: boolean,
): IStyleData {
  const result: IStyleData = preserveStyles ? (base ? { ...base } : {}) : stripToCleanStyle(base)
  if (showFormulaText) delete result.n

  if (highlight === 'placeholder') {
    result.bg = { rgb: PLACEHOLDER_BG }
    return result
  }
  if (highlight === 'row-added') {
    if (!preserveStyles || !result.bg) result.bg = { rgb: DIFF_PALETTE.added.rowBg }
    return result
  }
  if (highlight === 'row-deleted') {
    if (!preserveStyles || !result.bg) result.bg = { rgb: DIFF_PALETTE.deleted.rowBg }
    return result
  }
  if (highlight) {
    const tone = DIFF_PALETTE[highlight]
    if (preserveStyles) {
      if (!result.bg) result.bg = { rgb: tone.bg }
      result.bd = makeDiffBorder(tone.border)
      if (highlight === 'deleted') result.st = { s: 1 }
    } else {
      result.bg = { rgb: tone.bg }
      result.cl = { rgb: tone.fg }
      if (highlight === 'deleted') result.st = { s: 1 }
    }
  }
  return result
}

function remapMerges(
  merges: IRange[] | undefined,
  origToDisplay: Map<number, number>,
  isIdentity: boolean,
): { merges: IRange[]; dropped: number } {
  if (!merges?.length) return { merges: [], dropped: 0 }
  if (isIdentity) return { merges: merges.map((m) => ({ ...m })), dropped: 0 }

  const out: IRange[] = []
  let dropped = 0
  for (const range of merges) {
    const start = origToDisplay.get(range.startRow)
    const end = origToDisplay.get(range.endRow)
    if (start === undefined || end === undefined) {
      dropped++
      continue
    }
    // 检查中间每一行在展示行里是否仍然紧邻连续（没有被插占位行或折叠拆开）
    const expectedSpan = range.endRow - range.startRow
    if (end - start !== expectedSpan) {
      dropped++
      continue
    }
    let contiguous = true
    for (let r = range.startRow; r <= range.endRow; r++) {
      if (origToDisplay.get(r) !== start + (r - range.startRow)) {
        contiguous = false
        break
      }
    }
    if (contiguous) {
      out.push({ startRow: start, endRow: end, startColumn: range.startColumn, endColumn: range.endColumn })
    } else {
      dropped++
    }
  }
  return { merges: out, dropped }
}

function buildAddressLabel(colLabel: string, leftRow: number | null, rightRow: number | null): string {
  if (leftRow !== null && rightRow !== null) {
    return leftRow === rightRow ? `${colLabel}${leftRow + 1}` : `${colLabel}${leftRow + 1} → ${colLabel}${rightRow + 1}`
  }
  if (rightRow !== null) return `+ ${colLabel}${rightRow + 1}`
  if (leftRow !== null) return `- ${colLabel}${leftRow + 1}`
  return colLabel
}

/** 轻量摘要计算（供后台给所有 Sheet 标签算差异角标，不构建完整 Univer 快照） */
export function computeSheetDiffSummary(
  leftWorkbook: IWorkbookData | null,
  rightWorkbook: IWorkbookData | null,
  alignMode: AlignMode = 'smart',
): SheetDiffStats {
  const left = extractSheet(leftWorkbook)
  const right = extractSheet(rightWorkbook)
  const notices: string[] = []
  const { rows: aligned } = alignRows(left, right, alignMode, notices)

  const stats: SheetDiffStats = {
    total: 0,
    added: 0,
    deleted: 0,
    modified: 0,
    formula: 0,
    addedRows: 0,
    deletedRows: 0,
    modifiedRows: 0,
  }

  for (const row of aligned) {
    if (row.kind === 'same') continue
    const lr = row.leftRow !== null ? left.rows[row.leftRow] : undefined
    const rr = row.rightRow !== null ? right.rows[row.rightRow] : undefined
    if (row.kind === 'added') stats.addedRows++
    else if (row.kind === 'deleted') stats.deletedRows++
    else if (row.kind === 'modified') stats.modifiedRows++

    const cols = new Set<number>()
    if (lr) for (const c of lr.cells.keys()) cols.add(c)
    if (rr) for (const c of rr.cells.keys()) cols.add(c)

    for (const c of cols) {
      const kind = compareNormalizedCells(lr?.cells.get(c) ?? EMPTY_NORM, rr?.cells.get(c) ?? EMPTY_NORM)
      if (!kind) continue
      stats.total++
      stats[kind]++
    }
  }

  return stats
}

/**
 * 核心比对函数：将左右单表 Univer 快照进行对齐、差异分析、着色与统一尺寸，
 * 输出可直接交给左右两个只读 Univer 实例同步渲染的结果。
 */
export function diffSheetSnapshots(
  leftWorkbook: IWorkbookData | null,
  rightWorkbook: IWorkbookData | null,
  options: DiffOptions,
  sheetTitle = 'Sheet1',
): SheetDiffResult {
  const left = extractSheet(leftWorkbook)
  const right = extractSheet(rightWorkbook)
  const notices: string[] = []

  const { rows: aligned, effectiveMode } = alignRows(left, right, options.alignMode, notices)

  const displayRows = buildDisplayRows(aligned, options.onlyDiffRows)

  const stats: SheetDiffStats = {
    total: 0,
    added: 0,
    deleted: 0,
    modified: 0,
    formula: 0,
    addedRows: 0,
    deletedRows: 0,
    modifiedRows: 0,
  }

  // 先统计全量行变更与单元格差异（即使开启了 onlyDiffRows，统计数也始终准确）
  const diffs: CellDiffEntry[] = []
  const cellDiffMap: Record<string, CellDiffEntry> = {}

  // 记录每一展示行上每个发生变更的列（用于在渲染阶段涂色）
  const rowCellDiffs = new Map<number, Map<number, DiffKind>>()

  // 建立 原始行 -> 展示行 映射（用于合并单元格重映射）
  const leftOrigToDisplay = new Map<number, number>()
  const rightOrigToDisplay = new Map<number, number>()
  for (let dRow = 0; dRow < displayRows.length; dRow++) {
    const meta = displayRows[dRow]!
    if (meta.leftRow !== null) leftOrigToDisplay.set(meta.leftRow, dRow)
    if (meta.rightRow !== null) rightOrigToDisplay.set(meta.rightRow, dRow)
  }

  for (let dRow = 0; dRow < displayRows.length; dRow++) {
    const meta = displayRows[dRow]!
    if (meta.kind === 'fold' || meta.kind === 'same') continue
    if (meta.kind === 'added') stats.addedRows++
    else if (meta.kind === 'deleted') stats.deletedRows++
    else if (meta.kind === 'modified') stats.modifiedRows++

    const lr = meta.leftRow !== null ? left.rows[meta.leftRow] : undefined
    const rr = meta.rightRow !== null ? right.rows[meta.rightRow] : undefined
    const cols = new Set<number>()
    if (lr) for (const c of lr.cells.keys()) cols.add(c)
    if (rr) for (const c of rr.cells.keys()) cols.add(c)
    const sortedCols = [...cols].sort((a, b) => a - b)

    let colDiffMap: Map<number, DiffKind> | undefined
    for (const col of sortedCols) {
      const lc = lr?.cells.get(col) ?? EMPTY_NORM
      const rc = rr?.cells.get(col) ?? EMPTY_NORM
      const kind = compareNormalizedCells(lc, rc)
      if (!kind) continue

      stats.total++
      stats[kind]++

      if (!colDiffMap) {
        colDiffMap = new Map()
        rowCellDiffs.set(dRow, colDiffMap)
      }
      colDiffMap.set(col, kind)

      if (diffs.length < MAX_DIFF_ENTRIES) {
        const colLbl = columnLabel(col)
        const entry: CellDiffEntry = {
          id: `${dRow}:${col}`,
          kind,
          displayRow: dRow,
          col,
          leftRow: meta.leftRow,
          rightRow: meta.rightRow,
          colLabel: colLbl,
          addressLabel: buildAddressLabel(colLbl, meta.leftRow, meta.rightRow),
          beforeValue: lc.displayText,
          afterValue: rc.displayText,
          beforeFormula: lc.formula,
          afterFormula: rc.formula,
          rowKind: meta.kind,
        }
        diffs.push(entry)
        cellDiffMap[entry.id] = entry
      } else {
        stats.truncated = true
      }
    }
  }

  // ---- 构造左右两份尺寸完全统一的 Univer 快照 ----
  const maxCol = Math.max(left.maxCol, right.maxCol, 1)
  const bandCols = Math.min(Math.max(maxCol, 8), 160)
  const rowCount = Math.max(50, displayRows.length + 5)
  const columnCount = Math.max(20, maxCol + 2)

  const leftStyles: Record<string, IStyleData> = {}
  const rightStyles: Record<string, IStyleData> = {}
  const internLeft = createStylePool(leftStyles)
  const internRight = createStylePool(rightStyles)

  const leftCellData: IWorksheetData['cellData'] = {}
  const rightCellData: IWorksheetData['cellData'] = {}
  const leftRowData: IWorksheetData['rowData'] = {}
  const rightRowData: IWorksheetData['rowData'] = {}
  const leftColData: IWorksheetData['columnData'] = {}
  const rightColData: IWorksheetData['columnData'] = {}

  const defaultRowHeight = Math.max(
    left.sheet?.defaultRowHeight ?? 20,
    right.sheet?.defaultRowHeight ?? 20,
  )
  const defaultColumnWidth = Math.max(
    left.sheet?.defaultColumnWidth ?? 72,
    right.sheet?.defaultColumnWidth ?? 72,
  )

  // 统一列宽（确保两边横向滚动严格齐平）
  for (let c = 0; c < columnCount; c++) {
    const lw = left.sheet?.columnData?.[c]?.w
    const rw = right.sheet?.columnData?.[c]?.w
    const w = lw !== undefined && rw !== undefined ? Math.max(lw, rw) : (lw ?? rw)
    const lhd = left.sheet?.columnData?.[c]?.hd
    const rhd = right.sheet?.columnData?.[c]?.hd
    // 任一边显示则两边都显示，防止看不到被取消隐藏的列
    const hd = lhd === 1 && rhd === 1 ? 1 : 0
    if (w !== undefined || hd) {
      const info: Partial<IColumnData> = {}
      if (w !== undefined) info.w = w
      if (hd) info.hd = 1
      leftColData[c] = { ...info }
      rightColData[c] = { ...info }
    }
  }

  const leftSrcCells = left.sheet?.cellData ?? {}
  const rightSrcCells = right.sheet?.cellData ?? {}
  const foldStyleLeft = internLeft(FOLD_STYLE)
  const foldStyleRight = internRight(FOLD_STYLE)
  const placeholderStyleLeft = internLeft({ bg: { rgb: PLACEHOLDER_BG } })
  const placeholderStyleRight = internRight({ bg: { rgb: PLACEHOLDER_BG } })

  for (let dRow = 0; dRow < displayRows.length; dRow++) {
    const meta = displayRows[dRow]!

    if (meta.kind === 'fold') {
      const label = `⋯ 已省略 ${meta.omittedCount ?? 0} 行未变化内容 ⋯`
      leftRowData[dRow] = { h: 24 }
      rightRowData[dRow] = { h: 24 }
      const lRow: Record<number, ICellData> = {}
      const rRow: Record<number, ICellData> = {}
      for (let c = 0; c < bandCols; c++) {
        lRow[c] = c === 0 ? { v: label, t: 1, s: foldStyleLeft } : { s: foldStyleLeft }
        rRow[c] = c === 0 ? { v: label, t: 1, s: foldStyleRight } : { s: foldStyleRight }
      }
      leftCellData[dRow] = lRow
      rightCellData[dRow] = rRow
      continue
    }

    // 统一行高（两侧取最大，避免换行导致纵轴错位）
    const lh = meta.leftRow !== null ? left.sheet?.rowData?.[meta.leftRow]?.h : undefined
    const rh = meta.rightRow !== null ? right.sheet?.rowData?.[meta.rightRow]?.h : undefined
    const unifiedH = lh !== undefined && rh !== undefined ? Math.max(lh, rh) : (lh ?? rh)
    if (unifiedH !== undefined) {
      leftRowData[dRow] = { h: unifiedH }
      rightRowData[dRow] = { h: unifiedH }
    }

    const colDiffs = rowCellDiffs.get(dRow)
    const isLeftPlaceholder = meta.leftRow === null
    const isRightPlaceholder = meta.rightRow === null
    const lSrcRow = meta.leftRow !== null ? leftSrcCells[meta.leftRow] : undefined
    const rSrcRow = meta.rightRow !== null ? rightSrcCells[meta.rightRow] : undefined

    const lOutRow: Record<number, ICellData> = {}
    const rOutRow: Record<number, ICellData> = {}

    if (isLeftPlaceholder) {
      for (let c = 0; c < bandCols; c++) lOutRow[c] = { s: placeholderStyleLeft }
    }
    if (isRightPlaceholder) {
      for (let c = 0; c < bandCols; c++) rOutRow[c] = { s: placeholderStyleRight }
    }

    // 整行新增或删除时，为整行铺一层极淡色带，提升扫视效率
    if (!isLeftPlaceholder && meta.kind === 'deleted') {
      const bandStyle = internLeft(composeCellStyle(undefined, 'row-deleted', options.preserveStyles, false))
      for (let c = 0; c < bandCols; c++) lOutRow[c] = { s: bandStyle }
    }
    if (!isRightPlaceholder && meta.kind === 'added') {
      const bandStyle = internRight(composeCellStyle(undefined, 'row-added', options.preserveStyles, false))
      for (let c = 0; c < bandCols; c++) rOutRow[c] = { s: bandStyle }
    }

    // 收集需要写入的列（原有单元格 + 差异列）
    const writeCols = new Set<number>()
    if (lSrcRow) for (const cKey of Object.keys(lSrcRow)) writeCols.add(Number(cKey))
    if (rSrcRow) for (const cKey of Object.keys(rSrcRow)) writeCols.add(Number(cKey))
    if (colDiffs) for (const c of colDiffs.keys()) writeCols.add(c)

    for (const c of writeCols) {
      if (!Number.isInteger(c) || c < 0) continue
      const diffKind = colDiffs?.get(c) ?? null

      // ---- 左侧单元格 ----
      if (!isLeftPlaceholder) {
        const src = lSrcRow?.[c]
        const formula = extractFormula(src)
        const baseStyle = resolveStyle(left.workbook, src?.s)
        // 右侧新增单元格在左侧显示极淡提示，其余保留对应 Diff 色
        const highlight: DiffKind | 'row-deleted' | null = diffKind === 'added'
          ? null
          : diffKind ?? (meta.kind === 'deleted' ? 'row-deleted' : null)
        const showFormula = Boolean(options.showFormulas && formula)
        const styleId = internLeft(composeCellStyle(baseStyle, highlight, options.preserveStyles, showFormula))
        if (src || styleId) {
          const outCell: ICellData = {}
          if (showFormula) {
            outCell.v = formula
            outCell.t = 1
          } else if (src?.v !== undefined) {
            outCell.v = src.v
            if (src.t !== undefined) outCell.t = src.t
          }
          if (styleId) outCell.s = styleId
          if (src?.custom) outCell.custom = src.custom
          lOutRow[c] = outCell
        }
      }

      // ---- 右侧单元格 ----
      if (!isRightPlaceholder) {
        const src = rSrcRow?.[c]
        const formula = extractFormula(src)
        const baseStyle = resolveStyle(right.workbook, src?.s)
        // 左侧被删单元格在右侧不强行覆盖非空，若为空则涂淡红提示此处被清空
        const highlight: DiffKind | 'row-added' | null = diffKind ?? (meta.kind === 'added' ? 'row-added' : null)
        const showFormula = Boolean(options.showFormulas && formula)
        const styleId = internRight(composeCellStyle(baseStyle, highlight, options.preserveStyles, showFormula))
        if (src || styleId) {
          const outCell: ICellData = {}
          if (showFormula) {
            outCell.v = formula
            outCell.t = 1
          } else if (src?.v !== undefined) {
            outCell.v = src.v
            if (src.t !== undefined) outCell.t = src.t
          }
          if (styleId) outCell.s = styleId
          if (src?.custom) outCell.custom = src.custom
          rOutRow[c] = outCell
        }
      }
    }

    if (Object.keys(lOutRow).length) leftCellData[dRow] = lOutRow
    if (Object.keys(rOutRow).length) rightCellData[dRow] = rOutRow
  }

  // 合并单元格重映射（插占位行或折叠行时安全处理）
  const isLeftIdentity = !options.onlyDiffRows && displayRows.every((r, idx) => r.leftRow === idx)
  const isRightIdentity = !options.onlyDiffRows && displayRows.every((r, idx) => r.rightRow === idx)
  const leftMerges = remapMerges(left.sheet?.mergeData, leftOrigToDisplay, isLeftIdentity)
  const rightMerges = remapMerges(right.sheet?.mergeData, rightOrigToDisplay, isRightIdentity)
  if (leftMerges.dropped > 0 || rightMerges.dropped > 0) {
    notices.push('部分跨行合并单元格在行对齐或折叠模式下已自动展开显示；如需查看原始合并效果，可切换为「按坐标对齐」。')
  }

  // 为折叠提示行横向合并前几列，避免省略提示文字在窄 A 列被截断
  const foldEndCol = Math.min(Math.max(bandCols - 1, 0), 7)
  if (foldEndCol > 0) {
    for (let dRow = 0; dRow < displayRows.length; dRow++) {
      if (displayRows[dRow]!.kind === 'fold') {
        const range: IRange = { startRow: dRow, endRow: dRow, startColumn: 0, endColumn: foldEndCol }
        leftMerges.merges.push(range)
        rightMerges.merges.push({ ...range })
      }
    }
  }

  // 统一冻结设置：仅当两端结构对齐且前 ySplit 行全部保持未变位置时保留冻结，避免两侧冻结错位
  const baseFreeze = left.sheet?.freeze ?? right.sheet?.freeze
  let freeze = { xSplit: 0, ySplit: 0, startRow: -1, startColumn: -1 }
  if (baseFreeze && (baseFreeze.xSplit > 0 || baseFreeze.ySplit > 0)) {
    const canKeepY = baseFreeze.ySplit === 0 || (
      !options.onlyDiffRows
      && displayRows.length >= baseFreeze.ySplit
      && displayRows.slice(0, baseFreeze.ySplit).every((r, idx) => r.leftRow === idx && r.rightRow === idx)
    )
    const ySplit = canKeepY ? baseFreeze.ySplit : 0
    const xSplit = baseFreeze.xSplit
    freeze = {
      xSplit,
      ySplit,
      startRow: ySplit > 0 ? ySplit : -1,
      startColumn: xSplit > 0 ? xSplit : -1,
    }
  }

  const stamp = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`
  const makeWorkbook = (
    side: 'left' | 'right',
    srcWb: IWorkbookData | null,
    srcSheet: Partial<IWorksheetData> | null,
    styles: Record<string, IStyleData>,
    cellData: IWorksheetData['cellData'],
    rowData: IWorksheetData['rowData'],
    columnData: IWorksheetData['columnData'],
    mergeData: IRange[],
  ): IWorkbookData => ({
    id: `cmp-${side}-${stamp}`,
    name: srcWb?.name ?? sheetTitle,
    appVersion: '1.3.1',
    locale: 'zhCN' as IWorkbookData['locale'],
    dateSystem: srcWb?.dateSystem ?? ('date1900' as IWorkbookData['dateSystem']),
    defaultStyle: srcWb?.defaultStyle ?? { ff: 'Arial', fs: 11, vt: 3 },
    styles,
    sheetOrder: [`cmp-sheet-${side}`],
    sheets: {
      [`cmp-sheet-${side}`]: {
        id: `cmp-sheet-${side}`,
        name: srcSheet?.name ?? sheetTitle,
        tabColor: srcSheet?.tabColor ?? '',
        hidden: 0,
        rowCount,
        columnCount,
        defaultRowHeight,
        defaultColumnWidth,
        zoomRatio: 1,
        scrollTop: 0,
        scrollLeft: 0,
        freeze,
        rowHeader: { width: 48, hidden: 0 },
        columnHeader: { height: 24, hidden: 0 },
        showGridlines: 1,
        rightToLeft: srcSheet?.rightToLeft ?? 0,
        cellData,
        rowData,
        columnData,
        mergeData,
      },
    },
  })

  return {
    leftWorkbook: makeWorkbook('left', left.workbook, left.sheet, leftStyles, leftCellData, leftRowData, leftColData, leftMerges.merges),
    rightWorkbook: makeWorkbook('right', right.workbook, right.sheet, rightStyles, rightCellData, rightRowData, rightColData, rightMerges.merges),
    diffs,
    cellDiffMap,
    rowMap: displayRows,
    stats,
    effectiveAlignMode: effectiveMode,
    notices,
  }
}
