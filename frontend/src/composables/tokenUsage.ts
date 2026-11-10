/** Cumulative model snapshots: cache and reasoning tokens are already included. */
export interface TokenUsageSummary {
  input: number
  output: number
  total: number
  cached: number
  reasoning: number
  calls: number
  estimatedCalls: number
  missingCalls: number
  inflightCalls: number
  turns: number
  recordedTurns: number
  missingTurns: number
  available: boolean
  estimated: boolean
  partial: boolean
}

interface UsageMessage {
  role: string
  id?: number
  key?: string
  meta?: Record<string, unknown>
  execution?: unknown
  events?: readonly unknown[]
  streaming?: boolean
}

function record(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : undefined
}

function count(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? Math.floor(value) : undefined
}

function empty(turns = 0): TokenUsageSummary {
  return { input: 0, output: 0, total: 0, cached: 0, reasoning: 0, calls: 0,
    estimatedCalls: 0, missingCalls: 0, inflightCalls: 0, turns,
    recordedTurns: 0, missingTurns: turns, available: false, estimated: false, partial: turns > 0 }
}

function snapshotUsage(value: unknown): TokenUsageSummary | undefined {
  const usage = record(value)
  if (!usage) return undefined
  const budget = record(usage.budget)
  const reportedInput = count(usage.prompt_tokens)
  const reportedOutput = count(usage.completion_tokens)
  const accountedInput = count(budget?.accounted_prompt_tokens)
  const accountedTotal = count(budget?.accounted_total_tokens)
  const hasAccounted = accountedInput !== undefined && accountedTotal !== undefined && accountedTotal >= accountedInput
  const hasReported = reportedInput !== undefined || reportedOutput !== undefined
  if (!hasReported && !hasAccounted) return undefined

  const estimatedCalls = count(budget?.estimated_usage_calls) ?? 0
  const missingCalls = Math.max(count(usage.usage_missing_calls) ?? 0, estimatedCalls)
  // Accounted values contain reported usage plus the existing fallback estimates.
  // Reserved tokens belong to requests still in flight and must not be counted.
  const useAccounted = hasAccounted && (estimatedCalls > 0 || !hasReported)
  const input = useAccounted ? accountedInput! : reportedInput ?? 0
  const output = useAccounted ? accountedTotal! - accountedInput! : reportedOutput ?? 0
  const stages = Object.values(record(usage.stages) ?? {}).map(record)
  const cached = count(usage.cached_tokens) ?? stages.reduce((sum, stage) => sum + (count(stage?.cached_tokens) ?? 0), 0)
  const reasoning = count(usage.reasoning_tokens) ?? stages.reduce((sum, stage) => sum + (count(stage?.reasoning_tokens) ?? 0), 0)
  const calls = count(usage.model_calls) ?? count(budget?.model_calls) ?? stages.reduce((sum, stage) => sum + (count(stage?.calls) ?? 0), 0)
  const inflightCalls = count(budget?.inflight_calls) ?? 0
  // Some older records initialized reported counters to zero even when every
  // provider response omitted usage. That zero does not mean a free request.
  const available = useAccounted || missingCalls === 0 || input + output > 0 || missingCalls < calls - inflightCalls
  return {
    input, output, total: input + output,
    cached: Math.min(input, cached), reasoning: Math.min(output, reasoning),
    calls,
    estimatedCalls, missingCalls, inflightCalls,
    turns: 1, recordedTurns: available ? 1 : 0, missingTurns: available ? 0 : 1, available,
    estimated: useAccounted && estimatedCalls > 0,
    partial: missingCalls > (useAccounted ? estimatedCalls : 0) || (!useAccounted && (reportedInput === undefined || reportedOutput === undefined)),
  }
}

function moreCurrent(next: TokenUsageSummary, current: TokenUsageSummary): boolean {
  if (!current.available && !current.calls && !current.missingCalls) return next.available || next.calls > 0 || next.missingCalls > 0
  if (!next.available && !next.calls && !next.missingCalls) return false
  const completed = next.calls - next.inflightCalls
  const previousCompleted = current.calls - current.inflightCalls
  return completed > previousCompleted || completed === previousCompleted && next.calls >= current.calls
}

/** Read one snapshot, never sum repeated events for the same assistant turn. */
export function messageTokenUsage(message: UsageMessage | null | undefined): TokenUsageSummary {
  if (!message || message.role !== 'assistant') return empty()
  let result = empty(1)
  // Later events replace earlier snapshots. Persisted metadata wins ties because
  // background title and memory calls may finish after the final streamed event.
  const eventCandidates = (message.events ?? []).flatMap((value) => {
    const event = record(value)
    if (!event || !['model_call_done', 'completion_check', 'execution_stop', 'execution_status', 'llm_usage'].includes(String(event.type))) return []
    return [event.llm_usage ?? event.usage]
  })
  const storedCandidates = [record(message.execution)?.llm_usage, record(message.meta?.execution)?.llm_usage]
  const candidates = message.streaming ? [...storedCandidates, ...eventCandidates] : [...eventCandidates, ...storedCandidates]
  for (const candidate of candidates) {
    const next = snapshotUsage(candidate)
    if (next && moreCurrent(next, result)) result = next
  }
  return result
}

function identities(message: UsageMessage): string[] {
  const keys: string[] = []
  if (typeof message.id === 'number') keys.push(`id:${message.id}`)
  if (message.key) keys.push(`key:${message.key}`)
  for (const field of ['job_id', 'request_message_id']) {
    const value = message.meta?.[field]
    if (typeof value === 'string' && value || typeof value === 'number') keys.push(`${field}:${value}`)
  }
  return keys
}

/** Pass messages from the current conversation only, including any live turn. */
export function conversationTokenUsage(messages: readonly UsageMessage[]): TokenUsageSummary {
  const groups: { identities: Set<string>; usage: TokenUsageSummary }[] = []
  for (const message of messages) {
    if (message.role !== 'assistant') continue
    const keys = identities(message)
    const matches = groups.filter((group) => keys.some((key) => group.identities.has(key)))
    const next = messageTokenUsage(message)
    const group = matches[0]
    if (!group) {
      groups.push({ identities: new Set(keys), usage: next })
      continue
    }
    for (const duplicate of matches.slice(1)) {
      duplicate.identities.forEach((key) => group.identities.add(key))
      if (moreCurrent(duplicate.usage, group.usage)) group.usage = duplicate.usage
      groups.splice(groups.indexOf(duplicate), 1)
    }
    keys.forEach((key) => group.identities.add(key))
    if (moreCurrent(next, group.usage)) group.usage = next
  }
  const result = empty()
  for (const { usage } of groups) {
    for (const field of ['input', 'output', 'total', 'cached', 'reasoning', 'calls', 'estimatedCalls',
      'missingCalls', 'inflightCalls', 'turns', 'recordedTurns', 'missingTurns'] as const) result[field] += usage[field]
    result.estimated ||= usage.estimated
    result.partial ||= usage.partial
  }
  result.available = result.recordedTurns > 0
  return result
}
