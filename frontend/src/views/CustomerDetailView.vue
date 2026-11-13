<script setup lang="ts">
import {
  ArrowLeft, ArrowRight, LoaderCircle, MessageSquare, MessageSquarePlus, Pencil, Sparkles, Star, Trash2,
} from '@lucide/vue'
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { conversationApi, customerApi } from '@/api'
import { errorText } from '@/api/http'
import type { CustomerDetail, FileInfo } from '@/api/types'
import CustomerDetailTabIcon from '@/components/brand/CustomerDetailTabIcon.vue'
import Mascot from '@/components/brand/Mascot.vue'
import BaseModal from '@/components/common/BaseModal.vue'
import CustomerFormDialog from '@/components/customer/CustomerFormDialog.vue'
import CustomerLogicSummary from '@/components/customer/CustomerLogicSummary.vue'
import MemoryPanel from '@/components/customer/MemoryPanel.vue'
import RecipePanel from '@/components/customer/RecipePanel.vue'
import RunRecipeDialog from '@/components/customer/RunRecipeDialog.vue'
import WorkflowFiles from '@/components/customer/WorkflowFiles.vue'
import { timeAgo } from '@/composables/chatModel'
import { primaryFirst, workflowFiles } from '@/composables/customerWorkflow'
import { isDeliveredOutput } from '@/composables/deliveryFiles'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import { useWorkspaceStore } from '@/stores/conversations'

const WorkbookPreview = defineAsyncComponent(() => import('@/features/workbook-preview/WorkbookPreview.vue'))

const props = defineProps<{ id: string }>()
const route = useRoute()
const router = useRouter()
const toast = useToast()
const { confirmDelete } = useConfirm()
const store = useWorkspaceStore()

type Tab = 'logic' | 'recipes' | 'files' | 'memory'
const TABS: { key: Tab; label: string }[] = [
  { key: 'logic', label: '处理逻辑' },
  { key: 'recipes', label: '流程' },
  { key: 'files', label: '文件' },
  { key: 'memory', label: '记忆与字段' },
]
const initialTab = route.query.tab === 'runs' ? 'recipes' : route.query.tab
const tab = ref<Tab>(TABS.find((item) => item.key === initialTab)?.key ?? 'logic')
const customer = ref<CustomerDetail | null>(null)
const loading = ref(true)
const summarizing = ref(false)
const settingPrimary = ref(false)
const editing = ref(false)
const runDialog = ref<{ recipeId: string | null } | null>(null)
const runRecipe = computed(() => customer.value?.recipes.find((recipe) => recipe.recipe_id === runDialog.value?.recipeId) ?? null)
const preview = ref<FileInfo | null>(null)
const conversationsOpen = ref(false)
let loadVersion = 0
let active = true

async function load() {
  const customerId = props.id
  const version = ++loadVersion
  try {
    const result = await customerApi.get(customerId)
    if (!active || version !== loadVersion || props.id !== customerId) return
    customer.value = result
  } catch (err) {
    if (!active || version !== loadVersion) return
    toast.error(errorText(err))
    router.replace({ name: 'customers' })
  } finally {
    if (active && version === loadVersion) loading.value = false
  }
}
onMounted(() => {
  if (route.query.tab === 'runs') void router.replace({ query: { ...route.query, tab: 'recipes' } })
  void load()
})
onBeforeUnmount(() => { active = false; loadVersion++ })
watch(() => props.id, () => {
  customer.value = null
  const nextTab = route.query.tab === 'runs' ? 'recipes' : route.query.tab
  tab.value = TABS.find((item) => item.key === nextTab)?.key ?? 'logic'
  preview.value = null
  runDialog.value = null
  editing.value = false
  conversationsOpen.value = false
  loading.value = true
  void load()
})
watch(() => route.query.tab, (next) => {
  const nextTab = next === 'runs' ? 'recipes' : next
  tab.value = TABS.find((item) => item.key === nextTab)?.key ?? 'logic'
})

const orderedRecipes = computed(() => primaryFirst(customer.value?.recipes ?? [], customer.value?.primary_recipe_id))
const primaryRecipe = computed(() => orderedRecipes.value[0] ?? null)
const fileCount = computed(() => workflowFiles(orderedRecipes.value.length
  ? orderedRecipes.value.flatMap((recipe) => [...recipe.files, ...recipe.output_files.filter(isDeliveredOutput)])
  : [...(customer.value?.files ?? []), ...(customer.value?.outputs ?? []).filter(isDeliveredOutput)]).length)

