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

// Keep preview work bounded before allocating a large browser-side workbook.
export const PREVIEW_LIMITS = {
  fileBytes: 25 * 1024 * 1024,
  unpackedBytes: 120 * 1024 * 1024,
  sheets: 100,
  rows: 100_000,
  columns: 512,
  cells: 500_000,
} as const

export class WorkbookPreviewError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'WorkbookPreviewError'
  }
}
