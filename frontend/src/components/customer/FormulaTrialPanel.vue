<script setup lang="ts">
/**
 * 公式试运行：在临时工作区里，用客户的文件（会按客户的字段映射统一列名）把公式（还没保存的也行）跑一遍，看结果预览。
 * 不会改动客户的任何数据。
 *
 * 和客户页「执行流程」是同一套逻辑：公式里每个「表」参数就是一个固定的位置，
 * 往里放数据只有三种办法——样例文件（默认）、上传新文件、选客户已有的文件。表由放进去的数据决定，不用再选。
 * 其余参数按声明的类型自动生成输入框：列 → 选这张表里的列，其余按类型填值。
 */
import { Check, CircleAlert, CircleCheck, ChevronDown, FlaskConical, LoaderCircle, Paperclip, Plus, Trash2, X } from '@lucide/vue'
import { computed, reactive, ref, watch } from 'vue'

import { customerApi } from '@/api'
import { errorText } from '@/api/http'
import type { FileInfo, FormulaTrialResult } from '@/api/types'
import BaseModal from '@/components/common/BaseModal.vue'
import DataTable from '@/components/common/DataTable.vue'
import { duration, timeAgo } from '@/composables/chatModel'
import { useToast } from '@/composables/useToast'
import { applyMapping, draftToBody, paramTypeLabel, splitList, type FormulaDraft, type ParamDraft } from '@/composables/formulaDraft'

interface Slot { fileId: string; sheet: string }
interface SlotTable extends Slot { param: string; name: string; label: string; columns: string[]; rows: number | null }

const props = defineProps<{
  customerId: string
  draft: FormulaDraft | null // 公式有问题时是 null
  blocked: string // draft 为 null 时给用户看的原因
  files: FileInfo[]
  fieldMapping: Record<string, string[]>
}>()

const STAGES: Record<string, string> = {
  definition: '公式定义有问题',
  data: '试运行用的数据有问题',
  params: '参数取值有问题',
  run: 'SQL 执行出错',
}

const toast = useToast()
const values = reactive<Record<string, string | string[]>>({}) // 参数取值：多选的是数组，其余都是文本
const pool = ref<FileInfo[]>([...props.files]) // 客户的文件；上传的新文件也放进来
// 每个表参数放了哪份数据：undefined 还没安排（用样例），null 用户清空了
const slots = reactive<Record<string, Slot | null | undefined>>({})
const corrected = reactive<Record<string, { columns: string[]; rows: number }>>({}) // 运行后用后端读到的真实列名校正
const uploading = ref<string | null>(null)
const picking = ref<string | null>(null) // 正在从已上传文件里选的表参数
const running = ref(false)
const result = ref<FormulaTrialResult | null>(null)

const usableFiles = computed(() => pool.value.filter((f) => f.sheets.length))
const declared = computed<ParamDraft[]>(() => props.draft?.params.filter((p) => p.name.trim()) ?? [])
const key = (p: ParamDraft) => p.name.trim()
const isTable = (p: ParamDraft) => p.type === 'table' || p.type === 'tables'
const tableParams = computed(() => declared.value.filter(isTable))

// 一个 tables 参数可以放多张表：每张表是一行，行名 `参数`、`参数#2`、`参数#3`…；table 参数永远只有一行
const MAX_ROWS = 5
const extraRows = reactive<Record<string, number>>({}) // tables 参数的行数（默认 1）
const baseOf = (rowKey: string) => rowKey.split('#')[0]!
const rowKeyOf = (name: string, n: number) => (n === 1 ? name : `${name}#${n}`)
const rowNumber = (rowKey: string) => Number(rowKey.split('#')[1] ?? 1)
const rowsOf = (p: ParamDraft): string[] => Array.from({ length: p.type === 'tables' ? extraRows[key(p)] ?? 1 : 1 }, (_, i) => rowKeyOf(key(p), i + 1))
const allRowKeys = computed(() => tableParams.value.flatMap(rowsOf))

// ---------------------------------------------------------------- 放数据的位置

