import type { IWorkbookData } from '@univerjs/core'

/** 单元格级差异类型 */
export type DiffKind = 'added' | 'deleted' | 'modified' | 'formula'

/** 行对齐模式：智能行对齐（支持增删行插入占位） / 按原始坐标对齐 */
export type AlignMode = 'smart' | 'coordinate'

/** 工作表配对状态 */
export type SheetMatchKind = 'matched' | 'added' | 'deleted' | 'manual'

/** 比对目标来源 */
export type CompareSource =
  | { kind: 'remote'; fileId: string }
  | { kind: 'local'; blobUrl: string; name: string }
  | { kind: 'file'; file: File }

/** 比对面板的一对工作表 */
export interface SheetPair {
  key: string
  kind: SheetMatchKind
  name: string
  leftIndex: number | null
  rightIndex: number | null
  leftName: string | null
  rightName: string | null
}

/** 渲染后的每一行对应的原始行信息 */
export interface AlignedRowMeta {
  /** 渲染行在左表中的原始 0-based 行号；新增行或折叠行为 null */
  leftRow: number | null
  /** 渲染行在右表中的原始 0-based 行号；删除行或折叠行为 null */
  rightRow: number | null
  kind: 'same' | 'modified' | 'added' | 'deleted' | 'fold'
  /** 仅 fold 行有值：被省略的未变化行数 */
  omittedCount?: number
}

/** 单条单元格差异明细 */
export interface CellDiffEntry {
  id: string
  kind: DiffKind
  /** 在比对展示快照中的 0-based 行号 */
  displayRow: number
  /** 0-based 列号 */
  col: number
  /** 改前原始 0-based 行号；新增行为 null */
  leftRow: number | null
  /** 改后原始 0-based 行号；删除行为 null */
  rightRow: number | null
  /** Excel 列字母（如 A、B、AA） */
  colLabel: string
  /** 可读坐标（如 C12、C12 → C14、→ C14、C12 →） */
  addressLabel: string
  beforeValue: string
  afterValue: string
  beforeFormula?: string
  afterFormula?: string
  rowKind: 'same' | 'modified' | 'added' | 'deleted'
}

/** 工作表级差异统计 */
export interface SheetDiffStats {
  total: number
  added: number
  deleted: number
  modified: number
  formula: number
  addedRows: number
  deletedRows: number
  modifiedRows: number
  truncated?: boolean
}

/** 比对视图选项 */
export interface DiffOptions {
  alignMode: AlignMode
  onlyDiffRows: boolean
  showFormulas: boolean
  preserveStyles: boolean
}

/** 单张工作表比对结果（供左右两个 Univer 实例直接渲染） */
export interface SheetDiffResult {
  leftWorkbook: IWorkbookData
  rightWorkbook: IWorkbookData
  diffs: CellDiffEntry[]
  /** `${displayRow}:${col}` -> 差异条目，便于点选单元格时立即展示改前/改后详情 */
  cellDiffMap: Record<string, CellDiffEntry>
  rowMap: AlignedRowMeta[]
  stats: SheetDiffStats
  effectiveAlignMode: AlignMode
  notices: string[]
}
