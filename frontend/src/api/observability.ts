import { http } from './http'

export interface ExecutionMetrics {
  scope: 'own'
  days: number
  from: string
  to: string
  coverage: { source: string; limit: number; truncated: boolean }
  tasks: number
  statuses: Record<string, number>
  recovered_tasks: number
  success_rate: number | null
  success_rate_tasks: number
  latency: { samples: number; p50_ms: number | null; p95_ms: number | null }
  usage: {
    model_calls: number
    prompt_tokens: number
    completion_tokens: number
    total_tokens: number
    cached_tokens: number
    cache_miss_tokens: number
    cache_hit_rate: number | null
    cached_tokens_missing_calls: number
    cache_miss_tokens_missing_calls: number
    usage_missing_calls: number
    pending_tasks: number
    missing_tasks: number
  }
  daily: { date: string; tasks: number; completed: number; failed: number; prompt_tokens: number; completion_tokens: number }[]
  failures: { code: string; tasks: number }[]
  tools: { tool: string; calls: number; completed: number; failed: number; pending: number; average_duration_ms: number | null }[]
  stages: { stage: string; model_calls: number; prompt_tokens: number; completion_tokens: number }[]
}

export async function getExecutionMetrics(days: number): Promise<ExecutionMetrics> {
  return (await http.get<ExecutionMetrics>('/observability/executions', { params: { days } })).data
}