/** 和后端 sanitize_table_name 同一套规则：中文、字母、数字、下划线，数字开头加 t_ */
function sanitizeName(raw: string): string {
  let text = raw.replace(/\.(xlsx|xlsm|xls|csv)$/i, '').replace(/[^\p{L}\p{N}_]+/gu, '_').replace(/^_+|_+$/g, '')
  if (!text) return '数据'
  if (/^\d/.test(text)) text = `t_${text}`
  return text.slice(0, 60)
}

/** 文件名去掉月份、日期：运费明细_2026-09.xlsx → 运费明细（和流程里输入表的命名一致） */
function stemOf(filename: string): string {
  return filename.replace(/\.(xlsx|xlsm|xls|csv)$/i, '').replace(/[\s_\-（(]*\d[\d\s_\-.年月日）)]*$/, '') || filename
}

/** 客户的输入文件（最早的在前，不会随后来上传的文件变）。样例的兜底顺序用它 */
const ownFiles = computed(() => {
  const oldestFirst = (a: FileInfo, b: FileInfo) => Date.parse(a.created_at ?? '') - Date.parse(b.created_at ?? '') || 0
  const own = props.files.filter((f) => f.sheets.length)
  return [...own.filter((f) => f.kind === 'input').sort(oldestFirst), ...own.filter((f) => f.kind !== 'input').sort(oldestFirst)]
})
const firstSheet = (f: FileInfo): Slot => ({ fileId: f.file_id, sheet: f.sheets[0]!.sheet })

/**
 * 这个表参数默认放哪份数据：
 * 1. AI 生成公式时记下的 sample（用户当时选的参考文件和工作表），文件在客户文件里能找到就用它；
 * 2. 老公式没有 sample：参数说明（再看公式名称、用途）里提到了哪个文件的名字就用哪个（「要统计的重量表」→ 重量表.xlsx）；
 * 3. 都没有：客户最早的输入文件，第几个表参数就用第几份。
 */
function sampleFor(rowKey: string): Slot | null {
  const param = baseOf(rowKey)
  const n = rowNumber(rowKey)
  const p = tableParams.value.find((x) => key(x) === param)
  if (n > 1) {
    // 多表参数新加的一行：默认接着用客户的下一份文件
    const base = ownFiles.value.findIndex((f) => f.file_id === sampleFor(param)?.fileId)
    const next = ownFiles.value.length ? ownFiles.value[(Math.max(base, 0) + n - 1) % ownFiles.value.length]! : null
    return next ? firstSheet(next) : null
  }
  const given = p?.extra?.sample as { file?: string; sheet?: string } | null | undefined
  if (given?.file) {
    const file = usableFiles.value.find((f) => f.filename === given.file)
    if (file) return { fileId: file.file_id, sheet: file.sheets.find((x) => x.sheet === given.sheet)?.sheet ?? file.sheets[0]!.sheet }
  }
  const mentioned = (text: string) => ownFiles.value
    .filter((f) => stemOf(f.filename).length >= 2 && text.includes(stemOf(f.filename)))
    .sort((a, b) => stemOf(b.filename).length - stemOf(a.filename).length)[0]
  // 公式的名称、用途里提到的表，只在公式只有一个表参数时才能确定是它；有两个表参数就只看各自的说明
  const hit = (p && mentioned(`${p.desc} ${p.name}`)) || (tableParams.value.length === 1 ? mentioned(`${props.draft?.name ?? ''} ${props.draft?.intent ?? ''}`) : undefined)
  if (hit) return firstSheet(hit)
  const index = tableParams.value.findIndex((x) => key(x) === param)
  const fallback = ownFiles.value.length ? ownFiles.value[Math.max(index, 0) % ownFiles.value.length]! : null
  return fallback ? firstSheet(fallback) : null
}
const slotOf = (param: string): Slot | null => (slots[param] === undefined ? sampleFor(param) : slots[param]) ?? null
const isSample = (param: string) => {
  const sample = sampleFor(param)
  const current = slotOf(param)
  return !!sample && !!current && sample.fileId === current.fileId && sample.sheet === current.sheet
}

