/**
 * 客户公式表单的「草稿」：表单里编辑的是草稿，保存 / 试运行时转成后端要的公式 JSON；JSON 模式下反过来。
 *
 * 校验规则和后端 engine/formulas/base.py 的 Formula.check_definition 保持一致（后端才是最终裁判；
 * SQL 语法只有 DuckDB 解析器能判断，由编辑器调后端的「检查」接口实时检查）。
 */

export type ParamType =
  | 'table' | 'tables' | 'column' | 'columns' | 'str' | 'int' | 'number' | 'bool' | 'list' | 'enum' | 'expr' | 'sql' | 'dict' | 'any'

/** 参数类型：label 给人看，hint 说明模板里 {{参数}} 会被代入成什么样子 */
export const PARAM_TYPES: { value: ParamType; label: string; hint: string }[] = [
  { value: 'table', label: '表', hint: '一张表，代入成带引号的表名，例如 "运费明细"' },
  { value: 'column', label: '列', hint: '某张表里的一列，代入成带引号的列名，例如 "金额"' },
  { value: 'columns', label: '多列', hint: '几列，代入成 "列1", "列2"' },
  { value: 'tables', label: '多张表', hint: '几张表，代入成 "表1", "表2"' },
  { value: 'str', label: '文本', hint: "文本，自动加单引号并转义，例如 '华东'" },
  { value: 'int', label: '整数', hint: '整数，例如 100' },
  { value: 'number', label: '数字', hint: '可以带小数，例如 12.5' },
  { value: 'bool', label: '是/否', hint: '代入成 TRUE / FALSE' },
  { value: 'enum', label: '选项', hint: "从固定的几个值里选一个，代入成 'abc'" },
  { value: 'list', label: '文本列表', hint: "一组文本，代入成 'a', 'b'，放在 IN (…) 里用" },
  { value: 'expr', label: '表达式', hint: '一段 SQL 表达式（比如筛选条件），代入时自动加括号' },
  { value: 'sql', label: '子查询', hint: '一条 SELECT，代入时自动加括号，放在 FROM (…) 里用' },
  { value: 'any', label: '任意', hint: '原样当作文本或数字代入' },
  { value: 'dict', label: '映射表', hint: '对象，不能直接放进模板（一般用不上）' },
]

export function paramTypeLabel(type: string): string {
  return PARAM_TYPES.find((t) => t.value === type)?.label ?? type
}

export const RESERVED_PARAM_NAMES = ['result_name'] // 公式工具自带的「结果表名」参数
export const MAX_PARAM_NAME = 64

export interface ParamDraft {
  uid: number
  name: string
  type: ParamType
  required: boolean
  desc: string
  default: string // 以文本编辑，保存时按类型转换
  values: string // 选项类型的可选值（逗号分隔）
  of: string // 列 / 多列属于哪个「表」参数
  extra: Record<string, unknown> // 表单不认识的字段原样保留（item_type、schema…）
}

export interface FormulaDraft {
  id: string
  name: string
  intent: string
  sql: string
  params: ParamDraft[]
  extra: Record<string, unknown> // category、triggers、example、status… 原样保留
}

let seq = 0

export function newParam(name = '', type: ParamType = 'str'): ParamDraft {
  return { uid: ++seq, name, type, required: false, desc: '', default: '', values: '', of: '', extra: {} }
}

export function emptyDraft(): FormulaDraft {
  return { id: '', name: '', intent: '', sql: '', params: [], extra: {} }
}

/** 新建时「填入示例」用：筛出金额大于阈值的行 */
export function exampleDraft(): FormulaDraft {
  const table = { ...newParam('table', 'table'), required: true, desc: '要筛选的表' }
  const col = { ...newParam('amount_col', 'column'), required: true, desc: '金额所在的列', of: 'table' }
  const threshold = { ...newParam('threshold', 'number'), desc: '金额大于这个数才保留', default: '10' }
  return {
    id: 'big_amount',
    name: '筛出大额行',
    intent: '只保留金额大于阈值的行，按金额从大到小排',
    sql: 'SELECT * FROM {{table}}\nWHERE {{amount_col}} > {{threshold}}\nORDER BY {{amount_col}} DESC',
    params: [table, col, threshold],
    extra: {},
  }
}

// ---------------------------------------------------------------- 默认值：文本 ⇄ 按类型的值

export function splitList(text: string): string[] {
  return text.split(/[,，、]/).map((s) => s.trim()).filter(Boolean)
}

