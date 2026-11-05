/** Read worksheet names once per local File, keeping upload and tab order. */
import { computed, onScopeDispose, ref, toValue, watch, type MaybeRefOrGetter } from 'vue'

import { fileApi } from '@/api'
import { errorText } from '@/api/http'
import type { TableReference } from '@/composables/tableReferences'
import { parseInWorker } from '@/features/workbook-preview/worker-client'

interface LocalReferenceFile {
  key: string
  file: File
  kind: 'input' | 'expected'
}

interface SheetEntry {
  file: File
  controller: AbortController
  sheets: string[] | null
  started: boolean
  error: string
}

// Route changes reuse the same draft File objects; only keep their small name lists.
const sheetCache = new WeakMap<File, string[]>()

async function readSheets(file: File, signal: AbortSignal): Promise<string[]> {
  if (!/\.(xlsx|xlsm|xls|csv)$/i.test(file.name)) return []
  if (/\.csv$/i.test(file.name)) return [file.name.replace(/\.csv$/i, '') || 'CSV']
  if (file.size > 25 * 1024 * 1024) throw new Error('文件超过 25 MB，暂时无法读取工作表名称。')
  if (/\.xls$/i.test(file.name)) return (await fileApi.previewUpload(file, undefined, 0, 1, signal)).sheets
  const buffer = await file.arrayBuffer()
  if (signal.aborted) throw new DOMException('已取消读取工作表', 'AbortError')
  const parsed = await parseInWorker(buffer, file.name, signal)
  return parsed.workbook.sheetOrder.flatMap((id) => {
    const name = parsed.workbook.sheets[id]?.name
    return name ? [name] : []
  })
}

export function useLocalFileReferences(source: MaybeRefOrGetter<LocalReferenceFile[]>) {
  const entries = new Map<string, SheetEntry>()
  const revision = ref(0)
  let active = 0
  let disposed = false

  function pump() {
    if (disposed) return
    // Bound concurrent workers so adding several workbooks does not flood memory.
    for (const item of toValue(source)) {
      if (active >= 2) break
      const entry = entries.get(item.key)
      if (!entry || entry.started || entry.sheets !== null) continue
      entry.started = true
      active++
      void readSheets(entry.file, entry.controller.signal).then((sheets) => {
        if (entry.controller.signal.aborted) return
        sheetCache.set(entry.file, sheets)
        if (entries.get(item.key) === entry) entry.sheets = sheets
      }).catch((error: unknown) => {
        if (!entry.controller.signal.aborted && entries.get(item.key) === entry) {
          entry.sheets = []
          entry.error = errorText(error)
        }
      }).finally(() => {
        active--
        revision.value++
        pump()
      })
    }
  }

  watch(() => toValue(source).map(({ key, file }) => ({ key, file })), (files) => {
    const desired = new Map(files.map((item) => [item.key, item.file]))
    for (const [key, entry] of entries) {
      if (desired.get(key) !== entry.file) {
        entry.controller.abort()
        entries.delete(key)
      }
    }
    for (const { key, file } of files) {
      if (entries.has(key)) continue
      entries.set(key, { file, controller: new AbortController(), sheets: sheetCache.get(file) ?? null, started: false, error: '' })
    }
    revision.value++
    pump()
  }, { immediate: true })

  const tableReferences = computed<TableReference[]>(() => {
    void revision.value
    return toValue(source).flatMap(({ key, file, kind }) => (entries.get(key)?.sheets ?? []).map((sheet) => ({ filename: file.name, sourceId: key, kind, sheet })))
  })
  const referencesLoading = computed(() => {
    void revision.value
    return [...entries.values()].some((entry) => entry.sheets === null)
  })
  const referenceErrors = computed(() => {
    void revision.value
    return toValue(source).flatMap(({ key, file }) => {
      const error = entries.get(key)?.error
      return error ? [{ filename: file.name, error }] : []
    })
  })

  onScopeDispose(() => {
    disposed = true
    for (const entry of entries.values()) entry.controller.abort()
    entries.clear()
  })

  return { tableReferences, referencesLoading, referenceErrors }
}
