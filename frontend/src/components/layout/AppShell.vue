<script setup lang="ts">
/** 主界面框架：侧边栏在底层画布上，右侧内容是一整块悬浮在画布上的面板 */
import { computed, onMounted, shallowRef, watch } from 'vue'
import { useRouter, type RouteLocationNormalizedLoaded } from 'vue-router'

import { useWorkspaceStore } from '@/stores/conversations'

import AppSidebar from './AppSidebar.vue'
import BackgroundPage from './BackgroundPage.vue'

const store = useWorkspaceStore()
const router = useRouter()
// 「设置」和「统计」是同级的覆盖层：盖在当前页面上，各自保持实例，关闭只是隐藏。
const settingsOpen = computed(() => router.currentRoute.value.name === 'settings')
const statsOpen = computed(() => router.currentRoute.value.name === 'stats')
const overlayOpen = computed(() => settingsOpen.value || statsOpen.value)
const backgroundRoute = shallowRef<RouteLocationNormalizedLoaded | null>(
  overlayOpen.value ? null : router.currentRoute.value,
)
const settingsRoute = shallowRef<RouteLocationNormalizedLoaded | null>(
  settingsOpen.value ? router.currentRoute.value : null,
)
const statsRoute = shallowRef<RouteLocationNormalizedLoaded | null>(
  statsOpen.value ? router.currentRoute.value : null,
)

watch(router.currentRoute, (route) => {
  if (route.name === 'settings') settingsRoute.value = route
  else if (route.name === 'stats') statsRoute.value = route
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
      <div
        v-if="settingsRoute"
        v-show="settingsOpen"
        class="absolute inset-0 z-40 bg-panel"
        :inert="!settingsOpen"
        :aria-hidden="!settingsOpen || undefined"
        aria-label="设置"
        @keydown.stop
      >
        <RouterView :route="settingsRoute" />
      </div>
      <div
        v-if="statsRoute"
        v-show="statsOpen"
        class="absolute inset-0 z-40 bg-panel"
        :inert="!statsOpen"
        :aria-hidden="!statsOpen || undefined"
        aria-label="统计"
        @keydown.stop
      >
        <RouterView :route="statsRoute" />
      </div>
    </main>
  </div>
</template>
