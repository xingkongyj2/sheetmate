/**
 * 文件列表里每个表格文件的脱敏状态。
 *
 * 传入当前列表里的文件 id（响应式的取值函数）：每个文件只请求一次、结果缓存；列表里新增了文件，只请求新增的。
 * 刚上传的文件会经历「还没识别 → 识别中 → 已识别」：有文件正在识别、或者是 10 秒内新出现且还没开始识别，
 * 就每 2 秒再拉一次这些文件的状态，直到都不是这两种为止（每个文件最多跟踪约 2 分钟，不会永远轮询）。
 * 请求失败静默忽略：图标不显示或保持原样，不打扰用户。组件卸载时停止。
 */
import { onBeforeUnmount, reactive, watch } from 'vue'

import { fileApi } from '@/api'
import type { PrivacyFileStatus } from '@/api/types'

const POLL_MS = 2000
const FRESH_MS = 10_000
const GIVE_UP_MS = 120_000

export function usePrivacyStatus(ids: () => string[]) {
  const statuses = reactive<Record<string, PrivacyFileStatus>>({})
  const requested = new Set<string>()
  const appeared = new Map<string, number>()   // 文件第一次出现在列表里的时间（只记第一次加载之后新出现的）
  const watching = new Map<string, number>()   // 开始跟踪（轮询）这个文件的时间
  let first = true
  let timer: ReturnType<typeof setTimeout> | undefined
  let disposed = false

  async function load(list: string[]) {
    if (!list.length) return
    try {
      const result = await fileApi.privacyStatus(list)
      if (disposed) return
      for (const id of list) {
        if (result[id]) statuses[id] = result[id]
      }
    } catch {
      // 静默：拿不到状态就不显示图标
    }
  }

  function pending(): string[] {
    const now = Date.now()
    return [...requested].filter((id) => {
      const status = statuses[id]
      if (!status) return false
      const wanted = status.state === 'running' || (status.state === 'none' && now - (appeared.get(id) ?? 0) < FRESH_MS)
      if (!wanted) {
        watching.delete(id)
        return false
      }
      const since = watching.get(id) ?? now
      watching.set(id, since)
      return now - since < GIVE_UP_MS
    })
  }

  function schedule() {
    if (disposed || timer !== undefined || !pending().length) return
    timer = setTimeout(async () => {
      timer = undefined
      await load(pending())
      schedule()
    }, POLL_MS)
  }

  watch(ids, async (current) => {
    const fresh = [...new Set(current)].filter((id) => id && !requested.has(id))
    if (!fresh.length) return
    for (const id of fresh) {
      requested.add(id)
      if (!first) appeared.set(id, Date.now())
    }
    first = false
    await load(fresh)
    schedule()
  }, { immediate: true })

  onBeforeUnmount(() => {
    disposed = true
    if (timer !== undefined) clearTimeout(timer)
    timer = undefined
  })

  return statuses
}
