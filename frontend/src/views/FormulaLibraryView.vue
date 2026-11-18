<script setup lang="ts">
import { ChevronRight, FlaskConical, Lightbulb, X, LoaderCircle, Pencil, Search, Sparkles, Trash2, TriangleAlert, WandSparkles } from '@lucide/vue'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { customerApi, recipeApi } from '@/api'
import { errorText } from '@/api/http'
import type { CustomerCard, CustomerDetail, CustomerFormula, FormulaInfo, FormulaSuggestion } from '@/api/types'
import FormulaLibraryIcon from '@/components/brand/FormulaLibraryIcon.vue'
import FunObject, { type FunKind } from '@/components/brand/FunObject.vue'
import BaseModal from '@/components/common/BaseModal.vue'
import FormulaAIGeneratorDialog from '@/components/customer/FormulaAIGeneratorDialog.vue'
import FormulaEditorDialog from '@/components/customer/FormulaEditorDialog.vue'
import FormulaTrialPanel from '@/components/customer/FormulaTrialPanel.vue'
import { formulaArt, timeAgo } from '@/composables/chatModel'
import { bodyFromFormula, draftFromBody, paramTypeLabel } from '@/composables/formulaDraft'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'

type LibraryFormula = CustomerFormula & { customerId: string; customerName: string }
type EditorState = { customer: CustomerDetail; formula: CustomerFormula; trial: boolean }
type AIState = { customer: CustomerDetail | null; formula: CustomerFormula | null; scope: 'customer' | 'global'; suggestion?: FormulaSuggestion }

const router = useRouter()
const route = useRoute()
const toast = useToast()
const { confirmDelete } = useConfirm()
const customers = ref<CustomerCard[]>([])
const custom = ref<LibraryFormula[]>([])
const builtin = ref<FormulaInfo[]>([])
const loading = ref(true)
const error = ref('')
const query = ref('')
// 当前页签记在地址里（?tab=）并存一份在浏览器里：刷新、离开再回来都还在原来的页签
const TAB_KEY = 'sheetmate.formula-tab'
function savedTab(): 'customer' | 'builtin' {
  const fromUrl = route.query.tab
  if (fromUrl === 'builtin' || fromUrl === 'customer') return fromUrl
  try {
    return localStorage.getItem(TAB_KEY) === 'builtin' ? 'builtin' : 'customer'
  } catch {
    return 'customer'
  }
}
const tab = ref<'customer' | 'builtin'>(savedTab())
watch(tab, (value) => {
  try { localStorage.setItem(TAB_KEY, value) } catch { /* 存不了就只靠地址 */ }
  void router.replace({ query: { ...route.query, tab: value } })
}, { immediate: true })
const choosing = ref(false)
const opening = ref(false)
const editor = ref<EditorState | null>(null)
const ai = ref<AIState | null>(null)
const reservedIds = ref<string[]>([])
const selectedBuiltin = ref<FormulaInfo | null>(null)
const suggestions = ref<FormulaSuggestion[]>([])
const suggestionText = ref('')
const trialing = ref<FormulaInfo | null>(null) // 正在试运行的内置公式
const trialDraft = computed(() => (trialing.value ? draftFromBody(bodyFromFormula(trialing.value as unknown as Record<string, unknown>)) : null))

