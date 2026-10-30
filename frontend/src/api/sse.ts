/**
 * POST + Server-Sent Events 读取器（EventSource 只支持 GET，这里用 fetch 流式读取）。
 * 每收到一个 `data: {...}` 就回调一次；`data: [DONE]` 表示结束。
 */
import type { AgentEvent } from './types'
import { ApiError } from './http'

export interface StreamHandle {
  done: Promise<void>
  abort: () => void
}

export function postStream(url: string, body: unknown, onEvent: (event: AgentEvent) => void): StreamHandle {
  const controller = new AbortController()
  const done = (async () => {
    const resp = await fetch(`/api${url}`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(body ?? {}),
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
    for (;;) {
      const { value, done: finished } = await reader.read()
      if (finished) break
      buffer += decoder.decode(value, { stream: true })
      let index: number
      while ((index = buffer.indexOf('\n\n')) >= 0) {
        const chunk = buffer.slice(0, index)
        buffer = buffer.slice(index + 2)
        for (const line of chunk.split('\n')) {
          if (!line.startsWith('data: ')) continue
          const payload = line.slice(6)
          if (payload === '[DONE]') return
          try {
            onEvent(JSON.parse(payload))
          } catch {
            /* 忽略无法解析的片段 */
          }
        }
      }
    }
  })()
  return { done, abort: () => controller.abort() }
}
