import type { AgentEvent, RecipeStepJSON } from '@/api/types'
import type { RecipeProgress } from './chatModel'
import { userFacingSummary, userFacingText } from './timelinePresentation'

export interface RecipeProcessDetail {
  text: string
  status: 'info' | 'ok' | 'warning' | 'error'
  fields?: { label: string; text: string }[]
}

export interface RecipeDetailField { label: string; text: string }

/** Resolve only recorded file, sheet and field names; executable parameters stay private. */
export function recipeStepFields(progress: RecipeProgress, stepId: number, planned?: RecipeStepJSON): RecipeDetailField[] {
  const plan = planned ?? progress.recipe?.steps.find((item) => item.id === stepId)
  const params = plan?.params ?? {}
  const named = (value: unknown): string => typeof value === 'string' ? userFacingText(value, '', 300) : ''
  const reference = (value: unknown): string => {
    const name = named(value)
    if (!name) return ''
    const loaded = progress.inputs.find((item) => item.alias === value)
    const input = progress.recipe?.inputs?.find((item) => item.alias === value)
    const file = named(loaded?.filename || input?.source_file || value)
    const sheet = named(loaded?.sheet || input?.sheet)
    return file ? `「${file}」${sheet ? ` · 工作表「${sheet}」` : ''}` : ''
  }
  const targets: unknown[] = ['table', 'lookup_table', 'left', 'right'].map((key) => params[key])
  for (const key of ['input_tables', 'tables']) if (Array.isArray(params[key])) targets.push(...params[key])
  if (params.input_files && typeof params.input_files === 'object' && !Array.isArray(params.input_files)) targets.push(...Object.values(params.input_files))
  const fields: RecipeDetailField[] = []
  const objects = [...new Set(targets.map(reference).filter(Boolean))]
  if (objects.length) fields.push({ label: '处理对象', text: objects.join('\n') })
  const columns: string[] = []
  const labels: Record<string, string> = { key: '匹配字段', lookup_key: '对照表匹配字段', column: '处理字段', columns: '处理字段', target: '写入字段', left_on: '左侧匹配字段', right_on: '右侧匹配字段', group_by: '分组字段', by: '分组字段', carry_columns: '保留字段' }
  for (const [key, label] of Object.entries(labels)) {
    const values = Array.isArray(params[key]) ? params[key] : [params[key]]
    const names = values.map(named).filter(Boolean)
    if (names.length) columns.push(`${label}：${names.map((name) => `「${name}」`).join('、')}`)
  }
  if (columns.length) fields.push({ label: '相关字段', text: columns.join('\n') })
  for (const [key, label] of [['mapping', plan?.formula === 'map_values' ? '值的对应' : plan?.formula === 'rename_columns' ? '字段名称变化' : '对应规则'], ['types', '字段类型']] as const) {
    const mapping = params[key]
    if (!mapping || typeof mapping !== 'object' || Array.isArray(mapping)) continue
    const entries = Object.entries(mapping).flatMap(([from, to]) => {
      const old = named(from), next = named(to)
      return old && next ? [`「${old}」→「${next}」`] : []
    })
    if (entries.length) fields.push({ label, text: entries.join('\n') })
  }
  const output = named(plan?.output || progress.steps.find((item) => item.step_id === stepId)?.output)
  if (output) fields.push({ label: '结果位置', text: `「${output}」` })
  return fields
}

/** Keep the recorded before/after and evidence separate, including in legacy saved runs. */
export function repairDescriptionFields(explanation?: string): RecipeDetailField[] {
  const text = explanation?.trim().replace(/^修正方案[：:]\s*/, '') ?? ''
  if (!text) return [{ label: '记录缺失', text: '本次未提供具体修改说明，将以重新执行与校验结果确认是否生效。' }]
  const labels = /(?:^|\n)\s*(处理对象|问题依据|实际修改|调整前|调整后|调整内容|校验目标|修正说明|下一步)[：:]\s*/g
  const matches = [...text.matchAll(labels)]
  if (!matches.length) {
    const detail = userFacingSummary(text, '', 2400)
    if (/^(?:已)?(?:调整(?:了)?(?:处理)?(?:方法|参数)|优化(?:了)?逻辑|修复(?:了)?)(?:[。！!\s]*)$/.test(detail)) {
      return [{ label: '记录缺失', text: '这条记录只有调整状态，未记录针对的对象、原做法与新做法。' }]
    }
    return detail ? [{ label: '调整内容', text: detail }] : [{ label: '记录缺失', text: '本次未记录可展示的具体修改说明。' }]
  }
  const fields: RecipeDetailField[] = []
  const prefix = userFacingSummary(text.slice(0, matches[0]!.index), '', 2400)
  if (prefix) fields.push({ label: '调整内容', text: prefix })
  for (const [index, match] of matches.entries()) {
    const detail = userFacingSummary(text.slice(match.index! + match[0].length, matches[index + 1]?.index ?? text.length), '', 2400)
    if (!detail) continue
    const label = match[1]!
    const previous = fields.find((field) => field.label === label)
    if (previous) previous.text += `\n${detail}`
    else fields.push({ label, text: detail })
  }
  return fields.length ? fields : [{ label: '记录缺失', text: '本次未记录可展示的具体修改说明。' }]
}

