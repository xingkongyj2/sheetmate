<script setup lang="ts">
/** 主界面框架：侧边栏在底层画布上，右侧内容是一整块悬浮在画布上的面板 */
import { computed, onMounted, shallowReactive, shallowRef, watch } from 'vue'
import { useRouter, type RouteLocationNormalizedLoaded } from 'vue-router'

import { useWorkspaceStore } from '@/stores/conversations'

import AppSidebar from './AppSidebar.vue'
import BackgroundPage from './BackgroundPage.vue'

const store = useWorkspaceStore()
const router = useRouter()
// 「个人设置」作为工作台覆盖层：盖在当前页面上，关闭只是隐藏。
const OVERLAYS = [
  { name: 'settings', label: '个人设置' },
] as const
const isOverlay = (name: unknown) => OVERLAYS.some((overlay) => overlay.name === name)
const overlayOpen = computed(() => isOverlay(router.currentRoute.value.name))
const openName = computed(() => router.currentRoute.value.name)
const backgroundRoute = shallowRef<RouteLocationNormalizedLoaded | null>(
  overlayOpen.value ? null : router.currentRoute.value,
)
const overlayRoutes = shallowReactive<Record<string, RouteLocationNormalizedLoaded | null>>(
  Object.fromEntries(OVERLAYS.map(({ name }) => [name, openName.value === name ? router.currentRoute.value : null])),
)

watch(router.currentRoute, (route) => {
  if (isOverlay(route.name)) overlayRoutes[route.name as string] = route
  else if (route.matched[0]?.path === '/') backgroundRoute.value = route
}, { flush: 'sync' })

onMounted(() => {
  store.refreshConversations().catch(() => undefined)
  store.refreshCustomers().catch(() => undefined)
})
</script>

<template>
  <div class="flex h-full overflow-hidden bg-canvas">
    <AppSidebar />
    <main class="app-panel relative my-2 mr-2 flex min-w-0 flex-1 flex-col overflow-hidden rounded-[14px] bg-panel">
      <BackgroundPage
        v-if="backgroundRoute"
        :route="backgroundRoute"
        :inert="overlayOpen"
        :aria-hidden="overlayOpen || undefined"
      />
      <template v-for="overlay in OVERLAYS" :key="overlay.name">
        <div
          v-if="overlayRoutes[overlay.name]"
          v-show="openName === overlay.name"
          class="absolute inset-0 z-40 bg-panel"
          :inert="openName !== overlay.name"
          :aria-hidden="openName !== overlay.name || undefined"
          :aria-label="overlay.label"
          @keydown.stop
        >
          <RouterView :route="overlayRoutes[overlay.name]!" />
        </div>
      </template>
    </main>
  </div>
</template>
