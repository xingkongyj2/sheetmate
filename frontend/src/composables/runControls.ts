import type { AgentEvent, RunSteer, StoredMessage } from '../api/types.ts'

const STEER_STATES = { queued: 0, committed: 1, withdrawn: 1 } as const

/** HTTP receipts and SSE may arrive in either order; adopted or withdrawn text stays final. */
export function mergeRunSteer(items: RunSteer[], incoming: RunSteer): RunSteer[] {
  const previous = items.find((item) => item.message_id === incoming.message_id)
  if (!previous) return [...items, incoming]
  if (STEER_STATES[previous.status] > STEER_STATES[incoming.status] ||
      previous.status !== 'queued' && previous.status !== incoming.status) return items
  return items.map((item) => item === previous ? { ...previous, ...incoming } : item)
}

export function applyRunSteerEvent(items: RunSteer[], event: AgentEvent): RunSteer[] {
  if (event.type === 'stream_snapshot' && Array.isArray(event.events)) {
    return event.events.reduce(applyRunSteerEvent, items)
  }
  const status = event.type === 'steer_queued' ? 'queued' : event.type === 'steer_committed'
    ? 'committed' : event.type === 'steer_withdrawn' ? 'withdrawn' : null
  if (!status || typeof event.message_id !== 'number') return items
  const previous = items.find((item) => item.message_id === event.message_id)
  return mergeRunSteer(items, {
    message_id: event.message_id,
    content: typeof event.content === 'string' ? event.content : previous?.content ?? '',
    status,
    job_id: typeof event.job_id === 'string' ? event.job_id : previous?.job_id ?? '',
    execution_id: typeof event.execution_id === 'string' ? event.execution_id : previous?.execution_id,
  })
}

function belongsToExecution(item: RunSteer, executionId?: string): boolean {
  return !executionId || !item.execution_id || item.execution_id === executionId
}

export function restoreRunSteers(messages: StoredMessage[], pending: RunSteer[] = [], executionId?: string): RunSteer[] {
  const latest = [...messages].reverse().find((message) => message.role === 'assistant' &&
    (!executionId || !message.meta.execution_id || message.meta.execution_id === executionId))
  let restored = (latest?.events ?? []).reduce(applyRunSteerEvent, [] as RunSteer[])
    .filter((item) => belongsToExecution(item, executionId) &&
      (item.status !== 'queued' || pending.some((next) => next.message_id === item.message_id)))
  // Message metadata is authoritative even when a transient adoption event was
  // not part of the assistant checkpoint. Keep terminal states for late receipts.
  for (const message of messages) {
    if (message.role !== 'user' || !message.meta.steering || typeof message.meta.steering !== 'object') continue
    const steering = message.meta.steering as Record<string, unknown>
    if (steering.status !== 'committed' && steering.status !== 'withdrawn') continue
    const item: RunSteer = { message_id: message.id, content: message.content, status: steering.status,
      job_id: typeof steering.job_id === 'string' ? steering.job_id : '',
      execution_id: typeof steering.execution_id === 'string' ? steering.execution_id : undefined }
    if (belongsToExecution(item, executionId)) restored = mergeRunSteer(restored, item)
  }
  return pending.filter((item) => belongsToExecution(item, executionId)).reduce(mergeRunSteer, restored)
}

/** Fresh GET snapshots remove missing queued drafts; terminal receipts never regress. */
export function reconcileRunSteers(previous: RunSteer[], snapshot: RunSteer[], executionId?: string,
  preserveQueued = false): RunSteer[] {
  const retained = previous.filter((item) => belongsToExecution(item, executionId) &&
    (item.status !== 'queued' || preserveQueued || snapshot.some((next) => next.message_id === item.message_id)))
  return snapshot.filter((item) => belongsToExecution(item, executionId)).reduce(mergeRunSteer, retained)
}

/** Queued drafts become ordinary user messages only after the executor adopts them. */
export function committedRunMessages(messages: StoredMessage[]): StoredMessage[] {
  const visible = messages.filter((message) => {
    if (message.role !== 'user') return true
    const steering = message.meta?.steering
    if (!steering || typeof steering !== 'object') return true
    return !['queued', 'withdrawn'].includes(String((steering as Record<string, unknown>).status))
  })
  const owners = new Map<number, number>()
  for (const message of visible) {
    if (message.role !== 'assistant' || !Array.isArray(message.meta.reply_to_message_ids)) continue
    for (const identifier of message.meta.reply_to_message_ids) {
      if (typeof identifier === 'number') owners.set(identifier, message.id)
    }
  }
  // The assistant checkpoint exists before a follow-up is queued. Group adopted
  // requests with its answer rather than leaving a trailing unanswered user row.
  const adopted = visible.filter((message) => message.role === 'user' &&
    typeof message.meta.steering === 'object' && message.meta.steering !== null && owners.has(message.id))
  const moved = new Set(adopted.map((message) => message.id))
  return visible.flatMap((message) => moved.has(message.id) ? [] : message.role === 'assistant'
    ? [...adopted.filter((item) => owners.get(item.id) === message.id), message] : [message])
}
