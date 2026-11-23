<script setup lang="ts">
/**
 * 侧边栏：搜索、新对话、客户库（统一的线条图标），下面是「客户」和「对话」两块列表，
 * 底部是头像和名字（点开是管理中心[仅管理员] / 退出登录）。
 */
import { CircleUserRound, LogOut, PanelLeft, Search, SquarePen } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import AdminCenterIcon from '@/components/brand/AdminCenterIcon.vue'
import LogoMark from '@/components/brand/LogoMark.vue'
import ProductShowcaseIcon from '@/components/brand/ProductShowcaseIcon.vue'
import UserAvatar from '@/components/brand/UserAvatar.vue'
import { useAuthStore } from '@/stores/auth'
import { useWorkspaceStore } from '@/stores/conversations'

import SearchDialog from './SearchDialog.vue'
import SidebarConversationList from './SidebarConversationList.vue'

const store = useWorkspaceStore()
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const narrowScreen = window.matchMedia('(max-width: 767px)')
const collapsed = ref(narrowScreen.matches)
const menuOpen = ref(false)
const accountBox = ref<HTMLElement | null>(null)
const searchOpen = ref(false)

const shortcutLabel = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘K' : 'Ctrl K'

function onShortcut(event: KeyboardEvent) {
  if ((event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === 'k') {
    event.preventDefault()
    searchOpen.value = !searchOpen.value
  }
}
const isAdmin = computed(() => auth.user?.role === 'admin')

const username = computed(() => auth.user?.display_name || auth.user?.account || '')
const avatarKey = computed(() => auth.user?.account || auth.user?.display_name || '')
const avatar = computed(() => auth.user?.avatar || '')

function collapseOnNarrowScreen(event: MediaQueryListEvent) {
  if (event.matches) collapsed.value = true
}

// 账号菜单：点外面或按 Esc 关闭
function closeMenu(event: Event) {
  if (event instanceof KeyboardEvent ? event.key === 'Escape' : !accountBox.value?.contains(event.target as Node)) menuOpen.value = false
}

onMounted(() => {
  narrowScreen.addEventListener('change', collapseOnNarrowScreen)
  window.addEventListener('keydown', onShortcut)
  window.addEventListener('pointerdown', closeMenu)
  window.addEventListener('keydown', closeMenu)
})
onBeforeUnmount(() => {
  narrowScreen.removeEventListener('change', collapseOnNarrowScreen)
  window.removeEventListener('keydown', onShortcut)
  window.removeEventListener('pointerdown', closeMenu)
  window.removeEventListener('keydown', closeMenu)
})

async function logout() {
  await auth.logout()
  router.replace({ name: 'login' })
}
</script>

<template>
  <aside
    class="flex h-full shrink-0 flex-col transition-[width] duration-200"
    :class="collapsed ? 'w-[64px]' : 'w-[268px]'"
  >
    <!-- 顶部 -->
    <div class="flex h-14 shrink-0 items-center justify-between px-3.5">
      <RouterLink v-if="!collapsed" to="/" class="pl-1"><LogoMark with-text :size="26" /></RouterLink>
      <button
        type="button"
        class="icon-btn hover:bg-black/[0.05]"
        :title="collapsed ? '展开侧边栏' : '收起侧边栏'"
        :aria-label="collapsed ? '展开侧边栏' : '收起侧边栏'"
        :aria-expanded="!collapsed"
        @click="collapsed = !collapsed"
      >
        <PanelLeft class="size-[18px]" aria-hidden="true" />
      </button>
    </div>

    <nav class="mt-1 space-y-0.5 px-3" aria-label="主导航">
      <button
        type="button"
        class="nav-row"
        :class="[collapsed ? 'justify-center px-0' : 'px-3', searchOpen ? 'sidebar-active' : 'text-ink/80']"
        :title="`搜索（${shortcutLabel}）`"
        aria-label="搜索"
        aria-haspopup="dialog"
        @click="searchOpen = true"
      >
        <span class="nav-icon"><Search aria-hidden="true" /></span>
        <template v-if="!collapsed">
          <span>搜索</span>
          <kbd class="ml-auto rounded-md border border-black/[0.08] px-1.5 font-sans text-[11px] leading-5 text-faint">{{ shortcutLabel }}</kbd>
        </template>
      </button>
      <RouterLink
        :to="{ name: 'home' }"
        class="nav-row"
        :class="[collapsed ? 'justify-center px-0' : 'px-3', route.name === 'home' ? 'sidebar-active' : 'text-ink/80']"
        title="新对话"
      >
        <span class="nav-icon"><SquarePen aria-hidden="true" /></span><span v-if="!collapsed">新对话</span>
      </RouterLink>
      <RouterLink
        :to="{ name: 'customers' }"
        class="nav-row"
        :class="[collapsed ? 'justify-center px-0' : 'px-3', route.name === 'customers' ? 'sidebar-active' : 'text-ink/80']"
        title="客户库"
      >
        <span class="nav-icon"><CircleUserRound aria-hidden="true" /></span><span v-if="!collapsed">客户库</span>
        <span v-if="!collapsed && store.customers.length" class="ml-auto text-xs text-faint">{{ store.customers.length }}</span>
      </RouterLink>
    </nav>

    <SidebarConversationList v-if="!collapsed" />
    <div v-else class="flex-1" />

    <!-- 账号 -->
    <div ref="accountBox" class="relative px-3 pb-2.5 pt-1">
      <div class="flex min-w-0 items-center" :class="collapsed ? 'justify-center' : 'gap-1'">
        <button
          type="button"
          class="flex min-w-0 items-center rounded-xl py-1.5 transition hover:bg-black/[0.05]"
          :class="collapsed ? 'w-full justify-center' : 'flex-1 gap-2.5 pl-3 pr-2 text-left'"
          :title="username"
          aria-label="账号菜单"
          aria-haspopup="menu"
          :aria-expanded="menuOpen"
          @click="menuOpen = !menuOpen"
        >
          <UserAvatar :seed="avatarKey" :src="avatar" class="size-6" />
          <span v-if="!collapsed" class="min-w-0 flex-1 truncate text-sm text-ink">{{ username }}</span>
        </button>
      </div>
      <div
        v-if="menuOpen"
        class="absolute bottom-12 left-3 z-30 w-52 animate-pop rounded-2xl border border-line bg-white p-1.5 shadow-xl"
        role="menu"
      >
        <RouterLink v-if="isAdmin" :to="{ name: 'admin-overview' }" role="menuitem" class="flex items-center gap-2 rounded-xl px-3 py-2 text-sm hover:bg-paper" @click="menuOpen = false">
          <AdminCenterIcon class="size-4" />管理中心
        </RouterLink>
        <RouterLink :to="{ name: 'showcase' }" role="menuitem" class="flex items-center gap-2 rounded-xl px-3 py-2 text-sm hover:bg-paper" @click="menuOpen = false">
          <ProductShowcaseIcon class="size-4" />产品展示
        </RouterLink>
        <button role="menuitem" class="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm text-error hover:bg-error/5" @click="logout">
          <LogOut class="size-4" />退出登录
        </button>
      </div>
    </div>
    <SearchDialog v-if="searchOpen" @close="searchOpen = false" />
  </aside>
</template>

<style scoped>
/* 侧栏导航行：线条图标 + 文字，图标统一 18px */
.nav-row { display: flex; width: 100%; align-items: center; gap: 0.625rem; border-radius: 0.75rem; padding-block: 0.5rem; font-size: 0.875rem; transition: background-color .15s, box-shadow .15s, color .15s; }
.nav-row:not(.sidebar-active):hover { background-color: rgb(0 0 0 / 0.04); }
.nav-icon { display: flex; width: 1.5rem; height: 1.5rem; flex-shrink: 0; align-items: center; justify-content: center; }
.nav-icon :deep(svg) { width: 18px; height: 18px; stroke-width: 2.25; }
</style>