// 客户公式按客户分组，用标题分隔；客户顺序沿用客户列表的顺序，搜索后没有结果的客户不显示
const customerGroups = computed(() => {
  const q = query.value.trim().toLowerCase()
  const groups = new Map<string, { customerId: string; customerName: string; formulas: LibraryFormula[] }>()
  for (const f of custom.value) {
    if (q && !`${f.name} ${f.id} ${f.intent} ${f.customerName}`.toLowerCase().includes(q)) continue
    const group = groups.get(f.customerId) ?? { customerId: f.customerId, customerName: f.customerName, formulas: [] }
    group.formulas.push(f)
    groups.set(f.customerId, group)
  }
  return [...groups.values()]
})
// 「自定义处理（SQL）」是系统给 AI 用的兜底公式，不在公式库里展示（id 仍然算被占用）
const shownBuiltin = computed(() => builtin.value.filter((f) => f.id !== 'custom_sql'))
// 内置公式按类别分组，用标题分隔；类别顺序沿用目录里的顺序，搜索后没有结果的类别不显示
const builtinGroups = computed(() => {
  const q = query.value.trim().toLowerCase()
  const groups = new Map<string, FormulaInfo[]>()
  for (const f of shownBuiltin.value) {
    if (q && !`${f.name} ${f.id} ${f.intent} ${f.category}`.toLowerCase().includes(q)) continue
    groups.set(f.category, [...(groups.get(f.category) ?? []), f])
  }
  return [...groups].map(([category, formulas]) => ({ category, formulas }))
})
const shownSuggestions = computed(() => {
  const q = query.value.trim().toLowerCase()
  return suggestions.value.filter((s) => !q || `${s.name} ${s.description} ${s.sql}`.toLowerCase().includes(q))
})
const idsFor = (customerId: string) => custom.value.filter((f) => f.customerId === customerId).map((f) => f.id)
/** 新增公式时不能用的 id：客户公式是这个客户已有的 + 内置的 + 系统保留的；内置公式没有客户的 */
const takenIds = (customerId: string | undefined, scope: 'customer' | 'global') => [...(scope === 'customer' && customerId ? idsFor(customerId) : []), ...builtin.value.map((f) => f.id), ...reservedIds.value]
const problemsOf = (f: CustomerFormula) => f.used_by.flatMap((u) => u.problems.map((p) => `「${u.name}」${p}`))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [people, catalog] = await Promise.all([customerApi.list(), recipeApi.formulas()])
    customers.value = people
    builtin.value = catalog.builtin
    reservedIds.value = catalog.reserved_ids
    const lists = await Promise.all(people.map(async (c) => (await customerApi.formulas(c.id))
      .map((f) => ({ ...f, customerId: c.id, customerName: c.name }))))
    custom.value = lists.flat()
    suggestions.value = await recipeApi.formulaSuggestions().catch(() => []) // 只有管理员能看到建议
  } catch (err) {
    error.value = errorText(err)
  } finally {
    loading.value = false
  }
}
onMounted(load)

/** AI 新增：只有一个客户就直接进；有多个就先列出来，点哪个用哪个 */
async function start() {
  // 内置公式不绑定客户：直接进，参考表在弹窗里上传
  if (tab.value === 'builtin') {
    ai.value = { customer: null, formula: null, scope: 'global' }
    return
  }
  choosing.value = true
  if (customers.value.length === 1) await proceed(customers.value[0]!.id)
}