/** 每个放了数据的表参数 → 工作区里的一张表（同名的自动加序号） */
const slotTables = computed<SlotTable[]>(() => {
  const used = new Set<string>()
  const out: SlotTable[] = []
  for (const rk of allRowKeys.value) {
    const slot = slotOf(rk)
    const file = slot && pool.value.find((f) => f.file_id === slot.fileId)
    const sheet = file?.sheets.find((x) => x.sheet === slot!.sheet)
    if (!slot || !file || !sheet) continue
    let name = sanitizeName(stemOf(file.filename))
    for (let i = 2; used.has(name); i++) name = `${sanitizeName(stemOf(file.filename))}_${i}`
    used.add(name)
    const fix = corrected[`${slot.fileId}::${slot.sheet}`]
    out.push({
      ...slot, param: rk, name, label: `${file.filename} · ${slot.sheet}`,
      columns: fix?.columns ?? applyMapping(sheet.columns.map((c) => c.name), props.fieldMapping), rows: fix?.rows ?? sheet.rows,
    })
  }
  return out
})
const tableOf = (param: string) => slotTables.value.find((t) => t.param === param)
const tablesOf = (name: string) => slotTables.value.filter((t) => baseOf(t.param) === name)
const fileOf = (param: string) => pool.value.find((f) => f.file_id === slotOf(param)?.fileId)

function choose(param: string, fileId: string, sheet: string) {
  slots[param] = { fileId, sheet }
  picking.value = null
  result.value = null
  // 换了数据，原来选的列就不一定还在了：属于这张表的列参数清掉重选
  for (const p of declared.value) if (p.of === baseOf(param)) values[key(p)] = p.type === 'columns' ? [] : ''
}

function addRow(p: ParamDraft) {
  extraRows[key(p)] = Math.min((extraRows[key(p)] ?? 1) + 1, MAX_ROWS)
  result.value = null
}

/** 去掉一行：后面的行依次往前挪 */
function removeRow(p: ParamDraft, rowKey: string) {
  const total = extraRows[key(p)] ?? 1
  if (total <= 1) return
  for (let n = rowNumber(rowKey); n < total; n++) slots[rowKeyOf(key(p), n)] = slotOf(rowKeyOf(key(p), n + 1))
  delete slots[rowKeyOf(key(p), total)]
  extraRows[key(p)] = total - 1
  result.value = null
  for (const q of declared.value) if (q.of === key(p)) values[key(q)] = q.type === 'columns' ? [] : ''
}

function clear(param: string) {
  slots[param] = null
  result.value = null
}

function useSample(param: string) {
  slots[param] = undefined
  result.value = null
  for (const p of declared.value) if (p.of === baseOf(param)) values[key(p)] = p.type === 'columns' ? [] : ''
}

/** 上传一份新文件：放进这个客户的文件里，直接放进这个位置（用它的第一张工作表） */
async function upload(param: string, event: Event) {
  const input = event.target as HTMLInputElement
  const chosen = input.files?.[0]
  input.value = ''
  if (!chosen || uploading.value) return
  uploading.value = param
  try {
    const info = await customerApi.uploadFile(props.customerId, chosen, 'input')
    pool.value.unshift(info)
    if (info.sheets[0]) choose(param, info.file_id, info.sheets[0].sheet)
    else toast.error('这个文件里没有可读取的数据')
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    uploading.value = null
  }
}

// ---------------------------------------------------------------- 参数取值

function columnsFor(p: ParamDraft): string[] {
  const owner = p.of ? tablesOf(p.of) : []
  const scope = owner.length ? owner : slotTables.value
  return [...new Set(scope.flatMap((t) => t.columns))]
}

function toggleMulti(name: string, item: string) {
  const cur = Array.isArray(values[name]) ? (values[name] as string[]) : []
  values[name] = cur.includes(item) ? cur.filter((x) => x !== item) : [...cur, item]
}

const multi = (p: ParamDraft) => p.type === 'columns'
const asList = (name: string) => (Array.isArray(values[name]) ? (values[name] as string[]) : [])
const asText = (name: string) => (typeof values[name] === 'string' ? (values[name] as string) : '')

