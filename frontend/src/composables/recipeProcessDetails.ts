import type { AgentEvent } from '@/api/types'
import type { RecipeProgress } from './chatModel'
import { userFacingSummary, userFacingText } from './timelinePresentation'

export interface RecipeProcessDetail {
  text: string
  status: 'info' | 'ok' | 'warning' | 'error'
}

/** Describe actual execution events without exposing the recipe's code or internal identifiers. */
export function recipeProcessDetails(progress: RecipeProgress, event: AgentEvent): RecipeProcessDetail[] {
  const result: RecipeProcessDetail[] = []
  const add = (value: string | undefined, status: RecipeProcessDetail['status'] = 'info') => {
    const text = userFacingSummary(value, '', 600)
    if (text) result.push({ text, status })
  }
  const step = progress.steps.find((item) => item.step_id === event.step_id)
  const name = userFacingText(event.name || step?.name, '处理数据', 100)
  switch (event.type) {
    case 'recipe_run_start':
      add(`开始处理${progress.recipeName ? `“${userFacingText(progress.recipeName)}”` : '本次任务'}。`)
      break
    case 'recipe_input': {
      const file = userFacingText(event.filename, '输入文件')
      const sheet = userFacingText(event.sheet)
      add(`已读取“${file}”${sheet ? `的“${sheet}”工作表` : ''}${event.kind === 'file' ? '' : `，共 ${event.rows} 行`}。`, 'ok')
      const renames = Object.entries(event.renames ?? {}).map(([from, to]) =>
        `“${userFacingText(from)}”对应“${userFacingText(String(to))}”`)
      if (renames.length) add(`已确认字段对应关系：${renames.join('、')}。`, 'ok')
      for (const warning of event.warnings ?? []) add(warning, 'warning')
      break
    }
    case 'recipe_step_start':
      add(`开始${name}。${userFacingText(step?.description)}`)
      break
    case 'recipe_step_done':
      add(`已完成“${name}”${typeof event.rows_out === 'number' && (event.rows_out > 0 || event.columns?.length) ? `，整理得到 ${event.rows_out} 条记录` : ''}。`, event.status === 'warning' ? 'warning' : 'ok')
      for (const check of event.checks ?? []) add(check.message, check.ok ? 'ok' : 'warning')
      for (const warning of event.warnings ?? []) add(warning, 'warning')
      break
    case 'recipe_step_error':
      add(`“${name}”遇到问题：${userFacingText(event.error, '需要调整处理方式。', 300)}`, 'warning')
      break
    case 'recipe_repair':
      if (event.status === 'trying') add(`正在调整“${name}”的处理方式。`)
      else if (event.status === 'retry') add(`重新尝试“${name}”。`)
      else if (event.status === 'gave_up') add(`“${name}”暂未完成，需要进一步处理。`, 'error')
      break
    case 'recipe_validation': {
      const report = event.report
      const status = report?.status === 'fail' ? 'error' : report?.status === 'warn' ? 'warning' : 'ok'
      add(`结果校验：${userFacingText(report?.summary, '检查已结束。')}`, status)
      for (const check of report?.checks ?? []) {
        add(`${userFacingText(check.description, '核对处理结果')}：${userFacingText(check.message, check.status === 'pass' ? '通过' : check.status === 'fail' ? '未通过' : '需核对')}`,
          check.status === 'fail' ? 'error' : check.status === 'pass' ? 'ok' : 'warning')
      }
      break
    }
    case 'recipe_verify':
      add(`样例比对：${userFacingText(event.report?.summary, event.report?.status === 'pass' ? '与样例一致。' : '存在差异，需要核对。')}`,
        event.report?.status === 'pass' ? 'ok' : 'warning')
      break
    case 'recipe_refine':
      add(`继续调整处理方式。${userFacingText(event.reason, '针对核对出的差异重新处理。')}`)
      break
    case 'recipe_run_done': {
      const failed = ['failed', 'escalated'].includes(event.status)
      add(failed ? '本次处理未完成，请结合上面的检查记录核对原因。' : '本次处理已结束。', failed ? 'error' : event.status === 'warning' ? 'warning' : 'ok')
      for (const output of progress.outputs ?? []) add(`已整理“${userFacingText(output.sheet, '结果表')}”，共 ${output.rows} 行。`, 'ok')
      break
    }
    case 'recipe_failed':
      add(event.error, 'error')
      break
  }
  return result
}
