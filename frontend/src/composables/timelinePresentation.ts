import type { TimelineItem } from './chatModel'

type ToolItem = Extract<TimelineItem, { kind: 'tool' }>

export interface ToolDetailSection {
  label: string
  items: string[]
  ordered?: boolean
}

/** Keep progress feedback readable without leaking payloads, code or execution logs. */
export function userFacingText(value: string | undefined, fallback = '', maxLength = 200): string {
  let text = value?.trim() ?? ''
  if (!text) return fallback

  if (/^[{[]/.test(text)) {
    try {
      const payload: unknown = JSON.parse(text)
      if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return fallback
      const fields = payload as Record<string, unknown>
      text = [fields.summary, fields.message, fields.error].find((field): field is string => typeof field === 'string' && !!field.trim()) ?? ''
    } catch {
      return fallback
    }
  }

  if (/PermissionError|Permission denied|文件.*(?:占用|锁定)/i.test(text)) return '文件暂时无法读写，请检查是否被占用或缺少权限。'
  if (/TimeoutError|ConnectionError|timed out|连接中断|连接失败|超时/i.test(text)) return '处理过程中发生超时或连接错误。'
  const missingField = text.match(/(?:KeyError|ColumnNotFoundError):\s*['"]?([^'"\n]{1,60})/)
  if (missingField) return `未找到需要的字段“${missingField[1]?.trim()}”。`
  if (/TemplatePreservationError|原合并单元格|无法.*保留.*格式/i.test(text)) return '写入结果时遇到格式或合并单元格冲突。'
  if (/(?:参数|响应|返回值).{0,20}JSON|JSON.{0,20}(?:参数|格式|对象)/i.test(text)) return '处理步骤的设置需要调整。'

  // An error summary can contain an entire stack trace or a truncated JSON response.
  text = text.split(/Traceback \(most recent call last\):|\n\s*File "/)[0] ?? ''
  text = text.replace(/```[\s\S]*?(?:```|$)/g, '')
    .replace(/\b(?:recipe_id|run_id|file_id|tool_call_id|result_call_id)\s*[=:]\s*[A-Za-z0-9_-]+/g, '')
    .replace(/\b(?:rcp|run|script|call)_[A-Za-z0-9_-]+\b/g, '')
    .replace(/\b(?:f|cv|scr)_[a-f0-9]{8,}\b/gi, '')
    .replace(/\bRecipe\b/gi, '处理流程')
    .replace(/\[(\s*['"][^\]\n]+['"]\s*)\]/g, (_, values: string) => values.replace(/['"]/g, '').replace(/\s*,\s*/g, '、'))
    .replace(/^[\w.]+(?:Error|Exception):\s*/, '')
    .replace(/\s+/g, ' ').trim()

  if (/[{[]\s*["'][\w-]+["']\s*:|\b(?:SELECT\b[\s\S]*\bFROM|INSERT\s+INTO|import\s+\w+|def\s+\w+\(|pd\.|np\.|openpyxl\.)/i.test(text)) return fallback
  if (!text || /^(?:脚本|工具)?\s*[：:·-]*$/.test(text)) return fallback
  return text.length > maxLength ? `${text.slice(0, maxLength)}…` : text
}

function names(value: unknown, limit = 6): string {
  const values = Array.isArray(value) ? value : [value]
  const readable = values.flatMap((entry) => typeof entry === 'string' && userFacingText(entry, '', 100)
    ? [`「${userFacingText(entry, '', 100)}」`] : [])
  return readable.slice(0, limit).join('、') + (readable.length > limit ? `等 ${readable.length} 项` : '')
}

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter((entry): entry is Record<string, unknown> =>
    !!entry && typeof entry === 'object' && !Array.isArray(entry)) : []
}

function publicToolDetail(value: unknown, maxLength = 600): string {
  if (typeof value !== 'string') return ''
  // Engine names in a declared explanation are vocabulary, not executable code.
  // Translate them before sanitizing so a useful change is not removed as an internal ID.
  const text = value.replace(/\bbuild_recipe\b/g, '生成处理流程')
    .replace(/\brevise_recipe\b/g, '修订处理流程').replace(/\brun_recipe\b/g, '执行已有流程')
    .replace(/\brun_python_script\b/g, '脚本处理').replace(/\binput_files\b/g, '输入文件绑定')
    .replace(/\binput_tables\b/g, '输入表').replace(/\bfile_id\b(?!\s*[=:])/g, '文件标识')
    .replace(/\bOUTPUT_DIR\b/g, '结果目录').replace(/\bdeliver_files\b/g, '交付文件清单')
    .replace(/\bresult_tables\b/g, '结果表').replace(/\bname\/ref\b/g, '名称和引用')
  return userFacingSummary(text, '', maxLength)
    .replace(/\b(?:Python|pandas)\b/gi, '脚本处理').replace(/\bNumPy\b/gi, '数值计算')
    .replace(/\bopenpyxl\b/gi, '工作簿处理').replace(/\b(?:DuckDB|SQL)\b/gi, '数据查询').trim()
}

function detailSection(label: string, items: unknown[], ordered = false): ToolDetailSection[] {
  const readable = items.flatMap((item) => {
    const text = publicToolDetail(item, 12000)
    return text ? [text] : []
  })
  return readable.length ? [{ label, items: [...new Set(readable)], ...(ordered ? { ordered: true } : {}) }] : []
}

function explicitSections(item: ToolItem): ToolDetailSection[] {
  const payload: unknown = 'details' in item ? item.details : undefined
  return records(payload).flatMap((section) => {
    const label = typeof section.label === 'string' ? userFacingText(section.label, '', 80) : ''
    return label && Array.isArray(section.items) ? detailSection(label, section.items, section.ordered === true) : []
  })
}

function mergeSections(...sources: ToolDetailSection[][]): ToolDetailSection[] {
  const merged: ToolDetailSection[] = []
  for (const section of sources.flat()) {
    const previous = merged.find((item) => item.label === section.label)
    if (previous) previous.items = [...new Set([...previous.items, ...section.items])]
    else merged.push({ ...section, items: [...section.items] })
  }
  return merged
}

/** Recover the public names from old list_files results, never the IDs or arbitrary result fields. */
function fileListSections(item: ToolItem): ToolDetailSection[] {
  if (item.status === 'running') return []
  const source = item.text?.trim() ?? ''
  let files: Record<string, unknown>[] = []
  if (/^[{[]/.test(source)) {
    try {
      const payload: unknown = JSON.parse(source)
      files = records(Array.isArray(payload) ? payload : payload && typeof payload === 'object' ? (payload as Record<string, unknown>).files : undefined)
    } catch { /* Older stored result text may have been truncated. */ }
  }
  const entries = files.flatMap((file) => {
    const filename = typeof file.filename === 'string' ? userFacingText(file.filename, '', 300) : ''
    if (!filename || /[/\\]/.test(filename)) return []
    const kinds: Record<string, string> = { input: '原始文件', expected: '期望结果样例', output: '结果文件' }
    const kind = typeof file.kind === 'string' ? kinds[file.kind] : ''
    const tables = names(file.tables, 100)
    return [`「${filename}」${kind ? `（${kind}）` : ''}${tables ? `；已加载为表：${tables}` : ''}`]
  })
  for (const line of source.split('\n')) {
    const file = line.match(/^\s*-\s*(?:file_id=[A-Za-z0-9_-]+\s*)?「([^」\n]+)」(?:（([^）\n]*)）)?(.*)$/)
    if (!file || /[/\\]/.test(file[1]!)) continue
    const filename = userFacingText(file[1], '', 300)
    const kind = file[2]?.match(/原始文件|输入文件|期望结果(?:样例)?|人工结果(?:样例)?|结果文件|输出文件/)?.[0]
    const tableList = file[3]?.match(/已加载为表[：:]\s*(\[[^\]\n]*\])/)
    let tables = ''
    if (tableList) {
      try { tables = names(JSON.parse(tableList[1]!.replace(/'/g, '"')), 100) } catch { /* Only unambiguous lists can be restored. */ }
    }
    if (filename) entries.push(`「${filename}」${kind ? `（${kind}）` : ''}${tables ? `；已加载为表：${tables}` : ''}`)
  }
  return detailSection('查看的文件', entries)
}

function scalar(value: unknown): string {
  if (value === null) return '空值'
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (value === '') return '空文本'
  return typeof value === 'string' ? publicToolDetail(value, 300) : ''
}

function mappingChanges(value: unknown): string[] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return []
  return Object.entries(value).flatMap(([before, after]) => {
    const from = publicToolDetail(before, 300)
    const to = scalar(after)
    return from && to ? [`「${from}」→「${to}」`] : []
  })
}

/** Translate simple expressions only; complex code stays outside public progress. */
function expressionDescription(value: unknown): string {
  if (typeof value !== 'string' || !value.trim()) return ''
  const source = value.trim()
  const field = '"([^"\\n]+)"'
  const literal = "(?:'([^'\\n]*)'|(-?\\d+(?:\\.\\d+)?)|(NULL))"
  const comparison = new RegExp(`^${field}\\s*(<=|>=|<>|!=|=|<|>)\\s*${literal}$`, 'i').exec(source)
  if (comparison) {
    const operators: Record<string, string> = { '=': '等于', '<>': '不等于', '!=': '不等于', '>': '大于', '<': '小于', '>=': '大于等于', '<=': '小于等于' }
    return `${names(comparison[1])}${operators[comparison[2]!]}${names(comparison[3] ?? comparison[4] ?? '空值')}`
  }
  const arithmetic = /^"([^"\n]+)"\s*([+*/-])\s*(?:"([^"\n]+)"|(-?\d+(?:\.\d+)?))$/.exec(source)
  if (arithmetic) {
    const operators: Record<string, string> = { '+': '加', '-': '减', '*': '乘', '/': '除以' }
    return `${names(arithmetic[1])}${operators[arithmetic[2]!]}${arithmetic[3] ? names(arithmetic[3]) : arithmetic[4]}`
  }
  return ''
}

function transformationSections(item: ToolItem): ToolDetailSection[] {
  const p = item.params
  const target = names(p.table)
  const sections = detailSection('处理对象', target ? [target] : [])
  switch (item.tool) {
    case 'rename_columns':
      return [...sections, ...detailSection('字段改名', mappingChanges(p.mapping))]
    case 'map_values':
      return [...sections, ...detailSection('处理字段', [names(p.column)]), ...detailSection('值的变化', mappingChanges(p.mapping)),
        ...detailSection('写入位置', [names(p.target || p.column)]),
        ...detailSection('未匹配的值', [p.default == null ? '保持原值。' : `改为「${scalar(p.default)}」。`])]
    case 'cast_columns': {
      const types: Record<string, string> = { integer: '整数', float: '小数', text: '文本', date: '日期', datetime: '日期时间' }
      const changes = p.types && typeof p.types === 'object' && !Array.isArray(p.types) ? Object.entries(p.types).flatMap(([column, type]) =>
        typeof type === 'string' && types[type] ? [`${names(column)} → ${types[type]}（未记录转换前的类型）`] : []) : []
      return [...sections, ...detailSection('类型变化', changes)]
    }
    case 'filter_rows':
      return [...sections, ...detailSection('保留条件', [expressionDescription(p.condition) || publicToolDetail(p.description) || '已保存的筛选条件较复杂，本记录未提供对应的文字说明。']),
        ...detailSection('处理方式', ['满足条件的记录写入结果表，其余记录不写入结果表。'])]
    case 'compute_column':
      return [...sections, ...detailSection('写入字段', [names(p.column)]),
        ...detailSection('计算规则', [expressionDescription(p.expression) || publicToolDetail(p.description) || '已保存的计算表达式较复杂，本记录未提供对应的文字说明。']),
        ...detailSection('写入方式', ['目标字段已存在时更新该字段，否则新增字段。', p.after ? `新增字段放在${names(p.after)}之后。` : ''])]
    case 'fill_null':
      return [...sections, ...detailSection('处理字段', [names(p.columns) || '所有字段']),
        ...detailSection('值的变化', [p.method === 'forward' ? '空值 → 同一字段上一个非空值。' : `空值 →「${scalar(p.value)}」。`])]
    case 'trim_text':
      return [...sections, ...detailSection('处理字段', [names(p.columns) || '所有文本字段']),
        ...detailSection('文本变化', ['去除首尾空格，并将连续多余空白合并。'])]
    default: return []
  }
}

/** Recover numbered requirements even when a provider puts the whole list on one line. */
function instructionSections(value: unknown, revise: boolean): ToolDetailSection[] {
  const text = publicToolDetail(value, 12000).replace(/\r\n?/g, '\n')
  if (!text) return []
  const markers = [...text.matchAll(/(?:^|(?<=[\s：:；;。]))(?:(\d{1,2})(?:[.．](?:[ \t]+(?=\S)|(?=[\p{Script=Han}「“"']))|[、)）][ \t]*(?=[^\d\s]))|[（(](\d{1,2})[)）][ \t]*(?=[^\d\s]))/gu)]
  const first = markers.findIndex((marker) => Number(marker[1] || marker[2]) === 1)
  const numbered: RegExpMatchArray[] = []
  if (first >= 0) {
    for (const marker of markers.slice(first)) {
      if (Number(marker[1] || marker[2]) !== numbered.length + 1) break
      numbered.push(marker)
    }
  }
  const heading = /(?:\*\*)?(?:具体要求|处理要求|执行要求|调整要求|要求)(?:\*\*)?\s*[：:]\s*$/u
  if (numbered.length > 1 || numbered.length === 1 && (numbered[0]!.index === 0 || heading.test(text.slice(0, numbered[0]!.index)))) {
    const goal = text.slice(0, numbered[0]!.index).trim().replace(heading, '').trim()
    const items = numbered.map((marker, index) => text.slice(marker.index! + marker[0].length, numbered[index + 1]?.index ?? text.length).trim())
    return [
      ...(goal ? [{ label: revise ? '调整目标' : '处理目标', items: [goal] }] : []),
      { label: revise ? '调整要求' : '具体要求', items, ordered: true },
    ]
  }
  const paragraphs = text.split(/\n\s*\n/).map((paragraph) => paragraph.trim()).filter(Boolean)
  const bullets = text.split(/(?:^|\n)[ \t]*[-*+][ \t]+/)
  if (bullets.length > 1) {
    const goal = bullets.shift()!.trim().replace(heading, '').trim()
    return [
      ...(goal ? [{ label: revise ? '调整目标' : '处理目标', items: [goal] }] : []),
      { label: revise ? '调整要求' : '具体要求', items: bullets.map((bullet) => bullet.trim()).filter(Boolean) },
    ]
  }
  return [{ label: revise ? '调整目标' : '处理目标', items: paragraphs }]
}

/** Read only declared recovery fields; older records may carry these labels in public feedback. */
function recoverySections(item: ToolItem): ToolDetailSection[] {
  const feedback = new Map<string, string>()
  const aliases: Record<string, string> = {
    '处理对象': 'target', '调整前': 'before', '调整后': 'after',
    '处理方法': 'method', '问题依据': 'evidence', '失败依据': 'evidence', '结果依据': 'evidence',
    '调整内容': 'change', '实质变化': 'change', '校验目标': 'validation', '验证目标': 'validation',
    '核对内容': 'validation', '下一步': 'next',
  }
  const source = item.status === 'running' ? '' : item.text?.replace(/\r\n?/g, '\n') ?? ''
  const labels = [...source.matchAll(/(?:^|\n)[ \t]*(处理对象|处理方法|问题依据|失败依据|结果依据|调整前|调整后|调整内容|实质变化|校验目标|验证目标|核对内容|下一步)[ \t]*[：:][ \t]*/g)]
  for (const [index, label] of labels.entries()) {
    const key = aliases[label[1]!]!
    const detail = publicToolDetail(source.slice(label.index! + label[0].length, labels[index + 1]?.index ?? source.length), 2400)
    if (detail && !feedback.has(key)) feedback.set(key, detail)
  }
  const existing = item.params.action === 'use_existing_result' || !item.params.action && labels.some((label) => label[1] === '结果依据')
  const fields = existing
    ? [['target', '处理对象'], ['evidence', '结果依据'], ['validation', '核对内容'], ['next', '下一步']]
    : [['target', '处理对象'], ['method', '处理方法'], ['evidence', '问题依据'], ['before', '调整前'], ['after', '调整后'], ['change', '调整内容'], ['validation', '校验目标'], ['next', '下一步']]
  return fields.flatMap(([key, label]) => {
    const value = (key === 'next' ? '' : publicToolDetail(item.params[key!], 2400)) || feedback.get(key!)
    return value ? [{ label: label!, items: [value] }] : []
  })
}

function toolDetailSections(item: ToolItem): ToolDetailSection[] {
  const explicit = explicitSections(item)
  let sections: ToolDetailSection[] = []
  if (item.tool === 'reconsider_approach') {
    sections = recoverySections(item)
    if (!explicit.length && !sections.length && item.status !== 'running') {
      sections = [{ label: '记录缺失', items: ['这条历史记录未保存具体的问题依据、调整前后做法和校验目标，无法还原具体改动。'] }]
    }
  } else if (item.tool === 'build_recipe' || item.tool === 'revise_recipe') {
    sections = instructionSections(item.tool === 'build_recipe' ? item.params.requirement : item.params.instruction, item.tool === 'revise_recipe')
    const inputs = Array.isArray(item.params.input_tables) ? item.params.input_tables : []
    const sources = inputs.flatMap((source) => typeof source === 'string' && userFacingText(source, '', 300)
      ? [`「${userFacingText(source, '', 300)}」`] : [])
    if (sources.length) sections.push({ label: '处理来源', items: [...new Set(sources)] })
  } else if (item.tool === 'list_files') {
    sections = fileListSections(item)
    if (!explicit.length && !sections.length && item.status === 'ok' && !/没有|0\s*个/.test(item.summary ?? '')) {
      sections = [{ label: '记录缺失', items: ['这条历史记录只保存了文件数量，未保存可还原的文件名称和查看范围。'] }]
    }
  } else sections = transformationSections(item)
  // An observed result is more specific than a declaration derived from call parameters.
  return mergeSections(explicit, sections.filter((section) => !explicit.some((observed) => observed.label === section.label)))
}

/** Describe declared work in business terms; never render arbitrary parameter values. */
function toolDetails(item: ToolItem): string[] {
  const p = item.params
  const table = names(p.table)
  const columns = names(p.columns)
  const detail = publicToolDetail
  switch (item.tool) {
    case 'table_info':
      return [`检查${table || '表格'}的列名、数据类型、空值和样本记录。`]
    case 'preview_table': {
      const offset = typeof p.offset === 'number' && p.offset >= 0 ? Math.floor(p.offset) : 0
      const count = typeof p.limit === 'number' && p.limit > 0 ? Math.min(Math.floor(p.limit), 100) : 20
      return [`读取${table || '表格'}从第 ${offset + 1} 行开始的最多 ${count} 行，核对实际内容。`]
    }
    case 'value_counts':
      return [`统计${table || '表格'}中${names(p.column) || '指定字段'}各取值出现的次数和占比。`]
    case 'column_stats':
      return [`检查${table || '表格'}${columns ? `的${columns}` : '中的各字段'}，核对取值统计和空值情况。`]
    case 'run_sql':
      return [detail(p.description), p.result_name ? `结果整理为${names(p.result_name)}。` : ''].filter(Boolean)
    case 'run_python_script':
      return [detail(p.description)].filter(Boolean)
    case 'build_recipe':
      return [detail(p.requirement), names(p.input_tables) ? `处理来源：${names(p.input_tables)}。` : ''].filter(Boolean)
    case 'revise_recipe':
      return [detail(p.instruction)].filter(Boolean)
    case 'export_excel': {
      const sheets = names(records(p.sheets).map((sheet) => sheet.sheet_name || sheet.table))
      return [sheets ? `导出工作表：${sheets}。` : '整理结果，生成可下载的 Excel。']
    }
    case 'create_workbook': {
      const sheets = records(p.sheets)
      return [names(sheets.map((sheet) => sheet.name)) ? `工作表：${names(sheets.map((sheet) => sheet.name))}。` : '',
        sheets.some((sheet) => records(sheet.styles).length || Array.isArray(sheet.merges) && sheet.merges.length)
          ? '按要求设置表格排版、合并区域和样式。' : ''].filter(Boolean)
    }
    case 'show_files': {
      const labels: Record<string, string> = { input: '原始文件', expected: '期望结果样例', output: '已生成的结果文件' }
      const kinds = (Array.isArray(p.kinds) ? p.kinds : [p.kind]).flatMap((kind) => typeof kind === 'string' && labels[kind] ? [labels[kind]] : [])
      return [kinds.length ? `提供${kinds.join('、')}的查看和下载入口。` : '提供所选文件的查看和下载入口。']
    }
    case 'select_columns':
      return [table ? `处理表格：${table}。` : '', columns ? `按给定顺序保留${columns}。` : ''].filter(Boolean)
    case 'drop_columns':
      return [table ? `处理表格：${table}。` : '', columns ? `删除${columns}。` : ''].filter(Boolean)
    case 'drop_duplicates':
      return [table ? `处理表格：${table}。` : '', `按${columns || '整行内容'}判断重复，保留${p.keep === 'last' ? '最后' : '第'}一条记录。`].filter(Boolean)
    case 'sort_rows': {
      const fields = records(p.by).map((entry) => `${names(entry.column)}${entry.order === 'desc' ? '从大到小' : '从小到大'}`).filter((value) => value.startsWith('「'))
      return [table ? `处理表格：${table}。` : '', fields.length ? `排序顺序：${fields.join('，')}。` : ''].filter(Boolean)
    }
    case 'group_aggregate': {
      const operations: Record<string, string> = { sum: '合计', avg: '平均值', count: '数量', count_distinct: '不同值数量', min: '最小值', max: '最大值', median: '中位数', first: '首项', last: '末项', concat: '合并内容' }
      const aggregates = records(p.aggregations).flatMap((entry) => typeof entry.func === 'string' && operations[entry.func]
        ? [`${entry.column === '*' ? '记录' : names(entry.column)}的${operations[entry.func]}`] : [])
      return [table ? `处理表格：${table}。` : '', names(p.group_by) ? `按${names(p.group_by)}分类汇总。` : '汇总整张表的数据。',
        aggregates.length ? `统计${aggregates.slice(0, 6).join('、')}。` : ''].filter(Boolean)
    }
    case 'lookup':
      return [`将${table || '当前表格'}与${names(p.lookup_table) || '对照表'}匹配。`,
        names(p.key) ? `匹配依据：${names(p.key)}${p.lookup_key && p.lookup_key !== p.key ? `对应${names(p.lookup_key)}` : ''}。` : '',
        columns ? `补充字段：${columns}。` : ''].filter(Boolean)
    case 'join_tables':
      return [`合并${names(p.left) || '左侧表格'}与${names(p.right) || '右侧表格'}。`,
        names(p.left_on) ? `匹配字段：${names(p.left_on)}${p.right_on ? `对应${names(p.right_on)}` : ''}。` : ''].filter(Boolean)
    case 'concat_tables':
      return [names(p.tables) ? `将${names(p.tables)}按列名对齐，依次追加记录。` : ''].filter(Boolean)
    case 'compute_column':
      return [table ? `处理表格：${table}。` : '', names(p.column) ? `按已确定的计算规则填写${names(p.column)}。` : ''].filter(Boolean)
    case 'pivot_wide':
      return [names(p.index) ? `每个${names(p.index)}整理为一行。` : '', names(p.pivot_column) ? `按${names(p.pivot_column)}拆出类别列，填入${names(p.value_column) || '对应数值'}。` : ''].filter(Boolean)
    case 'unpivot_long':
      return [names(p.id_columns) ? `保留${names(p.id_columns)}。` : '', `将${names(p.value_columns) || '其他数据列'}逐项展开为明细记录。`].filter(Boolean)
    default:
      return [table ? `处理表格：${table}。` : '', columns ? `涉及字段：${columns}。` : ''].filter(Boolean)
  }
}

export function toolPresentation(item: ToolItem): { label: string; summary: string; details: string[]; sections: ToolDetailSection[] } {
  const label = userFacingText(item.label, '处理数据', 80)
    .replace(/^SQL\s*查询/i, '查询数据')
    .replace(/^长转宽（拆成列）/, '按类别展开数据')
    .replace(/^宽转长/, '整理为逐项明细')
  const fallback = item.status === 'error' ? '本次处理未完成。' : '已完成'
  // The original action and its scope stay in place; append the result when it arrives.
  let summary = item.status === 'running' ? '' : userFacingText(
    item.tool === 'run_python_script' && item.status === 'ok' ? item.text || item.summary : item.summary,
    fallback, 300,
  )
  if (item.status === 'ok' && ['filter_rows', 'compute_column', 'rename_columns', 'map_values', 'cast_columns', 'join_tables'].includes(item.tool)) {
    // Formula summaries may contain a condition, expression or raw parameter dictionary.
    const output = item.summary?.match(/→\s*(「[^」]+」)\s*(\d+)\s*行/)
    summary = output ? `已整理${userFacingText(output[1])}，共 ${output[2]} 行。` : '已完成本次数据整理。'
  }
  const sections = toolDetailSections(item)
  if (item.tool === 'reconsider_approach' && sections.length && /^(?:已调整处理方法|已选择新方法|已核对已有结果)[。.]?$/.test(summary)) summary = ''
  const details = sections.length ? sections.map((section) => `${section.label}：${section.items.map((text, index) =>
    section.ordered ? `${index + 1}. ${text}` : text).join(section.ordered ? '\n' : '、')}`) : toolDetails(item)
  return { label, summary, details, sections }
}

/** Preserve paragraphs and lists in public feedback when no technical content was removed. */
export function userFacingSummary(value: string | undefined, fallback = '', maxLength = 800): string {
  const text = userFacingText(value, fallback, maxLength)
  const source = value?.trim() ?? ''
  return source && source.replace(/\s+/g, ' ') === text ? source : text
}
