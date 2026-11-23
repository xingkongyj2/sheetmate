import type JSZip from 'jszip'

import { buildWorkbookSnapshot } from './snapshot.ts'
import { PREVIEW_LIMITS, WorkbookPreviewError } from './types.ts'
import type { ParsedWorkbook, SheetMeta, WorkbookSession } from './types.ts'

export { buildWorkbookSnapshot } from './snapshot.ts'
export { PREVIEW_LIMITS, WorkbookPreviewError } from './types.ts'
export type { ParsedWorkbook, SheetMeta, WorkbookSession } from './types.ts'

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

type JSZipInstance = JSZip

const STUB_SHEET = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData/></worksheet>'
// ExcelJS 4.4 tries to reconcile these objects even when their sheet nodes are ignored. Legal chart/comment/table relationships can then throw.
const UNRENDERABLE_PART = /^xl\/(?:drawings|media|charts|tables|pivotTables|pivotCache|comments|threadedComments|persons)\/|^xl\/(?:comments\d+\.xml|vbaProject\.bin)$/i
const relsPathOf = (sheetPath: string) => sheetPath.replace(/([^/]+)$/, '_rels/$1.rels')

function decodeXml(text: string): string {
  return text.replace(/&(?:#(\d+)|#x([0-9a-f]+)|(lt|gt|amp|quot|apos));/gi, (_all, dec, hex, named) => {
    if (named) return { lt: '<', gt: '>', amp: '&', quot: '"', apos: "'" }[String(named).toLowerCase()]!
    return String.fromCodePoint(Number(dec ?? Number.parseInt(hex, 16)))
  })
}

function resolveTarget(target: string): string {
  if (target.startsWith('/')) return target.slice(1)
  const out: string[] = []
  for (const part of `xl/${target}`.split('/')) {
    if (part === '..') out.pop()
    else if (part !== '.') out.push(part)
  }
  return out.join('/')
}

interface SheetEntry extends SheetMeta { path: string }

