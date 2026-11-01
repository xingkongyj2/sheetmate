import { buildWorkbookSnapshot } from './snapshot.ts'
import { PREVIEW_LIMITS, WorkbookPreviewError } from './types.ts'
import type { ParsedWorkbook } from './types.ts'

export { buildWorkbookSnapshot } from './snapshot.ts'
export { PREVIEW_LIMITS, WorkbookPreviewError } from './types.ts'
export type { ParsedWorkbook } from './types.ts'

/** Inspect ZIP metadata before ExcelJS expands the workbook in browser memory. */
function inspectArchive(buffer: ArrayBuffer): string[] {
  const view = new DataView(buffer)
  let end = -1
  for (let offset = buffer.byteLength - 22; offset >= Math.max(0, buffer.byteLength - 65_557); offset--) {
    if (view.getUint32(offset, true) === 0x06054b50) { end = offset; break }
  }
  if (end < 0) throw new WorkbookPreviewError('文件不是有效的 Excel 工作簿，可能已损坏或经过加密。')
  let cursor = view.getUint32(end + 16, true)
  const entries = view.getUint16(end + 10, true)
  let unpacked = 0
  const warnings = new Set<string>()
  const decoder = new TextDecoder()
  for (let index = 0; index < entries; index++) {
    if (cursor + 46 > view.byteLength || view.getUint32(cursor, true) !== 0x02014b50) throw new WorkbookPreviewError('Excel 文件目录不完整，请下载原文件检查。')
    unpacked += view.getUint32(cursor + 24, true)
    if (unpacked > PREVIEW_LIMITS.unpackedBytes) throw new WorkbookPreviewError('文件解压后的内容过大，暂不支持浏览器预览，请下载原文件查看。')
    const length = view.getUint16(cursor + 28, true)
    const extra = view.getUint16(cursor + 30, true), comment = view.getUint16(cursor + 32, true)
    if (cursor + 46 + length + extra + comment > view.byteLength) throw new WorkbookPreviewError('Excel 文件目录不完整，请下载原文件检查。')
    const name = decoder.decode(new Uint8Array(buffer, cursor + 46, length))
    if (/^xl\/charts\//i.test(name)) warnings.add('文件中的图表暂不在表格预览中显示，请下载原文件查看。')
    if (/^xl\/pivotTables\//i.test(name)) warnings.add('数据透视表仅显示文件中已保存的单元格结果。')
    if (/^xl\/drawings\//i.test(name)) warnings.add('图片、图形等绘图对象暂不在表格预览中显示，请下载原文件查看。')
    if (/vbaProject\.bin$/i.test(name)) warnings.add('此预览只读取单元格，宏不会运行。')
    cursor += 46 + length + extra + comment
  }
  return [...warnings]
}

/** All parsing and conversion happens locally in the browser. */
export async function parseWorkbook(buffer: ArrayBuffer, filename: string): Promise<ParsedWorkbook> {
  if (!/\.(xlsx|xlsm)$/i.test(filename)) throw new WorkbookPreviewError('原样式预览支持 .xlsx 和 .xlsm 文件，请使用数据预览或下载此文件。')
  if (!buffer.byteLength) throw new WorkbookPreviewError('文件内容为空，无法预览。')
  if (buffer.byteLength > PREVIEW_LIMITS.fileBytes) throw new WorkbookPreviewError(`文件超过 ${PREVIEW_LIMITS.fileBytes / 1024 / 1024} MB，暂不支持浏览器预览，请下载原文件查看。`)
  const archiveWarnings = inspectArchive(buffer)
  try {
    const { default: ExcelJS } = await import('exceljs')
    const source = new ExcelJS.Workbook()
    await source.xlsx.load(buffer)
    const parsed = buildWorkbookSnapshot(source, filename)
    parsed.warnings = [...new Set([...archiveWarnings, ...parsed.warnings])]
    return parsed
  } catch (error) {
    if (error instanceof WorkbookPreviewError) throw error
    throw new WorkbookPreviewError('无法读取这个 Excel 文件，文件可能已损坏、加密或包含暂不支持的结构。请下载原文件检查。')
  }
}
