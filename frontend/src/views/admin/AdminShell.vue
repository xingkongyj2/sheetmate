<script setup lang="ts">
/** 管理后台主框架：与用户工作台一致的底层画布 + 左侧分组导航 + 右侧悬浮主面板。 */
import {
  ArrowLeft,
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
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const narrowScreen = window.matchMedia('(max-width: 767px)')
const collapsed = ref(narrowScreen.matches)

function onMediaChange(event: MediaQueryListEvent) {
  if (event.matches) collapsed.value = true
}

onMounted(() => narrowScreen.addEventListener('change', onMediaChange))
onBeforeUnmount(() => narrowScreen.removeEventListener('change', onMediaChange))

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
      { name: 'admin-customers', label: '客户与流程', icon: Building2 },
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
const avatar = computed(() => auth.user?.avatar || '')
const initial = computed(() => (username.value || 'A').slice(0, 1).toUpperCase())

async function logout() {
  await auth.logout()
  router.replace({ name: 'login' })
}
</script>

<template>
  <div class="flex h-full overflow-hidden bg-canvas">
    <aside
      class="flex h-full shrink-0 flex-col transition-[width] duration-200"
      :class="collapsed ? 'w-[64px]' : 'w-[240px]'"
    >
      <!-- 顶部品牌与折叠 -->
      <div class="flex h-14 shrink-0 items-center justify-between px-3.5">
        <RouterLink v-if="!collapsed" :to="{ name: 'admin-overview' }" class="flex items-center gap-2 pl-1">
          <LogoMark :size="24" />
          <span class="text-[17px] font-semibold tracking-tight text-ink">SheetMate</span>
          <span class="rounded-md bg-black/[0.06] px-1.5 py-0.5 text-[10px] font-medium text-mute">后台</span>
        </RouterLink>
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

      <!-- 分组导航 -->
      <nav class="mt-2 min-h-0 flex-1 space-y-5 overflow-y-auto px-3" aria-label="管理后台导航">
        <div v-for="group in navGroups" :key="group.label" class="space-y-0.5">
          <p v-if="!collapsed" class="px-3 pb-1 text-[11px] font-medium tracking-wide text-faint">{{ group.label }}</p>
          <RouterLink
            v-for="item in group.items"
            :key="item.name"
            :to="{ name: item.name }"
            class="admin-nav-row"
            :class="[
              collapsed ? 'justify-center px-0' : 'px-3',
              route.name === item.name ? 'bg-black/[0.065] font-medium text-ink' : 'text-ink/75 hover:bg-black/[0.04] hover:text-ink',
            ]"
            :title="item.label"
            :aria-current="route.name === item.name ? 'page' : undefined"
          >
            <span class="admin-nav-icon"><component :is="item.icon" aria-hidden="true" /></span>
            <span v-if="!collapsed" class="truncate">{{ item.label }}</span>
          </RouterLink>
        </div>
      </nav>

      <!-- 底部返回工作台与账号 -->
      <div class="shrink-0 space-y-1.5 border-t border-black/[0.06] px-3 py-3">
        <RouterLink
          :to="{ name: 'home' }"
          class="admin-nav-row text-mute hover:bg-black/[0.04] hover:text-ink"
          :class="collapsed ? 'justify-center px-0' : 'px-3'"
          title="返回工作台"
        >
          <span class="admin-nav-icon"><ArrowLeft aria-hidden="true" /></span>
          <span v-if="!collapsed">返回工作台</span>
        </RouterLink>

        <div class="flex items-center gap-2 rounded-xl py-1.5" :class="collapsed ? 'justify-center px-0' : 'px-3'">
          <span
            aria-hidden="true"
            class="flex size-6 shrink-0 items-center justify-center overflow-hidden rounded-full bg-ink text-[11px] font-semibold text-white"
          >
            <img v-if="avatar" :src="avatar" alt="" class="size-full object-cover" />
            <template v-else>{{ initial }}</template>
          </span>
          <span v-if="!collapsed" class="min-w-0 flex-1 truncate text-xs font-medium text-ink">{{ username }}</span>
          <button
            v-if="!collapsed"
            type="button"
            class="icon-btn size-7 shrink-0 text-faint hover:bg-black/[0.05] hover:text-error"
            title="退出登录"
            aria-label="退出登录"
            @click="logout"
          >
            <LogOut class="size-3.5" />
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
.admin-nav-row {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 0.625rem;
  border-radius: 0.75rem;
  padding-block: 0.5rem;
  font-size: 0.875rem;
  transition: background-color 0.15s, color 0.15s;
}
.admin-nav-icon {
  display: flex;
  width: 1.25rem;
  height: 1.25rem;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
}
.admin-nav-icon :deep(svg) {
  width: 17px;
  height: 17px;
  stroke-width: 1.95;
}
</style>
