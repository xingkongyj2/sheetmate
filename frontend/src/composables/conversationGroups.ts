import type { ConversationBrief, CustomerCard } from '@/api/types'

const time = (iso: string | null | undefined) => Date.parse(iso ?? '') || 0

/** 全部对话，最近更新的在前。 */
export function sortByRecent(conversations: ConversationBrief[]) {
  return [...conversations].sort((a, b) => time(b.updated_at) - time(a.updated_at))
}

/** 侧边栏「客户」：每个客户带上自己的对话（最近的在前），有最近对话的客户排在前面。 */
export function groupByCustomer(customers: CustomerCard[], conversations: ConversationBrief[]) {
  const byCustomer = new Map<string, ConversationBrief[]>()
  for (const conversation of sortByRecent(conversations)) {
    const id = conversation.customer?.id
    if (!id) continue
    if (!byCustomer.has(id)) byCustomer.set(id, [])
    byCustomer.get(id)!.push(conversation)
  }
  return customers
    .map((customer) => {
      const items = byCustomer.get(customer.id) ?? []
      return { customer, items, activeAt: Math.max(time(items[0]?.updated_at), time(customer.last_activity)) }
    })
    .sort((a, b) => b.activeAt - a.activeAt)
}

/** 列表里的短时间：今天显示时刻，昨天显示「昨天」，今年显示月/日，更早显示年/月/日。 */
export function shortTime(at: number | string | null | undefined, now = new Date()): string {
  const value = typeof at === 'number' ? at : time(at)
  if (!value) return ''
  const date = new Date(value)
  const pad = (n: number) => String(n).padStart(2, '0')
  const dayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  if (value >= dayStart) return `${pad(date.getHours())}:${pad(date.getMinutes())}`
  if (value >= dayStart - 86_400_000) return '昨天'
  if (date.getFullYear() === now.getFullYear()) return `${date.getMonth() + 1}/${date.getDate()}`
  return `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()}`
}
