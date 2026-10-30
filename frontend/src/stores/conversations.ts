/** 侧边栏的会话列表 + 客户列表（多个页面共用） */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { conversationApi, customerApi } from '@/api'
import type { ConversationBrief, CustomerCard } from '@/api/types'

export const useWorkspaceStore = defineStore('workspace', () => {
  const conversations = ref<ConversationBrief[]>([])
  const customers = ref<CustomerCard[]>([])
  const loaded = ref(false)

  async function refreshConversations() {
    conversations.value = await conversationApi.list()
    loaded.value = true
  }

  async function refreshCustomers() {
    customers.value = await customerApi.list()
  }

  function upsertConversation(conv: ConversationBrief) {
    const index = conversations.value.findIndex((c) => c.id === conv.id)
    if (index >= 0) conversations.value[index] = { ...conversations.value[index], ...conv }
    else conversations.value.unshift(conv)
  }

  function patchConversation(id: string, patch: Partial<ConversationBrief>) {
    const conv = conversations.value.find((c) => c.id === id)
    if (conv) Object.assign(conv, patch)
  }

  function removeConversation(id: string) {
    conversations.value = conversations.value.filter((c) => c.id !== id)
  }

  /** 按时间分组：今天 / 最近 7 天 / 更早 */
  const grouped = computed(() => {
    const now = new Date()
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
    const groups: { label: string; items: ConversationBrief[] }[] = [
      { label: '今天', items: [] },
      { label: '最近 7 天', items: [] },
      { label: '更早', items: [] },
    ]
    for (const conv of conversations.value) {
      const t = new Date(conv.updated_at).getTime()
      if (t >= startOfToday) groups[0].items.push(conv)
      else if (t >= startOfToday - 6 * 86400_000) groups[1].items.push(conv)
      else groups[2].items.push(conv)
    }
    return groups.filter((g) => g.items.length)
  })

  return {
    conversations, customers, loaded, grouped,
    refreshConversations, refreshCustomers, upsertConversation, patchConversation, removeConversation,
  }
})
