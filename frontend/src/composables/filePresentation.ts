import type { FileInfo, TableBrief } from '@/api/types'
import type { FileArtifact, TableArtifact, UiMessage } from './chatModel'
import { isDeliveredFile, progressDeliveryFiles } from './deliveryFiles'
import { recipeDeliveries } from './recipeDeliveries'

export interface FilePresentationContext {
  tables?: TableBrief[]
  files?: FileInfo[]
}

export function fileKindLabel(kind: unknown): string {
  switch (kind) {
    case 'input': return '输入文件'
    case 'expected': return '期望结果'
    case 'output': return '输出文件'
    default: return ''
  }
}

export function deliveredFiles(message: UiMessage): FileArtifact[] {
  return [...new Map(message.artifacts.flatMap((artifact) =>
    artifact.kind === 'file' ? [artifact.file] : artifact.kind === 'recipe' ? progressDeliveryFiles(artifact.progress) : [],
  ).filter(isDeliveredFile).map((file) => [file.file_id, file])).values()]
}

function exportedTables(params: Record<string, unknown>): string[] {
  if (!Array.isArray(params.sheets)) return []
  return params.sheets.flatMap((sheet) => typeof sheet === 'string' ? [sheet] :
    sheet && typeof sheet === 'object' && typeof sheet.table === 'string' ? [sheet.table] : [])
}

/** Resolve files through their identity or originating tool; never guess from a workbook's display name. */
export function fileIdForTable(name: string, tables: TableBrief[], messages: UiMessage[]): string | undefined {
  const current = tables.find((table) => table.name === name)
  if (current?.file_id) return current.file_id
  for (const message of [...messages].reverse()) {
    const files = deliveredFiles(message)
    const table = [...message.artifacts].reverse().find((artifact) => artifact.kind === 'table' && artifact.table.name === name)
    if (table?.kind === 'table') {
      if (table.table.file_id) return table.table.file_id
      const matching = files.filter((file) => file.toolCallId && file.toolCallId === table.table.toolCallId)
      if (matching.length === 1) return matching[0]!.file_id
      // Legacy messages lack tool ids on the table model; one result file is unambiguous.
      if (table.table.role === 'result' && !table.table.toolCallId && files.length === 1 && !files[0]!.toolCallId) return files[0]!.file_id
    }
    const exported = [...message.timeline].reverse().find((item) => item.kind === 'tool' && item.tool === 'export_excel' &&
      item.status === 'ok' && exportedTables(item.params).includes(name))
    if (exported?.kind === 'tool') {
      const file = files.find((candidate) => candidate.toolCallId === exported.id) ??
        (files.length === 1 && !files[0]!.toolCallId ? files[0] : undefined)
      if (file) return file.file_id
      return undefined
    }
    // A more recent result must not open an older run's workbook with the same table name.
    if (table?.kind === 'table' && table.table.role === 'result') return undefined
  }
  return undefined
}

function fileFromTable(table: TableArtifact, context: FilePresentationContext): FileArtifact | undefined {
  const fileId = table.file_id || context.tables?.find((candidate) => candidate.name === table.name)?.file_id
  if (!fileId) return undefined
  const file = context.files?.find((candidate) => candidate.file_id === fileId)
  if (file) return { file_id: file.file_id, filename: file.filename, kind: file.kind, kind_label: file.kind_label, download_url: file.download_url, sheets: file.sheets, run_id: file.run_id ?? undefined,
    delivery_status: file.delivery_status, intermediate: file.intermediate }
  if (!table.filename) return undefined
  return { file_id: fileId, filename: table.filename, download_url: `/api/files/${encodeURIComponent(fileId)}/download` }
}

/** Explicit deliveries take precedence; reading a source file can also offer that original directly. */
export function standaloneFiles(message: UiMessage, context: FilePresentationContext = {}): FileArtifact[] {
  const recipeIds = new Set(recipeDeliveries(message).deliveries.flatMap((artifact) =>
    progressDeliveryFiles(artifact.progress).map((file) => file.file_id)))
  const explicit = deliveredFiles(message)
  const hasFileEvents = message.artifacts.some((artifact) => artifact.kind === 'file' || artifact.kind === 'recipe' &&
    !!(artifact.progress.file || artifact.progress.files?.length))
  const files = explicit.length || hasFileEvents ? explicit : message.artifacts.flatMap((artifact) => {
    if (artifact.kind !== 'table') return []
    const file = fileFromTable(artifact.table, context)
    return file ? [file] : []
  })
  return [...new Map(files.filter((file) => {
    if (recipeIds.has(file.file_id)) return false
    const status = message.deliveryStatuses?.[file.file_id]
    return isDeliveredFile({ ...file, delivery_status: status ?? file.delivery_status })
  }).map((file) => [file.file_id, file])).values()]
}
