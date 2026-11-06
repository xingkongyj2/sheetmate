import type { PlanStatus, TimelineItem, UiMessage } from './chatModel'
import { PLAN_STATUS_LABELS, recipeAttempts, recipeFailure } from './messagePresentation'
import { userFacingText } from './timelinePresentation'

type PlanItem = Extract<TimelineItem, { kind: 'plan' }>
export type AssistantAttempt = ReturnType<typeof recipeAttempts>[number]

/** A chronological unit: collapsed actions first, then its public summary / recipe outcome. */
export interface AssistantStage {
  key: string
  number: number
  title: string
  status: PlanStatus
  statusLabel: string
  items: TimelineItem[]
  summary: string
  attempts: AssistantAttempt[]
  toolCount: number
  plan?: PlanItem
}

export type AssistantDisplayBlock =
  | { kind: 'text'; key: string; text: string; closed: boolean }
  | { kind: 'process'; key: string; stage: AssistantStage }

export type AssistantDisplayGroup =
  | Extract<AssistantDisplayBlock, { kind: 'text' }>
  | { kind: 'process'; key: string; stages: AssistantStage[]; running: boolean }

/** Adjacent operations share one disclosure; every text block keeps its original boundary. */
export function groupAssistantBlocks(blocks: AssistantDisplayBlock[]): AssistantDisplayGroup[] {
  const groups: AssistantDisplayGroup[] = []
  for (const block of blocks) {
    if (block.kind === 'text') {
      groups.push(block)
      continue
    }
    // Only actual public progress gets a disclosure; empty / reasoning-only anchors stay hidden.
    const visibleItems = block.stage.items.some((item) =>
      item.kind === 'tool' || item.kind === 'plan' || item.kind === 'agent' ||
      ((item.kind === 'note' || item.kind === 'info') && !!userFacingText(item.text)),
    )
    if (!visibleItems && !block.stage.attempts.length && !block.stage.plan && !userFacingText(block.stage.summary)) continue
    const previous = groups.at(-1)
    if (previous?.kind === 'process') {
      previous.stages.push(block.stage)
      previous.running ||= block.stage.status === 'running'
    } else {
      groups.push({ kind: 'process', key: block.key, stages: [block.stage], running: block.stage.status === 'running' })
    }
  }
  return groups
}

/** Keep every displayed section at the anchor created by its first event. */
export function assistantDisplayBlocks(message: UiMessage): AssistantDisplayBlock[] {
  if (!message.blocks) {
    return [
      ...assistantStages(message).map((stage): AssistantDisplayBlock => ({ kind: 'process', key: stage.key, stage })),
      ...(message.content ? [{ kind: 'text' as const, key: `${message.key}_reply`, text: message.content, closed: !message.streaming }] : []),
    ]
  }
  const items = new Map(message.timeline.map((item) => [item.key, item]))
  const attempts = new Map(recipeAttempts(message).map((attempt) => [attempt.key, attempt]))
  let number = 0
  return message.blocks.map((block): AssistantDisplayBlock => {
    if (block.kind === 'text') return block
    const stage: AssistantStage = {
      key: block.key, number: ++number, title: block.title,
      status: 'done', statusLabel: '',
      items: block.itemKeys.flatMap((key) => items.has(key) ? [items.get(key)!] : []),
      attempts: block.recipeKeys.flatMap((key) => attempts.has(key) ? [attempts.get(key)!] : []),
      summary: '', toolCount: 0,
    }
    stage.toolCount = stage.items.filter((item) => item.kind === 'tool').length
    stage.status = stageStatus(stage)
    // A completed operation must stop animating even while the reply continues streaming.
    if (stage.status === 'done' && !block.closed && message.streaming &&
        !stage.toolCount && !stage.attempts.length && stage.items.some((item) => item.kind === 'info')) {
      stage.status = 'running'
    }
    stage.statusLabel = PLAN_STATUS_LABELS[stage.status]
    return { kind: 'process', key: block.key, stage }
  })
}

const ACTIVE_RECIPE_STATUSES = new Set(['thinking', 'running', 'verifying', 'refining'])
const RECIPE_TOOLS = new Set(['build_recipe', 'revise_recipe', 'run_recipe'])

function itemStep(item: TimelineItem): number | undefined {
  // Thinking / information events from older stored messages do not carry a step.
  return 'step' in item ? item.step : undefined
}

