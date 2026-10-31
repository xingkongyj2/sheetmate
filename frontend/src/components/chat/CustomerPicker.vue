<script setup lang="ts">
/**
 * 选择客户（相当于 Qoder 里「选择目录」）：绑定客户后按客户的记忆和流程来聊；也可以不绑定，作为临时对话。
 */
import { Check, ChevronDown, Plus, Search } from '@lucide/vue'
import { computed, nextTick, ref } from 'vue'

import type { CustomerCard } from '@/api/types'
import Mascot from '@/components/brand/Mascot.vue'
import UnboundCustomerIcon from '@/components/brand/UnboundCustomerIcon.vue'
import CustomerFormDialog from '@/components/customer/CustomerFormDialog.vue'
import { useWorkspaceStore } from '@/stores/conversations'

const props = defineProps<{ modelValue: string | null }>()
const emit = defineEmits<{ 'update:modelValue': [value: string | null] }>()

const store = useWorkspaceStore()
const open = ref(false)
const dropUp = ref(false) // 下面放不下就向上展开
const root = ref<HTMLElement | null>(null)
const search = ref<HTMLInputElement | null>(null)
const panelEl = ref<HTMLElement | null>(null)
const query = ref('')
const creating = ref(false)

const current = computed<CustomerCard | undefined>(() => store.customers.find((c) => c.id === props.modelValue))
const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  return q ? store.customers.filter((c) => `${c.name}${c.code}`.toLowerCase().includes(q)) : store.customers
})

/** 先向下展开；渲染出来量一下，下面放不下、上面放得下才翻到上面（客户很多、窗口又矮的时候） */
function toggle() {
  open.value = !open.value
  if (!open.value) return
  dropUp.value = false
  nextTick(() => {
    search.value?.focus({ preventScroll: true })
    const panel = panelEl.value?.getBoundingClientRect()
    const trigger = root.value?.getBoundingClientRect()
    if (panel && trigger && panel.bottom > window.innerHeight - 8 && trigger.top - panel.height - 8 > 8) dropUp.value = true
  })
}

function choose(id: string | null) {
  emit('update:modelValue', id)
  open.value = false
}

function startCreating() {
  open.value = false
  creating.value = true
}

function onCreated(customer: CustomerCard) {
  creating.value = false
  query.value = ''
  store.customers = [customer, ...store.customers.filter((c) => c.id !== customer.id)]
  choose(customer.id)
  store.refreshCustomers().catch(() => undefined)
}
</script>

<template>
  <div ref="root" class="relative">
    <!-- 整条抽屉都是按钮：左边是图标 / 客户形象 + 名字，右边是展开箭头 -->
    <button
      class="flex h-10 w-full items-center gap-2.5 rounded-b-[22px] px-3.5 py-2 text-left text-[13px] text-mute transition hover:bg-black/[0.03] hover:text-ink"
      @click="toggle"
    >
      <span class="flex size-6 shrink-0 items-center justify-center overflow-hidden rounded-full bg-paper">
        <UnboundCustomerIcon v-if="!current" class="size-5 shrink-0" />
        <Mascot v-else :variant="current.mascot" :size="26" :shadow="false" :track="false" />
      </span>
      <span class="min-w-0 truncate" :class="current ? 'font-medium text-ink' : ''">{{ current ? current.name : '选择客户' }}</span>
      <ChevronDown class="ml-auto size-3.5 shrink-0 text-faint transition" :class="{ 'rotate-180': open }" />
    </button>

    <div
      v-if="open"
      ref="panelEl"
      class="absolute left-0 z-40 w-80 max-w-full animate-pop rounded-2xl border border-line bg-white p-2 shadow-2xl"
      :class="dropUp ? 'bottom-full mb-2' : 'top-full mt-2'"
    >
      <div class="flex items-center gap-2 rounded-xl bg-paper px-3 py-2">
        <Search class="size-3.5 text-faint" />
        <input ref="search" v-model="query" class="w-full bg-transparent text-sm outline-none placeholder:text-faint" placeholder="搜索客户" />
      </div>
      <div class="mt-1.5 max-h-72 overflow-y-auto">
        <button class="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left hover:bg-paper" @click="choose(null)">
          <span class="flex size-8 shrink-0 items-center justify-center rounded-full bg-paper">
            <UnboundCustomerIcon class="size-6 shrink-0" />
          </span>
          <span class="block min-w-0 flex-1 truncate text-sm font-medium">无客户</span>
          <Check v-if="!modelValue" class="size-4" />
        </button>
        <button v-for="c in filtered" :key="c.id" class="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left hover:bg-paper" @click="choose(c.id)">
          <span class="flex size-8 items-center justify-center overflow-hidden rounded-full bg-paper">
            <Mascot :variant="c.mascot" :size="34" :shadow="false" :track="false" />
          </span>
          <span class="block min-w-0 flex-1 truncate text-sm font-medium">{{ c.name }}</span>
          <Check v-if="modelValue === c.id" class="size-4" />
        </button>
      </div>
      <div class="mt-1 border-t border-line pt-1.5">
        <button class="flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-sm text-mute hover:bg-paper hover:text-ink" @click="startCreating">
          <Plus class="size-4" />新建客户
        </button>
      </div>
    </div>
    <div v-if="open" class="fixed inset-0 z-30" @click="open = false" />
    <CustomerFormDialog v-if="creating" @close="creating = false" @saved="onCreated" />
  </div>
</template>
