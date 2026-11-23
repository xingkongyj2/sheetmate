<script setup lang="ts">
/** 管理页面框架：与工作台完全对齐的侧栏宽度（268px / 64px）、Logo、线条图标规格与底部账号栏。 */
import {
  Building2,
  ChartColumn,
  Component as FormulaIcon,
  Cpu,
  LayoutDashboard,
  LogOut,
  PanelLeft,
  ShieldCheck,
  Users,
} from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import LogoMark from '@/components/brand/LogoMark.vue'
import UserAvatar from '@/components/brand/UserAvatar.vue'
import WorkspaceReturnIcon from '@/components/brand/WorkspaceReturnIcon.vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const narrowScreen = window.matchMedia('(max-width: 767px)')
const collapsed = ref(narrowScreen.matches)
const menuOpen = ref(false)
const accountBox = ref<HTMLElement | null>(null)

function onMediaChange(event: MediaQueryListEvent) {
  if (event.matches) collapsed.value = true
}

function closeMenu(event: Event) {
  if (event instanceof KeyboardEvent ? event.key === 'Escape' : !accountBox.value?.contains(event.target as Node)) {
    menuOpen.value = false
  }
}

onMounted(() => {
  narrowScreen.addEventListener('change', onMediaChange)
  window.addEventListener('pointerdown', closeMenu)
  window.addEventListener('keydown', closeMenu)
})
onBeforeUnmount(() => {
  narrowScreen.removeEventListener('change', onMediaChange)
  window.removeEventListener('pointerdown', closeMenu)
  window.removeEventListener('keydown', closeMenu)
})

const navGroups = [
  {
    label: '运营',
    items: [
      { name: 'admin-overview', label: '总览', icon: LayoutDashboard },
      { name: 'admin-stats', label: '数据统计', icon: ChartColumn },
    ],
  },
  {
    label: '资源',
    items: [
      { name: 'admin-users', label: '人员管理', icon: Users },
      { name: 'admin-customers', label: '客户管理', icon: Building2 },
      { name: 'admin-formulas', label: '公式库', icon: FormulaIcon },
    ],
  },
  {
    label: '配置',
    items: [
      { name: 'admin-models', label: '模型配置', icon: Cpu },
      { name: 'admin-privacy', label: '数据脱敏', icon: ShieldCheck },
    ],
  },
] as const

const username = computed(() => auth.user?.display_name || auth.user?.account || '')
const avatarKey = computed(() => auth.user?.account || auth.user?.display_name || '')
const avatar = computed(() => auth.user?.avatar || '')

async function logout() {
  await auth.logout()
  router.replace({ name: 'login' })
}
</script>

<template>
  <div class="flex h-full overflow-hidden bg-canvas">
    <aside
      class="flex h-full shrink-0 flex-col transition-[width] duration-200"
      :class="collapsed ? 'w-[64px]' : 'w-[268px]'"
    >
      <!-- 顶部：与工作台侧边栏完全对齐 -->
      <div class="flex h-14 shrink-0 items-center justify-between px-3.5">
        <RouterLink v-if="!collapsed" :to="{ name: 'admin-overview' }" class="pl-1"><LogoMark with-text :size="26" /></RouterLink>
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

      <!-- 主导航：与工作台统一的 .nav-row 与 18px / 2.25 线条图标 -->
      <nav class="mt-0.5 min-h-0 flex-1 space-y-4 overflow-y-auto px-3 pb-3 pt-0.5" aria-label="管理导航">
        <div v-for="group in navGroups" :key="group.label" class="space-y-0.5">
          <p v-if="!collapsed" class="px-3 py-1.5 text-[13px] text-mute">{{ group.label }}</p>
          <RouterLink
            v-for="item in group.items"
            :key="item.name"
            :to="{ name: item.name }"
            class="nav-row"
            :class="[
              collapsed ? 'justify-center px-0' : 'px-3',
              route.name === item.name ? 'sidebar-active' : 'text-ink/80',
            ]"
            :title="item.label"
            :aria-current="route.name === item.name ? 'page' : undefined"
          >
            <span class="nav-icon"><component :is="item.icon" aria-hidden="true" /></span>
            <span v-if="!collapsed" class="truncate">{{ item.label }}</span>
          </RouterLink>
        </div>
      </nav>

      <!-- 底部账号：与工作台侧边栏完全对齐 -->
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
          <RouterLink :to="{ name: 'home' }" role="menuitem" class="flex items-center gap-2 rounded-xl px-3 py-2 text-sm hover:bg-paper" @click="menuOpen = false">
            <WorkspaceReturnIcon class="size-4" />返回工作台
          </RouterLink>
          <button role="menuitem" class="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm text-error hover:bg-error/5" @click="logout">
            <LogOut class="size-4" />退出登录
          </button>
        </div>
      </div>
    </aside>

    <main class="app-panel relative my-2 mr-2 flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-[14px] bg-panel">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
/* 与工作台 AppSidebar 完全一致的侧栏导航行与图标规格 */
.nav-row { display: flex; width: 100%; align-items: center; gap: 0.625rem; border-radius: 0.75rem; padding-block: 0.5rem; font-size: 0.875rem; transition: background-color .15s, box-shadow .15s, color .15s; }
.nav-row:not(.sidebar-active):hover { background-color: rgb(0 0 0 / 0.04); }
.nav-icon { display: flex; width: 1.5rem; height: 1.5rem; flex-shrink: 0; align-items: center; justify-content: center; }
.nav-icon :deep(svg) { width: 18px; height: 18px; stroke-width: 2.25; }
</style>
