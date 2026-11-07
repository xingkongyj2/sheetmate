import type { AssistantDisplayGroup, AssistantStage } from './assistantTimeline'
import type { PlanStatus, RecipeProgress, TimelineItem } from './chatModel'
import { PLAN_STATUS_LABELS, recipeFailure } from './messagePresentation'
import { toolPresentation, userFacingText } from './timelinePresentation'

type ProcessGroup = Extract<AssistantDisplayGroup, { kind: 'process' }>
type ToolItem = Extract<TimelineItem, { kind: 'tool' }>

export interface ProcessHeader {
  title: string
  status: PlanStatus
}

export interface ProcessCategory extends ProcessGroup {
  category: string
  label: string
}

const ACTIVE_RECIPE_STATUSES = new Set(['thinking', 'running', 'verifying', 'refining'])
const RECIPE_TOOLS = new Set(['build_recipe', 'revise_recipe', 'run_recipe'])
const GENERIC_TITLES = new Set(['处理过程', '理解需求'])
const TOOL_LABELS: Record<string, string> = {
  table_info: '查看结构', preview_table: '查看数据', column_stats: '描述统计', value_counts: '取值分布',
  build_recipe: '生成处理流程', revise_recipe: '修订处理流程', run_recipe: '执行已有流程',
  list_files: '查看文件', show_files: '展示文件', load_file: '加载文件', run_sql: '查询数据',
  run_python_script: '处理表格', reconsider_approach: '调整处理方法', export_excel: '导出 Excel',
  create_workbook: '新建 Excel', get_recipe: '查看流程', list_recipes: '查看已保存流程',
  save_as_recipe: '保存流程', delete_recipe: '删除已保存流程', delete_recipes: '批量删除流程',
  ask_user: '确认处理要求',
}

