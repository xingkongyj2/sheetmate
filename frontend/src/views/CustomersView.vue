<script setup lang="ts">
/** 客户页：卡片墙，每个客户一个 IP 形象，显示处理逻辑摘要、流程、文件数 */
import { Plus, Search, Workflow } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import type { CustomerCard } from '@/api/types'
import FloatingObjects from '@/components/brand/FloatingObjects.vue'
import Mascot from '@/components/brand/Mascot.vue'
import { mascotStyle } from '@/components/brand/mascots'
import CustomerFormDialog from '@/components/customer/CustomerFormDialog.vue'
import { timeAgo } from '@/composables/chatModel'
import { useWorkspaceStore } from '@/stores/conversations'

const store = useWorkspaceStore()
const router = useRouter()
const query = ref('')
const creating = ref(false)
const loading = ref(true)

onMounted(async () => {
  await store.refreshCustomers().catch(() => undefined)
  loading.value = false
})

const list = computed(() => {
  const q = query.value.trim().toLowerCase()
  return q ? store.customers.filter((c) => `${c.name}${c.code}${c.excerpt}`.toLowerCase().includes(q)) : store.customers
})

async function onSaved(customer: CustomerCard) {
  creating.value = false
  await store.refreshCustomers()
  router.push({ name: 'customer', params: { id: customer.id } })
}
</script>

<template>
  <div class="h-full overflow-y-auto">
    <div class="mx-auto max-w-[1200px] px-8 py-10">
      <div class="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 class="text-[28px] font-semibold tracking-tight">客户</h1>
          <p class="mt-1 text-sm text-mute">每个客户的 Excel 处理逻辑、流程（Recipe）、文件和历史都在这里</p>
        </div>
        <div class="flex items-center gap-2">
          <div class="flex items-center gap-2 rounded-full border border-line bg-white px-3.5 py-2">
            <Search class="size-4 text-faint" />
            <input v-model="query" class="w-48 bg-transparent text-sm outline-none placeholder:text-faint" placeholder="搜索客户" />
          </div>
          <button class="btn-ink" @click="creating = true"><Plus class="size-4" />新建客户</button>
        </div>
      </div>

      <div class="mt-8 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
        <RouterLink
          v-for="(c, i) in list"
          :key="c.id"
          :to="{ name: 'customer', params: { id: c.id } }"
          class="group flex animate-rise flex-col overflow-hidden rounded-3xl border border-line bg-white transition duration-200 hover:-translate-y-1 hover:shadow-[0_24px_48px_-28px_rgba(0,0,0,0.35)]"
          :style="{ animationDelay: `${i * 40}ms` }"
        >
          <div class="relative h-36 overflow-hidden bg-paper">
            <div class="absolute right-4 top-4 flex gap-1">
              <span class="size-2 rounded-full" :style="{ background: mascotStyle(c.mascot).body }" />
              <span class="size-2 rounded-full bg-line" />
              <span class="size-2 rounded-full bg-line" />
            </div>
            <div class="absolute bottom-[-46px] left-1/2 -translate-x-1/2 transition duration-300 group-hover:bottom-[-30px]">
              <Mascot :variant="c.mascot" :size="150" :shadow="false" />
            </div>
          </div>
          <div class="flex flex-1 flex-col p-5">
            <div class="flex items-baseline gap-2">
              <h3 class="truncate text-[17px] font-semibold">{{ c.name }}</h3>
              <span v-if="c.code" class="shrink-0 font-mono text-[11px] text-faint">{{ c.code }}</span>
            </div>
            <p class="mt-1.5 line-clamp-2 min-h-[2.6em] text-[13px] leading-relaxed text-mute">{{ c.excerpt || '还没有处理逻辑说明，打开客户上传文件或新建对话' }}</p>
            <div class="mt-4 flex flex-wrap gap-1.5">
              <span v-for="r in c.recipes" :key="r.recipe_id" class="chip"><Workflow class="size-3" />{{ r.name }}<span class="text-faint">· {{ r.steps }} 步</span></span>
            </div>
            <div class="mt-auto flex items-center gap-3 pt-4 text-xs text-faint">
              <span><b class="font-semibold text-ink">{{ c.recipe_count }}</b> 流程</span>
              <span><b class="font-semibold text-ink">{{ c.file_count }}</b> 文件</span>
              <span><b class="font-semibold text-ink">{{ c.conversation_count }}</b> 对话</span>
              <span class="ml-auto">{{ timeAgo(c.last_activity) }}</span>
            </div>
          </div>
        </RouterLink>

        <button
          class="relative flex min-h-[300px] flex-col items-center justify-center overflow-hidden rounded-3xl border-2 border-dashed border-line text-mute transition hover:border-ink/30 hover:text-ink"
          @click="creating = true"
        >
          <FloatingObjects preset="empty" class="opacity-60" />
          <span class="relative flex size-12 items-center justify-center rounded-full bg-ink text-white"><Plus class="size-5" /></span>
          <span class="relative mt-3 text-sm font-medium">新建客户</span>
          <span class="relative mt-1 text-xs text-faint">或者在对话里「保存到客户」</span>
        </button>
      </div>

      <div v-if="!loading && !store.customers.length" class="mt-16 text-center text-sm text-faint">还没有客户</div>
    </div>
    <CustomerFormDialog v-if="creating" @close="creating = false" @saved="onSaved" />
  </div>
</template>