/** Use declared actions and execution feedback; never substitute generic text for an unreported change. */
export function recipeProcessDetails(progress: RecipeProgress, event: AgentEvent): RecipeProcessDetail[] {
  const result: RecipeProcessDetail[] = []
  const add = (value: string | undefined, status: RecipeProcessDetail['status'] = 'info', fields: { label: string; text?: string }[] = []) => {
    const text = userFacingSummary(value, '', 600)
    const readable = fields.flatMap((field) => {
      const detail = userFacingSummary(field.text, '', 2400)
      return detail ? [{ label: field.label, text: detail }] : []
    })
    if (text) result.push({ text, status, ...(readable.length ? { fields: readable } : {}) })
  }
  const step = progress.steps.find((item) => item.step_id === event.step_id)
  const name = userFacingText(event.name || step?.name, '处理数据', 100)
  const statusFor = (status: string | undefined): RecipeProcessDetail['status'] => {
    if (status === 'fail' || status === 'failed' || status === 'escalated') return 'error'
    if (status === 'warn' || status === 'warning') return 'warning'
    if (status === 'pass' || status === 'success' || status === 'repaired') return 'ok'
    return 'info'
  }
  switch (event.type) {
    case 'recipe_generated': {
      const steps = event.recipe?.steps ?? []
      add(`已生成${steps.length ? ` ${steps.length} 步` : ''}处理方案${event.round > 1 ? `（第 ${event.round} 轮）` : ''}。`, 'ok')
      for (const [index, planned] of steps.entries()) {
        add(`${index + 1}. ${userFacingText(planned.name, '处理数据', 120)}`, 'info', [
          ...recipeStepFields(progress, planned.id, planned),
          { label: '具体操作', text: planned.description },
          { label: '处理依据', text: planned.explanation },
        ])
      }
      break
    }
    case 'recipe_run_start':
      add(`开始处理${progress.recipeName ? `“${userFacingText(progress.recipeName)}”` : '本次任务'}。`)
      break
    case 'recipe_input': {
      const file = userFacingText(event.filename, '输入文件')
      const sheet = userFacingText(event.sheet)
      add(`已读取“${file}”${sheet ? `的“${sheet}”工作表` : ''}${event.kind === 'file' || typeof event.rows !== 'number' ? '' : `，共 ${event.rows} 行`}。`, 'ok')
      const renames = Object.entries(event.renames ?? {}).map(([from, to]) =>
        `“${userFacingText(from)}”对应“${userFacingText(String(to))}”`)
      if (renames.length) add('已确认字段对应关系。', 'ok', [{ label: '字段对应', text: renames.join('\n') }])
      for (const warning of event.warnings ?? []) add(warning, 'warning')
      break
    }
    case 'recipe_step_start':
      add(`开始“${name}”。`, 'info', [
        ...recipeStepFields(progress, event.step_id),
        { label: '具体操作', text: event.description || step?.description },
      ])
      break
    case 'recipe_step_done': {
      const failed = event.status === 'failed'
      const rowCount = typeof event.rows_out === 'number' && (event.rows_out > 0 || event.columns?.length) ? `，整理得到 ${event.rows_out} 条记录` : ''
      add(`${failed ? '未完成' : event.status === 'warning' ? '已执行（需核对）' : event.status === 'repaired' ? '修正后已完成' : '已完成'}“${name}”${rowCount}。`, statusFor(event.status), [
        ...recipeStepFields(progress, event.step_id),
        { label: '执行内容', text: event.description || step?.description },
        { label: '输入记录', text: Object.entries(event.rows_in ?? {}).map(([table, rows]) => `“${userFacingText(table)}”：${rows} 行`).join('\n') },
        { label: '结果字段', text: Array.isArray(event.columns) ? event.columns.map((column: string) => userFacingText(column)).filter(Boolean).join('、') : '' },
      ])
      for (const check of event.checks ?? []) add(check.message, check.ok ? 'ok' : 'warning')
      for (const check of event.assertions ?? []) add(`${userFacingText(check.description)}${check.description ? '：' : ''}${userFacingText(check.message)}`, statusFor(check.status))
      for (const warning of event.warnings ?? []) add(warning, 'warning')
      break
    }
    case 'recipe_step_error':
      add(`“${name}”遇到问题。`, 'warning', [
        { label: '问题反馈', text: userFacingText(event.error, '尚未收到具体错误说明。', 1200) },
      ])
      break
    case 'recipe_repair':
      if (event.status === 'trying') {
        add(`正在调整“${name}”。`, 'info', [
          ...recipeStepFields(progress, event.step_id).filter((field) => field.label === '处理对象'),
          { label: '问题反馈', text: userFacingText(step?.error, '正在核对上一步失败的原因。', 1200) },
          { label: '当前进度', text: '正在制定修正方案，尚未收到具体修改内容。' },
        ])
      } else if (event.status === 'retry') {
        const fields = repairDescriptionFields(event.explanation)
        add(`修正方案已提交，重新执行“${name}”。`, 'info', [
          ...(!fields.some((field) => field.label === '处理对象') ? recipeStepFields(progress, event.step_id).filter((field) => field.label === '处理对象') : []),
          ...fields,
          { label: '下一步', text: '按修正后的步骤重新执行，核对执行结果与原有校验。' },
        ])
      } else if (event.status === 'gave_up') {
        add(`“${name}”调整后仍未完成。`, 'error', [
          { label: '最后问题', text: userFacingText(event.explanation || step?.error, '尚未收到具体失败说明。', 1200) },
          { label: '执行结果', text: '自动调整已停止，本步骤未确认完成。' },
        ])
      }
      break
    case 'recipe_validation': {
      const report = event.report
      add(`结果校验：${userFacingText(report?.summary, '检查已结束。')}`, statusFor(report?.status))
      for (const check of report?.checks ?? []) {
        add(userFacingText(check.description, '核对处理结果'), statusFor(check.status), [
          { label: '核对结果', text: userFacingText(check.message, check.status === 'pass' ? '通过' : check.status === 'fail' ? '未通过' : '需核对', 1200) },
        ])
      }
      break
    }
    case 'recipe_verify': {
      const report = event.report
      add(`样例比对：${userFacingText(report?.summary, report?.status === 'pass' ? '与样例一致。' : report?.status ? '存在差异，需要核对。' : '尚未收到具体比对结论。')}`, statusFor(report?.status))
      for (const detail of report?.details ?? []) add(detail, statusFor(report?.status))
      for (const sheet of report?.sheets ?? []) {
        const messages = (sheet.messages ?? []).map((message: string) => userFacingText(message, '', 1200)).filter(Boolean)
        if (messages.length) add(`“${userFacingText(sheet.sheet, '结果表')}”比对反馈`, statusFor(sheet.status), [{ label: '具体反馈', text: messages.join('\n') }])
      }
      break
    }
    case 'recipe_refine':
      add(`根据核对结果修订处理方案${event.round ? `，准备第 ${event.round} 轮执行` : ''}。`, 'info', [
        { label: '调整原因', text: userFacingSummary(event.reason, '尚未收到具体的差异说明。', 2400) },
        { label: '下一步', text: '修订后的具体步骤将在生成方案后展示，再执行并重新校验。' },
      ])
      break
    case 'recipe_run_done': {
      const failed = ['failed', 'escalated'].includes(event.status)
      add(failed ? '本次处理未完成。' : event.status === 'warning' ? '本次处理已结束，仍有待核对项。' : '本次处理已结束。', failed ? 'error' : statusFor(event.status), [
        { label: '执行反馈', text: event.summary || progress.runSummary },
      ])
      for (const output of progress.outputs ?? []) add(`已整理“${userFacingText(output.sheet, '结果表')}”，共 ${output.rows} 行。`, 'ok')
      break
    }
    case 'recipe_failed':
      add('本次处理未完成。', 'error', [{ label: '失败原因', text: userFacingText(event.error, '尚未收到具体失败说明。', 1200) }])
      break
  }
  return result
}
