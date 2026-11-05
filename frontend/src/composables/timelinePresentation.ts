import type { TimelineItem } from './chatModel'

type ToolItem = Extract<TimelineItem, { kind: 'tool' }>

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
    .replace(/\b(?:recipe_id|run_id|file_id|tool_call_id)\s*[=:]\s*[A-Za-z0-9_-]+/g, '')
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

/** Describe declared work in business terms; never render arbitrary parameter values. */
function toolDetails(item: ToolItem): string[] {
  const p = item.params
  const table = names(p.table)
  const columns = names(p.columns)
  const detail = (value: unknown) => {
    if (typeof value !== 'string') return ''
    const text = userFacingSummary(value, '', 600)
    // Human-readable descriptions occasionally include a technical method;
    // retain the business sentences and leave implementation details in diagnostics.
    return text.split(/(?<=[。；\n])/).filter((sentence) =>
      !/\b(?:Python|pandas|NumPy|openpyxl|DuckDB|SQL|API|JSON|OUTPUT_DIR|deliver_files|result_tables)\b|内置公式|脚本代码|工具参数/i.test(sentence),
    ).join('').trim()
  }
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

export function toolPresentation(item: ToolItem): { label: string; summary: string; details: string[] } {
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
  return { label, summary, details: toolDetails(item) }
}

/** Preserve paragraphs and lists in public feedback when no technical content was removed. */
export function userFacingSummary(value: string | undefined, fallback = '', maxLength = 800): string {
  const text = userFacingText(value, fallback, maxLength)
  const source = value?.trim() ?? ''
  return source && source.replace(/\s+/g, ' ') === text ? source : text
}
