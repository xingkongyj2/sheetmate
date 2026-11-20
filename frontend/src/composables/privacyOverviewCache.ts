import type { PrivacyOverview } from '@/api/types'

/** 每个会话最近一次拿到的脱敏概览：切走再切回「脱敏」tab 时先显示它，同时在后台刷新，不用每次都等转圈。 */
const latest = new Map<string, PrivacyOverview>()
const MAX = 30

export function cachedOverview(conversationId: string | null | undefined): PrivacyOverview | null {
  return (conversationId && latest.get(conversationId)) || null
}

export function rememberOverview(conversationId: string, overview: PrivacyOverview): void {
  latest.delete(conversationId)
  latest.set(conversationId, overview)
  if (latest.size > MAX) latest.delete(latest.keys().next().value as string)
}
