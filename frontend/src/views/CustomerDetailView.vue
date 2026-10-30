<script setup lang="ts">
/**
 * 客户详情：
 * - 处理逻辑：AI 写的「Excel 整体处理逻辑」说明 + 主流程图
 * - 流程：每个 Recipe 的流程图、输入要求、校验规则、版本，可用新文件执行
 * - 文件：客户上传过的 Excel（含人工结果样例）和生成的结果
 * - 运行记录：每次执行的明细（可单步查看、断点重跑）
 * - 记忆：业务规则、偏好、字段映射
 */
import {
  ArrowLeft, BookOpen, Download, Eye, FileCheck2, FileSpreadsheet, LoaderCircle, MessageSquarePlus, Pencil, Play, Sparkles,
  Trash2, Upload,
} from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { conversationApi, customerApi, fileApi } from '@/api'
import { errorText } from '@/api/http'
import type { CustomerDetail, FileInfo } from '@/api/types'
import Mascot from '@/components/brand/Mascot.vue'
import MarkdownView from '@/components/common/MarkdownView.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import TableViewer from '@/components/common/TableViewer.vue'
import CustomerFormDialog from '@/components/customer/CustomerFormDialog.vue'
import CustomerFormulas from '@/components/customer/CustomerFormulas.vue'
import FormulaLibraryDialog from '@/components/customer/FormulaLibraryDialog.vue'
import MemoryPanel from '@/components/customer/MemoryPanel.vue'
import RecipePanel from '@/components/customer/RecipePanel.vue'
import RunDetailDialog from '@/components/customer/RunDetailDialog.vue'
import RunRecipeDialog from '@/components/customer/RunRecipeDialog.vue'
import RecipeFlow from '@/components/recipe/RecipeFlow.vue'
import { duration, percent, timeAgo } from '@/composables/chatModel'
import { useToast } from '@/composables/useToast'
import { useAuthStore } from '@/stores/auth'
import { useWorkspaceStore } from '@/stores/conversations'

const props = defineProps<{ id: string }>()
const route = useRoute()
const router = useRouter()
const toast = useToast()
const store = useWorkspaceStore()
const auth = useAuthStore()

type Tab = 'logic' | 'recipes' | 'files' | 'runs' | 'memory'
const TABS: [Tab, string][] = [['logic', '处理逻辑'], ['recipes', '流程'], ['files', '文件'], ['runs', '运行记录'], ['memory', '记忆与字段']]
const tab = ref<Tab>((route.query.tab as Tab) || 'logic')
const customer = ref<CustomerDetail | null>(null)
const loading = ref(true)
const summarizing = ref(false)
const editingSummary = ref(false)
const summaryDraft = ref('')
const editing = ref(false)
const runDialog = ref<{ recipeId: string | null } | null>(null)
const runDetail = ref<string | null>(null)
const preview = ref<FileInfo | null>(null)
const formulas = ref(false)
const uploadKind = ref<'input' | 'expected'>('input')
const uploading = ref(false)

async function load() {
  try {
    customer.value = await customerApi.get(props.id)
  } catch (err) {
    toast.error(errorText(err))
    router.replace({ name: 'customers' })
  } finally {
    loading.value = false
  }
}
onMounted(load)

const mainRecipe = computed(() => customer.value?.recipes.find((r) => r.status === 'active') ?? customer.value?.recipes[0])
const inputFiles = computed(() => customer.value?.files ?? [])

async function newChat() {
  try {
    const conv = await conversationApi.create({ customer_id: props.id, mode: 'analyze' })
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
    toast.success(result.by_ai ? 'AI 已重新总结处理逻辑' : '已按流程结构生成（配置模型后可以让 AI 写得更好）')
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    summarizing.value = false
  }
}

