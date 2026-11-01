import type { ConversationBrief } from '@/api/types'

/** 按本地日历日期划分，近期分组只收纳尚未归入更近日期的对话。 */
export function groupConversations(conversations: ConversationBrief[], now = new Date()) {
  const dayStart = (daysAgo: number) =>
    new Date(now.getFullYear(), now.getMonth(), now.getDate() - daysAgo).getTime()
  const groups: { label: string; since: number; items: ConversationBrief[] }[] = [
    { label: '今天', since: dayStart(0), items: [] },
    { label: '昨天', since: dayStart(1), items: [] },
    { label: '前天', since: dayStart(2), items: [] },
    { label: '近 7 天', since: dayStart(6), items: [] },
    { label: '近 30 天', since: dayStart(29), items: [] },
    { label: '30 天前', since: -Infinity, items: [] },
  ]
  for (const conversation of conversations) {
    const updatedAt = Date.parse(conversation.updated_at) || 0
    groups.find((group) => updatedAt >= group.since)!.items.push(conversation)
  }
  return groups.filter((group) => group.items.length).map(({ label, items }) => ({
    label,
    items: items.sort((a, b) => (Date.parse(b.updated_at) || 0) - (Date.parse(a.updated_at) || 0)),
  }))
}