function stageStatus(stage: AssistantStage): PlanStatus {
  if (stage.items.some((item) => (item.kind === 'tool' || item.kind === 'agent') && item.status === 'running') ||
      stage.attempts.some((attempt) => ACTIVE_RECIPE_STATUSES.has(attempt.progress.status))) return 'running'
  if (stage.items.some((item) => (item.kind === 'tool' || item.kind === 'agent') && item.status === 'error') ||
      stage.attempts.some((attempt) => recipeFailure(attempt.progress))) return 'error'
  const plan = stage.items.find((item) => item.kind === 'plan')
  if (plan?.kind === 'plan') return plan.status
  return 'done'
}

function actionSummary(stage: AssistantStage): string {
  if (stage.summary || stage.attempts.length) return stage.summary
  const tools = stage.items.filter((item): item is Extract<TimelineItem, { kind: 'tool' }> => item.kind === 'tool')
  if (!tools.length || tools.some((tool) => tool.status === 'running')) return ''
  const failed = tools.filter((tool) => tool.status === 'error').length
  if (failed) return `本阶段完成 ${tools.length - failed} 项操作，${failed} 项未完成。`
  // The clarification card already contains the question and its options.
  if (tools.every((tool) => tool.tool === 'ask_user')) return ''
  return userFacingText(tools.at(-1)?.summary, `本阶段已完成 ${tools.length} 项操作。`)
}

/**
 * Reconstruct the order from timeline anchors, never from "successful artifacts first".
 * Notes close the preceding processing unit; a recipe outcome closes its own attempt.
 * Plan markers are declarations emitted together, so their position is not execution order.
 * This function only uses public notes / tool summaries for visible stage summaries.
 */