async function saveSummary() {
  try {
    await customerApi.update(props.id, { summary: summaryDraft.value })
    if (customer.value) {
      customer.value.summary = summaryDraft.value
      customer.value.summary_by = 'human'
    }
    editingSummary.value = false
  } catch (err) {
    toast.error(errorText(err))
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
  if (!window.confirm(`删除文件「${f.filename}」？`)) return
  await fileApi.remove(f.file_id).catch((err) => toast.error(errorText(err)))
  await load()
}

async function removeCustomer() {
  if (!customer.value || !window.confirm(`删除客户「${customer.value.name}」？客户的流程、记忆会一起删除，文件记录保留。`)) return
  try {
    await customerApi.remove(props.id)
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
    <div v-else-if="customer" class="mx-auto max-w-[1200px] px-8 pb-16 pt-6">
      <RouterLink :to="{ name: 'customers' }" class="btn-text -ml-2"><ArrowLeft class="size-4" />全部客户</RouterLink>

      <!-- 头部 -->
      <header class="mt-4 flex flex-wrap items-center gap-6">
        <div class="relative flex size-[132px] shrink-0 items-end justify-center overflow-hidden rounded-[36px] bg-paper">
          <div class="translate-y-5"><Mascot :variant="customer.mascot" :size="140" :shadow="false" /></div>
        </div>
        <div class="min-w-0 flex-1">
          <div class="flex flex-wrap items-baseline gap-3">
            <h1 class="text-[30px] font-semibold tracking-tight">{{ customer.name }}</h1>
            <span v-if="customer.code" class="font-mono text-sm text-faint">{{ customer.code }}</span>
            <button class="icon-btn" title="编辑" @click="editing = true"><Pencil class="size-4" /></button>
          </div>
          <p class="mt-1 max-w-2xl text-sm text-mute">{{ customer.description || '还没有介绍' }}</p>
          <div class="mt-3 flex flex-wrap gap-4 text-sm text-mute">
            <span><b class="text-ink">{{ customer.recipe_count }}</b> 个流程</span>
            <span><b class="text-ink">{{ customer.file_count }}</b> 个文件</span>
            <span><b class="text-ink">{{ customer.conversation_count }}</b> 个对话</span>
            <span>最近活动 {{ timeAgo(customer.last_activity) }}</span>
          </div>
        </div>
        <div class="flex flex-wrap gap-2">
          <button class="btn-ink" @click="newChat"><MessageSquarePlus class="size-4" />新建对话</button>
          <button v-if="customer.recipes.length" class="btn-line" @click="runDialog = { recipeId: null }"><Play class="size-4" />用新文件执行</button>
        </div>
      </header>

      <!-- 标签 -->
      <nav class="mt-8 flex gap-1 border-b border-line">
        <button
          v-for="[key, label] in TABS"
          :key="key"
          class="-mb-px border-b-2 px-4 py-2.5 text-sm transition"
          :class="tab === key ? 'border-ink font-medium text-ink' : 'border-transparent text-mute hover:text-ink'"
          @click="tab = key; router.replace({ query: { tab: key } })"
        >
          {{ label }}
        </button>
      </nav>

      <!-- 处理逻辑 -->
      <section v-if="tab === 'logic'" class="mt-6 grid gap-6 lg:grid-cols-[1.1fr_1fr]">
        <div class="card-soft p-6">
          <div class="mb-3 flex items-center gap-2">
            <BookOpen class="size-4" />
            <h2 class="text-[15px] font-semibold">Excel 整体处理逻辑</h2>
            <span v-if="customer.summary_updated_at" class="text-xs text-faint">更新于 {{ timeAgo(customer.summary_updated_at) }}</span>
            <span
              class="tag"
              :title="customer.summary_by === 'human' ? '你手动编辑过，流程变化时不会自动覆盖；点「重新总结」恢复自动更新' : '流程有变化时会自动更新'"
            >
              {{ customer.summary_by === 'human' ? '手动编辑 · 不自动更新' : '自动更新' }}
            </span>
            <div class="ml-auto flex gap-1">
              <button class="btn-text" :disabled="summarizing" @click="regenerate">
                <LoaderCircle v-if="summarizing" class="size-3.5 animate-spin" /><Sparkles v-else class="size-3.5" />重新总结
              </button>
              <button class="btn-text" @click="editingSummary = !editingSummary; summaryDraft = customer.summary"><Pencil class="size-3.5" />编辑</button>
            </div>
          </div>
          <div v-if="editingSummary">
            <textarea v-model="summaryDraft" class="field h-96 font-mono text-[13px]" />
            <div class="mt-2 flex justify-end gap-2">
              <button class="btn-line" @click="editingSummary = false">取消</button>
              <button class="btn-ink" @click="saveSummary">保存</button>
            </div>
          </div>
          <MarkdownView v-else-if="customer.summary" :source="customer.summary" />
          <div v-else class="py-10 text-center text-sm text-faint">
            还没有处理逻辑说明<br />
            <button class="btn-line mt-3" @click="regenerate"><Sparkles class="size-4" />让 AI 总结</button>
          </div>
        </div>

        <div class="space-y-6">
          <div v-if="mainRecipe" class="card-soft p-5">
            <div class="mb-3 flex items-center gap-2">
              <h2 class="text-[15px] font-semibold">主流程：{{ mainRecipe.name }}</h2>
              <StatusPill :status="mainRecipe.status" />
              <button class="btn-text ml-auto" @click="tab = 'recipes'">详情 →</button>
            </div>
            <RecipeFlow :flow="mainRecipe.flow" direction="vertical" />
            <div v-if="mainRecipe.verification" class="mt-3 text-xs text-mute">
              与人工结果比对：{{ mainRecipe.verification.summary }}
            </div>
          </div>
          <div v-else class="card-soft flex flex-col items-center p-8 text-center">
            <Mascot :variant="customer.mascot" :size="72" mood="thinking" />
            <p class="mt-3 text-sm text-mute">还没有处理流程。<br />新建对话、上传表格，切换到「修改」模式让 AI 整理，<br />就会沉淀成可以每月重复执行的流程。</p>
            <button class="btn-ink mt-4" @click="newChat"><MessageSquarePlus class="size-4" />新建对话</button>
          </div>
          <div class="card-soft p-5">
            <div class="mb-2 flex items-center justify-between">
              <h2 class="text-[15px] font-semibold">最近对话</h2>
            </div>
            <RouterLink v-for="c in customer.conversations.slice(0, 6)" :key="c.id" :to="{ name: 'chat', params: { id: c.id } }" class="flex items-center gap-2 rounded-xl px-2 py-2 text-sm hover:bg-paper">
              <span class="min-w-0 flex-1 truncate">{{ c.title }}</span>
              <span class="tag">{{ c.mode === 'modify' ? '修改' : '分析' }}</span>
              <span class="text-xs text-faint">{{ timeAgo(c.updated_at) }}</span>
            </RouterLink>
            <p v-if="!customer.conversations.length" class="py-3 text-center text-sm text-faint">还没有对话</p>
          </div>
        </div>
      </section>

      <!-- 流程 -->
      <section v-else-if="tab === 'recipes'" class="mt-6 space-y-5">
        <div class="flex items-center justify-between">
          <p class="text-sm text-mute">流程由一步步「公式 + 参数」组成：内置公式覆盖常见操作，客户独有逻辑单独标出。正常执行不调用 AI。</p>
          <button class="btn-line" @click="formulas = true"><BookOpen class="size-4" />公式库</button>
        </div>
        <RecipePanel v-for="r in customer.recipes" :key="r.recipe_id" :recipe="r" @run="(id) => (runDialog = { recipeId: id })" @changed="load" />
        <div v-if="!customer.recipes.length" class="card-soft py-14 text-center text-sm text-faint">还没有流程</div>
        <CustomerFormulas
          :customer-id="customer.id"
          :formulas="customer.formulas"
          :files="customer.files"
          :field-mapping="customer.field_mapping"
          @changed="load"
        />
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
    <FormulaLibraryDialog v-if="formulas" :customer-id="customer?.id" @close="formulas = false" />
  </div>
</template>
