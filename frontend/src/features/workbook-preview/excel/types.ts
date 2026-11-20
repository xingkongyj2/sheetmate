import type { IWorkbookData } from '@univerjs/core'

export interface ParsedWorkbook {
  workbook: IWorkbookData
  warnings: string[]
  stats: {
    sheetCount: number
    cellCount: number
    formulaCount: number
  }
}

/** 工作簿里的一张工作表：只有目录信息，内容要用 readSheet 按需读取。 */
export interface SheetMeta {
  index: number
  name: string
  /** 0 显示，1 隐藏，2 深度隐藏 */
  hidden: 0 | 1 | 2
}

/**
 * 打开的工作簿：先只读出工作表目录（很快），哪张表要看再解析哪张。
 * 十几张表、每张几千行的文件，不用为了看第一张表把全部内容解析、渲染一遍。
 */
export interface WorkbookSession {
  sheets: SheetMeta[]
  /** 文件保存时停留的工作表（在 sheets 里的位置） */
  active: number
  /** 整个文件层面的提示（图表、批注、加密等） */
  warnings: string[]
  /** 读取并转换一张工作表，返回只含这一张表的快照；这张表的提示放在 warnings 里 */
  readSheet(index: number): Promise<ParsedWorkbook>
}

// Keep preview work bounded before allocating a large browser-side workbook.
export const PREVIEW_LIMITS = {
  fileBytes: 25 * 1024 * 1024,
  unpackedBytes: 120 * 1024 * 1024,
  sheets: 100,
  rows: 100_000,
  columns: 512,
  /** 单张工作表的上限（按表加载后不再按整个工作簿计） */
  cells: 500_000,
} as const

export class WorkbookPreviewError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'WorkbookPreviewError'
  }
}