/** 只读 workbook.xml 和它的关系文件，得到工作表目录和各自的文件位置，不碰工作表本身的内容。 */
async function readDirectory(zip: JSZipInstance): Promise<{ sheets: SheetEntry[]; active: number }> {
  const text = async (path: string) => zip.file(path)?.async('string')
  const workbookXml = await text('xl/workbook.xml')
  const relsXml = await text('xl/_rels/workbook.xml.rels')
  if (!workbookXml || !relsXml) throw new WorkbookPreviewError('Excel 文件缺少工作表目录，请使用数据预览或下载原文件查看。')
  const targets = new Map<string, { type: string; target: string }>()
  for (const tag of relsXml.matchAll(/<(?:[\w.-]+:)?Relationship\b[^>]*>/gi)) {
    const id = attribute(tag[0], 'Id'), target = attribute(tag[0], 'Target')
    if (id && target) targets.set(id, { type: attribute(tag[0], 'Type') ?? '', target })
  }
  const sheets: SheetEntry[] = []
  const listed: Array<number | undefined> = []         // workbook.xml 里每一项对应的工作表位置（图表页之类不是工作表，没有）
  for (const tag of workbookXml.matchAll(/<(?:[\w.-]+:)?sheet\b[^>]*>/gi)) {
    const id = /\b[\w.-]+:id\s*=\s*(["'])([\s\S]*?)\1/.exec(tag[0])?.[2]
    const rel = id ? targets.get(id) : undefined
    if (!rel || !/\/worksheet$/i.test(rel.type)) { listed.push(undefined); continue }
    const state = attribute(tag[0], 'state')
    listed.push(sheets.length)
    sheets.push({ index: sheets.length, name: decodeXml(attribute(tag[0], 'name') ?? `Sheet${sheets.length + 1}`), hidden: state === 'veryHidden' ? 2 : state === 'hidden' ? 1 : 0, path: resolveTarget(rel.target) })
  }
  if (!sheets.length) throw new WorkbookPreviewError('这个文件没有可预览的工作表。')
  if (sheets.length > PREVIEW_LIMITS.sheets) throw new WorkbookPreviewError(`工作表超过 ${PREVIEW_LIMITS.sheets} 张的完整样式预览范围，请使用数据预览或下载原文件。`)
  const activeTab = Number(attribute(/<(?:[\w.-]+:)?workbookView\b[^>]*>/i.exec(workbookXml)?.[0] ?? '', 'activeTab') ?? 0)
  const wanted = listed[Number.isFinite(activeTab) ? activeTab : 0]
  const visible = sheets.find((sheet) => sheet.hidden === 0)
  const active = wanted !== undefined && sheets[wanted]!.hidden === 0 ? wanted : (visible ?? sheets[0]!).index
  return { sheets, active }
}

/** 清掉预览用不到的对象，并检查这张表有没有超出预览范围。 */
function cleanWorksheetXml(original: string, warnings: Set<string>, budget?: { cells: number; mergedCells: number; merges: number }): string {
  checkWorksheetXml(original, budget ?? { cells: 0, mergedCells: 0, merges: 0 })
  const cleaned = withoutNodes(original, ['drawing', 'legacyDrawing', 'legacyDrawingHF', 'picture', 'tableParts', 'dataValidations', 'conditionalFormatting', 'extLst'])
  if (/<(?:[\w.-]+:)?conditionalFormatting\b/i.test(original)) warnings.add('条件格式暂未应用，预览显示单元格的基础样式。')
  return cleaned
}

/** 提取工作表 XML 中没有缓存结果（<v> 缺失）且包含跨表引用（!）的公式文本。 */
function uncachedCrossSheetFormulas(xml: string): string[] {
  const formulas: string[] = []
  for (const cell of xml.matchAll(/<(?:[\w.-]+:)?c\b[^>]*>([\s\S]*?)<\/(?:[\w.-]+:)?c>/gi)) {
    const inner = cell[1] ?? ''
    if (!/<(?:[\w.-]+:)?f\b/i.test(inner)) continue
    if (/<(?:[\w.-]+:)?v\b[^>]*>[^<]+<\/(?:[\w.-]+:)?v>/i.test(inner)) continue
    const match = /<(?:[\w.-]+:)?f\b[^>]*>([\s\S]*?)<\/(?:[\w.-]+:)?f>/i.exec(inner)
    if (!match?.[1]) continue
    const text = decodeXml(match[1])
    if (text.includes('!')) formulas.push(text)
  }
  return formulas
}

/** 找出跨表未缓存公式所依赖的其他工作表（供 Univer 实时求值），在预览单元格预算内按需加载。 */
async function collectFormulaDependencies(
  zip: JSZipInstance,
  target: SheetEntry,
  targetXml: string,
  allSheets: SheetEntry[],
  budget: { cells: number; mergedCells: number; merges: number },
): Promise<{ entries: SheetEntry[]; cleanedXml: Map<string, string> }> {
  const cleanedXml = new Map<string, string>()
  const entries: SheetEntry[] = []
  const visited = new Set<string>([target.path])
  const queue: Array<{ sheet: SheetEntry; xml: string; depth: number }> = [{ sheet: target, xml: targetXml, depth: 0 }]

  while (queue.length) {
    const current = queue.shift()!
    if (current.depth >= 3) continue
    const formulas = uncachedCrossSheetFormulas(current.xml)
    if (!formulas.length) continue
    for (const candidate of allSheets) {
      if (visited.has(candidate.path)) continue
      const escaped = candidate.name.replace(/'/g, "''")
      const referenced = formulas.some((formula) => formula.includes(`${candidate.name}!`) || formula.includes(`'${escaped}'!`))
      if (!referenced) continue
      visited.add(candidate.path)
      const rawXml = await zip.file(candidate.path)?.async('string')
      if (!rawXml) continue
      const nextBudget = { ...budget }
      try {
        const cleaned = cleanWorksheetXml(rawXml, new Set(), nextBudget)
        budget.cells = nextBudget.cells
        budget.mergedCells = nextBudget.mergedCells
        budget.merges = nextBudget.merges
        cleanedXml.set(candidate.path, cleaned)
        entries.push(candidate)
        queue.push({ sheet: candidate, xml: rawXml, depth: current.depth + 1 })
      } catch {
        // 依赖表超出预览体量上限时跳过，不影响当前主表预览
      }
    }
  }
  return { entries, cleanedXml }
}

function cleanWorksheetRels(original: string): string {
  return original.replace(/<(?:[\w.-]+:)?Relationship\b[^>]*\/\s*>/gi, (tag) => {
    const type = attribute(tag, 'Type') ?? ''
    return /\/(?:drawing|vmlDrawing|comments|threadedComment|table|image|pivotTable|control|ctrlProp|oleObject)$/i.test(type) ? '' : tag
  })
}

/**
 * 打开工作簿：只读工作表目录，立即返回。
 * 看哪张表才解析哪张：把别的工作表换成空壳再交给 ExcelJS，它就只需要解析这一张表的内容（共享字符串、样式仍会读，但只有一份）。
 * 整本解析的耗时和内存都随全部工作表的单元格总数增长；按表加载后只和当前这张表有关。
 */
export async function openWorkbook(buffer: ArrayBuffer, filename: string): Promise<WorkbookSession> {
  if (!/\.(xlsx|xlsm)$/i.test(filename)) throw new WorkbookPreviewError('原样式预览支持 .xlsx 和 .xlsm 文件，请使用数据预览或下载此文件。')
  if (!buffer.byteLength) throw new WorkbookPreviewError('文件内容为空，无法预览。')
  if (buffer.byteLength > PREVIEW_LIMITS.fileBytes) throw new WorkbookPreviewError(`文件超过 ${PREVIEW_LIMITS.fileBytes / 1024 / 1024} MB，暂不支持浏览器预览，请下载原文件查看。`)
  const archiveWarnings = inspectArchive(buffer)
  let zip: JSZipInstance, directory: Awaited<ReturnType<typeof readDirectory>>, JSZipCtor: typeof JSZip
  try {
    ;({ default: JSZipCtor } = await import('jszip'))
    zip = await JSZipCtor.loadAsync(buffer)
    directory = await readDirectory(zip)
  } catch (error) {
    if (error instanceof WorkbookPreviewError) throw error
    throw new WorkbookPreviewError('这个工作簿的完整样式暂时无法解析，请使用数据预览或下载原文件查看。')
  }
  void import('exceljs').catch(() => undefined)         // 解析库的代码先加载起来，读第一张表时就不用等
  const sheetPaths = new Set(directory.sheets.map((sheet) => sheet.path))
  const relsPaths = new Set(directory.sheets.map((sheet) => relsPathOf(sheet.path)))
  const shared = new Map<string, string | Uint8Array>()        // 每张表都要带上的公共部分（样式、共享字符串、主题…）：只解压一次
  async function sharedParts(): Promise<Map<string, string | Uint8Array>> {
    if (shared.size) return shared
    for (const entry of Object.values(zip.files)) {
      if (entry.dir) continue
      const name = entry.name.replace(/^\//, '')
      if (UNRENDERABLE_PART.test(name) || sheetPaths.has(name) || relsPaths.has(name)) continue
      if (/^xl\/workbook\.xml$/i.test(name)) {
        // Named ranges are not used by this cached-value-only viewer. ExcelJS otherwise expands every address in a large named range on import.
        shared.set(entry.name, withoutNodes(await entry.async('string'), ['definedNames']))
      } else shared.set(entry.name, await entry.async('uint8array'))
    }
    return shared
  }

  async function readSheet(index: number): Promise<ParsedWorkbook> {
    const target = directory.sheets[index]
    if (!target) throw new WorkbookPreviewError('没有找到这张工作表。')
    try {
      const warnings = new Set<string>()
      const budget = { cells: 0, mergedCells: 0, merges: 0 }
      const targetRawXml = await zip.file(target.path)?.async('string')
      if (!targetRawXml) throw new WorkbookPreviewError('没有找到这张工作表。')
      const targetCleanedXml = cleanWorksheetXml(targetRawXml, warnings, budget)
      const deps = await collectFormulaDependencies(zip, target, targetRawXml, directory.sheets, budget)
      const out = new JSZipCtor()
      for (const [name, content] of await sharedParts()) out.file(name, content)
      for (const entry of Object.values(zip.files)) {
        if (entry.dir) continue
        const name = entry.name.replace(/^\//, '')
        if (sheetPaths.has(name)) {
          if (name === target.path) out.file(entry.name, targetCleanedXml)
          else if (deps.cleanedXml.has(name)) out.file(entry.name, deps.cleanedXml.get(name)!)
          else out.file(entry.name, STUB_SHEET)
        } else if (name === relsPathOf(target.path)) {
          out.file(entry.name, cleanWorksheetRels(await entry.async('string')))
        }
      }
      const prepared = await out.generateAsync({ type: 'arraybuffer', compression: 'STORE' })
      const { default: ExcelJS } = await import('exceljs')
      const source = new ExcelJS.Workbook()
      await source.xlsx.load(prepared, { ignoreNodes: ['dataValidations', 'conditionalFormatting', 'extLst'] })
      const resolveWorksheet = (meta: SheetEntry) =>
        source.worksheets[meta.index]?.name === meta.name ? source.worksheets[meta.index] : source.getWorksheet(meta.name)
      const worksheet = resolveWorksheet(target)
      if (!worksheet) throw new WorkbookPreviewError('没有找到这张工作表。')
      const depWorksheets = deps.entries.map(resolveWorksheet).filter((ws): ws is NonNullable<typeof ws> => Boolean(ws))
      const parsed = depWorksheets.length
        ? buildWorkbookSnapshot(source, filename, { include: [worksheet, ...depWorksheets] })
        : buildWorkbookSnapshot(source, filename, { only: worksheet })
      parsed.warnings = [...new Set([...warnings, ...parsed.warnings])]
      return parsed
    } catch (error) {
      if (error instanceof WorkbookPreviewError) throw error
      throw new WorkbookPreviewError('这张工作表的完整样式暂时无法解析，请使用数据预览或下载原文件查看。')
    }
  }

  return {
    sheets: directory.sheets.map(({ index, name, hidden }) => ({ index, name, hidden })),
    active: directory.active,
    warnings: archiveWarnings,
    readSheet,
  }
}
