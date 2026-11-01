import type { TableArtifact, UiMessage } from './chatModel'

/** Inline tables are complete, small calculation results; workbooks use file previews. */
export const INLINE_TABLE_LIMITS = Object.freeze({ rows: 20, cols: 8, cells: 120 })

function smallTemporaryTable(table: TableArtifact): boolean {
  if (['input', 'expected', 'result'].includes(table.role ?? '') || table.source === 'uploaded' || table.file_id) return false
  if (!(table.ephemeral || table.role === 'query' || table.role === 'derived' || table.source === 'computed')) return false
  if (!Number.isInteger(table.rows) || table.rows < 0 || table.rows > INLINE_TABLE_LIMITS.rows) return false
  if (!Number.isInteger(table.cols) || table.cols < 1 || table.cols > INLINE_TABLE_LIMITS.cols) return false
  if (table.rows * table.cols > INLINE_TABLE_LIMITS.cells || table.columns.length !== table.cols) return false
  if (table.preview.length < table.rows) return false
  return table.preview.slice(0, table.rows).every((row) => Array.isArray(row) && row.length >= table.cols)
}

function exportedTableNames(message: UiMessage): Set<string> {
  const hasFile = message.artifacts.some((artifact) => artifact.kind === 'file'
    ? !!artifact.file.file_id : artifact.kind === 'recipe' && !!artifact.progress.file?.file_id)
  const names = new Set<string>()
  if (!hasFile) return names
  for (const item of message.timeline) {
    if (item.kind !== 'tool' || item.tool !== 'export_excel' || item.status !== 'ok' || !Array.isArray(item.params.sheets)) continue
    for (const sheet of item.params.sheets) {
      const name = typeof sheet === 'string' ? sheet
        : sheet && typeof sheet === 'object' && 'table' in sheet ? sheet.table : undefined
      if (typeof name === 'string' && name) names.add(name)
    }
  }
  return names
}

export function inlineTables(message: UiMessage): TableArtifact[] {
  const tables = message.artifacts.flatMap((artifact) => artifact.kind === 'table' ? [artifact.table] : [])
  const lastPositions = new Map<string, number>()
  tables.forEach((table, index) => { if (table.name) lastPositions.set(table.name, index) })
  const exported = exportedTableNames(message)
  return tables.filter((table, index) => (!table.name || (lastPositions.get(table.name) === index && !exported.has(table.name)))
    && smallTemporaryTable(table)).map((table) => table.preview.length === table.rows
      ? table : { ...table, preview: table.preview.slice(0, table.rows) })
}

export function lastInlineTable(message: UiMessage): TableArtifact | undefined {
  return inlineTables(message).at(-1)
}
