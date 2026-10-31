<script setup lang="ts">
/** 客户页：紧凑卡片展示客户摘要、文件与对话概况。 */
import { Plus, Search } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import type { CustomerCard } from '@/api/types'
import Mascot from '@/components/brand/Mascot.vue'
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
    <div class="mx-auto max-w-[1200px] px-4 py-8 sm:px-8 sm:py-10">
      <div class="flex items-center gap-3 sm:gap-4">
        <h1 class="shrink-0 text-2xl font-semibold tracking-tight sm:text-[28px]">客户库</h1>
        <div class="ml-auto flex min-w-0 items-center gap-2">
          <label class="flex min-w-0 items-center gap-2 rounded-full border border-line bg-white px-3.5 py-2">
            <Search class="size-4 shrink-0 text-faint" />
            <input v-model="query" aria-label="搜索客户" class="w-full min-w-0 bg-transparent text-sm outline-none placeholder:text-faint sm:w-48" placeholder="搜索客户" />
          </label>
          <button class="btn-ink shrink-0 whitespace-nowrap px-3 sm:px-5" @click="creating = true"><Plus class="size-4" />新建客户</button>
        </div>
      </div>

      <div class="mt-7 grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        <RouterLink
          v-for="(c, i) in list"
          :key="c.id"
          :to="{ name: 'customer', params: { id: c.id } }"
          class="group flex min-w-0 animate-rise flex-col overflow-hidden rounded-[22px] bg-white shadow-[0_4px_24px_rgba(0,0,0,0.06),0_1px_4px_rgba(0,0,0,0.03)] transition-[translate,box-shadow] duration-300 ease-out hover:shadow-[0_6px_28px_rgba(0,0,0,0.09),0_2px_6px_rgba(0,0,0,0.04)] motion-safe:hover:-translate-y-px motion-reduce:animate-none motion-reduce:transition-none"
          :style="{ animationDelay: `${i * 40}ms` }"
        >
          <div class="relative h-24 overflow-hidden bg-paper">
            <div class="absolute -bottom-8 left-1/2 -translate-x-1/2 transition-transform duration-400 ease-in-out motion-safe:group-hover:-translate-y-2 motion-reduce:transition-none">
              <Mascot :variant="c.mascot" :size="112" :shadow="false" />
            </div>
          </div>
          <div class="flex min-w-0 flex-1 flex-col p-4">
            <div class="flex min-w-0 items-baseline gap-2">
              <h3 class="min-w-0 truncate text-base font-semibold" :title="c.name">{{ c.name }}</h3>
              <span v-if="c.code" class="max-w-[35%] shrink-0 truncate font-mono text-[11px] text-faint" :title="c.code">{{ c.code }}</span>
            </div>
            <p class="mt-1.5 line-clamp-2 min-h-[2.6em] text-[13px] leading-relaxed text-mute">{{ c.excerpt || '还没有处理逻辑说明，打开客户上传文件或新建对话' }}</p>
            <div class="mt-auto flex items-center gap-3 pt-3 text-xs text-faint">
              <span><b class="font-semibold text-ink">{{ c.file_count }}</b> 文件</span>
              <span><b class="font-semibold text-ink">{{ c.conversation_count }}</b> 对话</span>
              <span class="ml-auto">{{ timeAgo(c.last_activity) }}</span>
            </div>
          </div>
        </RouterLink>
      </div>

      <div v-if="!loading && !store.customers.length" class="mt-16 text-center text-sm text-faint">还没有客户</div>
    </div>
    <CustomerFormDialog v-if="creating" @close="creating = false" @saved="onSaved" />
  </div>
</template>
