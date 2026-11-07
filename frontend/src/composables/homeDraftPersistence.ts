/** Synchronous draft text plus IndexedDB-backed attachment contents, scoped to one account. */
import type { Attachment } from '@/components/chat/ChatComposer.vue'
import type { DraftFile } from '../stores/homeDraft.ts'

export interface HomeDraftMetadata {
  version: 1
  text: string
  thinking: boolean
  customerId: string | null
  initialized: boolean
  fileKeys: string[]
  attachments: Attachment[]
}

export interface HomeDraftStorage {
  readMetadata(owner: string): HomeDraftMetadata | null
  writeMetadata(owner: string, metadata: HomeDraftMetadata | null): void
  readFiles(owner: string): Promise<DraftFile[]>
  writeFiles(owner: string, files: DraftFile[]): Promise<void>
}

interface StoredFile {
  key: string
  kind: DraftFile['kind']
  name: string
  type: string
  lastModified: number
  blob: Blob
}

const METADATA_KEY = 'sheetmate.home-draft.v1:'
let database: Promise<IDBDatabase> | null = null

function openDatabase(): Promise<IDBDatabase> {
  if (database) return database
  const opening = new Promise<IDBDatabase>((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      reject(new Error('浏览器无法保存附件草稿'))
      return
    }
    const request = indexedDB.open('sheetmate-home-drafts', 1)
    request.onupgradeneeded = () => request.result.createObjectStore('files')
    request.onerror = () => reject(request.error ?? new Error('无法打开草稿缓存'))
    request.onblocked = () => reject(new Error('草稿缓存暂时不可用，请关闭其他旧页面后重试'))
    request.onsuccess = () => {
      const db = request.result
      db.onversionchange = () => { db.close(); database = null }
      resolve(db)
    }
  })
  database = opening
  void opening.catch(() => { if (database === opening) database = null })
  return opening
}

export const browserHomeDraftStorage: HomeDraftStorage = {
  readMetadata(owner) {
    const json = localStorage.getItem(METADATA_KEY + owner)
    if (!json) return null
    const value = JSON.parse(json) as HomeDraftMetadata
    if (value.version !== 1 || typeof value.text !== 'string' || !Array.isArray(value.fileKeys) || !Array.isArray(value.attachments)) {
      throw new Error('草稿缓存格式无法读取')
    }
    return value
  },
  writeMetadata(owner, metadata) {
    if (metadata) localStorage.setItem(METADATA_KEY + owner, JSON.stringify(metadata))
    else localStorage.removeItem(METADATA_KEY + owner)
  },
  async readFiles(owner) {
    const db = await openDatabase()
    const stored = await new Promise<StoredFile[]>((resolve, reject) => {
      const transaction = db.transaction('files', 'readonly')
      const request = transaction.objectStore('files').get(owner)
      transaction.oncomplete = () => resolve(request.result ?? [])
      transaction.onerror = () => reject(transaction.error ?? new Error('无法读取附件草稿'))
      transaction.onabort = () => reject(transaction.error ?? new Error('附件草稿读取已中断'))
    })
    return stored.map((item) => ({
      key: item.key,
      kind: item.kind,
      file: new File([item.blob], item.name, { type: item.type, lastModified: item.lastModified }),
    }))
  },
  async writeFiles(owner, files) {
    const db = await openDatabase()
    const stored: StoredFile[] = files.map((item) => ({
      key: item.key, kind: item.kind, name: item.file.name, type: item.file.type,
      lastModified: item.file.lastModified, blob: item.file,
    }))
    await new Promise<void>((resolve, reject) => {
      const transaction = db.transaction('files', 'readwrite')
      const store = transaction.objectStore('files')
      if (stored.length) store.put(stored, owner)
      else store.delete(owner)
      transaction.oncomplete = () => resolve()
      transaction.onerror = () => reject(transaction.error ?? new Error('无法保存附件草稿'))
      transaction.onabort = () => reject(transaction.error ?? new Error('附件草稿保存已中断'))
    })
  },
}
