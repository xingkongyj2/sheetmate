import { computed, onScopeDispose, ref, watch } from 'vue'

import type { UiMessage } from './chatModel'

function validDuration(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
}

/** 流式时按实际时间更新；结束后使用服务端耗时或本地封存的起止时间。 */
export function replyDurationMs(message: UiMessage, now: number): number | undefined {
  if (!message.streaming && validDuration(message.execution?.duration_ms)) return message.execution.duration_ms
  if (!validDuration(message.replyStartedAt)) return undefined
  const end = message.streaming ? now : message.replyFinishedAt
  return validDuration(end) ? Math.max(0, end - message.replyStartedAt) : undefined
}

export function formatReplyDuration(ms: number): string {
  const totalSeconds = Math.floor(Math.max(0, ms) / 1000)
  const seconds = totalSeconds % 60
  const minutes = Math.floor(totalSeconds / 60) % 60
  const hours = Math.floor(totalSeconds / 3600)
  if (hours) return `${hours} 小时 ${minutes} 分 ${seconds} 秒`
  if (minutes) return `${minutes} 分 ${seconds} 秒`
  return `${seconds} 秒`
}

/** 回复完成的时刻（毫秒时间戳）：优先用服务端记录的开始时间 + 耗时，没有就用本地封存的结束时间；还在回复时为空。 */
export function replyFinishedAtMs(message: UiMessage): number | undefined {
  if (message.streaming) return undefined
  const execution = message.execution
  if (validDuration(execution?.started_at) && validDuration(execution?.duration_ms)) return execution.started_at + execution.duration_ms
  return validDuration(message.replyFinishedAt) ? message.replyFinishedAt : undefined
}

/** 只显示几点几分（本地时间），如 09:06。 */
export function replyClockTime(message: UiMessage): string {
  const finishedAt = replyFinishedAtMs(message)
  if (finishedAt === undefined) return ''
  const date = new Date(finishedAt)
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

export function useReplyTimer(source: () => UiMessage) {
  const now = ref(Date.now())
  let timer: ReturnType<typeof setInterval> | undefined

  function stop() {
    clearInterval(timer)
    timer = undefined
  }

  watch([() => source().key, () => source().streaming], () => {
    stop()
    now.value = Date.now()
    if (source().streaming) timer = setInterval(() => { now.value = Date.now() }, 1000)
  }, { immediate: true, flush: 'sync' })
  onScopeDispose(stop)

  const label = computed(() => {
    const message = source()
    const elapsed = replyDurationMs(message, now.value)
    if (elapsed === undefined) return ''
    return formatReplyDuration(elapsed)
  })

  return { label }
}
