/** 首页未发送的草稿独立于路由组件，保留原始 File，切换会话后仍可继续编辑。 */
import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'

import type { Attachment } from '@/components/chat/ChatComposer.vue'
import { browserHomeDraftStorage, type HomeDraftMetadata, type HomeDraftStorage } from '../composables/homeDraftPersistence.ts'

export interface DraftFile {
  key: string
  file: File
  kind: 'input' | 'expected'
}

export const useHomeDraftStore = defineStore('homeDraft', () => {
  const text = ref('')
  const customerId = ref<string | null>(null)
  const files = ref<DraftFile[]>([])
  const attachments = ref<Attachment[]>([])
  const busy = ref(false)
  const generation = ref(0)
  const ready = ref(false)
  const savingFiles = ref(false)
  const initialized = ref(false)
  const metadataError = ref('')
  const fileError = ref('')
  const hasMissingFiles = computed(() => attachments.value.some((attachment) => !files.value.some((file) => file.key === attachment.key)))
  const cacheError = computed(() => metadataError.value || (hasMissingFiles.value ? '部分附件未能完整恢复，请重试，或移除后重新选择。' : fileError.value))
  let owner: string | null = null
  let persistence: HomeDraftStorage = browserHomeDraftStorage
  let hydration: Promise<void> | null = null
  let fileWrites = Promise.resolve()

  function metadata(): HomeDraftMetadata {
    return {
      version: 1, text: text.value, customerId: customerId.value,
      initialized: initialized.value, fileKeys: [...new Set([...files.value.map((item) => item.key), ...attachments.value.map((item) => item.key)])],
      attachments: attachments.value.map((item) => ({ ...item, tables: item.tables ? [...item.tables] : undefined })),
    }
  }

  function saveMetadata() {
    if (!ready.value || owner === null) return
    try {
      persistence.writeMetadata(owner, metadata())
      metadataError.value = ''
    } catch {
      metadataError.value = '草稿尚未保存，请检查浏览器存储空间后重试。'
    }
  }

  function queueFiles(target: string, snapshot: DraftFile[], storage: HomeDraftStorage, unavailableKeys: string[]) {
    const revision = generation.value
    savingFiles.value = true
    const operation = fileWrites.catch(() => undefined).then(async () => {
      // Preserve any bytes that became available after a partial restore, while
      // still saving files added to the editable draft in the meantime.
      const retained = unavailableKeys.length
        ? (await storage.readFiles(target)).filter((file) => unavailableKeys.includes(file.key))
        : []
      await storage.writeFiles(target, [...snapshot, ...retained])
    })
    fileWrites = operation
    void operation.then(() => {
      if (owner === target && revision === generation.value) fileError.value = ''
    }, () => {
      if (owner === target && revision === generation.value) fileError.value = '附件草稿尚未保存，请检查浏览器存储空间后重试。'
    }).finally(() => {
      if (fileWrites === operation && revision === generation.value) savingFiles.value = false
    })
  }

  function saveFiles() {
    if (!ready.value || owner === null) return
    const snapshot = files.value.map((item) => ({ key: item.key, file: item.file, kind: item.kind }))
    const available = new Set(snapshot.map((item) => item.key))
    queueFiles(owner, snapshot, persistence, attachments.value.filter((item) => !available.has(item.key)).map((item) => item.key))
  }

  // Synchronous metadata writes protect even text entered immediately before refresh.
  watch([text, customerId, initialized, attachments, () => files.value.map((item) => item.key)], saveMetadata, { deep: true, flush: 'sync' })
  watch([files, () => attachments.value.map((item) => item.key)], saveFiles, { deep: true, flush: 'sync' })

  async function hydrate(userId: string | number, storage: HomeDraftStorage = browserHomeDraftStorage) {
    const target = String(userId)
    if (owner === target && ready.value) return
    if (owner === target && hydration) return hydration
    const revision = ++generation.value
    ready.value = false
    owner = target
    persistence = storage
    metadataError.value = ''
    fileError.value = ''
    busy.value = false
    savingFiles.value = false
    text.value = ''
    customerId.value = null
    files.value = []
    attachments.value = []
    initialized.value = false
    const loading = (async () => {
      try {
        const saved = storage.readMetadata(target)
        if (saved) {
          text.value = saved.text
          customerId.value = typeof saved.customerId === 'string' ? saved.customerId : null
          initialized.value = saved.initialized === true
          // A previous visit can still be saving its last attachment snapshot.
          if (saved.fileKeys.length) await fileWrites.catch(() => undefined)
          let cached: DraftFile[] = []
          if (saved.fileKeys.length) {
            try { cached = await storage.readFiles(target) } catch { /* Keep the text editable and expose missing attachments below. */ }
          }
          if (revision !== generation.value || owner !== target) return
          const byKey = new Map(cached.map((item) => [item.key, item]))
          files.value = saved.fileKeys.flatMap((key) => byKey.has(key) ? [byKey.get(key)!] : [])
          attachments.value = saved.attachments.map((item) => ({
            ...item, status: byKey.has(item.key) ? 'pending' : 'error',
            error: byKey.has(item.key) ? undefined : '附件未完整恢复，请重试，或移除后重新选择',
          }))
        }
        if (revision === generation.value && owner === target) ready.value = true
      } catch {
        if (revision === generation.value && owner === target) metadataError.value = '草稿暂时无法恢复，请重试；已保存的内容仍会保留。'
      }
    })()
    hydration = loading
    try {
      await loading
    } finally {
      if (hydration === loading) hydration = null
    }
  }

  async function flush() {
    await fileWrites.catch(() => undefined)
  }

  async function retrySave() {
    if (hasMissingFiles.value && owner !== null) {
      const target = owner
      const revision = generation.value
      try {
        await flush()
        const cached = await persistence.readFiles(target)
        if (owner !== target || revision !== generation.value) return
        const desired = new Set(attachments.value.map((item) => item.key))
        const existing = new Set(files.value.map((item) => item.key))
        files.value.push(...cached.filter((item) => desired.has(item.key) && !existing.has(item.key)))
        const available = new Set(files.value.map((item) => item.key))
        attachments.value = attachments.value.map((item) => available.has(item.key) ? { ...item, status: 'pending', error: undefined } : item)
      } catch { /* The original metadata and attachment names remain available for another retry. */ }
    }
    saveMetadata()
    saveFiles()
    await flush()
  }

  /** URL 中的客户仅用于新草稿，返回首页时以用户已选的客户为准。 */
  function initialize(initialCustomerId: string | null) {
    if (initialized.value) return
    initialized.value = true
    customerId.value = initialCustomerId
  }

  function resetInitializationIfEmpty() {
    if (!text.value && !files.value.length && !attachments.value.length && customerId.value === null) initialized.value = false
  }

  function clear() {
    const previousOwner = owner
    const previousStorage = persistence
    generation.value += 1
    ready.value = false
    owner = null
    hydration = null
    initialized.value = false
    text.value = ''
    customerId.value = null
    files.value = []
    attachments.value = []
    busy.value = false
    savingFiles.value = false
    metadataError.value = ''
    fileError.value = ''
    if (previousOwner !== null) {
      try { previousStorage.writeMetadata(previousOwner, null) } catch { /* The in-memory account is already cleared. */ }
      const clearing = fileWrites.catch(() => undefined).then(() => previousStorage.writeFiles(previousOwner, []))
      fileWrites = clearing
      void clearing.catch(() => undefined)
    }
  }

  return { text, customerId, files, attachments, busy, generation, ready, savingFiles, cacheError, hasMissingFiles,
    hydrate, flush, retrySave, initialize, resetInitializationIfEmpty, clear }
})
