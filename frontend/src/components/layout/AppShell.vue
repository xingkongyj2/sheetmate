<script setup lang="ts">
/** 主界面框架：左侧边栏 + 右侧内容（参考 ChatGPT / Qoder 的布局） */
import { computed, onMounted, shallowRef, watch } from 'vue'
import { useRouter, type RouteLocationNormalizedLoaded } from 'vue-router'

import { useWorkspaceStore } from '@/stores/conversations'

import AppSidebar from './AppSidebar.vue'
import BackgroundPage from './BackgroundPage.vue'

const store = useWorkspaceStore()
const router = useRouter()
const settingsOpen = computed(() => router.currentRoute.value.name === 'settings')
const backgroundRoute = shallowRef<RouteLocationNormalizedLoaded | null>(
  settingsOpen.value ? null : router.currentRoute.value,
)
const settingsRoute = shallowRef<RouteLocationNormalizedLoaded | null>(
  settingsOpen.value ? router.currentRoute.value : null,
)

// 两层页面各自保持实例，关闭设置只隐藏覆盖层。
watch(router.currentRoute, (route) => {
  if (route.name === 'settings') settingsRoute.value = route
  else if (route.matched[0]?.path === '/') backgroundRoute.value = route
}, { flush: 'sync' })

onMounted(() => {
  store.refreshConversations().catch(() => undefined)
  store.refreshCustomers().catch(() => undefined)
})
</script>

<template>
  <div class="flex h-full overflow-hidden bg-white">
    <AppSidebar />
    <main class="relative flex min-w-0 flex-1 flex-col">
      <BackgroundPage
        v-if="backgroundRoute"
        :route="backgroundRoute"
        :inert="settingsOpen"
        :aria-hidden="settingsOpen || undefined"
      />
      <div
        v-if="settingsRoute"
        v-show="settingsOpen"
        class="absolute inset-0 z-40 bg-white"
        :inert="!settingsOpen"
        :aria-hidden="!settingsOpen || undefined"
        aria-label="设置"
        @keydown.stop
      >
        <RouterView :route="settingsRoute" />
      </div>
    </main>
  </div>
</template>
