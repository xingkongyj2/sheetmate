import type { RecipeArtifact } from '@/api/types'
import type { FileArtifact, RecipeProgress } from './chatModel'

export type FileDeliveryStatus = 'candidate' | 'final' | 'intermediate' | 'superseded'

/** Explicit file requests may inspect a retained intermediate; automatic deliveries show final files only. */
export function isDeliveredFile(file: { delivery_status?: FileDeliveryStatus; intermediate?: boolean; explicit?: boolean }): boolean {
  return file.explicit === true || (!file.intermediate && file.delivery_status !== 'candidate' &&
    file.delivery_status !== 'intermediate' && file.delivery_status !== 'superseded')
}

export function progressDeliveryFiles(progress: RecipeProgress): FileArtifact[] {
  const files = progress.files ?? (progress.file ? [progress.file] : [])
  return [...new Map(files.filter(isDeliveredFile).map((file) => [file.file_id, file])).values()]
}

interface RunDeliverySource {
  output_file_id?: string | null
  validation?: { files?: RecipeArtifact[] }
}

/** Older runs may only store a primary pointer; known intermediate files never qualify as that fallback. */
export function primaryRunDeliveryId(run: RunDeliverySource | null): string | null {
  const primary = run?.output_file_id
  if (!primary) return null
  return run?.validation?.files?.some((file) => file.file_id === primary && !isDeliveredFile(file)) ? null : primary
}

export function runDeliveryFiles(run: RunDeliverySource | null): { id: string; name: string }[] {
  const files = new Map((run?.validation?.files ?? [])
    .filter(isDeliveredFile)
    .flatMap((file) => file.file_id ? [[file.file_id, { id: file.file_id, name: file.filename || file.name || '运行结果' }] as const] : []))
  const primary = primaryRunDeliveryId(run)
  if (primary && !files.has(primary)) return [{ id: primary, name: '运行结果' }, ...files.values()]
  return [...files.values()]
}