/** 参数值填好的就保留，没填的补默认值（表参数不在这里：由放进去的数据决定） */
function syncValues() {
  for (const p of declared.value) {
    const k = key(p)
    if (isTable(p)) continue
    if (!(k in values)) values[k] = p.type === 'columns' ? [] : p.default
  }
}

watch(declared, syncValues, { immediate: true, deep: true })

function collectParams(): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const p of declared.value) {
    const k = key(p)
    if (isTable(p)) {
      const own = tablesOf(k)
      if (own.length) out[k] = p.type === 'tables' ? own.map((t) => t.name) : own[0]!.name
      continue
    }
    if (multi(p)) {
      if (asList(k).length) out[k] = asList(k)
      continue
    }
    const text = asText(k).trim()
    if (!text) continue
    if (p.type === 'int' || p.type === 'number') out[k] = Number.isNaN(Number(text)) ? text : Number(text) // 不是数字就原样交给后端报「类型不对」
    else if (p.type === 'bool') out[k] = text === 'true'
    else if (p.type === 'list') out[k] = splitList(text)
    else if (p.type === 'dict') {
      try {
        out[k] = JSON.parse(text)
      } catch {
        out[k] = text
      }
    } else out[k] = text
  }
  return out
}

// ---------------------------------------------------------------- 运行

async function run() {
  if (!props.draft || running.value) return
  running.value = true
  result.value = null
  try {
    const inputs = slotTables.value.map((t) => ({ table: t.name, file_id: t.fileId, sheet: t.sheet }))
    const r = await customerApi.trialFormula(props.customerId, { formula: draftToBody(props.draft), params: collectParams(), inputs })
    for (const info of r.inputs ?? []) {
      const t = slotTables.value.find((x) => x.name === info.table)
      if (t) corrected[`${t.fileId}::${t.sheet}`] = { columns: info.columns, rows: info.rows } // 用后端读到的真实列名校正
    }
    result.value = r
  } catch (err) {
    result.value = { status: 'failed', stage: 'run', error: errorText(err) } // 权限、网络这类请求本身的错误
  } finally {
    running.value = false
  }
}

const previewColumns = computed(() => result.value?.preview?.columns.map((c) => c.name) ?? [])
const renamed = computed(() => (result.value?.inputs ?? []).flatMap((i) => Object.entries(i.renamed ?? {}).map(([from, to]) => `${i.table}：「${from}」→「${to}」`)))

</script>

