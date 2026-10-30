<script setup lang="ts">
/**
 * 客户公式编辑器：
 * - 表单：id、名称、用途、参数行（名称 / 类型 / 必填 / 说明 / 默认值）、SQL 模板；参数类型、默认值、占位符当场校验
 * - 高级模式：直接编辑公式 JSON，实时校验，出错时指出第几行
 * - 模板检查：SQL 语法只有后端的 DuckDB 解析器能判断，停下来 0.5 秒后调后端的「检查」接口，和保存时的检查完全一样
 * - 试运行：右边的面板，用客户的文件或粘贴的数据把当前这份（还没保存的也行）公式跑一遍
 */
import { CircleCheck, LoaderCircle, Plus, Sparkles, Trash2, TriangleAlert } from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { customerApi, recipeApi } from '@/api'
import { errorText } from '@/api/http'
import type { CustomerFormula, FileInfo } from '@/api/types'
import BaseModal from '@/components/common/BaseModal.vue'
import {
  PARAM_TYPES, bodyFromFormula, draftFromBody, draftToBody, emptyDraft, exampleDraft, jsonErrorInfo, newParam, parseDefault,
  placeholdersIn, splitList, validateDraft, type FormulaDraft, type JsonErrorInfo, type ParamDraft,
} from '@/composables/formulaDraft'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'

import FormulaTrialPanel from './FormulaTrialPanel.vue'

const props = defineProps<{
  customerId: string
  formula: CustomerFormula | null // null = 新建
  existingIds: string[]
  files: FileInfo[]
  fieldMapping: Record<string, string[]>
  focusTrial?: boolean
}>()
const emit = defineEmits<{ close: []; saved: [formula: CustomerFormula] }>()
const toast = useToast()
const { confirm } = useConfirm()

const draft = ref<FormulaDraft>(props.formula ? draftFromBody(bodyFromFormula(props.formula)) : emptyDraft())
const initialBody = JSON.stringify(draftToBody(draft.value))
const mode = ref<'form' | 'json'>('form')
const jsonText = ref('')
const jsonError = ref<JsonErrorInfo | null>(null)
const saving = ref(false)
const catalog = ref<{ reservedIds: string[]; builtinIds: Record<string, string> }>({ reservedIds: [], builtinIds: {} })
const sqlArea = ref<HTMLTextAreaElement | null>(null)
const jsonArea = ref<HTMLTextAreaElement | null>(null)
const trialEl = ref<HTMLElement | null>(null)

onMounted(async () => {
  try {
    const data = await recipeApi.formulas(props.customerId)
    catalog.value = { reservedIds: data.reserved_ids, builtinIds: Object.fromEntries(data.builtin.map((f) => [f.id, f.name])) }
  } catch {
    /* 拿不到目录只是少了「和系统工具 / 内置公式重名」的提前提醒，保存时后端照样会检查 */
  }
  if (props.focusTrial) nextTick(() => trialEl.value?.scrollIntoView({ block: 'start' }))
})

// ---------------------------------------------------------------- 校验

const body = computed(() => draftToBody(draft.value))
const issues = computed(() =>
  validateDraft(draft.value, { ...catalog.value, existingIds: props.existingIds, editingId: props.formula?.id ?? null }),
)
const jsonBroken = computed(() => mode.value === 'json' && jsonError.value !== null)
const serverProblems = ref<string[]>([])
const checking = ref(false)
const allProblems = computed(() => [...issues.value.problems, ...serverProblems.value])
const dirty = computed(() => jsonBroken.value || JSON.stringify(body.value) !== initialBody)
const canSave = computed(() => dirty.value && !jsonBroken.value && !allProblems.value.length && !saving.value)
const trialDraft = computed(() => (jsonBroken.value || allProblems.value.length ? null : draft.value))
const trialBlocked = computed(() => (jsonBroken.value ? 'JSON 格式有误，先改好' : allProblems.value[0] ?? ''))

// 停下来 0.5 秒后让后端检查（SQL 语法、模板代入）；前端能发现的问题不用打扰后端
let timer: ReturnType<typeof setTimeout> | undefined
let ticket = 0
watch(
  body,
  (value) => {
    clearTimeout(timer)
    const mine = ++ticket
    serverProblems.value = []
    if (issues.value.problems.length || jsonBroken.value) {
      checking.value = false
      return
    }
    checking.value = true
    timer = setTimeout(async () => {
      try {
        const problems = await customerApi.checkFormula(props.customerId, value)
        if (mine === ticket) serverProblems.value = problems
      } catch {
        /* 网络问题保存时会再报 */
      } finally {
        if (mine === ticket) checking.value = false
      }
    }, 500)
  },
  { immediate: true },
)
onBeforeUnmount(() => clearTimeout(timer))

