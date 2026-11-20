<script setup lang="ts">
/**
 * 流程详情页（整页、单列）：头部（名称、版本、比对、流程图、执行）下面三个标签——
 * 概览（说明 → 输入文件 → 处理节点 → 输出文件 → 核对规则 → 列名对应）、运行记录、版本历史；标签记在地址栏 ?tab= 里。
 * 版本历史里点开某个版本，看它的完整信息和与上一版相比改了什么。
 * 设为主流程、流程数据、导出、删除这些低频操作放在页面最底部。
 */
import {
  ArrowLeft, Braces, Check, ChevronRight, FolderDown, LoaderCircle, Pencil, Play, ShieldCheck, Star, Trash2, Workflow, X,
} from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { customerApi, recipeApi } from '@/api'
import { errorText } from '@/api/http'
import type { CustomerDetail, RecipeVersion } from '@/api/types'
import BaseModal from '@/components/common/BaseModal.vue'
import RecipeRunsList from '@/components/customer/RecipeRunsList.vue'
import RecipeVersionDialog from '@/components/customer/RecipeVersionDialog.vue'
import RunRecipeDialog from '@/components/customer/RunRecipeDialog.vue'
import RecipeFlow from '@/components/recipe/RecipeFlow.vue'
import RecipeOverview from '@/components/recipe/RecipeOverview.vue'
import { percent, timeAgo } from '@/composables/chatModel'
import { workflowFiles } from '@/composables/customerWorkflow'
import { isDeliveredOutput } from '@/composables/deliveryFiles'
import { inspectorStates } from '@/composables/recipeFlowInspector'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import { useWorkspaceStore } from '@/stores/conversations'

const props = defineProps<{ id: string; recipeId: string }>()
const route = useRoute()
const router = useRouter()
const toast = useToast()
const { confirmDelete } = useConfirm()
const store = useWorkspaceStore()

type Tab = 'overview' | 'runs' | 'versions'
const TABS: { key: Tab; label: string }[] = [
  { key: 'overview', label: '概览' },
  { key: 'runs', label: '运行记录' },
  { key: 'versions', label: '版本历史' },
]
const tabFromRoute = () => TABS.find((item) => item.key === route.query.tab)?.key ?? 'overview'
const tab = ref<Tab>(tabFromRoute())
watch(() => route.query.tab, () => { tab.value = tabFromRoute() })
function setTab(next: Tab) {
  tab.value = next
  void router.replace({ query: { ...route.query, tab: next === 'overview' ? undefined : next } })
}

// ---- 数据：整个客户一起取，流程从里面找 ----
const customer = ref<CustomerDetail | null>(null)
const loading = ref(true)
let loadVersion = 0
let active = true

const recipe = computed(() => customer.value?.recipes.find((item) => item.recipe_id === props.recipeId) ?? null)
const primary = computed(() => !!recipe.value && customer.value?.primary_recipe_id === recipe.value.recipe_id)
const backTo = computed(() => ({ name: 'customer', params: { id: props.id }, query: { tab: 'recipes' } }))

async function load() {
  const version = ++loadVersion
  try {
    const result = await customerApi.get(props.id)
    if (!active || version !== loadVersion) return
    customer.value = result
    if (!result.recipes.some((item) => item.recipe_id === props.recipeId)) {
      toast.error('流程不存在或已被删除')
      void router.replace(backTo.value)
    }
  } catch (err) {
    if (!active || version !== loadVersion) return
    toast.error(errorText(err))
    void router.replace({ name: 'customers' })
  } finally {
    if (active && version === loadVersion) loading.value = false
  }
}

// ---- 版本历史 ----
const versions = ref<RecipeVersion[] | null>(null)
const versionError = ref('')
async function loadVersions() {
  versionError.value = ''
  try {
    versions.value = (await recipeApi.get(props.recipeId)).versions
  } catch (err) {
    versionError.value = errorText(err)
  }
}

onMounted(() => { void load(); void loadVersions() })
onBeforeUnmount(() => { active = false; loadVersion++ })
watch(() => [props.id, props.recipeId], () => {
  customer.value = null
  versions.value = null
  loading.value = true
  void load()
  void loadVersions()
})

