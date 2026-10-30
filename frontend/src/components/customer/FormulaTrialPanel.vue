<script setup lang="ts">
/**
 * 公式试运行：在临时工作区里，用客户的文件（会按客户的字段映射统一列名）或粘贴的几行数据，
 * 把公式（还没保存的也行）跑一遍，看结果预览。不会改动客户的任何数据。
 *
 * 参数的输入框按公式声明的类型自动生成：表 → 选一份试运行数据，列 → 选这张表里的列，其余按类型填值。
 */
import { CircleAlert, CircleCheck, ClipboardPaste, FileSpreadsheet, FlaskConical, LoaderCircle, Plus, Sparkles, X } from '@lucide/vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'

import { customerApi } from '@/api'
import { errorText } from '@/api/http'
import type { FileInfo, FormulaTrialResult } from '@/api/types'
import DataTable from '@/components/common/DataTable.vue'
import { duration } from '@/composables/chatModel'
import { applyMapping, draftToBody, paramTypeLabel, splitList, type FormulaDraft, type ParamDraft } from '@/composables/formulaDraft'

interface TrialTable {
  name: string
  kind: 'file' | 'text'
  fileId?: string
  sheet?: string
  text?: string
  label: string
  columns: string[]
  rows: number | null
}

const props = defineProps<{
  customerId: string
  draft: FormulaDraft | null // 公式有问题时是 null
  blocked: string // draft 为 null 时给用户看的原因
  files: FileInfo[]
  fieldMapping: Record<string, string[]>
}>()

/** 示例数据：从 Excel 里复制出来的样子（Tab 分隔，第一行是表头），没有客户文件时也能马上试 */
const SAMPLES = [
  {
    label: '运费明细（示例）', name: '运费明细',
    text: '运单号\t店铺名称\t费用类型\t金额\nSF001\t鲜果日记旗舰店\t运费\t18.5\nSF001\t鲜果日记旗舰店\t保价费\t3\nSF002\t山野茶舍\t运费\t26\nSF003\t海风零食铺\t运费\t9.8\nSF004\t新食研直营店\t运费\t31.2\nSF004\t新食研直营店\t包装费\t3\nSF005\t山野茶舍\t运费\t14.6',
  },
  {
    label: '店铺归属（示例）', name: '店铺归属',
    text: '店铺名称\t归属\t负责人\n鲜果日记旗舰店\t华东事业部\t王敏\n山野茶舍\t华南事业部\t李想\n海风零食铺\t华东事业部\t王敏',
  },
]

const STAGES: Record<string, string> = {
  definition: '公式定义有问题',
  data: '试运行用的数据有问题',
  params: '参数取值有问题',
  run: 'SQL 执行出错',
}

const tables = ref<TrialTable[]>([])
const values = reactive<Record<string, string | string[]>>({}) // 参数取值：多选的是数组，其余都是文本
const source = ref<'file' | 'text' | 'sample'>('file')
const pickedFile = ref('')
const pasted = ref('')
const newName = ref('')
const running = ref(false)
const result = ref<FormulaTrialResult | null>(null)

const usableFiles = computed(() => props.files.filter((f) => f.sheets.length))
const declared = computed<ParamDraft[]>(() => props.draft?.params.filter((p) => p.name.trim()) ?? [])
const key = (p: ParamDraft) => p.name.trim()
const tableNames = computed(() => tables.value.map((t) => t.name))

// ---------------------------------------------------------------- 试运行数据

/** 和后端 sanitize_table_name 同一套规则：中文、字母、数字、下划线，数字开头加 t_ */
function sanitizeName(raw: string): string {
  let text = raw.replace(/\.(xlsx|xlsm|xls|csv)$/i, '').replace(/[^\p{L}\p{N}_]+/gu, '_').replace(/^_+|_+$/g, '')
  if (!text) return '数据'
  if (/^\d/.test(text)) text = `t_${text}`
  return text.slice(0, 60)
}

function uniqueName(base: string): string {
  const name = sanitizeName(base)
  let candidate = name
  for (let i = 2; tableNames.value.includes(candidate); i++) candidate = `${name}_${i}`
  return candidate
}

