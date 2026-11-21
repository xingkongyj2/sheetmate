<script setup lang="ts">
/** 主界面框架：侧边栏在底层画布上，右侧内容是一整块（或由子页拆分的多块）悬浮在画布上的面板 */
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'

import { useWorkspaceStore } from '@/stores/conversations'

import AppSidebar from './AppSidebar.vue'

const store = useWorkspaceStore()
const route = useRoute()
const splitPanel = computed(() => Boolean(route.meta.splitPanel))

onMounted(() => {
  store.refreshConversations().catch(() => undefined)
  store.refreshCustomers().catch(() => undefined)
})
</script>

<template>
  <div class="flex h-full overflow-hidden bg-canvas">
    <AppSidebar />
    <main
      class="relative my-2 mr-2 flex min-h-0 min-w-0 flex-1 flex-col"
      :class="splitPanel ? '' : 'app-panel overflow-hidden rounded-[14px] bg-panel'"
    >
      <RouterView />
    </main>
  </div>
</template>