// ---- 重命名：点铅笔就地改成输入框，回车保存、Esc 取消 ----
const MAX_NAME = 30  // 与后端 MAX_RECIPE_NAME 一致
const renaming = ref(false)
const draftName = ref('')
const savingName = ref(false)
const renameError = ref('')

function startRename() {
  draftName.value = recipe.value?.name ?? ''
  renameError.value = ''
  renaming.value = true
}
async function saveName() {
  const name = draftName.value.trim()
  if (savingName.value || !recipe.value) return
  if (!name || name === recipe.value.name) { renaming.value = false; return }
  savingName.value = true
  renameError.value = ''
  try {
    await recipeApi.rename(recipe.value.recipe_id, name)
    renaming.value = false
    await Promise.all([load(), store.refreshCustomers()])
  } catch (err) {
    renameError.value = errorText(err)
  } finally {
    savingName.value = false
  }
}

// ---- 派生数据 ----
const body = computed(() => recipe.value?.recipe)
const verification = computed(() => recipe.value?.verification)
const latestRun = computed(() => recipe.value?.runs.find((run) => run.recipe_version === recipe.value?.version))
const states = computed(() => inspectorStates([], latestRun.value?.steps ?? []))
const files = computed(() => workflowFiles([...(recipe.value?.files ?? []), ...(recipe.value?.output_files ?? [])]))
// 概览里能查看的文件：输入文件 + 最终结果（不含过程文件）
const shownFiles = computed(() => workflowFiles([...(recipe.value?.files ?? []), ...(recipe.value?.output_files ?? []).filter(isDeliveredOutput)]))

// ---- 版本：点开看这个版本的详情和改动 ----
const openedVersion = ref<RecipeVersion | null>(null)
const versionSource = (v: RecipeVersion) => (v.created_by === 'human' ? '人工修改' : v.version <= 1 ? 'AI 生成' : 'AI 修订')

// ---- 操作 ----
const flowView = ref<InstanceType<typeof RecipeFlow> | null>(null)
const running = ref(false)
const showJson = ref(false)
const settingPrimary = ref(false)

async function setPrimary() {
  if (settingPrimary.value || primary.value) return
  settingPrimary.value = true
  try {
    await customerApi.update(props.id, { primary_recipe_id: props.recipeId })
    await load()
    toast.success('已设为主流程')
    void store.refreshCustomers()
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    settingPrimary.value = false
  }
}

async function removeRecipe() {
  if (!recipe.value) return
  const recipeId = recipe.value.recipe_id
  if (!await confirmDelete({
    title: '删除这个流程？',
    target: recipe.value.name,
    description: '这个流程及其所有版本都会被删除，文件和运行记录会保留。删除后无法恢复。',
    confirmText: '删除流程',
    action: () => recipeApi.remove(recipeId),
  })) return
  toast.success('已删除流程')
  void store.refreshCustomers()
  void router.replace(backTo.value)
}
</script>

