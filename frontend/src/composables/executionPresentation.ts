import type { FileInfo, RecipeBrief, TableBrief } from '@/api/types'
import type { RecipeProgress } from './chatModel'
import { isDeliveredFile } from './deliveryFiles'

function timestamp(value?: string | null): number {
  const time = value ? Date.parse(value) : NaN
  return Number.isFinite(time) ? time : Number.POSITIVE_INFINITY
}

export function chronological<T extends { created_at?: string | null }>(items: T[]): T[] {
  return [...items].sort((a, b) => timestamp(a.created_at) - timestamp(b.created_at))
}

export function executionFileGroups(files: FileInfo[]) {
  const ordered = chronological(files)
  return [
    { key: 'input', label: '输入文件', description: '上传的原始文件、人工样例与参考资料', files: ordered.filter((file) => file.kind !== 'output') },
    { key: 'output', label: '输出文件', description: '处理完成后生成的交付文件', files: ordered.filter((file) => file.kind === 'output' && isDeliveredFile(file)) },
  ]
}

export function executionTables(tables: TableBrief[]): TableBrief[] {
  return chronological(tables.filter((table) => table.source === 'computed'))
}

/** Keep run details and saved metadata together when they describe the same recipe version. */
export function executionRecipeSections(recipes: RecipeBrief[], progresses: RecipeProgress[]) {
  const progressSections = progresses.map((progress) => {
    const recipeId = progress.saved?.recipe_id ?? progress.recipeId ?? progress.recipe?.id
    const version = progress.deleted ? undefined : progress.saved?.version ?? progress.recipeVersion ?? progress.recipe?.version
    const recipe = progress.deleted ? undefined : recipes.find((item) => item.recipe_id === recipeId && item.version === version)
    return { progress, recipe, version }
  })
  const represented = new Set(progressSections.map((section) => section.recipe))
  return { progresses: progressSections, saved: recipes.filter((recipe) => !represented.has(recipe)) }
}

export function conversationTables(tables: TableBrief[], conversationId: string): TableBrief[] {
  return tables.filter((table) => !table.run_id && !table.preview_only &&
    (!table.conversation_id || table.conversation_id === conversationId))
}

/** A name is unique only within its source workspace. */
export function executionTableKey(table: TableBrief): string {
  if (table.snapshot_id) return `preview:${table.snapshot_id}`
  return `${table.run_id ? `run:${table.run_id}` : `conversation:${table.conversation_id ?? ''}`}:${table.name}`
}

export function executionTime(value?: string | null): string {
  if (!value || !Number.isFinite(Date.parse(value))) return '时间未记录'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(value))
}

export function executionFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
