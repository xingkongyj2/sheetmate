<script setup lang="ts">
/**
 * 客户公式编辑器：
 * - 两个标签：定义（名称、用途、参数、SQL 模板）和试运行，每次只看一块
 * - 定义：参数类型、默认值、占位符当场校验；说明文字放在悬停提示里，页面上只留必要的标签
 * - 高级模式：直接编辑公式 JSON，实时校验，出错时指出第几行
 * - 模板检查：SQL 语法只有后端的 DuckDB 解析器能判断，停下来 0.5 秒后调后端的「检查」接口，和保存时的检查完全一样
 * - 试运行：用客户的文件或粘贴的数据把当前这份（还没保存的也行）公式跑一遍
 */
import { CircleCheck, CircleQuestionMark, Code, LoaderCircle, Plus, Trash2, TriangleAlert } from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { customerApi, recipeApi } from '@/api'
import { errorText } from '@/api/http'
import type { CustomerFormula, FileInfo } from '@/api/types'
import BaseModal from '@/components/common/BaseModal.vue'
import {
  PARAM_TYPES, bodyFromFormula, draftFromBody, draftToBody, jsonErrorInfo, newParam, parseDefault,
  placeholdersIn, splitList, validateDraft, type FormulaDraft, type JsonErrorInfo, type ParamDraft,
} from '@/composables/formulaDraft'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'

import FormulaTrialPanel from './FormulaTrialPanel.vue'

const props = defineProps<{
  customerId: string
  formula: CustomerFormula // 编辑已有公式；新增统一走 AI 生成
  existingIds: string[]
  files: FileInfo[]
  fieldMapping: Record<string, string[]>
  focusTrial?: boolean
}>()
const emit = defineEmits<{ close: []; saved: [formula: CustomerFormula] }>()
const toast = useToast()
const { confirm } = useConfirm()

const draft = ref<FormulaDraft>(draftFromBody(bodyFromFormula(props.formula)))
const initialSnapshot = JSON.stringify(draftToBody(draft.value))
const tab = ref<'define' | 'trial'>(props.focusTrial ? 'trial' : 'define')
const mode = ref<'form' | 'json'>('form')
const jsonText = ref('')
const jsonError = ref<JsonErrorInfo | null>(null)
const saving = ref(false)
const catalog = ref<{ reservedIds: string[]; builtinIds: Record<string, string> }>({ reservedIds: [], builtinIds: {} })
const sqlArea = ref<HTMLTextAreaElement | null>(null)
const jsonArea = ref<HTMLTextAreaElement | null>(null)