function setTab(next: Tab) {
  tab.value = next
  void router.replace({ query: { ...route.query, tab: next } })
}

async function setPrimary(recipeId: string) {
  if (settingPrimary.value || !customer.value?.recipes.some((recipe) => recipe.recipe_id === recipeId)
    || customer.value.primary_recipe_id === recipeId) return
  const customerId = props.id
  settingPrimary.value = true
  try {
    await customerApi.update(customerId, { primary_recipe_id: recipeId })
    if (active && props.id === customerId && customer.value?.id === customerId) {
      await load()
      if (!active || props.id !== customerId) return
      toast.success('已设为主流程')
      void store.refreshCustomers()
    }
  } catch (err) {
    if (active && props.id === customerId) toast.error(errorText(err))
  } finally {
    settingPrimary.value = false
  }
}

async function newChat() {
  try {
    const conv = await conversationApi.create({ customer_id: props.id, recipe_id: primaryRecipe.value?.recipe_id })
    store.upsertConversation(conv)
    router.push({ name: 'chat', params: { id: conv.id } })
  } catch (err) {
    toast.error(errorText(err))
  }
}

async function regenerate() {
  if (summarizing.value) return
  const recipeId = primaryRecipe.value?.recipe_id
  const customerId = props.id
  summarizing.value = true
  try {
    const result = await customerApi.refreshSummary(customerId, recipeId)
    if (!active || props.id !== customerId || customer.value?.id !== customerId) return
    const updatedAt = new Date().toISOString()
    const recipe = customer.value.recipes.find((item) => item.recipe_id === recipeId)
    if (recipe) {
      Object.assign(recipe, { summary: result.summary, summary_by: 'ai', summary_updated_at: updatedAt })
    }
    if (primaryRecipe.value?.recipe_id === recipeId) {
      Object.assign(customer.value, { summary: result.summary, summary_by: 'ai', summary_updated_at: updatedAt })
    }
    toast.success(result.by_ai ? '处理说明已更新' : '已根据现有资料整理处理说明')
  } catch (err) {
    if (active && props.id === customerId) toast.error(errorText(err))
  } finally {
    summarizing.value = false
  }
}

async function removeCustomer() {
  if (!customer.value) return
  const customerId = props.id
  if (!await confirmDelete({
    title: '删除这个客户？',
    target: customer.value.name,
    description: '客户将被删除，已有会话及其文件会保留。此操作无法撤销。',
    confirmText: '删除客户',
    action: () => customerApi.remove(customerId),
  })) return
  try {
    await store.refreshCustomers()
    router.replace({ name: 'customers' })
  } catch (err) {
    toast.error(errorText(err))
  }
}

async function onEdited() {
  editing.value = false
  await Promise.all([load(), store.refreshCustomers()])
}
</script>

