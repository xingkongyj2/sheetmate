<script setup lang="ts">
/**
 * 侧边栏列表，分两块：
 * - 客户：头像、名字，点开展开这个客户的对话；
 * - 对话：未关联客户的对话，按更新时间排列。
 * 两块的收起状态和展开的客户记在本机浏览器里，读写失败就用默认值。
 */
import { ArrowUpRight, ChevronDown, Plus, SquarePen } from '@lucide/vue'
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { conversationApi } from '@/api'
import ConversationEmptyIcon from '@/components/brand/ConversationEmptyIcon.vue'
import Mascot from '@/components/brand/Mascot.vue'
import CustomerFormDialog from '@/components/customer/CustomerFormDialog.vue'
import type { CustomerCard } from '@/api/types'
import { useConfirm } from '@/composables/useConfirm'
import { useAuthStore } from '@/stores/auth'
import { useWorkspaceStore } from '@/stores/conversations'
import { useHomeDraftStore } from '@/stores/homeDraft'

import SidebarConversationItem from './SidebarConversationItem.vue'

const store = useWorkspaceStore()
const auth = useAuthStore()
const homeDraft = useHomeDraftStore()
const standaloneConversations = computed(() => store.recent.filter((c) => !c.customer?.id))
const route = useRoute()
const router = useRouter()
const { confirmDelete } = useConfirm()

async function startCustomerChat(customerId: string) {
  if (auth.user) await homeDraft.hydrate(auth.user.id)
  homeDraft.selectCustomer(customerId)
  await router.push({ name: 'home', query: { customer: customerId } })
}

const creating = ref(false)

async function onCreated(customer: CustomerCard) {
  creating.value = false
  await store.refreshCustomers()
  router.push({ name: 'customer', params: { id: customer.id } })
}

const STORAGE_KEY = 'sheetmate.sidebar'
const PREVIEW = 5

function load(): { customersClosed: boolean; conversationsClosed: boolean; expanded: string[] } {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
    return {
      customersClosed: saved.customersClosed === true,
      conversationsClosed: saved.conversationsClosed === true,
      expanded: Array.isArray(saved.expanded) ? saved.expanded.filter((id: unknown) => typeof id === 'string') : [],
    }
  } catch {
    return { customersClosed: false, conversationsClosed: false, expanded: [] }
  }
}

const saved = load()
const state = reactive({
  customersClosed: saved.customersClosed,
  conversationsClosed: saved.conversationsClosed,
  expanded: new Set(saved.expanded),
  showAll: new Set<string>(),
})

watch(state, () => {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      customersClosed: state.customersClosed,
      conversationsClosed: state.conversationsClosed,
      expanded: [...state.expanded],
    }))
  } catch { /* 本机存储不可用时只是不记住 */ }
}, { deep: true })

// 打开某个客户的对话时，自动展开这个客户
watch(() => [route.params.id, store.loaded] as const, ([id]) => {
  const customerId = store.conversations.find((c) => c.id === id)?.customer?.id
  if (customerId) state.expanded.add(customerId)
}, { immediate: true })

function toggle(id: string) {
  if (state.expanded.has(id)) state.expanded.delete(id)
  else state.expanded.add(id)
}

async function remove(id: string, title: string) {
  if (!await confirmDelete({
    title: '删除这段对话？',
    target: title,
    description: '对话记录和对话里的临时数据将一起删除，删除后无法恢复。',
    confirmText: '删除对话',
    action: () => conversationApi.remove(id),
  })) return
  store.removeConversation(id)
  if (route.params.id === id) router.push({ name: 'home' })
}
</script>