const ph = (name: string) => `{{${name}}}` // 模板里的占位符写法；放在函数里是因为 Vue 模板里不能直接写 {{ }} 字面量
const fieldError = (p: ParamDraft, field: string) => issues.value.fields[`p:${p.uid}:${field}`]
const typeHint = (p: ParamDraft) => PARAM_TYPES.find((t) => t.value === p.type)?.hint ?? ''
const typeOptions = (p: ParamDraft) => PARAM_TYPES.filter((t) => t.value !== 'dict' || p.type === 'dict')
const tableParams = computed(() => draft.value.params.map((p) => ({ type: p.type, name: p.name.trim() })).filter((p) => p.type === 'table' && p.name))
const undeclared = computed(() => {
  const names = new Set(draft.value.params.map((p) => p.name.trim()))
  return placeholdersIn(draft.value.sql).filter((n) => !names.has(n))
})

// ---------------------------------------------------------------- 表单操作

function addParam(name = '') {
  draft.value.params.push(newParam(name))
}

function removeParam(index: number) {
  draft.value.params.splice(index, 1)
}

function onTypeChange(p: ParamDraft) {
  if ((p.type === 'column' || p.type === 'columns') && !p.of && tableParams.value.length === 1) p.of = tableParams.value[0].name
  if (p.type !== 'column' && p.type !== 'columns') p.of = ''
  if (parseDefault(p).error) p.default = '' // 换了类型，原来的默认值不合适就清掉
}

function insertPlaceholder(name: string) {
  const token = `{{${name}}}`
  const el = sqlArea.value
  if (!el) {
    draft.value.sql += token
    return
  }
  const start = el.selectionStart
  const end = el.selectionEnd
  draft.value.sql = draft.value.sql.slice(0, start) + token + draft.value.sql.slice(end)
  nextTick(() => {
    el.focus()
    el.setSelectionRange(start + token.length, start + token.length)
  })
}

function fillExample() {
  draft.value = exampleDraft()
}

// ---------------------------------------------------------------- JSON 模式

function switchMode(next: 'form' | 'json') {
  if (next === mode.value) return
  if (next === 'json') {
    jsonText.value = JSON.stringify(draftToBody(draft.value), null, 2)
    jsonError.value = null
    mode.value = 'json'
  } else if (jsonError.value) {
    toast.error('JSON 格式有误，先改好再切回表单')
  } else {
    mode.value = 'form'
  }
}

function onJsonInput() {
  try {
    const parsed = JSON.parse(jsonText.value)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('公式应该是一个 JSON 对象，用 { } 括起来')
    draft.value = draftFromBody(parsed)
    jsonError.value = null
  } catch (err) {
    jsonError.value = jsonErrorInfo(jsonText.value, err)
  }
}

const errorLineText = computed(() => (jsonError.value?.line ? jsonText.value.split('\n')[jsonError.value.line - 1] ?? '' : ''))

function locateJsonError() {
  const el = jsonArea.value
  const line = jsonError.value?.line
  if (!el || !line) return
  const lines = jsonText.value.split('\n')
  const start = lines.slice(0, line - 1).reduce((n, l) => n + l.length + 1, 0)
  el.focus()
  el.setSelectionRange(start, start + (lines[line - 1]?.length ?? 0))
  el.scrollTop = Math.max(0, (line - 3) * 20) // 一行 20px（leading-5）
}

// ---------------------------------------------------------------- 保存 / 关闭

async function save() {
  if (!canSave.value) return
  const id = draft.value.id.trim()
  const overridden = catalog.value.builtinIds[id]
  if (overridden && !await confirm({
    title: '覆盖内置公式？',
    target: `${overridden}（${id}）`,
    description: '保存后，这个客户所有流程中使用该公式的步骤，都会改用你编写的版本。',
    confirmText: '继续保存',
    cancelText: '返回编辑',
  })) return
  saving.value = true
  try {
    emit('saved', await customerApi.saveFormula(props.customerId, body.value))
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    saving.value = false
  }
}

async function requestClose() {
  if (saving.value) return
  if (dirty.value && !await confirm({
    title: '放弃未保存的修改？',
    description: '关闭后，本次尚未保存的公式修改将丢失。',
    confirmText: '放弃修改',
    cancelText: '继续编辑',
  })) return
  emit('close')
}
</script>