onMounted(async () => {
  try {
    const data = await recipeApi.formulas(props.customerId)
    catalog.value = { reservedIds: data.reserved_ids, builtinIds: Object.fromEntries(data.builtin.map((f) => [f.id, f.name])) }
  } catch {
    /* 拿不到目录只是少了「和系统工具 / 内置公式重名」的提前提醒，保存时后端照样会检查 */
  }
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
const dirty = computed(() => jsonBroken.value || JSON.stringify(body.value) !== initialSnapshot)
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

const sqlHelp = '只写一条 SELECT，用 {{参数名}} 引用参数。表名、列名自动加双引号，文本自动加单引号，不要自己再加引号。表名、列名建议做成「表」「列」参数，不要写死。SQL 在沙箱里执行：读不了本机文件、连不了网，也不能修改或创建表。'
const ph = (name: string) => `{{${name}}}` // 模板里的占位符写法；放在函数里是因为 Vue 模板里不能直接写 {{ }} 字面量
const fieldError = (p: ParamDraft, field: string) => issues.value.fields[`p:${p.uid}:${field}`]
const typeHint = (p: ParamDraft) => PARAM_TYPES.find((t) => t.value === p.type)?.hint ?? ''
const hasColumnOwner = (p: ParamDraft) => p.type === 'column' || p.type === 'columns'
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
  <BaseModal width="max-w-3xl" @close="requestClose">
    <template #title>
      <div class="flex min-w-0 items-center gap-3">
        <h3 class="shrink-0 text-base font-semibold">编辑公式</h3>
        <div class="flex rounded-full bg-paper p-0.5 text-xs" role="tablist" aria-label="编辑区域">
          <button role="tab" :aria-selected="tab === 'define'" class="rounded-full px-3.5 py-1 transition" :class="tab === 'define' ? 'bg-white font-medium shadow-sm' : 'text-mute'" data-testid="tab-define" @click="tab = 'define'">定义</button>
          <button role="tab" :aria-selected="tab === 'trial'" class="rounded-full px-3.5 py-1 transition" :class="tab === 'trial' ? 'bg-white font-medium shadow-sm' : 'text-mute'" data-testid="tab-trial" @click="tab = 'trial'">试运行</button>
        </div>
      </div>
    </template>

    <!-- 定义 -->
    <div v-show="tab === 'define'" class="min-w-0 space-y-6" data-testid="formula-editor">
      <ul v-if="issues.warnings.length" class="space-y-1 rounded-xl bg-warning/10 px-3.5 py-2.5 text-xs leading-relaxed text-[#8a5b00]">
        <li v-for="w in issues.warnings" :key="w" class="flex gap-2"><TriangleAlert class="mt-0.5 size-3.5 shrink-0" />{{ w }}</li>
      </ul>

      <!-- 表单 -->
      <template v-if="mode === 'form'">
        <section class="grid gap-3">
          <label class="block">
            <span class="mb-1.5 block text-xs text-mute">名称</span>
            <input v-model="draft.name" class="field" :class="issues.fields.name && draft.name ? '!border-error/50' : ''" placeholder="例如 筛出大额行" data-testid="f-name" />
            <span v-if="issues.fields.name && draft.name" class="mt-1 block text-xs text-error">{{ issues.fields.name }}</span>
          </label>
          <label class="block" title="流程图和 AI 选公式时都会看到这句话">
            <span class="mb-1.5 block text-xs text-mute">用途</span>
            <input v-model="draft.intent" class="field" placeholder="一句话说明，例如 只保留金额大于阈值的行" data-testid="f-intent" />
          </label>
        </section>

        <!-- 参数 -->
        <section>
          <div class="mb-2.5 flex items-center gap-2">
            <h4 class="text-sm font-semibold">参数</h4>
            <span v-if="draft.params.length" class="text-xs tabular-nums text-faint">{{ draft.params.length }}</span>
            <span class="cursor-help" :title="`流程里每一步填的就是这些参数的取值，SQL 模板里用 ${ph('参数名')} 引用。表名、列名、阈值这些会变的东西，都建议做成参数。`"><CircleQuestionMark class="size-3.5 text-faint" /></span>
          </div>
          <div class="space-y-2.5">
            <div v-for="(p, i) in draft.params" :key="p.uid" class="rounded-2xl border border-line bg-white p-3" data-testid="param-row">
              <div class="grid gap-2 sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_auto_auto] sm:items-start">
                <div>
                  <input v-model="p.name" class="field py-1.5 font-mono text-[13px]" :class="fieldError(p, 'name') ? '!border-error/50' : ''" placeholder="参数名" data-testid="p-name" />
                  <p v-if="fieldError(p, 'name') && p.name" class="mt-1 text-[11px] leading-snug text-error">{{ fieldError(p, 'name') }}</p>
                </div>
                <select v-model="p.type" class="select select-sm w-full rounded-xl" :title="typeHint(p)" data-testid="p-type" @change="onTypeChange(p)">
                  <option v-for="t in typeOptions(p)" :key="t.value" :value="t.value">{{ t.label }}</option>
                </select>
                <label class="flex h-8 items-center gap-1.5 whitespace-nowrap text-xs text-mute"><input v-model="p.required" type="checkbox" class="checkbox checkbox-xs" />必填</label>
                <button class="icon-btn hover:text-error" title="删除参数" aria-label="删除参数" @click="removeParam(i)"><Trash2 class="size-4" /></button>
              </div>
              <div class="mt-2 grid gap-2 sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)] sm:items-start">
                <input v-model="p.desc" class="field py-1.5" placeholder="说明" />
                <div>
                  <select v-if="p.type === 'bool'" v-model="p.default" class="select select-sm w-full rounded-xl">
                    <option value="">默认值</option><option value="true">是</option><option value="false">否</option>
                  </select>
                  <select v-else-if="p.type === 'enum'" v-model="p.default" class="select select-sm w-full rounded-xl">
                    <option value="">默认值</option>
                    <option v-for="v in splitList(p.values)" :key="v" :value="v">{{ v }}</option>
                  </select>
                  <input v-else-if="p.type === 'table' || p.type === 'tables'" class="field py-1.5" disabled placeholder="—" />
                  <input v-else v-model="p.default" class="field py-1.5" :class="fieldError(p, 'default') ? '!border-error/50' : ''" placeholder="默认值" data-testid="p-default" />
                  <p v-if="fieldError(p, 'default')" class="mt-1 text-[11px] leading-snug text-error">{{ fieldError(p, 'default') }}</p>
                </div>
              </div>
              <div v-if="p.type === 'enum'" class="mt-2">
                <input v-model="p.values" class="field py-1.5" :class="fieldError(p, 'values') ? '!border-error/50' : ''" placeholder="可选值，逗号分隔：华东, 华南, 华北" />
                <p v-if="fieldError(p, 'values')" class="mt-1 text-[11px] text-error">{{ fieldError(p, 'values') }}</p>
              </div>
              <select v-if="hasColumnOwner(p) && tableParams.length > 1" v-model="p.of" class="select select-sm mt-2 w-full rounded-xl" title="指定后，试运行和执行时会检查这些列在不在这张表里">
                <option value="">所属的表（可不选）</option>
                <option v-for="t in tableParams" :key="t.name" :value="t.name">{{ t.name }}</option>
              </select>
            </div>
            <button class="flex w-full items-center justify-center gap-1.5 rounded-2xl border border-dashed border-line py-2.5 text-sm text-mute transition hover:border-ink/30 hover:text-ink" data-testid="add-param" @click="addParam()"><Plus class="size-4" />添加参数</button>
          </div>
        </section>

        <!-- SQL 模板 -->
        <section>
          <div class="mb-2.5 flex flex-wrap items-center gap-1.5">
            <h4 class="text-sm font-semibold">SQL 模板</h4>
            <span class="cursor-help" :title="sqlHelp"><CircleQuestionMark class="size-3.5 text-faint" /></span>
            <div class="ml-auto flex flex-wrap items-center justify-end gap-1.5">
              <button v-for="p in draft.params.filter((x) => x.name.trim())" :key="p.uid" class="chip font-mono !py-0.5 transition hover:border-ink/30 hover:text-ink" :title="`插入 ${ph(p.name.trim())}`" @click="insertPlaceholder(p.name.trim())">{{ p.name.trim() }}</button>
            </div>
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
            <p v-else-if="checking" class="flex items-center gap-1.5 text-faint"><LoaderCircle class="size-3 animate-spin" />检查中</p>
            <p v-else-if="draft.sql.trim() && !allProblems.length" class="flex items-center gap-1.5 text-success"><CircleCheck class="size-3.5" />检查通过</p>
          </div>
        </section>
      </template>

      <!-- JSON -->
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
              格式有误<template v-if="jsonError.line">：第 {{ jsonError.line }} 行，第 {{ jsonError.column }} 列</template>
            </div>
            <p v-if="jsonError.hint" class="mt-0.5 break-words">{{ jsonError.hint }}</p>
            <p class="mt-0.5 break-words text-[11px] opacity-70">{{ jsonError.message }}</p>
            <pre v-if="jsonError.line" class="mt-1.5 overflow-x-auto rounded-lg bg-white px-2.5 py-1.5 font-mono text-[11px] leading-5 text-ink">{{ jsonError.line }} │ {{ errorLineText }}</pre>
            <button v-if="jsonError.line" class="btn-text mt-1.5 !text-error" @click="locateJsonError">定位到这一行</button>
          </div>
          <ul v-else-if="allProblems.length" class="mt-2 space-y-1 rounded-xl bg-error/5 px-3.5 py-2.5 text-xs text-error" data-testid="json-problems">
            <li v-for="p in allProblems" :key="p" class="break-words">· {{ p }}</li>
          </ul>
          <p v-else class="mt-2 flex items-center gap-1.5 text-xs text-success"><CircleCheck class="size-3.5" />检查通过</p>
        </div>
      </template>
    </div>

    <!-- 试运行：用 v-show 保留已经添加的数据和填好的参数 -->
    <div v-show="tab === 'trial'" class="min-w-0">
      <FormulaTrialPanel :customer-id="customerId" :draft="trialDraft" :blocked="trialBlocked" :files="files" :field-mapping="fieldMapping" />
    </div>

    <template #footer>
      <button
        v-if="tab === 'define'"
        class="btn-text mr-auto gap-1.5 text-xs" :class="mode === 'json' ? '!text-ink' : ''"
        :title="mode === 'json' ? '回到表单' : '用 JSON 直接编辑（高级）'" data-testid="mode-json"
        @click="switchMode(mode === 'json' ? 'form' : 'json')"
      ><Code class="size-4" />{{ mode === 'json' ? '表单' : 'JSON' }}</button>
      <div class="min-w-0 flex-1 text-xs" :class="tab === 'define' ? 'hidden' : ''">
        <p v-if="allProblems.length" class="truncate text-error" :title="allProblems.join('\n')">{{ allProblems[0] }}</p>
      </div>
      <p v-if="tab === 'define' && allProblems.length" class="mr-2 min-w-0 flex-1 truncate text-right text-xs text-error" :title="allProblems.join('\n')">{{ allProblems[0] }}{{ allProblems.length > 1 ? `（还有 ${allProblems.length - 1} 处）` : '' }}</p>
      <p v-else-if="jsonBroken" class="mr-2 text-xs text-error">JSON 格式有误</p>
      <button class="btn-line" @click="requestClose">取消</button>
      <button class="btn-ink" :disabled="!canSave" data-testid="save-formula" @click="save">
        <LoaderCircle v-if="saving" class="size-4 animate-spin" />保存新版本
      </button>
    </template>
  </BaseModal>
</template>
