<script setup lang="ts">
/**
 * 侧边栏：新对话、客户、设置，下面是按时间分组的对话列表（显示绑定的客户），底部是账号菜单。
 */
import { LoaderCircle, LogOut, MessageSquare, PanelLeftClose, PanelLeftOpen, Plus, Settings, Trash2, Users } from '@lucide/vue'
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { conversationApi } from '@/api'
import { errorText } from '@/api/http'
import LogoMark from '@/components/brand/LogoMark.vue'
import { mascotStyle } from '@/components/brand/mascots'
import { useToast } from '@/composables/useToast'
import { useAuthStore } from '@/stores/auth'
import { useWorkspaceStore } from '@/stores/conversations'

const store = useWorkspaceStore()
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const toast = useToast()
const collapsed = ref(false)
const menuOpen = ref(false)

const initial = computed(() => (auth.user?.display_name || auth.user?.account || '?').slice(0, 1).toUpperCase())

async function remove(id: string, title: string) {
  if (!window.confirm(`删除对话「${title}」？对话里的临时数据也会删除。`)) return
  try {
    await conversationApi.remove(id)
    store.removeConversation(id)
    if (route.params.id === id) router.push({ name: 'home' })
  } catch (err) {
    toast.error(errorText(err))
  }
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
        class="flex items-center gap-2.5 rounded-xl px-3 py-2 text-sm transition hover:bg-black/[0.04]"
        :class="String(route.name).startsWith('customer') ? 'bg-black/[0.05] font-medium text-ink' : 'text-mute'"
        title="客户"
      >
        <Users class="size-4" /><span v-if="!collapsed">客户</span>
        <span v-if="!collapsed && store.customers.length" class="ml-auto text-xs text-faint">{{ store.customers.length }}</span>
      </RouterLink>
    </nav>

    <!-- 对话列表 -->
    <div v-if="!collapsed" class="mt-4 min-h-0 flex-1 overflow-y-auto px-3 pb-3">
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
              <span v-else>临时对话</span>
              <span>· {{ conv.mode === 'modify' ? '修改' : '分析' }}</span>
            </span>
          </RouterLink>
          <button
            class="absolute right-2 top-2.5 hidden rounded-md p-1 text-faint hover:bg-white hover:text-error group-hover:block"
            title="删除对话"
            @click.prevent="remove(conv.id, conv.title)"
          >
            <Trash2 class="size-3.5" />
          </button>
        </div>
      </div>
      <div v-if="store.loaded && !store.conversations.length" class="px-3 py-6 text-center text-xs leading-relaxed text-faint">
        <MessageSquare class="mx-auto mb-2 size-5 opacity-60" />
        还没有对话<br />点「新对话」上传一张表试试
      </div>
    </div>
    <div v-else class="flex-1" />

    <!-- 账号 -->
    <div class="relative border-t border-line p-3">
      <button class="flex w-full items-center gap-2.5 rounded-xl p-1.5 text-left transition hover:bg-black/[0.04]" @click="menuOpen = !menuOpen">
        <span class="flex size-8 shrink-0 items-center justify-center rounded-full bg-ink text-sm font-semibold text-white">{{ initial }}</span>
        <span v-if="!collapsed" class="min-w-0 flex-1">
          <span class="block truncate text-sm font-medium">{{ auth.user?.display_name }}</span>
          <span class="block truncate text-[11px] text-faint">{{ auth.user?.role === 'admin' ? '管理员' : '成员' }} · {{ auth.user?.account }}</span>
        </span>
      </button>
      <div
        v-if="menuOpen"
        class="absolute bottom-16 left-3 z-30 w-52 animate-pop rounded-2xl border border-line bg-white p-1.5 shadow-xl"
        @mouseleave="menuOpen = false"
      >
        <RouterLink :to="{ name: 'settings' }" class="flex items-center gap-2 rounded-xl px-3 py-2 text-sm hover:bg-paper" @click="menuOpen = false">
          <Settings class="size-4" />设置（模型 / 账号）
        </RouterLink>
        <button class="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm text-error hover:bg-error/5" @click="logout">
          <LogOut class="size-4" />退出登录
        </button>
      </div>
    </div>
  </aside>
</template>
