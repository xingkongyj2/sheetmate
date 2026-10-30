<script setup lang="ts">
/**
 * 选择客户（相当于 Qoder 里「选择目录」）：绑定客户后按客户的记忆和流程来聊；也可以不绑定，作为临时对话。
 */
import { Check, ChevronDown, Plus, Search, Users, UserRoundX } from '@lucide/vue'
import { computed, nextTick, ref } from 'vue'

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
const dropUp = ref(false) // 下面放不下就向上展开
const root = ref<HTMLElement | null>(null)
const search = ref<HTMLInputElement | null>(null)
const panelEl = ref<HTMLElement | null>(null)
const query = ref('')
const creating = ref(false)
const newName = ref('')

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
  <div ref="root" class="relative">
    <!-- 整条抽屉都是按钮：左边是图标 / 客户形象 + 名字，右边是展开箭头 -->
    <button
      class="flex w-full items-center gap-2.5 rounded-b-[18px] px-3.5 py-2 text-left text-[13px] text-mute transition hover:bg-black/[0.03] hover:text-ink"
      @click="toggle"
    >
      <span v-if="current" class="flex size-6 shrink-0 items-center justify-center overflow-hidden rounded-full bg-white">
        <Mascot :variant="current.mascot" :size="26" :shadow="false" :track="false" />
      </span>
      <Users v-else class="size-4 shrink-0" />
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
          <span class="flex size-8 items-center justify-center rounded-full bg-paper"><UserRoundX class="size-4 text-faint" /></span>
          <span class="min-w-0 flex-1 text-sm">不绑定客户</span>
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
