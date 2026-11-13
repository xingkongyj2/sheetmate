/** 「用新文件执行」里，每个流程输入该用哪个文件、哪张工作表（流程已经录制好，不需要再做匹配）。 */
import type { FileInfo, RecipeJSON, SheetBrief } from '@/api/types'

export type RunInput = RecipeJSON['inputs'][number]

/** 录制时绑定的样例文件：优先按输入绑定的文件 ID，其次按录制时的文件名。 */
export function sampleFile(recipe: Pick<RecipeJSON, 'learned_from'>, input: RunInput, files: FileInfo[]): FileInfo | null {
  const bindings = recipe.learned_from?.input_bindings as Record<string, { file_id?: string | null }> | undefined
  const bound = bindings?.[input.alias]?.file_id
  return files.find((file) => file.file_id === bound)
    ?? (input.source_file ? files.find((file) => file.filename === input.source_file) : undefined)
    ?? null
}

/** 数据表输入要读哪张工作表：和录制时同名的优先，其次列名重合最多的，最后是第一张。 */
export function pickSheet(input: RunInput, file: FileInfo | null): string {
  const sheets = file?.sheets ?? []
  if (!sheets.length) return ''
  if (input.sheet && sheets.some((item) => item.sheet === input.sheet)) return input.sheet
  const overlap = (sheet: SheetBrief) => input.expected_columns.filter((name) => sheet.columns.some((column) => column.name === name)).length
  return sheets.reduce((best, item) => (overlap(item) > overlap(best) ? item : best), sheets[0]).sheet
}

/** 传给后端的输入键：完整文件是文件 ID，数据表是「文件 ID::工作表」。 */
export function inputKey(input: RunInput, file: FileInfo | null, sheet: string): string {
  if (!file) return ''
  if (input.kind === 'file') return file.file_id
  return sheet ? `${file.file_id}::${sheet}` : ''
}

/** 选中的工作表里缺少的必需列（只是提示：列名对应关系在后端还会再处理一遍）。 */
export function missingColumns(input: RunInput, file: FileInfo | null, sheet: string): string[] {
  if (input.kind === 'file' || !file) return []
  const columns = file.sheets.find((item) => item.sheet === sheet)?.columns ?? []
  return input.required_columns.filter((name) => !columns.some((column) => column.name === name))
}