/** 文件名去掉月份、日期：运费明细_2026-09.xlsx → 运费明细（和流程里输入表的命名一致） */
function stemOf(filename: string): string {
  return filename.replace(/\.(xlsx|xlsm|xls|csv)$/i, '').replace(/[\s_\-（(]*\d[\d\s_\-.年月日）)]*$/, '') || filename
}

function parseText(text: string): { columns: string[]; rows: number } {
  const lines = text.replace(/\r\n?/g, '\n').split('\n')
  while (lines.length && !lines[0].trim()) lines.shift()
  const head = lines[0] ?? ''
  const delimiter = ['\t', ',', ';', '|'].find((d) => head.includes(d)) ?? ','
  return { columns: head.split(delimiter).map((c) => c.trim()), rows: lines.slice(1).filter((l) => l.trim()).length }
}

function addFile(fileKey: string, name?: string) {
  const [fileId, sheetName] = fileKey.split('::')
  const file = props.files.find((f) => f.file_id === fileId)
  const sheet = file?.sheets.find((s) => s.sheet === sheetName)
  if (!file || !sheet) return
  tables.value.push({
    name: uniqueName(name || stemOf(file.filename)), kind: 'file', fileId, sheet: sheetName, label: `${file.filename} · ${sheetName}`,
    columns: applyMapping(sheet.columns.map((c) => c.name), props.fieldMapping), rows: sheet.rows,
  })
}

function addText(text: string, name: string, label: string) {
  const parsed = parseText(text)
  if (!parsed.columns.some(Boolean)) return
  tables.value.push({ name: uniqueName(name), kind: 'text', text, label, columns: parsed.columns, rows: parsed.rows })
}

function addSource() {
  if (source.value === 'file') {
    if (!pickedFile.value) return
    addFile(pickedFile.value, newName.value || undefined)
  } else if (source.value === 'text') {
    if (!pasted.value.trim()) return
    addText(pasted.value, newName.value || '粘贴的数据', '粘贴的表格')
    pasted.value = ''
  }
  newName.value = ''
  result.value = null
}

function addSample(i: number) {
  const s = SAMPLES[i]
  addText(s.text, s.name, s.label)
  result.value = null
}

function removeTable(name: string) {
  tables.value = tables.value.filter((t) => t.name !== name)
  result.value = null
}

// ---------------------------------------------------------------- 参数取值

function columnsFor(p: ParamDraft): string[] {
  const owner = p.of ? values[p.of] : ''
  const scope = typeof owner === 'string' && owner ? tables.value.filter((t) => t.name === owner) : tables.value
  return [...new Set(scope.flatMap((t) => t.columns))]
}

function toggleMulti(name: string, item: string) {
  const cur = Array.isArray(values[name]) ? (values[name] as string[]) : []
  values[name] = cur.includes(item) ? cur.filter((x) => x !== item) : [...cur, item]
}

const multi = (p: ParamDraft) => p.type === 'tables' || p.type === 'columns'
const asList = (name: string) => (Array.isArray(values[name]) ? (values[name] as string[]) : [])
const asText = (name: string) => (typeof values[name] === 'string' ? (values[name] as string) : '')

/** 参数值填好的就保留，没填的补默认值；「表」参数自动指到试运行数据上（有两个表参数就依次指到不同的表） */
function syncValues() {
  const names = tableNames.value
  const used = new Set<string>()
  for (const p of declared.value) {
    const k = key(p)
    if (!(k in values)) values[k] = multi(p) ? [] : p.default
    if (p.type === 'table') {
      const cur = values[k]
      if (typeof cur === 'string' && names.includes(cur)) used.add(cur)
      else {
        const next = names.find((n) => !used.has(n)) ?? names[0] ?? ''
        values[k] = next
        if (next) used.add(next)
      }
    } else if (p.type === 'tables') {
      const cur = asList(k).filter((n) => names.includes(n))
      values[k] = cur.length || !names.length ? cur : [...names]
    }
  }
}

watch([declared, tableNames], syncValues, { immediate: true, deep: true })