<template>
  <div class="h-full overflow-y-auto">
    <div v-if="loading" class="flex h-full items-center justify-center"><LoaderCircle class="size-6 animate-spin text-mute" /></div>
    <div v-else-if="customer" class="sheetmate-content-width mx-auto w-full px-6 pb-16 pt-6">
      <RouterLink :to="{ name: 'customers' }" class="btn-ink size-8 p-0 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ink" title="返回全部客户" aria-label="返回全部客户"><ArrowLeft class="size-3.5" aria-hidden="true" /></RouterLink>

      <!-- 头部 -->
      <header class="mt-5 grid grid-cols-[72px_minmax(0,1fr)] items-center gap-x-4 gap-y-4 sm:grid-cols-[96px_minmax(0,1fr)] sm:gap-x-5">
        <div class="relative flex size-[72px] items-center justify-center overflow-hidden rounded-[22px] bg-paper sm:size-24 sm:rounded-[28px]">
          <Mascot :variant="customer.mascot" :size="100" :shadow="false" class="size-[76px] shrink-0 sm:size-[100px]" />
        </div>
        <div class="min-w-0">
          <div class="flex items-center gap-2">
            <div class="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
              <h1 class="min-w-0 break-words text-2xl font-semibold leading-snug tracking-tight sm:text-[26px]">{{ customer.name }}</h1>
              <span v-if="customer.code" class="shrink-0 font-mono text-xs text-faint">{{ customer.code }}</span>
            </div>
            <button class="icon-btn size-7 shrink-0" title="编辑客户" @click="editing = true"><Pencil class="size-3.5" /></button>
            <button class="icon-btn size-7 shrink-0 hover:text-error" title="删除客户" aria-label="删除客户" @click="removeCustomer"><Trash2 class="size-3.5" /></button>
          </div>
          <p v-if="customer.description" class="mt-2 text-sm leading-relaxed text-mute">{{ customer.description }}</p>
          <div class="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-mute">
            <button
              type="button"
              class="rounded transition hover:text-ink hover:underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ink"
              title="查看客户对话"
              aria-haspopup="dialog"
              :aria-expanded="conversationsOpen"
              @click="conversationsOpen = true"
            ><b class="font-medium text-ink">{{ customer.conversation_count }}</b> 个对话</button>
            <span>最近活动 {{ timeAgo(customer.last_activity) }}</span>
          </div>
        </div>
        <div class="col-span-2 flex flex-wrap items-center gap-2 sm:col-span-1 sm:col-start-2">
          <button class="btn-ink h-8 gap-1.5 whitespace-nowrap px-3 py-0 text-xs" @click="newChat"><MessageSquarePlus class="size-3.5" />新建对话</button>
        </div>
      </header>

      <!-- 标签 -->
      <nav class="mt-8 flex w-fit max-w-full items-center gap-1 overflow-x-auto rounded-full bg-paper/80 p-1.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden" aria-label="客户详情">
        <button
          v-for="{ key, label } in TABS"
          :key="key"
          type="button"
          :aria-pressed="tab === key"
          class="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-2 text-sm transition-[background-color,color,box-shadow] duration-200 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ink/40 motion-reduce:transition-none"
          :class="tab === key ? 'bg-white font-medium text-ink shadow-[0_2px_8px_rgba(34,34,66,0.08)]' : 'text-mute hover:bg-white/60 hover:text-ink'"
          @click="setTab(key)"
        >
          <CustomerDetailTabIcon :name="key" class="size-5 shrink-0 transition-opacity duration-200 motion-reduce:transition-none" :class="tab === key ? 'opacity-100' : 'opacity-70'" />
          <span>{{ label }}</span>
          <span v-if="key === 'recipes'" class="rounded-full bg-black/[0.05] px-1.5 py-0.5 text-[11px] leading-none tabular-nums text-mute">{{ customer.recipes.length }}</span>
          <span v-else-if="key === 'files'" class="rounded-full bg-black/[0.05] px-1.5 py-0.5 text-[11px] leading-none tabular-nums text-mute">{{ fileCount }}</span>
        </button>
      </nav>

      <!-- 处理逻辑 -->
      <section v-if="tab === 'logic'" class="mt-6">
        <div class="mx-auto max-w-3xl">
          <div class="mb-2 flex justify-end">
            <button class="btn-text" :disabled="summarizing" @click="regenerate">
              <LoaderCircle v-if="summarizing" class="size-3.5 animate-spin" /><Sparkles v-else class="size-3.5" />重新总结
            </button>
          </div>
          <div class="rounded-[26px] bg-white p-6 shadow-[0_4px_24px_-8px_rgba(34,34,66,0.10),0_0_0_1px_rgba(34,34,66,0.035)] sm:p-9">
          <div class="mb-7 border-b border-line pb-5">
            <h2 v-if="primaryRecipe" class="break-words text-[20px] font-bold leading-snug text-ink [text-wrap:balance]">{{ primaryRecipe.name }}</h2>
            <p v-if="customer.summary_updated_at" class="mt-1.5 text-xs text-faint">{{ customer.summary_by === 'human' ? '更新于' : '自动更新于' }} {{ timeAgo(customer.summary_updated_at) }}</p>
          </div>
          <CustomerLogicSummary v-if="customer.summary" :source="customer.summary" />
          <div v-else class="py-10 text-center text-sm text-faint">
            还没有处理逻辑说明<br />
            <button class="btn-line mt-3" :disabled="summarizing" @click="regenerate"><Sparkles class="size-4" />让 AI 总结</button>
          </div>
          </div>
        </div>
      </section>

      <!-- 流程 -->
      <section v-else-if="tab === 'recipes'" class="mt-6 space-y-5">
        <RecipePanel
          v-for="recipe in orderedRecipes"
          :key="`${customer.id}:${recipe.recipe_id}`"
          :recipe="recipe"
          :files="workflowFiles([...recipe.files, ...recipe.output_files])"
          :primary="recipe.recipe_id === primaryRecipe?.recipe_id"
          :setting-primary="settingPrimary"
          :refresh="load"
          @set-primary="setPrimary"
          @run="(id) => (runDialog = { recipeId: id })"
          @changed="load"
        />
        <div v-if="!customer.recipes.length" class="card-soft py-14 text-center text-sm text-faint">还没有流程</div>
      </section>

      <!-- 文件 -->
      <section v-else-if="tab === 'files'" class="mt-6 space-y-8">
        <section v-for="recipe in orderedRecipes" :key="`${customer.id}:${recipe.recipe_id}`" class="space-y-3">
          <header class="flex items-center gap-2 px-1">
            <h2 class="min-w-0 break-words text-[16px] font-semibold leading-relaxed">{{ recipe.name }}</h2>
            <span v-if="recipe.recipe_id === primaryRecipe?.recipe_id" class="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full bg-[#6265DC]/8 px-2 py-1 text-[10px] text-[#6265DC]"><Star class="size-3 fill-current" />主流程</span>
          </header>
          <WorkflowFiles :customer-id="customer.id" :recipe-id="recipe.recipe_id" :files="recipe.files" :outputs="recipe.output_files" :refresh="load" @preview="preview = $event" />
        </section>
        <WorkflowFiles v-if="!orderedRecipes.length" :key="customer.id" :customer-id="customer.id" :files="customer.files" :outputs="customer.outputs" :refresh="load" @preview="preview = $event" />
      </section>

      <!-- 记忆 -->
      <section v-else-if="tab === 'memory'" class="mt-6">
        <div class="space-y-8">
          <section v-for="recipe in orderedRecipes" :key="`${customer.id}:${recipe.recipe_id}`" class="space-y-3">
            <header class="flex items-center gap-2 px-1">
              <h2 class="min-w-0 break-words text-[16px] font-semibold leading-relaxed">{{ recipe.name }}</h2>
              <span v-if="recipe.recipe_id === primaryRecipe?.recipe_id" class="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full bg-[#6265DC]/8 px-2 py-1 text-[10px] text-[#6265DC]"><Star class="size-3 fill-current" />主流程</span>
            </header>
            <MemoryPanel :customer-id="customer.id" :recipe-id="recipe.recipe_id" :memories="recipe.memories" :mapping="recipe.field_mapping" :mapping-entries="recipe.field_mapping_entries" :refresh="load" />
          </section>
          <MemoryPanel v-if="!orderedRecipes.length" :key="customer.id" :customer-id="customer.id" :memories="customer.memories" :mapping="customer.field_mapping" :mapping-entries="customer.field_mapping_entries ?? []" :refresh="load" />
        </div>
      </section>
    </div>

    <BaseModal v-if="conversationsOpen && customer" :title="`${customer.name}的对话`" width="max-w-xl" @close="conversationsOpen = false">
      <div v-if="customer.conversations.length" class="space-y-1">
        <RouterLink
          v-for="c in customer.conversations"
          :key="c.id"
          :to="{ name: 'chat', params: { id: c.id } }"
          class="group flex items-center gap-3 rounded-xl px-3 py-3 text-sm transition hover:bg-paper focus-visible:outline-2 focus-visible:outline-ink"
          @click="conversationsOpen = false"
        >
          <MessageSquare class="size-4 shrink-0 text-mute" />
          <span class="min-w-0 flex-1 truncate">{{ c.title || '新对话' }}</span>
          <span class="shrink-0 text-xs text-faint">{{ timeAgo(c.updated_at) }}</span>
          <ArrowRight class="size-4 shrink-0 text-faint transition group-hover:text-ink" />
        </RouterLink>
      </div>
      <p v-else class="py-10 text-center text-sm text-faint">还没有对话</p>
    </BaseModal>
    <CustomerFormDialog v-if="editing && customer" :customer="customer" @close="editing = false" @saved="onEdited" />
    <RunRecipeDialog
      v-if="runRecipe && customer"
      :key="runRecipe.recipe_id"
      :customer-id="customer.id"
      :recipe="runRecipe"
      @close="runDialog = null"
      @done="load"
    />
    <WorkbookPreview v-if="preview" :key="preview.file_id" :file="preview" @close="preview = null" />
  </div>
</template>
