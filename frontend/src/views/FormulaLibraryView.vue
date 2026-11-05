<script setup lang="ts">
import { FlaskConical, LoaderCircle, Pencil, Plus, Power, Search, Sparkles, Trash2, TriangleAlert } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { customerApi, recipeApi } from '@/api'
import { errorText } from '@/api/http'
import type { CustomerCard, CustomerDetail, CustomerFormula, FormulaInfo } from '@/api/types'
import FormulaLibraryIcon from '@/components/brand/FormulaLibraryIcon.vue'
import FunObject, { type FunKind } from '@/components/brand/FunObject.vue'
import BaseModal from '@/components/common/BaseModal.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import FormulaAIGeneratorDialog from '@/components/customer/FormulaAIGeneratorDialog.vue'
import FormulaEditorDialog from '@/components/customer/FormulaEditorDialog.vue'
import { formulaArt, timeAgo } from '@/composables/chatModel'
import { paramTypeLabel } from '@/composables/formulaDraft'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'

type LibraryFormula = CustomerFormula & { customerId: string; customerName: string }
type EditorState = { customer: CustomerDetail; formula: CustomerFormula | null; trial: boolean; initialBody?: Record<string, unknown> }

const route = useRoute()
const router = useRouter()
const toast = useToast()
const { confirmDelete } = useConfirm()
const customers = ref<CustomerCard[]>([])
const custom = ref<LibraryFormula[]>([])
const builtin = ref<FormulaInfo[]>([])
const loading = ref(true)
const error = ref('')
const query = ref('')
const tab = ref<'customer' | 'builtin'>('customer')
const customerFilter = ref(typeof route.query.customer === 'string' ? route.query.customer : '')
const statusFilter = ref('')
const categoryFilter = ref('')
const choosing = ref<'manual' | 'ai' | null>(null)
const targetCustomer = ref('')
const opening = ref(false)
const editor = ref<EditorState | null>(null)
const aiCustomer = ref<CustomerDetail | null>(null)
const selectedBuiltin = ref<FormulaInfo | null>(null)

const filteredCustom = computed(() => {
  const q = query.value.trim().toLowerCase()
  return custom.value.filter((f) => (!customerFilter.value || f.customerId === customerFilter.value)
    && (!statusFilter.value || f.status === statusFilter.value)
    && (!q || `${f.name} ${f.id} ${f.intent} ${f.customerName}`.toLowerCase().includes(q)))
})
const categories = computed(() => [...new Set(builtin.value.map((f) => f.category))])
const filteredBuiltin = computed(() => {
  const q = query.value.trim().toLowerCase()
  return builtin.value.filter((f) => (!categoryFilter.value || f.category === categoryFilter.value)
    && (!q || `${f.name} ${f.id} ${f.intent} ${f.category}`.toLowerCase().includes(q)))
})
const idsFor = (customerId: string) => custom.value.filter((f) => f.customerId === customerId).map((f) => f.id)
const problemsOf = (f: CustomerFormula) => f.used_by.flatMap((u) => u.problems.map((p) => `「${u.name}」${p}`))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [people, catalog] = await Promise.all([customerApi.list(), recipeApi.formulas()])
    customers.value = people
    builtin.value = catalog.builtin
    const lists = await Promise.all(people.map(async (c) => (await customerApi.formulas(c.id))
      .map((f) => ({ ...f, customerId: c.id, customerName: c.name }))))
    custom.value = lists.flat()
    if (customerFilter.value && !people.some((c) => c.id === customerFilter.value)) customerFilter.value = ''
  } catch (err) {
    error.value = errorText(err)
  } finally {
    loading.value = false
  }
}
onMounted(load)

async function start(mode: 'manual' | 'ai') {
  targetCustomer.value = customerFilter.value || (customers.value.length === 1 ? customers.value[0]!.id : '')
  choosing.value = mode
  if (targetCustomer.value) await proceed()
}

