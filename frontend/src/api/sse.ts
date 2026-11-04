/**
 * GET / POST + Server-Sent Events 读取器。
 * 每收到一个 `data: {...}` 就回调一次；`data: [DONE]` 表示结束。
 */
import type { AgentEvent } from './types'
import { ApiError } from './http.ts'

export interface StreamHandle {
  done: Promise<void>
  abort: () => void
}

export function postStream(url: string, body: unknown, onEvent: (event: AgentEvent) => void): StreamHandle {
  return stream(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body ?? {}),
  }, onEvent)
}

export function getStream(url: string, onEvent: (event: AgentEvent) => void): StreamHandle {
  return stream(url, { method: 'GET', headers: { Accept: 'text/event-stream' } }, onEvent)
}

function stream(url: string, init: RequestInit, onEvent: (event: AgentEvent) => void): StreamHandle {
  const controller = new AbortController()
  const done = (async () => {
    const resp = await fetch(`/api${url}`, {
      ...init,
      credentials: 'include',
      signal: controller.signal,
    })
    if (!resp.ok || !resp.body) {
      let message = `请求失败（${resp.status}）`
      try {
        const data = await resp.json()
        message = data?.error?.message ?? message
      } catch {
        /* 不是 JSON */
      }
      throw new ApiError(message, resp.status)
    }
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    try {
      for (;;) {
        const { value, done: finished } = await reader.read()
        if (finished) throw new ApiError('回复连接已中断')
        buffer += decoder.decode(value, { stream: true })
        let boundary: RegExpExecArray | null
        while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
          const chunk = buffer.slice(0, boundary.index)
          buffer = buffer.slice(boundary.index + boundary[0].length)
          const payload = chunk.split(/\r?\n/)
            .filter((line) => line.startsWith('data:'))
            .map((line) => line.slice(5).replace(/^ /, ''))
            .join('\n')
          if (!payload) continue
          if (payload === '[DONE]') return
          let event: AgentEvent
          try {
            event = JSON.parse(payload)
          } catch {
            continue
          }
          onEvent(event)
        }
      }
    } finally {
      await reader.cancel().catch(() => undefined)
      reader.releaseLock()
    }
  })()
  return { done, abort: () => controller.abort() }
}