<template>
  <BaseModal :title="formula ? `编辑公式 · ${formula.name}` : '新建客户公式'" width="max-w-6xl" @close="requestClose">
    <div class="grid gap-6 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
      <!-- 左：编辑 -->
      <div class="min-w-0 space-y-5" data-testid="formula-editor">
        <div class="flex items-center gap-2">
          <div class="flex rounded-full bg-paper p-0.5 text-xs">
            <button class="rounded-full px-3.5 py-1 transition" :class="mode === 'form' ? 'bg-white font-medium shadow-sm' : 'text-mute'" @click="switchMode('form')">表单</button>
            <button class="rounded-full px-3.5 py-1 transition" :class="mode === 'json' ? 'bg-white font-medium shadow-sm' : 'text-mute'" data-testid="mode-json" @click="switchMode('json')">JSON（高级）</button>
          </div>
          <button v-if="!formula && mode === 'form'" class="btn-text ml-auto" @click="fillExample"><Sparkles class="size-3.5" />填入示例</button>
        </div>

        <ul v-if="issues.warnings.length" class="space-y-1 rounded-xl bg-warning/10 px-3.5 py-2.5 text-xs leading-relaxed text-[#8a5b00]">
          <li v-for="w in issues.warnings" :key="w" class="flex gap-2"><TriangleAlert class="mt-0.5 size-3.5 shrink-0" />{{ w }}</li>
        </ul>

        <!-- 表单模式 -->
        <template v-if="mode === 'form'">
          <div class="grid gap-3 sm:grid-cols-2">
            <label class="block">
              <span class="mb-1.5 block text-xs font-medium text-mute">公式 id{{ formula ? '（保存后不能改）' : '' }}</span>
              <input v-model="draft.id" class="field font-mono text-[13px]" :class="issues.fields.id && draft.id ? '!border-error/50' : ''" :disabled="!!formula" placeholder="例如 big_amount" data-testid="f-id" />
              <span v-if="issues.fields.id && draft.id" class="mt-1 block text-xs text-error">{{ issues.fields.id }}</span>
              <span v-else class="mt-1 block text-[11px] text-faint">小写字母、数字、下划线；流程里靠它调用这个公式</span>
            </label>
            <label class="block">
              <span class="mb-1.5 block text-xs font-medium text-mute">名称</span>
              <input v-model="draft.name" class="field" :class="issues.fields.name && draft.name ? '!border-error/50' : ''" placeholder="例如 筛出大额行" data-testid="f-name" />
              <span v-if="issues.fields.name && draft.name" class="mt-1 block text-xs text-error">{{ issues.fields.name }}</span>
            </label>
          </div>
          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-mute">用途（一句话，流程图和 AI 选公式时都会看到）</span>
            <input v-model="draft.intent" class="field" placeholder="例如 只保留金额大于阈值的行，按金额从大到小排" data-testid="f-intent" />
          </label>

          <!-- 参数 -->
          <div>
            <div class="mb-2 flex items-center gap-2">
              <span class="text-xs font-medium text-mute">参数</span>
              <span class="text-[11px] text-faint">流程里每一步填的就是这些参数的取值，模板里用 {{ ph('参数名') }} 引用</span>
              <button class="btn-text ml-auto" data-testid="add-param" @click="addParam()"><Plus class="size-3.5" />添加参数</button>
            </div>
            <p v-if="!draft.params.length" class="rounded-xl border border-dashed border-line py-5 text-center text-xs text-faint">还没有参数。表名、列名、阈值这些会变的东西，都建议做成参数</p>
            <div class="space-y-2">
              <div v-for="(p, i) in draft.params" :key="p.uid" class="rounded-xl border border-line p-2.5" data-testid="param-row">
                <div class="grid gap-2 sm:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)_auto_auto] sm:items-start">
                  <div>
                    <input v-model="p.name" class="field py-1.5 font-mono text-[13px]" :class="fieldError(p, 'name') ? '!border-error/50' : ''" placeholder="参数名" data-testid="p-name" />
                    <p v-if="fieldError(p, 'name') && p.name" class="mt-1 text-[11px] leading-snug text-error">{{ fieldError(p, 'name') }}</p>
                  </div>
                  <select v-model="p.type" class="select select-sm w-full rounded-xl" data-testid="p-type" @change="onTypeChange(p)">
                    <option v-for="t in typeOptions(p)" :key="t.value" :value="t.value">{{ t.label }}</option>
                  </select>
                  <label class="flex h-8 items-center gap-1.5 whitespace-nowrap text-xs text-mute"><input v-model="p.required" type="checkbox" class="checkbox checkbox-xs" />必填</label>
                  <button class="icon-btn hover:text-error" title="删除这个参数" @click="removeParam(i)"><Trash2 class="size-4" /></button>
                </div>
                <div class="mt-2 grid gap-2 sm:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)] sm:items-start">
                  <input v-model="p.desc" class="field py-1.5" placeholder="说明（给人看）" />
                  <div>
                    <select v-if="p.type === 'bool'" v-model="p.default" class="select select-sm w-full rounded-xl">
                      <option value="">无默认值</option><option value="true">是</option><option value="false">否</option>
                    </select>
                    <select v-else-if="p.type === 'enum'" v-model="p.default" class="select select-sm w-full rounded-xl">
                      <option value="">无默认值</option>
                      <option v-for="v in splitList(p.values)" :key="v" :value="v">{{ v }}</option>
                    </select>
                    <input v-else-if="p.type === 'table' || p.type === 'tables'" class="field py-1.5" disabled placeholder="—" />
                    <input v-else v-model="p.default" class="field py-1.5" :class="fieldError(p, 'default') ? '!border-error/50' : ''" placeholder="默认值" data-testid="p-default" />
                    <p v-if="fieldError(p, 'default')" class="mt-1 text-[11px] leading-snug text-error">{{ fieldError(p, 'default') }}</p>
                  </div>
                </div>
                <div v-if="p.type === 'enum'" class="mt-2">
                  <input v-model="p.values" class="field py-1.5" :class="fieldError(p, 'values') ? '!border-error/50' : ''" placeholder="可选值，用逗号分隔，例如 华东, 华南, 华北" />
                  <p v-if="fieldError(p, 'values')" class="mt-1 text-[11px] text-error">{{ fieldError(p, 'values') }}</p>
                </div>
                <div v-if="p.type === 'column' || p.type === 'columns'" class="mt-2 flex items-center gap-2 text-xs text-mute">
                  <span class="shrink-0">这列属于哪张表</span>
                  <select v-model="p.of" class="select select-sm min-w-0 flex-1 rounded-xl">
                    <option value="">不指定（试运行 / 执行时不检查列在不在）</option>
                    <option v-for="t in tableParams" :key="t.name" :value="t.name">{{ t.name }}</option>
                  </select>
                </div>
                <p class="mt-1.5 text-[11px] text-faint">{{ typeHint(p) }}</p>
              </div>
            </div>
          </div>

          <!-- SQL 模板 -->
          <div>
            <div class="mb-2 flex flex-wrap items-center gap-1.5">
              <span class="text-xs font-medium text-mute">SQL 模板</span>
              <span class="text-[11px] text-faint">点参数插入到光标处：</span>
              <button v-for="p in draft.params.filter((x) => x.name.trim())" :key="p.uid" class="chip font-mono !py-0.5 transition hover:border-ink/30 hover:text-ink" @click="insertPlaceholder(p.name.trim())">
                {{ p.name.trim() }}
              </button>
            </div>
            <textarea
              ref="sqlArea"
              v-model="draft.sql"
              class="field h-44 font-mono text-[13px] leading-relaxed"
              :class="(issues.fields.sql && draft.sql.trim()) || serverProblems.length ? '!border-error/50' : ''"
              spellcheck="false"
              placeholder="SELECT * FROM {{table}} WHERE {{col}} > {{threshold}}"
              data-testid="f-sql"
            />
            <div class="mt-1.5 min-h-5 text-xs" data-testid="sql-status">
              <div v-if="issues.fields.sql && draft.sql.trim()" class="flex flex-wrap items-center gap-2 text-error">
                <span>{{ issues.fields.sql }}</span>
                <button v-for="n in undeclared" :key="n" class="btn-text !px-1.5 !py-0.5 text-xs" @click="addParam(n)"><Plus class="size-3" />添加参数「{{ n }}」</button>
              </div>
              <p v-else-if="serverProblems.length" class="break-words text-error">{{ serverProblems[0] }}</p>
              <p v-else-if="checking" class="flex items-center gap-1.5 text-faint"><LoaderCircle class="size-3 animate-spin" />检查模板…</p>
              <p v-else-if="draft.sql.trim() && !allProblems.length" class="flex items-center gap-1.5 text-success"><CircleCheck class="size-3.5" />模板检查通过（语法正确，参数都声明了）</p>
            </div>
            <details class="mt-2 rounded-xl bg-paper px-3 py-2 text-xs leading-relaxed text-mute">
              <summary class="cursor-pointer">模板怎么写</summary>
              <ul class="mt-2 list-disc space-y-1 pl-4">
                <li>只能写一条 SELECT。用 <code class="font-mono">{{ ph('参数名') }}</code> 引用参数，执行时会按参数类型自动转义：表名、列名自动加双引号，文本自动加单引号，<b>不要自己再加引号</b>。</li>
                <li>表名、列名建议都做成「表」「列」参数，不要写死——流程换了文件名或列名时只用改参数，系统也能据此检查列在不在。</li>
                <li>例：<code class="font-mono">SELECT * FROM {{ ph('table') }} WHERE {{ ph('amount_col') }} &gt; {{ ph('threshold') }}</code></li>
                <li>模板里的 SQL 在沙箱里执行：读不了本机文件、连不了网，也不能修改或创建表。</li>
              </ul>
            </details>
          </div>
        </template>

        <!-- JSON 模式 -->
        <template v-else>
          <div>
            <textarea
              ref="jsonArea"
              v-model="jsonText"
              class="field h-[430px] font-mono text-[12px] leading-5"
              :class="jsonError ? '!border-error/50' : ''"
              spellcheck="false"
              data-testid="json-text"
              @input="onJsonInput"
            />
            <div v-if="jsonError" class="mt-2 rounded-xl bg-error/5 px-3.5 py-2.5 text-xs text-error" data-testid="json-error">
              <div class="font-medium">
                JSON 格式有误<template v-if="jsonError.line">：第 {{ jsonError.line }} 行，第 {{ jsonError.column }} 列</template>
              </div>
              <p v-if="jsonError.hint" class="mt-0.5 break-words">{{ jsonError.hint }}</p>
              <p class="mt-0.5 break-words text-[11px] opacity-70">{{ jsonError.message }}</p>
              <pre v-if="jsonError.line" class="mt-1.5 overflow-x-auto rounded-lg bg-white px-2.5 py-1.5 font-mono text-[11px] leading-5 text-ink">{{ jsonError.line }} │ {{ errorLineText }}</pre>
              <button v-if="jsonError.line" class="btn-text mt-1.5 !text-error" @click="locateJsonError">定位到出错的这一行</button>
            </div>
            <ul v-else-if="allProblems.length" class="mt-2 space-y-1 rounded-xl bg-error/5 px-3.5 py-2.5 text-xs text-error" data-testid="json-problems">
              <li v-for="p in allProblems" :key="p" class="break-words">· {{ p }}</li>
            </ul>
            <p v-else class="mt-2 flex items-center gap-1.5 text-xs text-success"><CircleCheck class="size-3.5" />JSON 格式正确，公式检查通过</p>
            <p class="mt-2 text-[11px] leading-relaxed text-faint">
              格式：<code class="font-mono">id</code>、<code class="font-mono">name</code>、<code class="font-mono">intent</code>、<code class="font-mono">params</code>（参数名 → {type, required, desc, default, values, of}）、<code class="font-mono">sql</code>。
              参数类型：{{ PARAM_TYPES.map((t) => t.value).join(' / ') }}
            </p>
          </div>
        </template>
      </div>

      <!-- 右：试运行 -->
      <div ref="trialEl" class="min-w-0">
        <FormulaTrialPanel :customer-id="customerId" :draft="trialDraft" :blocked="trialBlocked" :files="files" :field-mapping="fieldMapping" />
      </div>
    </div>

    <template #footer>
      <div class="mr-auto min-w-0 flex-1 text-xs">
        <p v-if="allProblems.length" class="truncate text-error" :title="allProblems.join('\n')">⚠ {{ allProblems[0] }}{{ allProblems.length > 1 ? `（还有 ${allProblems.length - 1} 处）` : '' }}</p>
        <p v-else-if="jsonBroken" class="text-error">⚠ JSON 格式有误</p>
        <p v-else class="text-faint">{{ dirty ? '有没保存的修改' : formula ? '没有修改' : '' }}</p>
      </div>
      <button class="btn-line" @click="requestClose">取消</button>
      <button class="btn-ink" :disabled="!canSave" data-testid="save-formula" @click="save">
        <LoaderCircle v-if="saving" class="size-4 animate-spin" />{{ formula ? '保存为新版本' : '保存公式' }}
      </button>
    </template>
  </BaseModal>
</template>
