<script setup lang="ts">
/**
 * 选择客户（相当于 Qoder 里「选择目录」）：绑定客户后按客户的记忆和流程来聊；也可以不绑定，作为临时对话。
 */
import { Check, ChevronDown, Plus, Search, UserRoundX } from '@lucide/vue'
import { computed, ref } from 'vue'

import { customerApi } from '@/api'
import { errorText } from '@/api/http'
import type { CustomerCard } from '@/api/types'
import Mascot from '@/components/brand/Mascot.vue'
import { useToast } from '@/composables/useToast'
import { useWorkspaceStore } from '@/stores/conversations'

const props = defineProps<{ modelValue: string | null }>()
const emit = defineEmits<{ 'update:modelValue': [value: string | null] }>()

const store = useWorkspaceStore()
const toast = useToast()
const open = ref(false)
const query = ref('')
const creating = ref(false)
const newName = ref('')

const current = computed<CustomerCard | undefined>(() => store.customers.find((c) => c.id === props.modelValue))
const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  return q ? store.customers.filter((c) => `${c.name}${c.code}`.toLowerCase().includes(q)) : store.customers
})

function choose(id: string | null) {
  emit('update:modelValue', id)
  open.value = false
}

async function create() {
  if (!newName.value.trim()) return
  try {
    const customer = await customerApi.create({ name: newName.value.trim() })
    await store.refreshCustomers()
    newName.value = ''
    creating.value = false
    choose(customer.id)
  } catch (err) {
    toast.error(errorText(err))
  }
}
</script>

<template>
  <div class="relative">
    <button
      class="flex items-center gap-2 rounded-full border border-line bg-white py-1 pl-1 pr-3 text-sm transition hover:border-ink/30"
      @click="open = !open"
    >
      <span class="flex size-7 items-center justify-center overflow-hidden rounded-full bg-paper">
        <Mascot v-if="current" :variant="current.mascot" :size="30" :shadow="false" :track="false" />
        <UserRoundX v-else class="size-3.5 text-faint" />
      </span>
      <span class="max-w-[180px] truncate" :class="current ? 'font-medium' : 'text-mute'">{{ current ? current.name : '不绑定客户' }}</span>
      <ChevronDown class="size-3.5 text-faint" />
    </button>

    <div v-if="open" class="absolute left-0 top-11 z-40 w-80 animate-pop rounded-2xl border border-line bg-white p-2 shadow-2xl">
      <div class="flex items-center gap-2 rounded-xl bg-paper px-3 py-2">
        <Search class="size-3.5 text-faint" />
        <input v-model="query" class="w-full bg-transparent text-sm outline-none placeholder:text-faint" placeholder="搜索客户" />
      </div>
      <div class="mt-1.5 max-h-72 overflow-y-auto">
        <button class="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left hover:bg-paper" @click="choose(null)">
          <span class="flex size-8 items-center justify-center rounded-full bg-paper"><UserRoundX class="size-4 text-faint" /></span>
          <span class="min-w-0 flex-1">
            <span class="block text-sm">不绑定客户</span>
            <span class="block text-[11px] text-faint">临时对话，数据只属于这次对话，之后可以保存到客户</span>
          </span>
          <Check v-if="!modelValue" class="size-4" />
        </button>
        <button v-for="c in filtered" :key="c.id" class="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left hover:bg-paper" @click="choose(c.id)">
          <span class="flex size-8 items-center justify-center overflow-hidden rounded-full bg-paper">
            <Mascot :variant="c.mascot" :size="34" :shadow="false" :track="false" />
          </span>
          <span class="min-w-0 flex-1">
            <span class="block truncate text-sm font-medium">{{ c.name }}</span>
            <span class="block truncate text-[11px] text-faint">{{ c.recipe_count }} 个流程 · {{ c.file_count }} 个文件</span>
          </span>
          <Check v-if="modelValue === c.id" class="size-4" />
        </button>
      </div>
      <div class="mt-1 border-t border-line pt-1.5">
        <div v-if="creating" class="flex gap-2 p-1">
          <input v-model="newName" class="field py-1.5" placeholder="新客户名称" @keydown.enter.prevent="create" />
          <button class="btn-ink px-3 py-1.5" @click="create">创建</button>
        </div>
        <button v-else class="flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-sm text-mute hover:bg-paper hover:text-ink" @click="creating = true">
          <Plus class="size-4" />新建客户
        </button>
      </div>
    </div>
    <div v-if="open" class="fixed inset-0 z-30" @click="open = false" />
  </div>
</template>
