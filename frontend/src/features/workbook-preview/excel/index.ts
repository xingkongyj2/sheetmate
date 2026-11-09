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
    if (view.getUint16(cursor + 8, true) & 1) throw new WorkbookPreviewError('这个 Excel 文件经过加密，请下载后在 Excel 中打开。')
    unpacked += view.getUint32(cursor + 24, true)
    if (unpacked > PREVIEW_LIMITS.unpackedBytes) throw new WorkbookPreviewError('文件解压后的内容过大，暂不支持浏览器预览，请下载原文件查看。')
    const length = view.getUint16(cursor + 28, true)
    const extra = view.getUint16(cursor + 30, true), comment = view.getUint16(cursor + 32, true)
    if (cursor + 46 + length + extra + comment > view.byteLength) throw new WorkbookPreviewError('Excel 文件目录不完整，请下载原文件检查。')
    const name = decoder.decode(new Uint8Array(buffer, cursor + 46, length))
    if (/^xl\/charts\//i.test(name)) warnings.add('文件中的图表暂不在表格预览中显示，请下载原文件查看。')
    if (/^xl\/pivotTables\//i.test(name)) warnings.add('数据透视表仅显示文件中已保存的单元格结果。')
    if (/^xl\/drawings\//i.test(name)) warnings.add('图片、图形等绘图对象暂不在表格预览中显示，请下载原文件查看。')
    if (/^xl\/(?:comments?\d*|comments\/|threadedComments\/)/i.test(name)) warnings.add('单元格批注暂不在表格预览中显示，请下载原文件查看。')
    if (/^xl\/tables\//i.test(name)) warnings.add('Excel 表格的筛选和主题样式暂未应用，预览保留单元格内容与基础样式。')
    if (/vbaProject\.bin$/i.test(name)) warnings.add('此预览只读取单元格，宏不会运行。')
    cursor += 46 + length + extra + comment
  }
  return [...warnings]
}

function attribute(tag: string, name: string): string | undefined {
  return new RegExp(`\\b${name}\\s*=\\s*(["'])([\\s\\S]*?)\\1`).exec(tag)?.[2]
}

function withoutNodes(xml: string, names: string[]): string {
  for (const name of names) {
    // Workbooks may use an explicit namespace prefix instead of a default one.
    const tag = `(?:[\\w.-]+:)?${name}`
    xml = xml.replace(new RegExp(`<${tag}\\b[^>]*(?:\\/\\s*>|>[\\s\\S]*?<\\/${tag}\\s*>)`, 'gi'), '')
  }
  return xml
}

function rangeCorner(value: string): { row: number; column: number } | undefined {
  const match = /^\$?([A-Z]+)\$?(\d+)$/i.exec(value)
  if (!match) return undefined
  let column = 0
  for (const letter of match[1]!.toUpperCase()) column = column * 26 + letter.charCodeAt(0) - 64
  return { row: Number(match[2]), column }
}

/** ExcelJS expands merged cells during load, before snapshot limits can run. */
function checkWorksheetXml(xml: string, budget: { cells: number; mergedCells: number; merges: number }): void {
  for (const _cell of xml.matchAll(/<(?:[\w.-]+:)?c\b[^>]*>/gi)) {
    if (++budget.cells > PREVIEW_LIMITS.cells) throw new WorkbookPreviewError('工作簿单元格过多，已超出完整样式预览范围，请使用数据预览或下载原文件。')
  }
  for (const merge of xml.matchAll(/<(?:[\w.-]+:)?mergeCell\b[^>]*>/gi)) {
    const [first, last = first] = (attribute(merge[0], 'ref') ?? '').split(':')
    const start = rangeCorner(first ?? ''), end = rangeCorner(last ?? '')
    if (!start || !end) continue
    budget.mergedCells += (Math.abs(end.row - start.row) + 1) * (Math.abs(end.column - start.column) + 1)
    if (++budget.merges > 5_000 || budget.mergedCells > PREVIEW_LIMITS.cells
      || Math.max(start.row, end.row) > PREVIEW_LIMITS.rows || Math.max(start.column, end.column) > PREVIEW_LIMITS.columns) {
      throw new WorkbookPreviewError('工作簿合并区域过大或过多，已超出完整样式预览范围，请使用数据预览或下载原文件。')
    }
  }
}

/** Remove objects the viewer cannot render from a temporary ZIP, leaving the original untouched. */
async function preparePreviewArchive(buffer: ArrayBuffer, warnings: string[]): Promise<ArrayBuffer> {
  const { default: JSZip } = await import('jszip')
  const zip = await JSZip.loadAsync(buffer)
  const budget = { cells: 0, mergedCells: 0, merges: 0 }
  let changed = false, sheets = 0
  for (const entry of Object.values(zip.files)) {
    if (entry.dir) continue
    const name = entry.name.replace(/^\//, '')
    // ExcelJS 4.4 tries to reconcile these objects even when their sheet nodes
    // are ignored. Legal chart/comment/table relationships can then throw.
    if (/^xl\/(?:drawings|media|charts|tables|pivotTables|pivotCache|comments|threadedComments|persons)\//i.test(name)
      || /^xl\/(?:comments\d+\.xml|vbaProject\.bin)$/i.test(name)) {
      zip.remove(entry.name)
      changed = true
      continue
    }
    if (/^xl\/worksheets\/[^/]+\.xml$/i.test(name)) {
      if (++sheets > PREVIEW_LIMITS.sheets) throw new WorkbookPreviewError(`工作表超过 ${PREVIEW_LIMITS.sheets} 张的完整样式预览范围，请使用数据预览或下载原文件。`)
      const original = await entry.async('string')
      checkWorksheetXml(original, budget)
      const cleaned = withoutNodes(original, ['drawing', 'legacyDrawing', 'legacyDrawingHF', 'picture', 'tableParts', 'dataValidations', 'conditionalFormatting', 'extLst'])
      if (/<(?:[\w.-]+:)?conditionalFormatting\b/i.test(original)) warnings.push('条件格式暂未应用，预览显示单元格的基础样式。')
      if (cleaned !== original) { zip.file(entry.name, cleaned); changed = true }
    } else if (/^xl\/worksheets\/_rels\/[^/]+\.rels$/i.test(name)) {
      const original = await entry.async('string')
      const cleaned = original.replace(/<(?:[\w.-]+:)?Relationship\b[^>]*\/\s*>/gi, (tag) => {
        const type = attribute(tag, 'Type') ?? ''
        return /\/(?:drawing|vmlDrawing|comments|threadedComment|table|image|pivotTable|control|ctrlProp|oleObject)$/i.test(type) ? '' : tag
      })
      if (cleaned !== original) { zip.file(entry.name, cleaned); changed = true }
    } else if (/^xl\/workbook\.xml$/i.test(name)) {
      // Named ranges are not used by this cached-value-only viewer. ExcelJS
      // otherwise expands every address in a large named range on import.
      const original = await entry.async('string')
      const cleaned = withoutNodes(original, ['definedNames'])
      if (cleaned !== original) { zip.file(entry.name, cleaned); changed = true }
    }
  }
  return changed ? zip.generateAsync({ type: 'arraybuffer', compression: 'DEFLATE', compressionOptions: { level: 1 } }) : buffer
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
    const previewBuffer = await preparePreviewArchive(buffer, archiveWarnings)
    await source.xlsx.load(previewBuffer, { ignoreNodes: ['dataValidations', 'conditionalFormatting', 'extLst'] })
    const parsed = buildWorkbookSnapshot(source, filename)
    parsed.warnings = [...new Set([...archiveWarnings, ...parsed.warnings])]
    return parsed
  } catch (error) {
    if (error instanceof WorkbookPreviewError) throw error
    throw new WorkbookPreviewError('这个工作簿的完整样式暂时无法解析，请使用数据预览或下载原文件查看。')
  }
}
