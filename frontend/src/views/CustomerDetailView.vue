<script setup lang="ts">
/**
 * 客户详情：
 * - 处理逻辑：AI 写的「Excel 整体处理逻辑」说明
 * - 流程：每个 Recipe 的流程图、输入要求、校验规则、版本，可用新文件执行
 * - 文件：客户上传过的 Excel（含人工结果样例）和生成的结果
 * - 运行记录：每次执行的明细（可单步查看、断点重跑）
 * - 记忆：业务规则、偏好、字段映射
 */
import {
  ArrowLeft, ArrowRight, Download, Eye, FileCheck2, FileSpreadsheet, LoaderCircle, MessageSquare, MessageSquarePlus, Pencil, Play, Sparkles,
  Trash2, Upload,
} from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { conversationApi, customerApi, fileApi } from '@/api'
import { errorText } from '@/api/http'
import type { CustomerDetail, FileInfo } from '@/api/types'
import CustomerDetailTabIcon from '@/components/brand/CustomerDetailTabIcon.vue'
import Mascot from '@/components/brand/Mascot.vue'
import BaseModal from '@/components/common/BaseModal.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import TableViewer from '@/components/common/TableViewer.vue'
import CustomerFormDialog from '@/components/customer/CustomerFormDialog.vue'
import CustomerLogicSummary from '@/components/customer/CustomerLogicSummary.vue'
import MemoryPanel from '@/components/customer/MemoryPanel.vue'
import RecipePanel from '@/components/customer/RecipePanel.vue'
import RunDetailDialog from '@/components/customer/RunDetailDialog.vue'
import RunRecipeDialog from '@/components/customer/RunRecipeDialog.vue'
import { duration, percent, timeAgo } from '@/composables/chatModel'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import { useAuthStore } from '@/stores/auth'
import { useWorkspaceStore } from '@/stores/conversations'

const props = defineProps<{ id: string }>()
const route = useRoute()
const router = useRouter()
const toast = useToast()
const { confirmDelete } = useConfirm()
const store = useWorkspaceStore()
const auth = useAuthStore()

type Tab = 'logic' | 'recipes' | 'files' | 'runs' | 'memory'
const TABS: { key: Tab; label: string }[] = [
  { key: 'logic', label: '处理逻辑' },
  { key: 'recipes', label: '流程' },
  { key: 'files', label: '文件' },
  { key: 'runs', label: '运行记录' },
  { key: 'memory', label: '记忆与字段' },
]
const tab = ref<Tab>((route.query.tab as Tab) || 'logic')
const customer = ref<CustomerDetail | null>(null)
const loading = ref(true)
const summarizing = ref(false)
const editing = ref(false)
const runDialog = ref<{ recipeId: string | null } | null>(null)
const runDetail = ref<string | null>(null)
const preview = ref<FileInfo | null>(null)
const conversationsOpen = ref(false)
const uploadKind = ref<'input' | 'expected'>('input')
const uploading = ref(false)

async function load() {
  try {
    customer.value = await customerApi.get(props.id)
    const previous = customer.value.summary
    if (customer.value.summary_by === 'ai' && previous && !/^#{1,3}\s+这项工作做什么\s*$/m.test(previous)) {
      void upgradeSummary(previous)
    }
  } catch (err) {
    toast.error(errorText(err))
    router.replace({ name: 'customers' })
  } finally {
    loading.value = false
  }
}
onMounted(load)

const inputFiles = computed(() => customer.value?.files ?? [])

async function upgradeSummary(previous: string) {
  if (summarizing.value) return
  const customerId = props.id
  summarizing.value = true
  try {
    const result = await customerApi.upgradeSummary(customerId)
    if (result.summary !== previous && customer.value?.id === customerId && customer.value.summary === previous) {
      customer.value.summary = result.summary
      customer.value.summary_by = result.summary_by
      if (result.updated) customer.value.summary_updated_at = new Date().toISOString()
    }
  } catch {
  } finally {
    summarizing.value = false
  }
}

async function newChat() {
  try {
    const conv = await conversationApi.create({ customer_id: props.id })
    store.upsertConversation(conv)
    router.push({ name: 'chat', params: { id: conv.id } })
  } catch (err) {
    toast.error(errorText(err))
  }
}

async function regenerate() {
  summarizing.value = true
  try {
    const result = await customerApi.refreshSummary(props.id)
    if (customer.value) {
      customer.value.summary = result.summary
      customer.value.summary_by = 'ai'
    }
    toast.success(result.by_ai ? '处理说明已更新' : '已根据现有资料整理处理说明')
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    summarizing.value = false
  }
}

async function upload(e: Event) {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''
  uploading.value = true
  try {
    for (const f of files) await customerApi.uploadFile(props.id, f, uploadKind.value)
    toast.success(`已上传 ${files.length} 个文件`)
    await load()
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    uploading.value = false
  }
}

async function removeFile(f: FileInfo) {
  if (!await confirmDelete({
    title: '删除这个文件？',
    target: f.filename,
    description: '文件将被永久删除，之后无法再预览或下载。此操作无法撤销。',
    confirmText: '删除文件',
    action: () => fileApi.remove(f.file_id),
  })) return
  await load()
}