export function assistantStages(message: UiMessage): AssistantStage[] {
  const plans = message.timeline.filter((item): item is PlanItem => item.kind === 'plan')
  const plansByNumber = new Map(plans.map((plan) => [plan.num, plan]))
  const planOrder = new Map(plans.map((plan, index) => [plan.key, index]))
  const attempts = recipeAttempts(message)
  const attemptsByTool = new Map<string, AssistantAttempt[]>()
  const recipeTools = message.timeline.filter((item): item is Extract<TimelineItem, { kind: 'tool' }> =>
    item.kind === 'tool' && RECIPE_TOOLS.has(item.tool))
  const assignedTools = new Set<string>()
  for (const attempt of attempts) {
    // Older events may omit their id even though the corresponding recipe tool
    // is present. Pair those by occurrence before falling back to artifact order.
    const anchor = recipeTools.find((tool) => tool.id === attempt.progress.toolCallId) ??
      recipeTools.find((tool) => !assignedTools.has(tool.id) && !attempts.some((other) => other.progress.toolCallId === tool.id))
    if (!anchor) continue
    assignedTools.add(anchor.id)
    const linked = attemptsByTool.get(anchor.id) ?? []
    linked.push(attempt)
    attemptsByTool.set(anchor.id, linked)
  }

  const stages: AssistantStage[] = []
  const linkedAttempts = new Set<string>()
  let current: AssistantStage | undefined
  let nextKey = 'start'
  let contextStep: number | undefined

  function ensureStage(): AssistantStage {
    if (!current) {
      const plan = contextStep === undefined ? undefined : plansByNumber.get(contextStep)
      current = {
        key: `stage_${nextKey}`, number: 0, title: plan?.description ?? '',
        status: 'running', statusLabel: PLAN_STATUS_LABELS.running,
        items: [], summary: '', attempts: [], toolCount: 0, plan,
      }
    }
    return current
  }

  function closeStage(boundaryKey: string) {
    if (current && (current.items.length || current.summary || current.attempts.length)) {
      current.status = stageStatus(current)
      current.summary = actionSummary(current)
      stages.push(current)
    }
    current = undefined
    nextKey = boundaryKey
  }

  for (const item of message.timeline) {
    if (item.kind === 'plan') continue
    const step = itemStep(item)
    if (step !== undefined && step !== contextStep) {
      if (current) closeStage(`plan_${step}`)
      contextStep = step
    } else if (item.kind === 'info' && item.tone === 'reflect') {
      if (current) closeStage(item.key)
      contextStep = undefined
    }

    const stage = ensureStage()
    if (item.kind === 'note') {
      if (!item.text.trim()) continue
      stage.summary = item.text.trim()
      closeStage(item.key)
      continue
    }
    stage.items.push(item)
    if (item.kind === 'tool') {
      stage.toolCount += 1
      const linked = attemptsByTool.get(item.id)
      if (linked?.length) {
        stage.attempts.push(...linked)
        linked.forEach((attempt) => linkedAttempts.add(attempt.key))
        closeStage(item.key)
      }
    }
  }
  const trailing = current
  closeStage(nextKey)

  // Legacy recipe events may lack a tool_call_id; retain those outcomes in artifact order.
  for (const [index, attempt] of attempts.entries()) {
    if (linkedAttempts.has(attempt.key)) continue
    const stage: AssistantStage = {
      key: `stage_${attempt.key}`, number: 0, title: attempt.label,
      status: ACTIVE_RECIPE_STATUSES.has(attempt.progress.status) ? 'running' : recipeFailure(attempt.progress) ? 'error' : 'done',
      statusLabel: '', items: [], summary: '', attempts: [attempt], toolCount: 0,
    }
    const nextLinked = attempts.slice(index + 1).find((next) => linkedAttempts.has(next.key))
    const nextIndex = nextLinked ? stages.findIndex((entry) => entry.attempts.some((next) => next.key === nextLinked.key)) : -1
    if (nextIndex >= 0) stages.splice(nextIndex, 0, stage)
    else stages.push(stage)
  }

  for (const [index, plan] of plans.entries()) {
    const last = stages.filter((stage) => stage.plan?.key === plan.key).at(-1)
    if (last) {
      const summary = plan.summary?.trim()
      if (summary && summary !== last.summary) last.summary = [last.summary, summary].filter(Boolean).join('\n\n')
      if (plan.status !== 'done' || last.status !== 'error') last.status = plan.status
    } else {
      const stage: AssistantStage = {
        key: `stage_${plan.key}`, number: 0, title: plan.description,
        status: plan.status, statusLabel: '', items: [], summary: plan.summary?.trim() ?? '',
        attempts: [], toolCount: 0, plan,
      }
      // A text-only step leaves no tool / note anchor in stored messages. Insert
      // it among its planned neighbours, before the final reflection stage.
      const nextIndex = stages.findIndex((entry) => entry.plan && (planOrder.get(entry.plan.key) ?? -1) > index)
      if (nextIndex >= 0) stages.splice(nextIndex, 0, stage)
      else {
        const previousIndex = stages.reduce((previous, entry, stageIndex) =>
          entry.plan && (planOrder.get(entry.plan.key) ?? plans.length) < index ? stageIndex : previous, -1)
        const reflectionIndex = stages.findIndex((entry) => entry.items.some((item) => item.kind === 'info' && item.tone === 'reflect'))
        const insertAt = previousIndex >= 0 ? previousIndex + 1 : reflectionIndex >= 0 ? reflectionIndex : stages.length
        stages.splice(insertAt, 0, stage)
      }
    }
  }

  // A short answer does not gain a compulsory "thinking" section just because the
  // provider emitted a reasoning event. Keep the initial indicator while streaming.
  const meaningful = stages.some((stage) => stage.toolCount || stage.attempts.length || stage.plan || stage.summary ||
    stage.items.some((item) => item.kind === 'agent' || item.kind === 'info' || item.kind === 'script'))
  if (!meaningful && (!message.streaming || message.content.trim())) return []

  if (message.streaming && !message.content.trim() && !stages.some((stage) => stage.status === 'running')) {
    const candidate = trailing && stages.includes(trailing) ? trailing : undefined
    if (candidate && !candidate.summary && !candidate.attempts.length) candidate.status = 'running'
    else stages.push({
      key: `stage_${nextKey}`, number: 0, title: '', status: 'running', statusLabel: '',
      items: [], summary: '', attempts: [], toolCount: 0,
    })
  }

  return stages.map((stage, index) => ({
    ...stage,
    number: index + 1,
    title: stage.title || stage.attempts[0]?.label || `第 ${index + 1} 阶段`,
    statusLabel: stage.status === 'pending' && !message.streaming ? '未执行' : PLAN_STATUS_LABELS[stage.status],
  }))
}
