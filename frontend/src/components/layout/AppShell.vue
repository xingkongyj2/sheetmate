<script setup lang="ts">
/** 主界面框架：左侧边栏 + 右侧内容（参考 ChatGPT / Qoder 的布局） */
import { onMounted } from 'vue'

import { useWorkspaceStore } from '@/stores/conversations'

import AppSidebar from './AppSidebar.vue'

const store = useWorkspaceStore()
onMounted(() => {
  store.refreshConversations().catch(() => undefined)
  store.refreshCustomers().catch(() => undefined)
})
</script>

<template>
  <div class="flex h-full overflow-hidden bg-white">
    <AppSidebar />
    <main class="relative flex min-w-0 flex-1 flex-col">
      <RouterView v-slot="{ Component, route }">
        <component :is="Component" :key="route.path" />
      </RouterView>
    </main>
  </div>
</template>