/** 默认值文本 → 按类型的值；空表示没有默认值 */
export function parseDefault(p: Pick<ParamDraft, 'type' | 'default' | 'values'>): { value?: unknown; error?: string } {
  const text = p.default.trim()
  if (!text) return {}
  switch (p.type) {
    case 'int':
      return /^-?\d+$/.test(text) ? { value: Number(text) } : { error: '默认值要是整数' }
    case 'number':
      return Number.isFinite(Number(text)) ? { value: Number(text) } : { error: '默认值要是数字' }
    case 'bool':
      return text === 'true' ? { value: true } : text === 'false' ? { value: false } : { error: '默认值要选「是」或「否」' }
    case 'tables':
    case 'columns':
    case 'list':
      return { value: splitList(text) }
    case 'enum':
      return splitList(p.values).includes(text) ? { value: text } : { error: '默认值必须是可选值之一' }
    case 'dict':
      try {
        const value = JSON.parse(text)
        if (value && typeof value === 'object' && !Array.isArray(value)) return { value }
      } catch {
        /* 下面统一报错 */
      }
      return { error: '默认值要是 JSON 对象，例如 {"旧值": "新值"}' }
    default:
      return { value: text }
  }
}

function defaultText(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (Array.isArray(value)) return value.join(', ')
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

// ---------------------------------------------------------------- 草稿 ⇄ 公式 JSON

const KNOWN_PARAM_KEYS = new Set(['type', 'required', 'desc', 'default', 'values', 'of'])
const KNOWN_FORMULA_KEYS = new Set(['id', 'name', 'intent', 'params', 'sql'])
const PARAM_TYPE_VALUES = new Set<string>(PARAM_TYPES.map((t) => t.value))

type Json = Record<string, any> // eslint-disable-line @typescript-eslint/no-explicit-any

/** 公式 JSON（后端返回的，或者 JSON 模式里写的）→ 草稿 */
export function draftFromBody(body: Json): FormulaDraft {
  const params: ParamDraft[] = []
  const rawParams = body.params && typeof body.params === 'object' && !Array.isArray(body.params) ? (body.params as Json) : {}
  for (const [name, spec] of Object.entries(rawParams)) {
    const s: Json = spec && typeof spec === 'object' ? spec : {}
    const type = (PARAM_TYPE_VALUES.has(s.type) ? s.type : 'any') as ParamType
    const extra = Object.fromEntries(Object.entries(s).filter(([k]) => !KNOWN_PARAM_KEYS.has(k)))
    params.push({
      uid: ++seq, name, type, required: Boolean(s.required), desc: String(s.desc ?? ''), default: defaultText(s.default),
      values: Array.isArray(s.values) ? s.values.join(', ') : '', of: String(s.of ?? ''), extra,
    })
  }
  return {
    id: String(body.id ?? ''),
    name: String(body.name ?? ''),
    intent: String(body.intent ?? ''),
    sql: String(body.sql ?? ''),
    params,
    extra: Object.fromEntries(Object.entries(body).filter(([k]) => !KNOWN_FORMULA_KEYS.has(k))),
  }
}

/** 列表里的公式 → 能编辑的公式 JSON（只取公式本身的字段，去掉版本、使用情况这些展示用的） */
export function bodyFromFormula(f: Json): Json {
  const body: Json = { id: f.id, name: f.name, intent: f.intent, category: f.category, params: f.params, sql: f.sql, status: f.status }
  if (f.triggers?.length) body.triggers = f.triggers
  if (f.example) body.example = f.example
  return body
}

function paramBody(p: ParamDraft): Json {
  const out: Json = { ...p.extra, type: p.type, required: p.required, desc: p.desc }
  const d = parseDefault(p)
  if (d.value !== undefined) out.default = d.value
  if (p.type === 'enum') out.values = splitList(p.values)
  if ((p.type === 'column' || p.type === 'columns') && p.of) out.of = p.of
  return out
}

/** 草稿 → 后端的公式 JSON */
export function draftToBody(d: FormulaDraft): Json {
  const params: Json = {}
  for (const p of d.params) params[p.name.trim()] = paramBody(p)
  return { id: d.id.trim(), name: d.name.trim(), intent: d.intent.trim(), ...d.extra, params, sql: d.sql }
}

// ---------------------------------------------------------------- 模板里的占位符

const PLACEHOLDER = /\{\{\s*([A-Za-z_一-鿿][\p{L}\p{N}_一-鿿]*)\s*\}\}/gu

export function placeholdersIn(sql: string): string[] {
  return [...new Set([...sql.matchAll(PLACEHOLDER)].map((m) => m[1]))]
}

/** 写在引号里面的占位符（'%{{x}}%'）：占位符代入时自己会带引号，外面再包一层就错了。注释里的引号不算（和后端 quoted_placeholders 一致）。 */
export function quotedPlaceholders(sql: string): string[] {
  const found: string[] = []
  const at = new RegExp(PLACEHOLDER.source, 'uy')
  let i = 0
  let quote = ''
  while (i < sql.length) {
    const ch = sql[i]
    if (quote) {
      if (ch === quote) {
        if (sql[i + 1] === quote) {
          i += 2
          continue
        }
        quote = ''
      } else {
        at.lastIndex = i
        const m = at.exec(sql)
        if (m) {
          found.push(m[1])
          i += m[0].length
          continue
        }
      }
    } else if (ch === "'" || ch === '"') {
      quote = ch
    } else if (sql.startsWith('--', i)) {
      const end = sql.indexOf('\n', i)
      i = end < 0 ? sql.length : end
      continue
    } else if (sql.startsWith('/*', i)) {
      const end = sql.indexOf('*/', i + 2)
      i = end < 0 ? sql.length : end + 2
      continue
    }
    i += 1
  }
  return quote ? [] : found
}

// ---------------------------------------------------------------- 校验

export interface ValidationContext {
  reservedIds: string[] // 系统工具名，不能当公式 id
  builtinIds: Record<string, string> // 内置公式 id → 名称（同 id 会覆盖内置公式，只提醒）
  existingIds: string[] // 这个客户已有的公式 id
  editingId: string | null // 正在编辑的公式 id（新建时为 null）
}

export interface DraftIssues {
  problems: string[] // 必须改掉才能保存
  warnings: string[] // 提醒，不影响保存
  fields: Record<string, string> // 字段级的错误：id / name / sql / p:{uid}:name|default|values|of
}

const ID_PATTERN = /^[a-z][a-z0-9_-]*$/
const PARAM_NAME_PATTERN = /^[A-Za-z_一-鿿][\p{L}\p{N}_一-鿿]*$/u

export function validateDraft(d: FormulaDraft, ctx: ValidationContext): DraftIssues {
  const issues: DraftIssues = { problems: [], warnings: [], fields: {} }
  const fail = (key: string, message: string, label = '') => {
    issues.fields[key] = message
    issues.problems.push(label ? `${label}：${message}` : message)
  }

  const id = d.id.trim()
  if (!id) fail('id', '请填写公式 id')
  else if (!ID_PATTERN.test(id)) fail('id', '只能用小写字母、数字、下划线、短横线，且以字母开头')
  else if (id.length > 64) fail('id', '最长 64 个字符')
  else if (ctx.reservedIds.includes(id)) fail('id', '和系统工具重名，请换一个')
  else if (ctx.editingId === null && ctx.existingIds.includes(id)) {
    issues.warnings.push(`这个客户已经有公式「${id}」，保存会把它升级成新版本`)
  }
  if (id && ctx.builtinIds[id]) {
    issues.warnings.push(`id「${id}」和内置公式「${ctx.builtinIds[id]}」相同：保存后，这个客户所有流程里用到它的步骤都会改用你写的版本`)
  }
  if (!d.name.trim()) fail('name', '请填写公式名称')
  else if (d.name.trim().length > 128) fail('name', '最长 128 个字符')

  const tableParams = d.params.filter((p) => p.type === 'table').map((p) => p.name.trim())
  const seen = new Set<string>()
  for (const p of d.params) {
    const name = p.name.trim()
    const label = `参数「${name || '（未命名）'}」`
    const key = (field: string) => `p:${p.uid}:${field}`
    if (!name) fail(key('name'), '请填写参数名', '参数')
    else if (!PARAM_NAME_PATTERN.test(name) || name.length > MAX_PARAM_NAME) {
      fail(key('name'), `只能用中文、字母、数字和下划线，不能以数字开头，最长 ${MAX_PARAM_NAME} 个字符`, label)
    } else if (RESERVED_PARAM_NAMES.includes(name)) fail(key('name'), '是系统保留的名字，请换一个', label)
    else if (seen.has(name)) fail(key('name'), '参数名重复了', label)
    seen.add(name)

    if (p.type === 'enum' && splitList(p.values).length === 0) fail(key('values'), '请填写可选值（用逗号分隔）', label)
    if ((p.type === 'column' || p.type === 'columns') && p.of && !tableParams.includes(p.of)) {
      fail(key('of'), '所属的表必须是一个「表」类型的参数', label)
    }
    const parsed = parseDefault(p)
    if (parsed.error) fail(key('default'), parsed.error, label)
  }

  const names = new Set(d.params.map((p) => p.name.trim()))
  const sql = d.sql
  if (!sql.trim()) fail('sql', '请填写 SQL 模板')
  else {
    const used = placeholdersIn(sql)
    const undeclared = used.filter((n) => !names.has(n))
    if (undeclared.length) fail('sql', `模板里用到了还没声明的参数：${undeclared.map((n) => `{{${n}}}`).join('、')}`)
    const dict = used.filter((n) => d.params.find((p) => p.name.trim() === n)?.type === 'dict')
    if (dict.length) fail('sql', `参数「${dict[0]}」是映射表类型，不能直接放进 SQL 模板`)
    const quoted = quotedPlaceholders(sql)
    if (quoted.length) {
      fail('sql', `{{${quoted[0]}}} 写在了引号里面。代入时会自动带上引号，请直接写 {{${quoted[0]}}}，不要再包一层引号`)
    } else if (!undeclared.length && sql.replace(PLACEHOLDER, '').includes('{{')) {
      fail('sql', '模板里有写法不对的占位符，应该写成 {{参数名}}')
    }
    for (const p of d.params) {
      const name = p.name.trim()
      if (name && !used.includes(name)) issues.warnings.push(`参数「${name}」声明了，但模板里没用到`)
    }
  }
  return issues
}

// ---------------------------------------------------------------- JSON 模式：定位出错的行

export interface JsonErrorInfo {
  message: string
  hint: string // 常见错误的中文提示（解析器是在出错位置之后才发现问题的，比如漏逗号会报在下一行）
  line: number | null
  column: number | null
}

function jsonHint(raw: string): string {
  if (/Expected ',' or '[}\]]'|Expected ','/.test(raw)) return '这一行或上一行的末尾可能少了逗号，也可能少了引号 / 括号'
  if (/Expected double-quoted property name|Unexpected token '?[}\]]/.test(raw)) return '最后一项后面多了逗号？JSON 里最后一项后面不能有逗号'
  if (/end of JSON input|Unexpected end/i.test(raw)) return 'JSON 没写完，缺少结尾的 } 或 ]'
  if (/Unexpected token|Unexpected non-whitespace/.test(raw)) return '这里出现了不该有的符号；文字要用英文双引号 " 括起来'
  return ''
}

/** JSON.parse 的报错 → 出错的行、列（浏览器的报错里有位置时才有）和一句中文提示 */
export function jsonErrorInfo(text: string, err: unknown): JsonErrorInfo {
  const raw = err instanceof Error ? err.message : String(err)
  const message = raw.replace(/\s*\(line \d+ column \d+\)\s*$/, '')
  const hint = jsonHint(raw)
  const lc = /line (\d+) column (\d+)/.exec(raw)
  if (lc) return { message, hint, line: Number(lc[1]), column: Number(lc[2]) }
  let pos = -1
  const at = /position (\d+)/.exec(raw)
  if (at) pos = Number(at[1])
  else if (/end of JSON input|Unexpected end/i.test(raw)) pos = text.length
  if (pos < 0) return { message, hint, line: null, column: null }
  const before = text.slice(0, Math.min(pos, text.length))
  const lines = before.split('\n')
  return { message, hint, line: lines.length, column: lines[lines.length - 1].length + 1 }
}

// ---------------------------------------------------------------- 字段映射（和后端 apply_field_mapping 一致）

const NOISE = /[\s_\-()（）[\]【】:：/\\.]+/g
const normalizeName = (s: string) => s.replace(NOISE, '').toLowerCase()

/** 读入表格时，客户的字段映射会把别名列改成标准名；试运行里的列提示也要一样 */
export function applyMapping(columns: string[], mapping: Record<string, string[]>): string[] {
  const taken = new Set(columns)
  const byNorm = new Map(columns.map((c) => [normalizeName(c), c]))
  const renames = new Map<string, string>()
  for (const [standard, aliases] of Object.entries(mapping)) {
    if (taken.has(standard)) continue
    for (const alias of aliases) {
      const actual = columns.includes(alias) ? alias : byNorm.get(normalizeName(alias))
      if (actual && !renames.has(actual) && actual !== standard) {
        renames.set(actual, standard)
        taken.add(standard)
        break
      }
    }
  }
  return columns.map((c) => renames.get(c) ?? c)
}