async function removeCustomer() {
  if (!customer.value) return
  const customerId = props.id
  if (!await confirmDelete({
    title: '删除这个客户？',
    target: customer.value.name,
    description: '客户的流程和记忆会一起删除，文件记录保留。此操作无法撤销。',
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

async function loadPreview(offset: number, limit: number) {
  const data = await fileApi.preview(preview.value!.file_id, undefined, offset, limit)
  return { columns: data.columns, rows: data.rows, total: data.total }
}
</script>

<template>
  <div class="h-full overflow-y-auto">
    <div v-if="loading" class="flex h-full items-center justify-center"><LoaderCircle class="size-6 animate-spin text-mute" /></div>
    <div v-else-if="customer" class="sheetmate-content-width mx-auto w-full px-6 pb-16 pt-6">
      <RouterLink :to="{ name: 'customers' }" class="btn-text -ml-2"><ArrowLeft class="size-4" />全部客户</RouterLink>

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
          </div>
          <p class="mt-2 text-sm leading-relaxed text-mute">{{ customer.description || '还没有介绍' }}</p>
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
          <button v-if="customer.recipes.length" class="btn-line h-8 gap-1.5 whitespace-nowrap px-3 py-0 text-xs" @click="runDialog = { recipeId: null }"><Play class="size-3.5" />用新文件执行</button>
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
          @click="tab = key; router.replace({ query: { tab: key } })"
        >
          <CustomerDetailTabIcon :name="key" class="size-5 shrink-0 transition-opacity duration-200 motion-reduce:transition-none" :class="tab === key ? 'opacity-100' : 'opacity-70'" />
          <span>{{ label }}</span>
          <span v-if="key === 'recipes'" class="rounded-full bg-black/[0.05] px-1.5 py-0.5 text-[11px] leading-none tabular-nums text-mute">{{ customer.recipes.length }}</span>
          <span v-else-if="key === 'files'" class="rounded-full bg-black/[0.05] px-1.5 py-0.5 text-[11px] leading-none tabular-nums text-mute">{{ customer.files.length + (customer.output_count ?? customer.outputs.length) }}</span>
        </button>
      </nav>

      <!-- 处理逻辑 -->
      <section v-if="tab === 'logic'" class="mt-6">
        <div class="rounded-[26px] bg-white p-5 shadow-[0_4px_24px_-8px_rgba(34,34,66,0.10),0_0_0_1px_rgba(34,34,66,0.035)] sm:p-6">
          <div class="mb-4 flex flex-wrap items-center gap-x-3 gap-y-2">
            <div class="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-faint">
              <span v-if="customer.summary_updated_at">更新于 {{ timeAgo(customer.summary_updated_at) }}</span>
              <span v-if="customer.summary_by !== 'human'" title="相关资料变化时会自动更新">自动更新</span>
            </div>
            <div class="ml-auto flex shrink-0 gap-1">
              <button class="btn-text" :disabled="summarizing" @click="regenerate">
                <LoaderCircle v-if="summarizing" class="size-3.5 animate-spin" /><Sparkles v-else class="size-3.5" />重新总结
              </button>
            </div>
          </div>
          <CustomerLogicSummary v-if="customer.summary" :source="customer.summary" />
          <div v-else class="py-10 text-center text-sm text-faint">
            还没有处理逻辑说明<br />
            <button class="btn-line mt-3" @click="regenerate"><Sparkles class="size-4" />让 AI 总结</button>
          </div>
        </div>

      </section>

      <!-- 流程 -->
      <section v-else-if="tab === 'recipes'" class="mt-6 space-y-5">
        <RecipePanel v-for="r in customer.recipes" :key="r.recipe_id" :recipe="r" @run="(id) => (runDialog = { recipeId: id })" @changed="load" />
        <div v-if="!customer.recipes.length" class="card-soft py-14 text-center text-sm text-faint">还没有流程</div>
      </section>

      <!-- 文件 -->
      <section v-else-if="tab === 'files'" class="mt-6 space-y-6">
        <div class="card-soft p-5">
          <div class="mb-4 flex flex-wrap items-center gap-2">
            <h2 class="text-[15px] font-semibold">上传过的表格</h2>
            <div class="ml-auto flex items-center gap-2">
              <select v-model="uploadKind" class="select select-sm rounded-xl">
                <option value="input">要处理的表</option>
                <option value="expected">人工做好的结果</option>
              </select>
              <label class="btn-ink cursor-pointer py-2">
                <LoaderCircle v-if="uploading" class="size-4 animate-spin" /><Upload v-else class="size-4" />上传
                <input type="file" class="hidden" multiple accept=".xlsx,.xlsm,.xls,.csv" @change="upload" />
              </label>
            </div>
          </div>
          <table class="w-full text-sm">
            <thead class="text-left text-xs text-faint">
              <tr><th class="py-2 font-normal">文件</th><th class="font-normal">类型</th><th class="font-normal">内容</th><th class="font-normal">上传</th><th /></tr>
            </thead>
            <tbody>
              <tr v-for="f in inputFiles" :key="f.file_id" class="border-t border-line">
                <td class="py-2.5">
                  <span class="flex items-center gap-2">
                    <FileCheck2 v-if="f.kind === 'expected'" class="size-4 text-[#b7860b]" />
                    <FileSpreadsheet v-else class="size-4 text-fun-green" />{{ f.filename }}
                  </span>
                </td>
                <td><span class="tag" :class="f.kind === 'expected' ? '!bg-fun-yellow/20 !text-[#8a6400]' : ''">{{ f.kind_label }}</span></td>
                <td class="text-xs text-mute">{{ f.sheets.map((s) => `${s.sheet}：${s.rows} 行 × ${s.cols} 列`).join('；') }}</td>
                <td class="text-xs text-faint">{{ timeAgo(f.created_at) }}</td>
                <td class="text-right">
                  <button class="icon-btn" title="预览" @click="preview = f"><Eye class="size-4" /></button>
                  <a :href="f.download_url" class="icon-btn" title="下载"><Download class="size-4" /></a>
                  <button class="icon-btn hover:text-error" title="删除" @click="removeFile(f)"><Trash2 class="size-4" /></button>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-if="!inputFiles.length" class="py-8 text-center text-sm text-faint">还没有文件</p>
        </div>
        <div class="card-soft p-5">
          <h2 class="mb-3 text-[15px] font-semibold">生成的结果</h2>
          <div v-for="f in customer.outputs" :key="f.file_id" class="flex items-center gap-3 border-t border-line py-2.5 text-sm first:border-0">
            <FileSpreadsheet class="size-4" />
            <span class="min-w-0 flex-1 truncate">{{ f.filename }}</span>
            <span class="text-xs text-mute">{{ f.sheets.map((s) => `${s.sheet} ${s.rows} 行`).join(' · ') }}</span>
            <span class="text-xs text-faint">{{ timeAgo(f.created_at) }}</span>
            <button class="icon-btn" title="预览" @click="preview = f"><Eye class="size-4" /></button>
            <a :href="f.download_url" class="icon-btn" title="下载"><Download class="size-4" /></a>
          </div>
          <p v-if="!customer.outputs.length" class="py-6 text-center text-sm text-faint">还没有生成过结果</p>
        </div>
      </section>

      <!-- 运行记录 -->
      <section v-else-if="tab === 'runs'" class="mt-6">
        <div class="card-soft overflow-hidden">
          <table class="w-full text-sm">
            <thead class="bg-paper text-left text-xs text-faint">
              <tr><th class="px-5 py-2.5 font-normal">时间</th><th class="font-normal">类型</th><th class="font-normal">内容</th><th class="font-normal">状态</th><th class="font-normal">比对</th><th class="font-normal">耗时</th><th /></tr>
            </thead>
            <tbody>
              <tr v-for="r in customer.runs" :key="r.run_id" class="cursor-pointer border-t border-line hover:bg-paper/60" @click="runDetail = r.run_id">
                <td class="px-5 py-3 text-xs text-mute">{{ r.created_at.replace('T', ' ').slice(5, 16) }}</td>
                <td><span class="tag">{{ r.kind_label }}</span></td>
                <td class="max-w-[360px] truncate pr-3">{{ r.request_text || r.tool_sequence }}</td>
                <td><StatusPill :status="r.status" :label="r.status_label" /></td>
                <td class="text-xs text-mute">{{ r.verification ? percent(r.verification.overall_match_rate) : '—' }}</td>
                <td class="font-mono text-xs text-faint">{{ duration(r.duration_ms) }}</td>
                <td class="pr-4 text-right" @click.stop>
                  <a v-if="r.output_file_id" :href="fileApi.downloadUrl(r.output_file_id)" class="icon-btn" title="下载结果"><Download class="size-4" /></a>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-if="!customer.runs.length" class="py-12 text-center text-sm text-faint">还没有运行记录</p>
        </div>
      </section>

      <!-- 记忆 -->
      <section v-else class="mt-6">
        <MemoryPanel :customer-id="customer.id" :memories="customer.memories" :mapping="customer.field_mapping" @changed="load" />
        <div v-if="auth.user?.role === 'admin' || customer" class="mt-8 flex justify-end">
          <button class="btn-text text-error hover:!bg-error/5 hover:!text-error" @click="removeCustomer"><Trash2 class="size-4" />删除客户</button>
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
      v-if="runDialog && customer"
      :customer-id="customer.id"
      :recipes="customer.recipes"
      :files="customer.files"
      :initial-recipe-id="runDialog.recipeId"
      @close="runDialog = null"
      @done="load"
    />
    <RunDetailDialog v-if="runDetail" :run-id="runDetail" @close="runDetail = null" @changed="load" />
    <TableViewer v-if="preview" :title="preview.filename" :subtitle="preview.kind_label" :loader="loadPreview" @close="preview = null" />
  </div>
</template>