<template>
  <div class="h-full overflow-y-auto">
    <div v-if="loading" class="flex h-full items-center justify-center"><LoaderCircle class="size-6 animate-spin text-mute" /></div>

    <div v-else-if="recipe && body && customer" class="mx-auto w-full max-w-3xl px-6 pb-20 pt-6">
      <nav class="flex min-w-0 items-center gap-3 text-xs text-mute" aria-label="返回">
        <RouterLink :to="backTo" class="btn-ink size-8 shrink-0 p-0 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ink" title="返回客户的流程" aria-label="返回客户的流程"><ArrowLeft class="size-3.5" aria-hidden="true" /></RouterLink>
        <RouterLink :to="backTo" class="truncate hover:text-ink hover:underline underline-offset-4">{{ customer.name }}</RouterLink>
      </nav>

      <!-- 头部 -->
      <header class="mt-6">
        <div class="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
          <div class="min-w-0 flex-1 basis-72">
            <form v-if="renaming" class="flex items-center gap-1.5" @submit.prevent="saveName">
              <input v-model="draftName" class="input min-w-0 flex-1 rounded-xl" aria-label="流程名称" :maxlength="MAX_NAME" autofocus @keydown.esc.stop="renaming = false" />
              <span class="shrink-0 text-[11px] tabular-nums" :class="draftName.length >= MAX_NAME ? 'text-[#b7770c]' : 'text-faint'" aria-live="polite">{{ draftName.length }}/{{ MAX_NAME }}</span>
              <button type="submit" class="icon-btn shrink-0 !text-[#6265dc]" :disabled="savingName" aria-label="保存名称" title="保存"><LoaderCircle v-if="savingName" class="size-4 animate-spin" /><Check v-else class="size-4" /></button>
              <button type="button" class="icon-btn shrink-0" aria-label="取消" title="取消" @click="renaming = false"><X class="size-4" /></button>
            </form>
            <div v-else class="flex min-w-0 items-center gap-2">
              <h1 class="min-w-0 break-words text-2xl font-semibold leading-snug tracking-tight sm:text-[26px]">{{ recipe.name }}</h1>
              <button type="button" class="icon-btn size-7 shrink-0" aria-label="修改流程名称" title="修改名称" @click="startRename"><Pencil class="size-3.5" /></button>
            </div>
            <p v-if="renameError" class="mt-1 text-xs text-error" role="alert">{{ renameError }}</p>

            <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[13px] text-mute">
              <span class="rounded-md bg-ink px-1.5 py-0.5 text-[11px] font-semibold text-white">v{{ recipe.version }}</span>
              <span v-if="primary" class="inline-flex items-center gap-1 text-[#6265DC]"><Star class="size-3.5 fill-current" />主流程</span>
              <span>{{ timeAgo(recipe.updated_at) }}更新</span>
              <span
                v-if="verification" class="inline-flex items-center gap-1 font-medium"
                :class="verification.informational ? 'text-info' : verification.status === 'pass' ? 'text-success' : 'text-[#b7770c]'"
              ><ShieldCheck class="size-3.5" />{{ verification.informational ? '与原样例' : '与人工结果' }} {{ percent(verification.match_rate) }}</span>
            </div>
          </div>

          <div class="flex shrink-0 items-center gap-2">
            <button type="button" class="btn-line size-9 justify-center p-0" title="流程图" aria-label="查看流程图" aria-haspopup="dialog" @click="flowView?.openFullscreen()"><Workflow class="size-4" /></button>
            <button type="button" class="btn-line size-9 justify-center p-0" title="用新文件执行" aria-label="用新文件执行" @click="running = true"><Play class="size-4" /></button>
          </div>
        </div>
      </header>

      <!-- 标签 -->
      <div class="mt-8 inline-flex rounded-full bg-paper/80 p-1.5" role="tablist" aria-label="流程详情">
        <button
          v-for="item in TABS" :key="item.key" type="button" role="tab" :aria-selected="tab === item.key"
          class="rounded-full px-4 py-1.5 text-sm transition focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ink/40"
          :class="tab === item.key ? 'bg-white font-medium text-ink shadow-[0_2px_8px_rgba(34,34,66,0.08)]' : 'text-mute hover:bg-white/60 hover:text-ink'"
          @click="setTab(item.key)"
        >{{ item.label }}</button>
      </div>

      <!-- 概览 -->
      <RecipeOverview v-if="tab === 'overview'" class="mt-8" :recipe="body" :flow="recipe.flow" :files="shownFiles" :states="states" />

      <!-- 运行记录：按天分组，每组自带标题和卡片 -->
      <RecipeRunsList v-else-if="tab === 'runs'" class="mt-8" :recipe-id="recipe.recipe_id" :recipe-name="recipe.name" :customer-id="recipe.customer_id" @changed="load" />

      <!-- 版本历史：点开看这个版本的完整信息和改动 -->
      <section v-else class="mt-8">
        <p v-if="versionError" class="ver-card py-5 text-center text-sm text-error" role="alert">{{ versionError }}</p>
        <p v-else-if="!versions" class="ver-card flex justify-center py-5"><LoaderCircle class="size-5 animate-spin text-mute" /></p>
        <ol v-else class="ver-card">
          <li v-for="v in versions" :key="v.version">
            <button type="button" class="ver-row" :aria-label="`查看 v${v.version} 的版本详情`" @click="openedVersion = v">
              <span class="tag shrink-0" :class="v.latest ? '!bg-ink !text-white' : ''">v{{ v.version }}</span>
              <span class="min-w-0 flex-1">
                <span class="block truncate text-sm" :class="v.note ? 'text-ink' : 'text-faint'">{{ v.note || '—' }}</span>
                <span class="mt-0.5 block text-xs text-faint">
                  {{ [versionSource(v) === v.note ? '' : versionSource(v), v.runs !== undefined ? `运行 ${v.runs} 次` : ''].filter(Boolean).join(' · ') }}
                </span>
              </span>
              <span v-if="v.latest" class="shrink-0 text-xs font-medium text-[#6265dc]">当前</span>
              <span class="hidden shrink-0 text-xs tabular-nums text-faint sm:inline">{{ v.created_at.slice(0, 16).replace('T', ' ') }}</span>
              <ChevronRight class="size-4 shrink-0 text-faint" />
            </button>
          </li>
        </ol>
      </section>

      <!-- 低频操作 -->
      <footer class="mt-14 flex flex-wrap items-center gap-x-1 gap-y-2 border-t border-line pt-5">
        <button type="button" class="btn-text text-xs" @click="showJson = true"><Braces class="size-3.5" />流程数据</button>
        <a :href="`/api/recipes/${recipe.recipe_id}/package`" class="btn-text text-xs" download><FolderDown class="size-3.5" />流程导出</a>
        <span class="ml-auto flex items-center gap-2">
          <button v-if="!primary" type="button" class="btn-line" :disabled="settingPrimary" @click="setPrimary"><Star class="size-4" />设为主流程</button>
          <button type="button" class="btn-line border-error/20 text-error hover:border-error/40 hover:bg-error/5" @click="removeRecipe"><Trash2 class="size-4" />删除流程</button>
        </span>
      </footer>
    </div>

    <!-- 页面里不画流程图，只提供全屏（头部「流程图」按钮打开） -->
    <RecipeFlow
      v-if="recipe"
      ref="flowView"
      toolbar-placement="hidden" :inline-canvas="false"
      :flow="recipe.flow" :states="states"
      :context="{ title: recipe.name, recipeId: recipe.recipe_id, version: recipe.version, customerId: recipe.customer_id, conversationId: recipe.conversation_id, recipe: recipe.recipe, files, runId: latestRun?.run_id }"
      @changed="load"
    />
    <RecipeVersionDialog v-if="openedVersion" :key="openedVersion.version" :recipe-id="recipeId" :item="openedVersion" @close="openedVersion = null" />
    <RunRecipeDialog v-if="running && recipe" :customer-id="customer?.id" :recipe="recipe" @close="running = false" @done="load" />
    <BaseModal v-if="showJson && recipe" :title="`${recipe.name} · 流程数据`" width="max-w-4xl" @close="showJson = false">
      <pre class="max-h-[65vh] overflow-auto rounded-xl bg-paper p-4 font-mono text-xs leading-relaxed">{{ JSON.stringify(recipe.recipe, null, 2) }}</pre>
    </BaseModal>
  </div>
</template>

<style scoped>
/* 版本列表：一张卡片，一行一个版本，整行可点 */
.ver-card { margin: 0; border: 1px solid var(--color-line); border-radius: 16px; background: #fff; padding: 8px; list-style: none; }
.ver-card > li + li { border-top: 1px solid #f0f0f4; }
.ver-row { display: flex; width: 100%; align-items: center; gap: 12px; border-radius: 12px; padding: 12px; text-align: left; transition: background-color .15s; }
.ver-row:hover { background: var(--color-paper); }
.ver-row:focus-visible { outline: 2px solid var(--color-ink); outline-offset: 2px; }
</style>