function readable(value: string | undefined, maxLength = 140): string {
  return userFacingText(value, '', maxLength).replace(/\*\*|__|^#+\s*/g, '').replace(/[。；;\s]+$/, '')
}

function categoryForStage(stage: AssistantStage): { key: string; label: string } {
  const tool = stage.items.find((item): item is ToolItem => item.kind === 'tool')
  if (tool) return { key: `tool:${tool.tool}`, label: TOOL_LABELS[tool.tool] || readable(toolPresentation(tool).label).split(/\s*[·→]\s*/)[0] || '处理数据' }
  const progress = stage.attempts[0]?.progress
  if (progress) {
    const tool = progress.mode === 'learn' ? 'build_recipe' : progress.mode === 'revise' ? 'revise_recipe' : 'run_recipe'
    return { key: `tool:${tool}`, label: TOOL_LABELS[tool]! }
  }
  const plan = stage.items.find((item) => item.kind === 'plan') ?? stage.plan
  if (plan?.kind === 'plan') return { key: `plan:${plan.description}`, label: readable(plan.description) || '执行计划步骤' }
  if (stage.items.some((item) => item.kind === 'agent')) return { key: 'agent', label: '分析表格' }
  const info = stage.items.find((item) => item.kind === 'info')
  if (info?.kind === 'info') {
    const labels = { memory: '查看处理记录', mapping: '核对字段对应', compact: '整理处理记录', reflect: '核对处理结果', aggregate: '汇总处理结果' }
    return { key: `info:${info.tone ?? 'progress'}`, label: info.tone ? labels[info.tone] : '处理进度' }
  }
  return { key: `detail:${stage.title}`, label: GENERIC_TITLES.has(stage.title) ? '处理说明' : readable(stage.title) || '处理说明' }
}

function publicStatus(stage: AssistantStage): PlanStatus {
  if (stage.items.some((item) => (item.kind === 'tool' || item.kind === 'agent' || item.kind === 'plan') && item.status === 'running') ||
      stage.attempts.some(({ progress }) => ACTIVE_RECIPE_STATUSES.has(progress.status))) return 'running'
  if (stage.items.some((item) => (item.kind === 'tool' || item.kind === 'agent') && item.status === 'error') ||
      stage.attempts.some(({ progress }) => recipeFailure(progress) || progress.validation?.status === 'fail' ||
        progress.verify?.status === 'fail' && !progress.verify.informational)) return 'error'
  if (stage.attempts.some(({ progress }) => progress.runStatus === 'warning' || progress.validation?.status === 'warn' ||
      progress.verify?.status === 'warn' && !progress.verify.informational)) return 'warning'
  const plan = stage.items.find((item) => item.kind === 'plan') ?? stage.plan
  return plan?.kind === 'plan' ? plan.status : 'done'
}

/** Older records can contain several tools in one stage. Keep each attempt with its own call. */
function operationStages(stage: AssistantStage, seenPlans: Set<string>): AssistantStage[] {
  const items = stage.items.filter((item) => {
    if (item.kind === 'thinking' || item.kind === 'script') return false
    if (item.kind !== 'plan') return true
    if (seenPlans.has(item.key)) return false
    seenPlans.add(item.key)
    return true
  })
  const assigned = new Set<string>()
  const units: AssistantStage[] = []
  const makeUnit = (key: string, unitItems: TimelineItem[], attempts: AssistantStage['attempts'], plan?: AssistantStage['plan']) => {
    const unit: AssistantStage = { ...stage, key, items: unitItems, attempts, plan, summary: '', toolCount: unitItems.filter((item) => item.kind === 'tool').length }
    unit.status = publicStatus(unit)
    // Information-only live anchors carry their running state on the stage itself.
    if (!unit.toolCount && !attempts.length && unitItems.some((item) => item.kind === 'info') && stage.status === 'running' && !stage.toolCount) unit.status = 'running'
    unit.statusLabel = PLAN_STATUS_LABELS[unit.status]
    units.push(unit)
  }
  if (stage.plan && !seenPlans.has(stage.plan.key)) {
    seenPlans.add(stage.plan.key)
    makeUnit(`${stage.key}_${stage.plan.key}`, [], [], stage.plan)
  }
  for (const item of items) {
    const attempts = item.kind === 'tool' ? stage.attempts.filter((attempt) => attempt.progress.toolCallId === item.id && !assigned.has(attempt.key)) : []
    if (item.kind === 'tool' && RECIPE_TOOLS.has(item.tool) && !attempts.length) {
      const unmatched = stage.attempts.find((attempt) => !attempt.progress.toolCallId && !assigned.has(attempt.key))
      if (unmatched) attempts.push(unmatched)
    }
    attempts.forEach((attempt) => assigned.add(attempt.key))
    makeUnit(`${stage.key}_${item.key}`, [item], attempts)
  }
  for (const attempt of stage.attempts) {
    if (!assigned.has(attempt.key)) makeUnit(`${stage.key}_${attempt.key}`, [], [attempt])
  }
  if (!units.length) return userFacingText(stage.summary) ? [{ ...stage, items: [], plan: undefined }] : []
  units[units.length - 1]!.summary = stage.summary
  return units
}

/** Run-length grouping: A, A, B, B, A stays A×2, B×2, A, within this text boundary. */
export function processGroupCategories(group: ProcessGroup): ProcessCategory[] {
  const categories: ProcessCategory[] = []
  const seenPlans = new Set<string>()
  for (const stage of group.stages.flatMap((entry) => operationStages(entry, seenPlans))) {
    const category = categoryForStage(stage)
    const previous = categories.at(-1)
    if (previous?.category === category.key) {
      previous.stages.push(stage)
      previous.running ||= stage.status === 'running'
    } else {
      categories.push({ kind: 'process', key: stage.key, category: category.key, label: category.label, stages: [stage], running: stage.status === 'running' })
    }
  }
  return categories
}

function runningLabel(value: string): string {
  return /^(?:正在|继续|重新)/.test(value) ? value : `正在${value}`
}

function recipeRunningTitle(progress: RecipeProgress, fallback: string): string {
  if (progress.status === 'refining') return `正在调整${readable(progress.recipeName) || '处理步骤'}`
  if (progress.status === 'verifying') return `正在核对${readable(progress.recipeName) || '处理结果'}`
  const steps = [...progress.steps].reverse()
  const repairing = steps.find((step) => step.status === 'repairing')
  if (repairing) return `正在调整：${readable(repairing.name)}`
  const step = steps.find((item) => item.status === 'running')
  if (step) return runningLabel(readable(step.name) || readable(step.description) || fallback)
  return runningLabel(readable(progress.recipeName) || fallback)
}

function runningTitle(stage: AssistantStage): string {
  const items = [...stage.items].reverse()
  const attempt = [...stage.attempts].reverse().find((item) => ACTIVE_RECIPE_STATUSES.has(item.progress.status))
  const tool = items.find((item): item is ToolItem => item.kind === 'tool' && item.status === 'running')
  const label = tool ? readable(toolPresentation(tool).label) : ''
  if (attempt) return recipeRunningTitle(attempt.progress, label || '执行处理步骤')
  // Use the same complete action title as the detail row, including its current target.
  if (tool) return label || categoryForStage(stage).label
  const agent = items.find((item) => item.kind === 'agent' && item.status === 'running')
  if (agent?.kind === 'agent') return `正在分析「${readable(agent.table)}」`
  const plan = items.find((item) => item.kind === 'plan' && item.status === 'running') ?? stage.plan
  if (plan?.kind === 'plan') return runningLabel(readable(plan.description))
  const info = items.find((item) => item.kind === 'info' && readable(item.text))
  if (info?.kind === 'info') return runningLabel(readable(info.text))
  return runningLabel(categoryForStage(stage).label)
}

/** Titles use declared action names, never generated outcome summaries or private reasoning. */
export function processGroupHeader(group: ProcessGroup): ProcessHeader {
  const running = [...group.stages].reverse().find((stage) => stage.status === 'running')
  if (running) return { title: runningTitle(running), status: 'running' }
  const failed = group.stages.some((stage) => stage.status === 'error' || stage.attempts.some(({ progress }) =>
    recipeFailure(progress) || progress.validation?.status === 'fail' || progress.verify?.status === 'fail' && !progress.verify.informational))
  const warning = group.stages.some((stage) => stage.status === 'warning' || stage.attempts.some(({ progress }) =>
    progress.runStatus === 'warning' || progress.validation?.status === 'warn' ||
    (progress.verify?.status === 'warn' && !progress.verify.informational)))
  const status = failed ? 'error' : warning ? 'warning' : group.stages.some((stage) => stage.status === 'stopped') ? 'stopped'
    : group.stages.some((stage) => stage.status === 'waiting') ? 'waiting'
      : group.stages.every((stage) => stage.status === 'pending') ? 'pending' : 'done'
  const category = categoryForStage(group.stages[0]!)
  return { title: category.label, status }
}
