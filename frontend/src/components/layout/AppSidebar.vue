<script setup lang="ts">
/**
 * 侧边栏：新对话、客户，下面是按时间分组的对话列表（显示绑定的客户），底部是用户名与账号操作。
 */
import { LoaderCircle, LogOut, PanelLeftClose, PanelLeftOpen, Plus, Settings, Trash2 } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { conversationApi } from '@/api'
import ConversationEmptyIcon from '@/components/brand/ConversationEmptyIcon.vue'
import CustomerLibraryIcon from '@/components/brand/CustomerLibraryIcon.vue'
import FormulaLibraryIcon from '@/components/brand/FormulaLibraryIcon.vue'
import LogoMark from '@/components/brand/LogoMark.vue'
import { mascotStyle } from '@/components/brand/mascots'
import { useConfirm } from '@/composables/useConfirm'
import { useAuthStore } from '@/stores/auth'
import { useWorkspaceStore } from '@/stores/conversations'

const store = useWorkspaceStore()
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const { confirmDelete } = useConfirm()
const narrowScreen = window.matchMedia('(max-width: 767px)')
const collapsed = ref(narrowScreen.matches)
const menuOpen = ref(false)
const settingsReturnPath = ref('/')
const settingsDestination = computed(() => route.name === 'settings' ? settingsReturnPath.value : { name: 'settings' })

const stopTrackingSettings = router.afterEach((to, from, failure) => {
  if (!failure && to.name === 'settings' && from.name !== 'settings' && from.matched[0]?.path === '/') {
    settingsReturnPath.value = from.fullPath
  }
})

const username = computed(() => auth.user?.account || '')
const initial = computed(() => (username.value || '?').slice(0, 1).toUpperCase())

function collapseOnNarrowScreen(event: MediaQueryListEvent) {
  if (event.matches) collapsed.value = true
}

onMounted(() => narrowScreen.addEventListener('change', collapseOnNarrowScreen))
onBeforeUnmount(() => {
  narrowScreen.removeEventListener('change', collapseOnNarrowScreen)
  stopTrackingSettings()
})

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

async function logout() {
  await auth.logout()
  router.replace({ name: 'login' })
}
</script>

