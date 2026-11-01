/** 侧边栏的会话列表 + 客户列表（多个页面共用） */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { conversationApi, customerApi } from '@/api'
import type { ConversationBrief, CustomerCard } from '@/api/types'
import { groupConversations } from '@/composables/conversationGroups'

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

  const grouped = computed(() => groupConversations(conversations.value))

  return {
    conversations, customers, loaded, grouped,
    refreshConversations, refreshCustomers, upsertConversation, patchConversation, removeConversation,
  }
})