async function proceed() {
  if (!targetCustomer.value || !choosing.value || opening.value) return
  opening.value = true
  try {
    const customer = await customerApi.get(targetCustomer.value)
    if (choosing.value === 'ai') aiCustomer.value = customer
    else editor.value = { customer, formula: null, trial: false }
    choosing.value = null
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    opening.value = false
  }
}

async function openEditor(f: LibraryFormula, trial = false) {
  if (opening.value) return
  opening.value = true
  try {
    const customer = await customerApi.get(f.customerId)
    editor.value = { customer, formula: f, trial }
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    opening.value = false
  }
}

async function reviewGenerated(body: Record<string, unknown>) {
  const customer = aiCustomer.value
  if (!customer) return
  // Refresh after reference uploads so the editor can also use those files for a trial.
  opening.value = true
  try {
    const fresh = await customerApi.get(customer.id)
    editor.value = { customer: fresh, formula: null, trial: false, initialBody: body }
    aiCustomer.value = null
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    opening.value = false
  }
}

async function onSaved(formula: CustomerFormula) {
  editor.value = null
  tab.value = 'customer'
  toast.success(`已保存「${formula.name}」`)
  await load()
}

async function toggle(f: LibraryFormula) {
  try {
    await customerApi.setFormulaStatus(f.customerId, f.id, f.status === 'deprecated' ? 'active' : 'deprecated')
    await load()
  } catch (err) {
    toast.error(errorText(err))
  }
}

async function remove(f: LibraryFormula) {
  const names = [...new Set(f.used_by.map((u) => `「${u.name}」`))].join('、')
  const impact = names ? `正在使用它的流程：${names}。${f.overrides_builtin ? '删除后将改用同名内置公式。' : '删除后需要为这些流程配置其他公式。'}` : ''
  if (!await confirmDelete({
    title: '删除这个公式？', target: `${f.customerName} · ${f.name}`,
    description: `这个公式的所有版本都会删除，删除后无法恢复。${impact}`,
    confirmText: '删除公式', action: () => customerApi.removeFormula(f.customerId, f.id),
  })) return
  toast.success('已删除')
  await load()
}
</script>