function collectParams(): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const p of declared.value) {
    const k = key(p)
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
    const inputs = tables.value.map((t) =>
      t.kind === 'file' ? { table: t.name, file_id: t.fileId, sheet: t.sheet } : { table: t.name, text: t.text },
    )
    const r = await customerApi.trialFormula(props.customerId, { formula: draftToBody(props.draft), params: collectParams(), inputs })
    for (const info of r.inputs ?? []) {
      const t = tables.value.find((x) => x.name === info.table)
      if (t) {
        t.columns = info.columns // 用后端读到的真实列名校正
        t.rows = info.rows
      }
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

onMounted(() => {
  // 客户有文件时先放一份进来，点「运行」就能试
  const first = usableFiles.value.find((f) => f.kind === 'input') ?? usableFiles.value[0]
  if (first) {
    pickedFile.value = `${first.file_id}::${first.sheets[0].sheet}`
    addFile(pickedFile.value)
    pickedFile.value = ''
  }
})
</script>

<template>
  <section class="space-y-4 rounded-2xl border border-line bg-paper/60 p-4" data-testid="trial-panel">
    <div class="flex items-center gap-2">
      <FlaskConical class="size-4" />
      <h3 class="text-sm font-semibold">试运行</h3>
      <span class="text-xs text-faint">在临时工作区里跑一遍，不会改动客户的任何数据</span>
    </div>

    <!-- 1. 数据 -->
    <div>
      <div class="label-mini mb-2">1 · 试运行用的数据</div>
      <div v-if="tables.length" class="mb-3 space-y-1.5">
        <div v-for="t in tables" :key="t.name" class="flex items-center gap-2 rounded-xl border border-line bg-white px-3 py-2 text-xs">
          <FileSpreadsheet v-if="t.kind === 'file'" class="size-3.5 shrink-0 text-fun-green" />
          <ClipboardPaste v-else class="size-3.5 shrink-0 text-fun-blue" />
          <div class="min-w-0 flex-1">
            <div class="flex items-baseline gap-2"><span class="font-mono font-medium text-ink">{{ t.name }}</span><span class="truncate text-faint">{{ t.label }}</span></div>
            <div class="truncate text-[11px] text-mute">{{ t.rows ?? '?' }} 行 · {{ t.columns.join('、') }}</div>
          </div>
          <button class="icon-btn size-6" title="移除" @click="removeTable(t.name)"><X class="size-3.5" /></button>
        </div>
      </div>

      <div class="rounded-xl border border-dashed border-line bg-white p-3">
        <div class="mb-2 flex gap-1 text-xs">
          <button
            v-for="[k, label] in ([['file', '客户文件'], ['text', '粘贴表格'], ['sample', '示例数据']] as const)"
            :key="k"
            class="rounded-full px-3 py-1 transition"
            :class="source === k ? 'bg-ink text-white' : 'text-mute hover:bg-paper'"
            @click="source = k"
          >
            {{ label }}
          </button>
        </div>
        <template v-if="source === 'file'">
          <p v-if="!usableFiles.length" class="py-2 text-xs text-faint">这个客户还没有上传文件，可以改用「粘贴表格」或「示例数据」</p>
          <div v-else class="flex flex-wrap gap-2">
            <select v-model="pickedFile" class="select select-sm min-w-0 flex-1 rounded-xl">
              <option value="">选一个文件的 sheet</option>
              <template v-for="f in usableFiles" :key="f.file_id">
                <option v-for="s in f.sheets" :key="s.sheet" :value="`${f.file_id}::${s.sheet}`">{{ f.filename }} · {{ s.sheet }}（{{ s.rows }} 行）</option>
              </template>
            </select>
            <input v-model="newName" class="field w-28 py-1.5 text-xs" placeholder="表名（可不填）" />
            <button class="btn-line py-1.5" :disabled="!pickedFile" @click="addSource"><Plus class="size-3.5" />添加</button>
          </div>
          <p v-if="Object.keys(fieldMapping).length" class="mt-2 text-[11px] text-faint">读入时会按这个客户的字段映射把别名列统一成标准名（和真正执行流程一样）</p>
        </template>
        <template v-else-if="source === 'text'">
          <textarea v-model="pasted" class="field h-24 font-mono text-xs" placeholder="从 Excel 里复制几行粘贴到这里：第一行是表头，列之间用 Tab（Excel 复制的默认格式）或逗号分隔" />
          <div class="mt-2 flex gap-2">
            <input v-model="newName" class="field w-36 py-1.5 text-xs" placeholder="表名（可不填）" />
            <button class="btn-line ml-auto py-1.5" :disabled="!pasted.trim()" @click="addSource"><Plus class="size-3.5" />添加</button>
          </div>
        </template>
        <div v-else class="flex flex-wrap gap-2">
          <button v-for="(s, i) in SAMPLES" :key="s.name" class="btn-line py-1.5" :disabled="tableNames.includes(s.name)" @click="addSample(i)">
            <Sparkles class="size-3.5" />{{ s.label }}
          </button>
        </div>
      </div>
    </div>

    <!-- 2. 参数 -->
    <div>
      <div class="label-mini mb-2">2 · 参数取值</div>
      <p v-if="!declared.length" class="text-xs text-faint">这个公式还没有声明参数</p>
      <div v-else class="space-y-2.5">
        <div v-for="p in declared" :key="p.uid" class="grid grid-cols-[104px_minmax(0,1fr)] items-start gap-2">
          <div class="pt-1.5 text-xs">
            <span class="break-all font-mono text-ink">{{ key(p) }}</span><span v-if="p.required" class="text-error">*</span>
            <div class="text-[10px] text-faint">{{ paramTypeLabel(p.type) }}</div>
          </div>
          <div class="min-w-0">
            <select v-if="p.type === 'table'" v-model="values[key(p)]" class="select select-sm w-full rounded-xl">
              <option value="">{{ tables.length ? '选一份数据' : '先添加试运行数据' }}</option>
              <option v-for="n in tableNames" :key="n" :value="n">{{ n }}</option>
            </select>
            <select v-else-if="p.type === 'column'" v-model="values[key(p)]" class="select select-sm w-full rounded-xl">
              <option value="">选一列</option>
              <option v-for="c in columnsFor(p)" :key="c" :value="c">{{ c }}</option>
            </select>
            <div v-else-if="multi(p)" class="flex flex-wrap gap-1.5">
              <button
                v-for="item in (p.type === 'tables' ? tableNames : columnsFor(p))"
                :key="item"
                class="rounded-full border px-2.5 py-0.5 text-xs transition"
                :class="asList(key(p)).includes(item) ? 'border-ink bg-ink text-white' : 'border-line bg-white text-mute hover:border-ink/30'"
                @click="toggleMulti(key(p), item)"
              >
                {{ item }}
              </button>
              <span v-if="!(p.type === 'tables' ? tableNames : columnsFor(p)).length" class="text-xs text-faint">先添加试运行数据</span>
            </div>
            <select v-else-if="p.type === 'bool'" v-model="values[key(p)]" class="select select-sm w-full rounded-xl">
              <option value="">不填</option><option value="true">是</option><option value="false">否</option>
            </select>
            <select v-else-if="p.type === 'enum'" v-model="values[key(p)]" class="select select-sm w-full rounded-xl">
              <option value="">不填</option>
              <option v-for="v in splitList(p.values)" :key="v" :value="v">{{ v }}</option>
            </select>
            <textarea v-else-if="p.type === 'sql' || p.type === 'dict'" v-model="values[key(p)]" class="field h-16 font-mono text-xs" :placeholder="p.type === 'sql' ? 'SELECT …' : '{&quot;旧值&quot;: &quot;新值&quot;}'" />
            <input
              v-else
              v-model="values[key(p)]"
              class="field py-1.5 text-xs"
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
      <p v-if="!draft" class="mt-2 text-xs text-[#b7770c]">{{ blocked || '公式还有问题，先按左边的提示改好' }}</p>
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
        <p v-if="renamed.length" class="mt-2 text-[11px] text-faint">已按字段映射统一列名：{{ renamed.join('；') }}</p>
        <DataTable class="mt-3" :columns="previewColumns" :rows="result.preview?.rows ?? []" max-height="280px" dense />
        <p v-if="(result.preview?.total ?? 0) > (result.preview?.rows.length ?? 0)" class="mt-1 text-[11px] text-faint">
          只显示前 {{ result.preview?.rows.length }} 行，共 {{ result.preview?.total }} 行
        </p>
      </template>
      <details v-if="result.sql" class="mt-3 rounded-xl border border-line bg-white px-3 py-2 text-xs">
        <summary class="cursor-pointer text-mute">实际执行的 SQL</summary>
        <pre class="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed">{{ result.sql }}</pre>
      </details>
    </div>
  </section>
</template>