<template>
  <section class="space-y-6" data-testid="trial-panel">
    <div v-if="declared.length">
      <h4 class="mb-2.5 text-sm font-semibold" title="在临时工作区里跑，不会改动客户的任何数据">参数</h4>
      <div class="space-y-2.5">
        <div v-for="p in declared" :key="p.uid" class="grid grid-cols-[104px_minmax(0,1fr)] items-start gap-2">
          <div class="pt-1.5 text-xs">
            <span class="break-all font-mono text-ink">{{ key(p) }}</span><span v-if="p.required" class="text-error">*</span>
            <div class="text-[10px] text-faint">{{ paramTypeLabel(p.type) }}</div>
          </div>
          <div class="min-w-0">
              <!-- 表：当前场景的那张表，默认是 AI 生成这个公式时对应的数据；要换才上传或选已有的 -->
            <div v-if="isTable(p)" class="space-y-1.5">
            <div v-for="rk in rowsOf(p)" :key="rk" class="flex min-h-10 flex-wrap items-center gap-x-2 gap-y-1 rounded-xl border border-line bg-white px-3 py-1 text-[13px]" :data-testid="`slot-${rk}`">
                <template v-if="tableOf(rk)">
                  <Check class="size-4 shrink-0 text-success" />
                  <span class="min-w-0 flex-1 truncate font-medium text-ink" :title="tableOf(rk)!.label">{{ fileOf(rk)!.filename }}<template v-if="(fileOf(rk)?.sheets.length ?? 0) > 1"> · {{ tableOf(rk)!.sheet }}</template></span>
                  <span class="shrink-0 tabular-nums text-faint">{{ tableOf(rk)!.rows ?? '?' }} 行</span>
                  <button class="icon-btn size-6 shrink-0" title="清除" aria-label="清除这份数据" @click="clear(rk)"><X class="size-3.5" /></button>
                </template>
                <span v-else class="min-w-0 flex-1 text-mute">{{ usableFiles.length ? '请选择数据' : '请上传文件' }}</span>
                <label class="btn-line h-7 shrink-0 cursor-pointer gap-1 px-2.5 py-0 text-xs" :class="uploading ? 'pointer-events-none opacity-60' : ''">
                  <LoaderCircle v-if="uploading === rk" class="size-3.5 animate-spin" /><Paperclip v-else class="size-3.5" />{{ tableOf(rk) ? '重新上传' : '上传' }}
                  <input type="file" accept=".xlsx,.xlsm,.xls,.csv" class="hidden" :disabled="!!uploading" data-testid="trial-upload" @change="upload(rk, $event)" />
                </label>
                <button v-if="usableFiles.length" type="button" class="btn-text h-7 shrink-0 gap-1 px-2 py-0 text-xs" aria-haspopup="dialog" @click="picking = rk">已上传<ChevronDown class="size-3.5" /></button>
                <button v-if="sampleFor(rk) && !isSample(rk)" type="button" class="btn-text h-7 shrink-0 px-2 py-0 text-xs" @click="useSample(rk)">样例文件</button>
                <button v-if="rowNumber(rk) > 1" class="icon-btn size-6 shrink-0" title="去掉这张表" aria-label="去掉这张表" @click="removeRow(p, rk)"><Trash2 class="size-3.5" /></button>
              </div>
              <button v-if="p.type === 'tables' && rowsOf(p).length < MAX_ROWS" type="button" class="btn-text h-7 gap-1 px-2 py-0 text-xs" data-testid="trial-add-table" @click="addRow(p)"><Plus class="size-3.5" />再加一张表</button>
            </div>
            <select v-else-if="p.type === 'column'" v-model="values[key(p)]" class="select select-sm h-10 min-h-10 w-full rounded-xl !border-line text-[13px]">
              <option value="">{{ columnsFor(p).length ? '选一列' : '先放入数据' }}</option>
              <option v-for="c in columnsFor(p)" :key="c" :value="c">{{ c }}</option>
            </select>
            <div v-else-if="multi(p)" class="flex flex-wrap gap-1.5">
              <button
                v-for="item in columnsFor(p)"
                :key="item"
                class="rounded-full border px-2.5 py-0.5 text-xs transition"
                :class="asList(key(p)).includes(item) ? 'border-ink bg-ink text-white' : 'border-line bg-white text-mute hover:border-ink/30'"
                @click="toggleMulti(key(p), item)"
              >
                {{ item }}
              </button>
              <span v-if="!columnsFor(p).length" class="text-xs text-faint">先放入数据</span>
            </div>
            <select v-else-if="p.type === 'bool'" v-model="values[key(p)]" class="select select-sm h-10 min-h-10 w-full rounded-xl !border-line text-[13px]">
              <option value="">不填</option><option value="true">是</option><option value="false">否</option>
            </select>
            <select v-else-if="p.type === 'enum'" v-model="values[key(p)]" class="select select-sm h-10 min-h-10 w-full rounded-xl !border-line text-[13px]">
              <option value="">不填</option>
              <option v-for="v in splitList(p.values)" :key="v" :value="v">{{ v }}</option>
            </select>
            <textarea v-else-if="p.type === 'sql' || p.type === 'dict'" v-model="values[key(p)]" class="field h-16 font-mono text-xs" :placeholder="p.type === 'sql' ? 'SELECT …' : '{&quot;旧值&quot;: &quot;新值&quot;}'" />
            <input
              v-else
              v-model="values[key(p)]"
              class="field h-10 py-0 text-[13px]"
              :class="p.type === 'expr' ? 'font-mono' : ''"
              :placeholder="p.type === 'expr' ? '例如：金额 > 100' : p.type === 'list' ? '用逗号分隔' : ''"
            />
            <p v-if="p.desc" class="mt-0.5 text-[11px] text-faint">{{ p.desc }}</p>
          </div>
        </div>
      </div>
    </div>

    <!-- 运行 -->
    <div>
      <button class="btn-ink w-full" :disabled="!draft || running" data-testid="trial-run" @click="run">
        <LoaderCircle v-if="running" class="size-4 animate-spin" /><FlaskConical v-else class="size-4" />{{ running ? '运行中…' : '运行' }}
      </button>
      <p v-if="!draft" class="mt-2 text-xs text-[#b7770c]">{{ blocked || '公式还有问题，先到「定义」里改好' }}</p>
    </div>

    <!-- 结果 -->
    <div v-if="result" data-testid="trial-result">
      <div v-if="result.status === 'failed'" class="rounded-xl bg-error/5 px-4 py-3 text-sm text-error">
        <div class="flex items-center gap-2 font-medium"><CircleAlert class="size-4 shrink-0" />{{ STAGES[result.stage ?? 'run'] }}</div>
        <p class="mt-1 whitespace-pre-wrap break-words text-[13px] leading-relaxed">{{ result.error }}</p>
      </div>
      <template v-else>
        <div class="flex flex-wrap items-center gap-2 rounded-xl bg-success/10 px-4 py-2.5 text-sm text-success">
          <CircleCheck class="size-4 shrink-0" />
          <span class="font-medium">运行成功</span>
          <span>{{ result.rows }} 行 × {{ result.columns?.length }} 列</span>
          <span class="ml-auto text-xs opacity-70">{{ duration(result.duration_ms) }}</span>
        </div>
        <ul v-if="result.warnings?.length" class="mt-2 space-y-1 text-xs text-[#b7770c]">
          <li v-for="w in result.warnings" :key="w">⚠ {{ w }}</li>
        </ul>
        <p v-if="renamed.length" class="mt-2 text-[11px] text-faint" :title="renamed.join('；')">已按字段映射统一 {{ renamed.length }} 个列名</p>
        <DataTable class="mt-3" :columns="previewColumns" :rows="result.preview?.rows ?? []" max-height="280px" dense />
        <p v-if="(result.preview?.total ?? 0) > (result.preview?.rows.length ?? 0)" class="mt-1 text-[11px] text-faint">前 {{ result.preview?.rows.length }} 行，共 {{ result.preview?.total }} 行</p>
      </template>
      <details v-if="result.sql" class="mt-3 rounded-xl border border-line bg-white px-3 py-2 text-xs">
        <summary class="cursor-pointer text-mute">实际执行的 SQL</summary>
        <pre class="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed">{{ result.sql }}</pre>
      </details>
    </div>

    <BaseModal v-if="picking" title="选择已上传的文件" width="max-w-md" @close="picking = null">
      <ul class="-mx-2 space-y-0.5" role="listbox" aria-label="已上传的文件">
        <template v-for="f in usableFiles" :key="f.file_id">
          <li v-for="sheet in f.sheets" :key="sheet.sheet" role="option" :aria-selected="slotOf(picking)?.fileId === f.file_id && slotOf(picking)?.sheet === sheet.sheet">
            <button type="button" class="flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left hover:bg-paper focus-visible:bg-paper focus-visible:outline-none" @click="choose(picking!, f.file_id, sheet.sheet)">
              <span class="min-w-0 flex-1 truncate text-sm">{{ f.filename }}<template v-if="f.sheets.length > 1"> · {{ sheet.sheet }}</template></span>
              <span class="shrink-0 text-xs text-faint">{{ timeAgo(f.created_at) }}</span>
              <Check v-if="slotOf(picking)?.fileId === f.file_id && slotOf(picking)?.sheet === sheet.sheet" class="size-4 shrink-0 text-success" />
            </button>
          </li>
        </template>
      </ul>
    </BaseModal>
  </section>
</template>