<template>
  <aside
    class="flex h-full shrink-0 flex-col border-r border-line bg-paper/70 transition-[width] duration-200"
    :class="collapsed ? 'w-[64px]' : 'w-[268px]'"
  >
    <!-- 顶部 -->
    <div class="flex h-14 items-center justify-between px-3.5">
      <RouterLink v-if="!collapsed" to="/" class="pl-1"><LogoMark with-text :size="26" /></RouterLink>
      <button class="icon-btn" :title="collapsed ? '展开侧边栏' : '收起侧边栏'" @click="collapsed = !collapsed">
        <PanelLeftOpen v-if="collapsed" class="size-4" />
        <PanelLeftClose v-else class="size-4" />
      </button>
    </div>

    <div class="px-3">
      <RouterLink :to="{ name: 'home' }" class="btn-ink w-full" :class="collapsed ? 'px-0' : ''" title="新对话">
        <Plus class="size-4" /><span v-if="!collapsed">新对话</span>
      </RouterLink>
    </div>

    <nav class="mt-3 space-y-0.5 px-3">
      <RouterLink
        :to="{ name: 'customers' }"
        class="flex items-center gap-2.5 rounded-xl py-2 text-sm transition hover:bg-black/[0.04]"
        :class="[collapsed ? 'justify-center px-0' : 'px-3', String(route.name).startsWith('customer') ? 'bg-black/[0.05] font-medium text-ink' : 'text-mute']"
        title="客户库"
      >
        <CustomerLibraryIcon class="shrink-0" /><span v-if="!collapsed">客户库</span>
        <span v-if="!collapsed && store.customers.length" class="ml-auto text-xs text-faint">{{ store.customers.length }}</span>
      </RouterLink>
      <RouterLink
        :to="{ name: 'formulas' }"
        class="flex items-center gap-2.5 rounded-xl py-2 text-sm transition hover:bg-black/[0.04]"
        :class="[collapsed ? 'justify-center px-0' : 'px-3', route.name === 'formulas' ? 'bg-black/[0.05] font-medium text-ink' : 'text-mute']"
        title="公式库"
      >
        <FormulaLibraryIcon class="shrink-0" /><span v-if="!collapsed">公式库</span>
      </RouterLink>
    </nav>

    <!-- 对话列表 -->
    <div
      v-if="!collapsed"
      class="mt-4 min-h-0 flex-1 overflow-y-auto px-3 pb-3"
      :class="{ 'flex items-center justify-center': store.loaded && !store.conversations.length }"
    >
      <div v-for="group in store.grouped" :key="group.label" class="mb-3">
        <div class="label-mini px-3 pb-1.5 pt-2">{{ group.label }}</div>
        <div v-for="conv in group.items" :key="conv.id" class="group relative">
          <RouterLink
            :to="{ name: 'chat', params: { id: conv.id } }"
            class="flex flex-col rounded-xl px-3 py-2 transition hover:bg-black/[0.04]"
            :class="route.params.id === conv.id ? 'bg-black/[0.06]' : ''"
          >
            <span class="flex items-center gap-1.5 pr-6 text-sm text-ink">
              <LoaderCircle v-if="conv.running" class="size-3.5 shrink-0 animate-spin text-info" />
              <span class="truncate">{{ conv.title }}</span>
            </span>
            <span class="mt-0.5 flex items-center gap-1.5 text-[11px] text-faint">
              <template v-if="conv.customer">
                <span class="size-2 rounded-full" :style="{ background: mascotStyle(conv.customer.mascot).body }" />
                <span class="truncate">{{ conv.customer.name }}</span>
              </template>
              <span v-else>无客户</span>
            </span>
          </RouterLink>
          <button
            class="absolute right-2 top-2.5 rounded-md p-1 text-faint opacity-0 transition hover:bg-white hover:text-error focus-visible:opacity-100 group-hover:opacity-100"
            title="删除对话"
            :aria-label="`删除对话 ${conv.title}`"
            @click.prevent="remove(conv.id, conv.title)"
          >
            <Trash2 class="size-3.5" />
          </button>
        </div>
      </div>
      <div v-if="store.loaded && !store.conversations.length" class="flex flex-col items-center gap-2 px-3 py-6 text-xs leading-relaxed text-faint">
        <ConversationEmptyIcon class="size-8 shrink-0" />
        <span>暂无对话</span>
      </div>
    </div>
    <div v-else class="flex-1" />

    <!-- 账号 -->
    <div class="relative border-t border-line p-3">
      <div v-if="!collapsed" class="flex min-w-0 items-center gap-2 py-1 pl-3 pr-1.5">
        <span aria-hidden="true" class="flex size-8 shrink-0 items-center justify-center rounded-full bg-ink text-sm font-semibold text-white">{{ initial }}</span>
        <span class="min-w-0 flex-1 truncate text-sm font-medium" :title="username">{{ username }}</span>
        <div class="flex shrink-0 items-center gap-1">
          <RouterLink
            :to="settingsDestination"
            class="icon-btn"
            :class="route.name === 'settings' ? 'bg-black/[0.05] text-ink' : ''"
            title="设置"
            aria-label="设置"
          >
            <Settings class="size-4" />
          </RouterLink>
          <button type="button" class="icon-btn hover:bg-error/5 hover:text-error" title="退出登录" aria-label="退出登录" @click="logout">
            <LogOut class="size-4" />
          </button>
        </div>
      </div>
      <button
        v-else
        type="button"
        class="flex w-full items-center justify-center rounded-xl py-1.5 transition hover:bg-black/[0.04]"
        :title="username"
        aria-label="账号菜单"
        :aria-expanded="menuOpen"
        @click="menuOpen = !menuOpen"
      >
        <span class="flex size-8 shrink-0 items-center justify-center rounded-full bg-ink text-sm font-semibold text-white">{{ initial }}</span>
      </button>
      <div
        v-if="collapsed && menuOpen"
        class="absolute bottom-16 left-3 z-30 w-52 animate-pop rounded-2xl border border-line bg-white p-1.5 shadow-xl"
        @mouseleave="menuOpen = false"
      >
        <RouterLink :to="settingsDestination" class="flex items-center gap-2 rounded-xl px-3 py-2 text-sm hover:bg-paper" @click="menuOpen = false">
          <Settings class="size-4" />设置
        </RouterLink>
        <button class="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm text-error hover:bg-error/5" @click="logout">
          <LogOut class="size-4" />退出登录
        </button>
      </div>
    </div>
  </aside>
</template>