<template>
  <div class="h-full overflow-y-auto">
    <div class="mx-auto max-w-[1200px] px-4 py-8 sm:px-8 sm:py-10">
      <header class="flex flex-wrap items-center gap-3 sm:gap-4">
        <h1 class="mr-auto text-2xl font-semibold tracking-tight sm:text-[28px]">公式库</h1>
        <label class="flex min-w-0 flex-1 items-center gap-2 rounded-full bg-paper/80 px-3.5 py-2.5 sm:max-w-56">
          <Search class="size-4 shrink-0 text-faint" />
          <input v-model="query" aria-label="搜索公式" class="w-full min-w-0 bg-transparent text-sm outline-none" />
        </label>
        <button class="btn-line shrink-0" :disabled="loading || opening" @click="start('manual')"><Plus class="size-4" />手动新增</button>
        <button class="btn-ink shrink-0 !bg-[#298c7a] hover:!bg-[#227867]" :disabled="loading || opening" @click="start('ai')"><Sparkles class="size-4" />AI 生成</button>
      </header>

      <div class="mt-7 flex flex-wrap items-center gap-3">
        <div class="flex rounded-full bg-paper/80 p-1" role="tablist" aria-label="公式类型">
          <button role="tab" :aria-selected="tab === 'customer'" class="rounded-full px-4 py-2 text-sm transition" :class="tab === 'customer' ? 'bg-white font-medium text-ink shadow-sm' : 'text-mute'" @click="tab = 'customer'">客户公式 <span class="ml-1 text-xs text-faint">{{ custom.length }}</span></button>
          <button role="tab" :aria-selected="tab === 'builtin'" class="rounded-full px-4 py-2 text-sm transition" :class="tab === 'builtin' ? 'bg-white font-medium text-ink shadow-sm' : 'text-mute'" @click="tab = 'builtin'">内置公式 <span class="ml-1 text-xs text-faint">{{ builtin.length }}</span></button>
        </div>
        <template v-if="tab === 'customer'">
          <select v-model="customerFilter" aria-label="按客户筛选" class="select ml-auto w-auto rounded-full bg-white text-xs">
            <option value="">全部客户</option><option v-for="c in customers" :key="c.id" :value="c.id">{{ c.name }}</option>
          </select>
          <select v-model="statusFilter" aria-label="按状态筛选" class="select w-auto rounded-full bg-white text-xs"><option value="">全部状态</option><option value="active">已启用</option><option value="deprecated">已停用</option></select>
        </template>
        <select v-else v-model="categoryFilter" aria-label="按类别筛选" class="select ml-auto w-auto rounded-full bg-white text-xs"><option value="">全部类别</option><option v-for="c in categories" :key="c" :value="c">{{ c }}</option></select>
      </div>

      <div v-if="error" class="mt-6 rounded-2xl bg-error/5 p-4 text-sm text-error" role="alert">{{ error }}<button class="btn-text ml-3" @click="load">重试</button></div>
      <div v-else-if="loading" class="flex justify-center py-20"><LoaderCircle class="size-5 animate-spin text-faint" /></div>
      <template v-else-if="tab === 'customer'">
        <div v-if="filteredCustom.length" class="mt-6 grid gap-4 md:grid-cols-2">
          <article v-for="f in filteredCustom" :key="`${f.customerId}:${f.id}`" class="formula-card flex min-w-0 flex-col rounded-[22px] bg-white p-5" :class="f.status === 'deprecated' ? 'opacity-65' : ''">
            <div class="flex items-start gap-3">
              <span class="flex size-10 shrink-0 items-center justify-center rounded-[14px] bg-[#eef8f5]"><FormulaLibraryIcon /></span>
              <div class="min-w-0 flex-1"><h2 class="break-words text-[15px] font-semibold">{{ f.name }}</h2><p class="mt-1 text-xs text-faint">{{ f.customerName }}</p></div>
              <StatusPill :status="f.status" :label="f.status_label" />
            </div>
            <p class="mt-4 line-clamp-3 text-[13px] leading-7 text-mute">{{ f.intent || '暂未填写用途' }}</p>
            <div v-if="problemsOf(f).length" class="mt-3 rounded-xl bg-error/5 px-3 py-2 text-xs leading-6 text-error"><p v-for="p in problemsOf(f)" :key="p" class="flex gap-1.5"><TriangleAlert class="mt-1 size-3.5 shrink-0" />{{ p }}</p></div>
            <div class="mt-auto flex flex-wrap items-center gap-2 pt-5 text-[11px] text-faint"><span>{{ f.used_by.length ? `${f.used_by.length} 个流程使用` : '尚未用于流程' }}</span><span>· v{{ f.version }}</span><span class="ml-auto">{{ timeAgo(f.updated_at) }}</span></div>
            <div class="mt-3 flex items-center gap-1 border-t border-line/60 pt-3">
              <button class="btn-text mr-auto text-xs" :disabled="opening" @click="openEditor(f, true)"><FlaskConical class="size-3.5" />试运行</button>
              <button class="icon-btn" :title="`编辑公式 ${f.name}`" :disabled="opening" @click="openEditor(f)"><Pencil class="size-4" /></button>
              <button class="icon-btn disabled:opacity-35" :title="f.status !== 'deprecated' && f.used_by.length ? '正在被流程使用，暂不能停用' : f.status === 'deprecated' ? '启用公式' : '停用公式'" :disabled="f.status !== 'deprecated' && f.used_by.length > 0" @click="toggle(f)"><Power class="size-4" /></button>
              <button class="icon-btn hover:text-error" :title="`删除公式 ${f.name}`" @click="remove(f)"><Trash2 class="size-4" /></button>
            </div>
          </article>
        </div>
        <div v-else class="py-24 text-center text-sm text-faint"><FormulaLibraryIcon class="mx-auto mb-4 !size-10 opacity-65" />{{ custom.length ? '没有找到符合条件的公式' : '还没有客户公式' }}</div>
      </template>
      <template v-else>
        <div v-if="filteredBuiltin.length" class="mt-6 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          <button v-for="f in filteredBuiltin" :key="f.id" class="formula-card flex min-w-0 flex-col rounded-[22px] bg-white p-5 text-left" @click="selectedBuiltin = f">
            <div class="flex w-full items-center gap-2.5"><FunObject :kind="formulaArt(f.id) as FunKind" :size="28" /><h2 class="min-w-0 flex-1 text-[15px] font-semibold">{{ f.name }}</h2><span class="text-[11px] text-faint">{{ f.category }}</span></div>
            <p class="mt-3 line-clamp-3 text-[13px] leading-7 text-mute">{{ f.intent }}</p>
          </button>
        </div>
        <p v-else class="py-24 text-center text-sm text-faint">没有找到符合条件的公式</p>
      </template>
    </div>

    <BaseModal v-if="choosing" :title="choosing === 'ai' ? 'AI 生成公式' : '手动新增公式'" width="max-w-md" @close="!opening && (choosing = null)">
      <template v-if="customers.length"><label class="block text-sm font-medium">所属客户<span class="ml-1 text-error">*</span><select v-model="targetCustomer" class="select mt-3 w-full rounded-xl" aria-label="公式所属客户" :disabled="opening"><option disabled value="">请选择客户</option><option v-for="c in customers" :key="c.id" :value="c.id">{{ c.name }}</option></select></label><div class="mt-5 flex justify-end"><button class="btn-ink" :disabled="!targetCustomer || opening" @click="proceed"><LoaderCircle v-if="opening" class="size-4 animate-spin" />继续</button></div></template>
      <div v-else class="space-y-5 text-sm text-mute"><p>先添加一个客户，再为它配置公式。</p><button class="btn-ink" @click="router.push({ name: 'customers' })">前往客户库</button></div>
    </BaseModal>

    <FormulaAIGeneratorDialog v-if="aiCustomer" :customer-id="aiCustomer.id" :files="aiCustomer.files" @close="aiCustomer = null" @generated="reviewGenerated" />
    <FormulaEditorDialog v-if="editor" :customer-id="editor.customer.id" :formula="editor.formula" :existing-ids="idsFor(editor.customer.id)" :files="editor.customer.files" :field-mapping="editor.customer.field_mapping" :focus-trial="editor.trial" :initial-body="editor.initialBody" @close="editor = null" @saved="onSaved" />

    <BaseModal v-if="selectedBuiltin" :title="selectedBuiltin.name" width="max-w-lg" @close="selectedBuiltin = null">
      <p class="text-sm leading-7 text-mute">{{ selectedBuiltin.intent }}</p><p class="mt-2 text-xs text-faint">内置公式 · {{ selectedBuiltin.category }}</p>
      <dl class="mt-5 space-y-3"><div v-for="(p, name) in selectedBuiltin.params" :key="name" class="rounded-xl bg-paper/70 px-4 py-3"><dt class="text-[13px] font-medium">{{ p.desc || name }}<span v-if="p.required" class="ml-1 text-error">*</span></dt><dd class="mt-1 text-xs text-faint">{{ name }} · {{ paramTypeLabel(p.type) }}</dd></div></dl>
    </BaseModal>
  </div>
</template>

<style scoped>
.formula-card { box-shadow: 0 4px 24px #2222420b, 0 1px 4px #22224205; transition: box-shadow 200ms ease, transform 200ms ease; }
.formula-card:hover { box-shadow: 0 7px 28px #22224212, 0 2px 6px #22224207; }
button.formula-card:hover { transform: translateY(-1px); }
@media (prefers-reduced-motion: reduce) { .formula-card { transition: none; } button.formula-card:hover { transform: none; } }
</style>