<template>
  <div
    class="mt-3 min-h-0 flex-1 overflow-y-auto px-3 pb-3 pt-1"
    :class="{ 'flex items-center justify-center': store.loaded && !store.conversations.length && !store.customerGroups.length }"
  >
    <!-- 客户（仅展示有对话的客户） -->
    <section v-if="store.customerGroups.length" class="mb-3" aria-label="客户">
      <div class="flex items-center justify-between pr-1">
        <button
          type="button"
          class="group-title"
          :aria-expanded="!state.customersClosed"
          @click="state.customersClosed = !state.customersClosed"
        >
          客户
          <ChevronDown class="size-3.5 transition-transform" :class="state.customersClosed && '-rotate-90'" aria-hidden="true" />
        </button>
        <button type="button" class="icon-btn size-6 hover:bg-black/[0.05]" title="新建客户" aria-label="新建客户" @click="creating = true">
          <Plus class="size-4" aria-hidden="true" />
        </button>
      </div>
      <div v-show="!state.customersClosed" class="mt-0.5 space-y-0.5">
        <div v-for="group in store.customerGroups" :key="group.customer.id">
          <div class="group relative">
            <button
              type="button"
              class="flex w-full items-center gap-2.5 rounded-xl py-2 pl-2.5 pr-8 text-left transition-colors group-hover:pr-14 group-focus-within:pr-14"
              :class="route.name === 'customer' && route.params.id === group.customer.id ? 'sidebar-active' : 'hover:bg-black/[0.04]'"
              :aria-expanded="state.expanded.has(group.customer.id)"
              @click="toggle(group.customer.id)"
            >
              <span class="flex size-[30px] shrink-0 items-center justify-center">
                <Mascot :variant="group.customer.mascot" :size="30" :shadow="false" :track="false" :mood="state.expanded.has(group.customer.id) ? 'idle' : 'sleep'" />
              </span>
              <span class="min-w-0 flex-1 truncate text-sm font-medium text-ink">{{ group.customer.name }}</span>
            </button>
            <span class="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs tabular-nums text-faint transition-opacity group-hover:opacity-0 group-focus-within:opacity-0">{{ group.items.length }}</span>
            <div class="pointer-events-none absolute right-1.5 top-1/2 flex -translate-y-1/2 items-center gap-0.5 opacity-0 transition-opacity group-hover:pointer-events-auto group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100">
              <RouterLink
                :to="{ name: 'customer', params: { id: group.customer.id } }"
                class="rounded-md p-1 text-faint transition hover:bg-black/[0.05] hover:text-ink"
                title="打开详情"
                :aria-label="`打开客户详情 ${group.customer.name}`"
              >
                <ArrowUpRight class="size-3.5" />
              </RouterLink>
              <button
                type="button"
                class="rounded-md p-1 text-faint transition hover:bg-black/[0.05] hover:text-ink"
                title="新增对话"
                :aria-label="`为 ${group.customer.name} 新增对话`"
                @click.stop="startCustomerChat(group.customer.id)"
              >
                <SquarePen class="size-3.5" />
              </button>
            </div>
          </div>
          <div v-if="state.expanded.has(group.customer.id)" class="mt-1 space-y-0.5 pb-1">
            <SidebarConversationItem
              v-for="conv in state.showAll.has(group.customer.id) ? group.items : group.items.slice(0, PREVIEW)"
              :key="conv.id"
              :conversation="conv"
              :active="route.params.id === conv.id"
              nested
              @remove="remove(conv.id, conv.title)"
            />
            <button
              v-if="group.items.length > PREVIEW && !state.showAll.has(group.customer.id)"
              type="button"
              class="w-full rounded-xl py-1.5 pl-[50px] text-left text-xs text-faint transition hover:bg-black/[0.04] hover:text-mute"
              @click="state.showAll.add(group.customer.id)"
            >
              还有 {{ group.items.length - PREVIEW }} 条
            </button>
          </div>
        </div>
      </div>
    </section>

    <!-- 对话（仅展示未关联客户的对话） -->
    <section v-if="standaloneConversations.length" aria-label="对话">
      <button
        type="button"
        class="group-title"
        :aria-expanded="!state.conversationsClosed"
        @click="state.conversationsClosed = !state.conversationsClosed"
      >
        对话
        <ChevronDown class="size-3.5 transition-transform" :class="state.conversationsClosed && '-rotate-90'" aria-hidden="true" />
      </button>
      <div v-show="!state.conversationsClosed" class="mt-0.5 space-y-0.5">
        <SidebarConversationItem
          v-for="conv in standaloneConversations"
          :key="conv.id"
          :conversation="conv"
          :active="route.params.id === conv.id"
          @remove="remove(conv.id, conv.title)"
        />
      </div>
    </section>

    <div v-if="store.loaded && !store.conversations.length && !store.customerGroups.length" class="flex flex-col items-center gap-2 px-3 py-6 text-xs leading-relaxed text-faint">
      <ConversationEmptyIcon class="size-8 shrink-0" />
      <span>暂无对话</span>
    </div>
    <CustomerFormDialog v-if="creating" @close="creating = false" @saved="onCreated" />
  </div>
</template>

<style scoped>
/* 分组标题：「客户 ⌄」「对话 ⌄」 */
.group-title { display: flex; align-items: center; gap: 0.25rem; border-radius: 0.375rem; padding: 0.375rem 0.75rem; font-size: 13px; color: var(--color-mute); transition: color .15s; }
.group-title:hover { color: var(--color-ink); }
</style>