async function proceed(customerId: string) {
  if (!choosing.value || opening.value) return
  opening.value = true
  try {
    ai.value = { customer: await customerApi.get(customerId), formula: null, scope: tab.value === 'builtin' ? 'global' : 'customer' }
    choosing.value = false
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

/** 继续用对话修改一个已有的公式：带着它进 AI 弹窗，保存成新版本 */
async function openAI(f: LibraryFormula) {
  if (opening.value) return
  opening.value = true
  try {
    ai.value = { customer: await customerApi.get(f.customerId), formula: f, scope: 'customer' }
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    opening.value = false
  }
}

/** 把一条建议交给 AI 整理成通用的内置公式：SQL 里会变的部分抽成参数，确认后才保存 */
function organize(s: FormulaSuggestion) {
  const note = s.description ? `（${s.description}）` : ''
  ai.value = { customer: null, formula: null, scope: 'global', suggestion: s }
  const examples = (s.variants.length ? s.variants : [{ customer: '', flow: '', sql: s.sql }])
    .map((v, i) => `写法 ${i + 1}${v.customer || v.flow ? `（${[v.customer, v.flow].filter(Boolean).join(' · ')}）` : ''}：\n${v.sql}`).join('\n\n')
  // 弹窗里可以继续改这段话
  suggestionText.value = `下面是在 ${s.count} 个流程里做法相同的 SQL（表名、列名、取值各不相同），请整理成一个通用的内置公式：表名、列名、阈值这些不同的部分抽成参数。\n步骤：${s.name}${note}\n\n${examples}`
}

async function ignore(s: FormulaSuggestion) {
  try {
    await recipeApi.dismissFormulaSuggestion(s.id)
    suggestions.value = suggestions.value.filter((x) => x.id !== s.id)
  } catch (err) {
    toast.error(errorText(err))
  }
}

async function onAISaved(formula: CustomerFormula | FormulaInfo) {
  const done = ai.value?.suggestion
  if (done) await recipeApi.dismissFormulaSuggestion(done.id).catch(() => undefined) // 整理好了，建议不再出现
  const revised = !!ai.value?.formula
  const global = ai.value?.scope === 'global'
  ai.value = null
  tab.value = global ? 'builtin' : 'customer'
  const broken = 'used_by' in formula ? formula.used_by.flatMap((u) => u.problems).length : 0
  if (broken) toast.error(`已保存「${formula.name}」，但有 ${broken} 处流程步骤需要调整`)
  else toast.success(revised ? `已更新「${formula.name}」` : `已添加「${formula.name}」`)
  await load()
}

async function removeBuiltin(f: FormulaInfo) {
  if (opening.value) return
  let refs: { name: string; customer_name: string }[] = []
  try {
    refs = await recipeApi.formulaReferences(f.id)
  } catch (err) {
    toast.error(errorText(err))
    return
  }
  const used = [...new Set(refs.map((r) => `${r.customer_name ? `${r.customer_name} · ` : ''}「${r.name}」`))].join('、')
  const impact = used ? `正在使用它的流程：${used}。删除后这些流程里用到它的步骤将无法执行。` : ''
  if (!await confirmDelete({
    title: '删除这个内置公式？', target: f.name,
    description: `${f.custom ? '这个公式的所有版本都会删除，删除后无法恢复。' : '这是系统自带的内置公式，删除后所有客户都不能再使用。'}${impact}`,
    confirmText: '删除公式', action: () => recipeApi.removeGlobalFormula(f.id),
  })) return
  if (selectedBuiltin.value?.id === f.id) selectedBuiltin.value = null
  toast.success('已删除')
  await load()
}

async function onSaved(formula: CustomerFormula) {
  editor.value = null
  tab.value = 'customer'
  toast.success(`已保存「${formula.name}」`)
  await load()
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
    <div class="mx-auto flex min-h-full max-w-[1200px] flex-col px-4 py-6 sm:px-8 sm:py-7">
      <header class="flex shrink-0 flex-wrap items-center gap-3 sm:gap-4">
        <h1 class="mr-auto text-xl font-semibold tracking-tight">公式库</h1>
        <label class="flex h-8 min-w-0 flex-1 items-center gap-2 rounded-full bg-paper px-3 sm:max-w-48">
          <Search class="size-3.5 shrink-0 text-faint" />
          <input v-model="query" aria-label="搜索公式" placeholder="搜索公式" class="w-full min-w-0 bg-transparent text-[13px] outline-none placeholder:text-faint" />
        </label>
        <button class="btn-ink size-8 shrink-0 !p-0" title="AI 新增" aria-label="AI 新增公式" :disabled="loading || opening" @click="start"><Sparkles class="size-4" /></button>
      </header>

      <div class="mt-5 flex shrink-0 flex-wrap items-center gap-3">
        <div class="flex rounded-full bg-paper p-1" role="tablist" aria-label="公式类型">
          <button role="tab" :aria-selected="tab === 'customer'" class="rounded-full px-3.5 py-1.5 text-[13px] transition" :class="tab === 'customer' ? 'bg-white font-medium text-ink shadow-sm' : 'text-mute'" @click="tab = 'customer'">客户公式 <span class="ml-1 text-xs text-faint">{{ custom.length }}</span></button>
          <button role="tab" :aria-selected="tab === 'builtin'" class="rounded-full px-3.5 py-1.5 text-[13px] transition" :class="tab === 'builtin' ? 'bg-white font-medium text-ink shadow-sm' : 'text-mute'" @click="tab = 'builtin'">内置公式 <span class="ml-1 text-xs text-faint">{{ shownBuiltin.length }}</span></button>
        </div>
      </div>

      <div v-if="error" class="mt-6 rounded-2xl bg-error/5 p-4 text-sm text-error" role="alert">{{ error }}<button class="btn-text ml-3" @click="load">重试</button></div>
      <div v-else-if="loading" class="flex justify-center py-20"><LoaderCircle class="size-5 animate-spin text-faint" /></div>
      <template v-else-if="tab === 'customer'">
        <div v-if="customerGroups.length" class="mt-7 space-y-9">
          <section v-for="group in customerGroups" :key="group.customerId" :aria-label="group.customerName">
            <h2 class="flex items-baseline gap-2 px-1 text-lg font-semibold tracking-tight">{{ group.customerName }}<span class="text-xs font-normal tabular-nums text-faint">{{ group.formulas.length }}</span></h2>
            <div class="mt-4 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              <article v-for="f in group.formulas" :key="`${f.customerId}:${f.id}`" class="formula-card group flex min-w-0 flex-col rounded-[20px] bg-white p-4">
                <div class="flex items-center gap-2.5">
                  <span class="flex size-8 shrink-0 items-center justify-center rounded-xl bg-[#eef8f5]"><FormulaLibraryIcon class="size-5" /></span>
                  <h3 class="min-w-0 flex-1 break-words text-[15px] font-semibold">{{ f.name }}</h3>
                  <div class="-mr-1.5 flex shrink-0 items-center">
                    <button class="icon-btn size-7" :title="`试运行 ${f.name}`" :aria-label="`试运行 ${f.name}`" :disabled="opening" @click="openEditor(f, true)"><FlaskConical class="size-4" /></button>
                    <button class="icon-btn size-7" :title="`AI 修改 ${f.name}`" :aria-label="`AI 修改 ${f.name}`" :disabled="opening" @click="openAI(f)"><WandSparkles class="size-4" /></button>
                    <button class="icon-btn size-7" :title="`编辑公式 ${f.name}`" :aria-label="`编辑公式 ${f.name}`" :disabled="opening" @click="openEditor(f)"><Pencil class="size-4" /></button>
                    <button class="icon-btn size-7 hover:text-error" :title="`删除公式 ${f.name}`" :aria-label="`删除公式 ${f.name}`" @click="remove(f)"><Trash2 class="size-4" /></button>
                  </div>
                </div>
                <p class="mt-2.5 line-clamp-2 text-[13px] leading-6 text-mute">{{ f.intent || '暂未填写用途' }}</p>
                <div v-if="problemsOf(f).length" class="mt-2.5 rounded-xl bg-error/5 px-3 py-2 text-xs leading-6 text-error"><p v-for="p in problemsOf(f)" :key="p" class="flex gap-1.5"><TriangleAlert class="mt-1 size-3.5 shrink-0" />{{ p }}</p></div>
                <div class="mt-auto flex items-center gap-2 pt-3 text-[11px] text-faint"><span>{{ f.used_by.length ? `${f.used_by.length} 个流程使用` : '未用于流程' }}</span><span class="ml-auto">{{ timeAgo(f.updated_at) }}</span></div>
              </article>
            </div>
          </section>
        </div>
        <div v-else class="flex flex-1 flex-col items-center justify-center gap-3 py-12 text-center text-sm text-faint" role="status">
          <FormulaLibraryIcon class="size-12 opacity-65" />
          <p>{{ custom.length ? '没有找到符合条件的公式' : '还没有客户公式' }}</p>
        </div>
      </template>
      <template v-else>
        <section v-if="shownSuggestions.length" class="mt-7" aria-label="建议整理成内置公式">
          <h2 class="flex items-baseline gap-2 px-1 text-lg font-semibold tracking-tight">可整理<span class="text-xs font-normal tabular-nums text-faint">{{ shownSuggestions.length }}</span></h2>
          <div class="mt-4 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            <article v-for="s in shownSuggestions" :key="s.id" class="formula-card flex min-w-0 flex-col rounded-[20px] bg-white p-4" data-testid="formula-suggestion">
              <div class="flex items-center gap-2.5">
                <span class="flex size-8 shrink-0 items-center justify-center rounded-xl bg-[#fff6e6] text-[#b7770c]"><Lightbulb class="size-4" /></span>
                <h3 class="min-w-0 flex-1 break-words text-[15px] font-semibold">{{ s.name }}</h3>
                <div class="-mr-1.5 flex shrink-0 items-center">
                  <button class="icon-btn size-7" :title="`整理成内置公式 ${s.name}`" :aria-label="`整理成内置公式 ${s.name}`" @click="organize(s)"><Sparkles class="size-4" /></button>
                  <button class="icon-btn size-7" :title="`忽略 ${s.name}`" :aria-label="`忽略 ${s.name}`" @click="ignore(s)"><X class="size-4" /></button>
                </div>
              </div>
              <p class="mt-2.5 line-clamp-2 break-all font-mono text-[11px] leading-5 text-mute">{{ s.sql }}</p>
              <p class="mt-auto pt-3 text-[11px] text-faint" :title="s.flows.join('、')">{{ s.count }} 个流程用到<template v-if="s.customers.length"> · {{ s.customers.join('、') }}</template></p>
            </article>
          </div>
        </section>
        <div v-if="builtinGroups.length" class="mt-7 space-y-9">
          <section v-for="group in builtinGroups" :key="group.category" :aria-label="group.category">
            <h2 class="flex items-baseline gap-2 px-1 text-lg font-semibold tracking-tight">{{ group.category }}<span class="text-xs font-normal tabular-nums text-faint">{{ group.formulas.length }}</span></h2>
            <div class="mt-4 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              <article v-for="f in group.formulas" :key="f.id" class="formula-card group relative flex min-w-0 flex-col rounded-[22px] bg-white p-5">
                <div class="flex w-full items-center gap-2.5">
                  <FunObject :kind="formulaArt(f.id) as FunKind" :size="28" />
                  <h3 class="min-w-0 flex-1 text-[15px] font-semibold"><button type="button" class="text-left after:absolute after:inset-0 after:rounded-[22px] after:content-['']" :aria-label="`查看详情 ${f.name}`" @click="selectedBuiltin = f">{{ f.name }}</button></h3>
                  <button class="icon-btn relative z-10 size-7 shrink-0" :title="`试运行 ${f.name}`" :aria-label="`试运行 ${f.name}`" @click="trialing = f"><FlaskConical class="size-4" /></button>
                  <button class="icon-btn relative z-10 -mr-1.5 size-7 shrink-0 hover:text-error" :title="`删除公式 ${f.name}`" :aria-label="`删除公式 ${f.name}`" @click="removeBuiltin(f)"><Trash2 class="size-4" /></button>
                </div>
                <p class="mt-3 line-clamp-3 text-[13px] leading-7 text-mute">{{ f.intent }}</p>
              </article>
            </div>
          </section>
        </div>
        <div v-else class="flex flex-1 flex-col items-center justify-center gap-3 py-12 text-center text-sm text-faint" role="status">
          <FormulaLibraryIcon class="size-12 opacity-65" />
          <p>没有找到符合条件的公式</p>
        </div>
      </template>
    </div>

    <BaseModal v-if="choosing" :title="customers.length ? '选择客户' : '还没有客户'" width="max-w-sm" @close="!opening && (choosing = false)">
      <ul v-if="customers.length" class="-mx-2 space-y-0.5" aria-label="客户">
        <li v-for="c in customers" :key="c.id">
          <button type="button" class="flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left transition hover:bg-paper focus-visible:bg-paper focus-visible:outline-none disabled:opacity-50" :disabled="opening" @click="proceed(c.id)">
            <span class="min-w-0 flex-1 truncate text-sm font-medium">{{ c.name }}</span>
            <LoaderCircle v-if="opening" class="size-4 shrink-0 animate-spin text-faint" /><ChevronRight v-else class="size-4 shrink-0 text-faint" />
          </button>
        </li>
      </ul>
      <button v-else class="btn-ink w-full" @click="router.push({ name: 'customers' })">前往客户库</button>
    </BaseModal>

    <FormulaAIGeneratorDialog
      v-if="ai" :key="ai.formula?.id ?? 'new'" :customer-id="ai.customer?.id" :files="ai.customer?.files ?? []"
      :formula="ai.formula ? bodyFromFormula(ai.formula) : null" :taken-ids="takenIds(ai.customer?.id, ai.scope)" :scope="ai.scope" :initial-description="ai.suggestion ? suggestionText : ''"
      @close="ai = null" @saved="onAISaved"
    />
    <FormulaEditorDialog v-if="editor" :customer-id="editor.customer.id" :formula="editor.formula" :existing-ids="idsFor(editor.customer.id)" :files="editor.customer.files" :field-mapping="editor.customer.field_mapping" :focus-trial="editor.trial" @close="editor = null" @saved="onSaved" />

    <BaseModal v-if="trialing" :title="`试运行 · ${trialing.name}`" width="max-w-xl" @close="trialing = null">
      <FormulaTrialPanel :key="trialing.id" :formula-id="trialing.id" :draft="trialDraft" blocked="" />
    </BaseModal>

    <BaseModal v-if="selectedBuiltin" :title="selectedBuiltin.name" width="max-w-lg" @close="selectedBuiltin = null">
      <p class="text-sm leading-7 text-mute">{{ selectedBuiltin.intent }}</p><p class="mt-2 text-xs text-faint">内置公式 · {{ selectedBuiltin.category }}</p>
      <dl class="mt-5 space-y-3"><div v-for="(p, name) in selectedBuiltin.params" :key="name" class="rounded-xl bg-paper/70 px-4 py-3"><dt class="text-[13px] font-medium">{{ p.desc || name }}<span v-if="p.required" class="ml-1 text-error">*</span></dt><dd class="mt-1 text-xs text-faint">{{ name }} · {{ paramTypeLabel(p.type) }}</dd></div></dl>
      <pre v-if="selectedBuiltin.sql" class="mt-4 max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-paper/70 px-4 py-3 font-mono text-[11px] leading-relaxed">{{ selectedBuiltin.sql }}</pre>
    </BaseModal>
  </div>
</template>

<style scoped>
.formula-card { box-shadow: 0 4px 24px #2222420b, 0 1px 4px #22224205; transition: box-shadow 200ms ease, transform 200ms ease; }
.formula-card:hover { box-shadow: 0 7px 28px #22224212, 0 2px 6px #22224207; }
button.formula-card:hover { transform: translateY(-1px); }
@media (prefers-reduced-motion: reduce) { .formula-card { transition: none; } button.formula-card:hover { transform: none; } }
</style>
