import type { UiMessage } from './chatModel'
import { messageTokenUsage } from './tokenUsage.ts'

type StoredUsageMessage = { id: number; role: string; content?: string; meta: Record<string, unknown> }

function record(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined
}

function sameIdentity(message: UiMessage, source: StoredUsageMessage): boolean {
  if (message.id != null) return source.id === message.id
  if (message.meta.job_id != null) return source.meta.job_id === message.meta.job_id
  return message.meta.request_message_id != null && source.meta.request_message_id === message.meta.request_message_id
}

function identified(message: UiMessage): boolean {
  return message.id != null || message.meta.job_id != null || message.meta.request_message_id != null
}

function isStale(message: UiMessage, source: StoredUsageMessage, fields: Record<string, unknown>): boolean {
  const current = messageTokenUsage(message)
  const next = messageTokenUsage(source)
  if (!next.available) return true
  if (!current.available) return false
  if (next.calls < current.calls || next.calls - next.inflightCalls < current.calls - current.inflightCalls) return true
  const previous = record(message.execution)?.llm_usage ?? record(message.meta.execution)?.llm_usage
  const previousElapsed = record(record(previous)?.budget)?.elapsed_seconds
  const nextElapsed = record(record(fields.llm_usage)?.budget)?.elapsed_seconds
  if (typeof previousElapsed === 'number' && typeof nextElapsed === 'number' && nextElapsed < previousElapsed) return true
  // Completion is monotonic even when a checkpoint and the final save share a
  // token snapshot. A delayed HTTP response must not restart finished polling.
  const previousPending = record(message.execution)?.llm_usage_pending ?? record(message.meta.execution)?.llm_usage_pending
  return previousPending === false && fields.llm_usage_pending === true && next.calls <= current.calls
}

/** Update only usage so late title/memory accounting never replays or resets the chat. */
export function syncMessageUsage(messages: UiMessage[], stored: StoredUsageMessage[]): void {
  const assistants = stored.filter((message) => message.role === 'assistant')
  const local = messages.filter((message) => message.role === 'assistant')
  const matches = new Map<UiMessage, StoredUsageMessage>()
  const claimed = new Set<number>()
  // Reserve stable matches before attempting any fallback for a freshly sent
  // assistant. A missing turn cannot shift a neighbouring turn's identity.
  for (const message of local) {
    if (!identified(message)) continue
    const source = assistants.find((item) => sameIdentity(message, item))
    if (source) {
      matches.set(message, source)
      claimed.add(source.id)
    }
  }
  for (const message of local) {
    // Streaming events are newer than an in-flight HTTP checkpoint response.
    if (message.streaming) continue
    let source = matches.get(message)
    if (!source && !identified(message) && message.content.trim()) {
      const candidates = assistants.filter((item) => !claimed.has(item.id) && item.content?.trim() === message.content.trim())
      const sameContent = local.filter((item) => !identified(item) && item.content.trim() === message.content.trim())
      if (candidates.length === 1 && sameContent.length === 1) source = candidates[0]
      // Repeated answers are only positionally safe when neither side is
      // missing a turn and every known identity agrees with that order.
      else if (local.length === assistants.length && local.every((item, index) => !identified(item) || sameIdentity(item, assistants[index]!))) {
        const positional = assistants[local.indexOf(message)]
        if (positional && candidates.includes(positional)) source = positional
      }
    }
    const fields = record(source?.meta.execution)
    if (!source || !fields || !record(fields.llm_usage) || isStale(message, source, fields)) continue
    claimed.add(source.id)
    const usage = { llm_usage: fields.llm_usage, llm_usage_pending: fields.llm_usage_pending === true }
    message.id = source.id
    message.meta = { ...message.meta, execution: { ...record(message.meta.execution), ...usage } }
    if (message.execution) message.execution = { ...message.execution, ...usage }
  }
}
