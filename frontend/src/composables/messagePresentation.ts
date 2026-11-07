import type { PlanStatus, RecipeProgress, RecipeViewArtifact, TimelineItem, UiMessage } from './chatModel'
import { deliveredFiles, standaloneFiles, type FilePresentationContext } from './filePresentation'
import { recipeViewReply } from './recipeViewPresentation'
import { recipeDeliveries } from './recipeDeliveries'
import { progressFlow } from './recipeFlow'
import { toolPresentation, userFacingSummary, userFacingText } from './timelinePresentation'

export const PLAN_STATUS_LABELS: Record<PlanStatus, string> = {
  pending: '待执行', running: '执行中', done: '已完成', warning: '需核对', waiting: '待确认', error: '失败', stopped: '已停止',
}

export function planSteps(message: UiMessage) {
  return message.timeline.flatMap((item) => {
    if (item.kind !== 'plan') return []
    const notes = message.timeline.filter((entry): entry is Extract<TimelineItem, { kind: 'note' }> =>
      entry.kind === 'note' && entry.step === item.num)
    const summary = item.summary ?? notes.at(-1)?.text ?? ''
    const status = item.status === 'pending' && !message.streaming ? '未执行' : PLAN_STATUS_LABELS[item.status]
    const calls = message.timeline.filter((entry) => entry.kind === 'tool' && entry.step === item.num).length
    return [{ ...item, summary, statusLabel: status, calls }]
  }).sort((left, right) => left.num - right.num)
}

export function recipeFailure(progress: RecipeProgress): { summary: string; nextStep: string; detail: string } | null {
  if (['thinking', 'running', 'verifying', 'refining'].includes(progress.status)) return null
  if (progress.status !== 'failed' && !progress.failed && !progress.escalation && !['failed', 'escalated'].includes(progress.runStatus ?? '')) return null
  const failedStep = progress.steps.find((step) => step.status === 'failed')
  const detail = [...new Set([progress.failed, progress.escalation, progress.runSummary, failedStep?.error]
    .filter((value): value is string => !!value?.trim()))].join('\n\n')
  if (/TemplatePreservationError|原合并单元格|无法.*保留.*格式|行列重排.*(?:公式|合并)/i.test(detail)) {
    return {
      summary: '这次修改在写入结果时遇到格式或合并单元格冲突，未能完成。',
      nextStep: '可从原始文件重新复制一份，调整冲突区域的处理方式后再试。',
      detail,
    }
  }
  if (/(?:列|字段).{0,18}(?:不存在|未找到|找不到)|(?:找不到|未找到).{0,18}(?:列|字段)|(?:column|field).{0,80}(?:not found|does not exist)/i.test(detail)) {
    return { summary: '处理需要的字段没有找到，这一步未能完成。', nextStep: '可先核对表头和字段对应关系，再重试失败的步骤。', detail }
  }
  if (/PermissionError|Permission denied|文件.*(?:占用|锁定)|没有.*写入权限/i.test(detail)) {
    return { summary: '文件读写被拒绝，本次修改未能完成。', nextStep: '可检查工作副本是否被其他程序占用，以及输出位置是否可写，再重试。', detail }
  }
  if (/TimeoutError|ConnectionError|timed out|连接中断|连接失败|超时/i.test(detail)) {
    return { summary: '处理过程中发生超时或连接错误，未能确认本次修改完成。', nextStep: '可先检查服务连接和已有结果，再重试未完成的步骤。', detail }
  }
  if (progress.validation?.status === 'fail' || (progress.verify?.status === 'fail' && !progress.verify.informational)) {
    return { summary: '处理结果未通过校验，本次修改还不能确认完成。', nextStep: '可先核对校验中不一致的内容，再调整对应步骤。', detail }
  }
  const reason = (detail.split('\n').find((line) => line.trim() && !/^\s*(?:Traceback|File ")/.test(line)) ?? '')
    .replace(/^[\w.]+(?:Error|Exception):\s*/, '').replace(/\s+/g, ' ').trim()
  const brief = userFacingText(reason, '', 150)
  return {
    summary: `${failedStep?.name ? `“${failedStep.name}”这一步` : '本次处理'}未完成${brief ? `：${brief}` : '，尚未收到具体原因。'}`,
    nextStep: '可先查看失败步骤和错误详情，调整对应处理方式后重试。',
    detail,
  }
}

export function recipeAttempts(message: UiMessage): { key: string; progress: RecipeProgress; notes: string[]; label: string }[] {
  const artifacts = message.artifacts.flatMap((item) => item.kind === 'recipe' ? [item] : [])
  const modes = { learn: '规划处理步骤', revise: '调整处理步骤', replay: '执行已有流程' }
  let cursor = 0
  return artifacts.map((artifact, index) => {
    const position = artifact.progress.toolCallId
      ? message.timeline.findIndex((item, itemIndex) => itemIndex >= cursor && item.kind === 'tool' && item.id === artifact.progress.toolCallId)
      : -1
    const notes = position < 0 ? [] : message.timeline.slice(cursor, position)
      .flatMap((item) => item.kind === 'note' && item.text.trim() ? [item.text.trim()] : [])
    if (position >= 0) cursor = position + 1
    const previous = artifacts[index - 1]?.progress
    const sameRecipe = previous && (
      (previous.saved?.recipe_id && previous.saved.recipe_id === artifact.progress.saved?.recipe_id) ||
      (previous.recipeName && previous.recipeName === artifact.progress.recipeName)
    )
    const retry = sameRecipe && recipeFailure(previous)
    return { key: artifact.key, progress: artifact.progress, notes, label: `第 ${index + 1} 次尝试 · ${retry ? '再次尝试' : modes[artifact.progress.mode]}` }
  })
}

function recipeResultSummary(progress: RecipeProgress): string {
  const failure = recipeFailure(progress)
  if (failure) return failure.summary
  if (progress.status !== 'done') return '尚未记录到这次处理的完成结果。'
  if (progress.validation?.status === 'fail' || (progress.verify?.status === 'fail' && !progress.verify.informational)) return '处理已结束，但结果未通过校验，需要核对。'
  if (progress.runStatus === 'warning' || progress.validation?.status === 'warn' || (progress.verify?.status === 'warn' && !progress.verify.informational)) return '流程已执行完成，仍有待核对项。'
  return userFacingSummary(progress.runSummary, '该次流程已执行完成。')
}

export function recipeOutcomeSummary(message: UiMessage): string {
  if (message.streaming || message.content.trim()) return ''
  const attempts = recipeAttempts(message)
  const last = attempts.at(-1)
  if (!last) return ''
  const failures = attempts.filter((attempt) => recipeFailure(attempt.progress))
  const completed = attempts.filter((attempt) => attempt.progress.status === 'done' && !recipeFailure(attempt.progress))
  const unfinished = attempts.length - failures.length - completed.length
  const hasFiles = message.artifacts.some((item) => item.kind === 'file' ? !!item.file.download_url : item.kind === 'recipe' && !!item.progress.file?.download_url)
  const delivery = hasFiles ? '本轮已有可下载文件，请结合对应尝试的状态核对后使用。' : '本轮未提供可下载结果。'
  if (message.error) return `本轮处理已中断，不能确认全部修改完成。\n\n${delivery}可查看执行详情，确认已完成的部分后再继续。`
  if (unfinished) return `本轮进行了 ${attempts.length} 次尝试，仍有 ${unfinished} 次没有记录到完成结果，暂不能确认全部修改完成。\n\n${delivery}可查看执行详情后继续处理。`
  const failure = recipeFailure(last.progress)
  const count = `本轮进行了 ${attempts.length} 次尝试，其中 ${completed.length} 次完成、${failures.length} 次未完成。`
  if (failure) {
    const summary = completed.length ? `${count}最后一次修改仍未完成。` : `本轮尝试了 ${attempts.length} 次，暂未完成修改。`
    return `${summary}\n\n${failure.summary}${failure.nextStep}\n\n${delivery}`
  }
  const summary = failures.length ? `${count}最后一次流程已执行结束。` : `本轮的 ${attempts.length} 次流程已执行结束。`
  return `${summary}\n\n${recipeResultSummary(last.progress)}\n\n${delivery}`
}

function inline(value: unknown): string {
  const text = typeof value === 'object' && value !== null ? JSON.stringify(value) : String(value ?? '')
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/([\\`*_{}[\]()#+.!|~-])/g, '\\$1').replace(/\r?\n/g, '<br>')
}

function recipeViewMarkdown(view: RecipeViewArtifact): string {
  const blocks = [`### ${inline(userFacingText(view.name, '处理流程'))} · v${view.version}`]
  if (view.deleted) blocks.push('流程已删除，以下为查看时的记录。')
  const description = userFacingSummary(view.description)
  if (description) blocks.push(description)
  if (view.flow.inputs.length) blocks.push(`输入：${view.flow.inputs.map((input) => inline(userFacingText(input.alias, '输入表'))).join('、')}`)
  if (view.flow.steps.length) blocks.push(view.flow.steps.map((step, index) => {
    const name = inline(userFacingText(step.name, '处理数据'))
    const detail = userFacingText(step.description)
    return `${index + 1}. ${name}${detail ? `：${inline(detail)}` : ''}`
  }).join('\n'))
  if (view.flow.outputs.length) blocks.push(`输出：${view.flow.outputs.map((output) => inline(userFacingText(output.name, '结果表'))).join('、')}`)
  return blocks.join('\n\n')
}

export function messageMarkdown(message: UiMessage, stages?: { title: string; summary: string }[], context: FilePresentationContext = {}): string {
  const blocks: string[] = []
  const steps = planSteps(message)
  const ordered = !!message.blocks?.length
  if (ordered) {
    let stageIndex = 0
    for (const block of message.blocks!) {
      if (block.kind === 'text') {
        if (block.text.trim()) blocks.push(block.text)
        continue
      }
      const stage = stages?.[stageIndex++]
      const items = message.timeline.filter((item) => block.itemKeys.includes(item.key))
      const details = items.flatMap((item) => {
        if (item.kind === 'tool') {
          const action = toolPresentation(item)
          return [`- ${[action.label, ...action.details, action.summary].filter(Boolean).join('；')}`]
        }
        if (item.kind === 'info') return [`- ${userFacingText(item.text)}`]
        return []
      })
      if (details.length || stage?.summary) blocks.push(`### ${inline(block.title)}\n\n${[...details, stage?.summary].filter(Boolean).join('\n\n')}`)
    }
  } else if (stages?.length) {
    blocks.push('## 处理经过', ...stages.map((stage) =>
      `### ${inline(stage.title)}${stage.summary ? `\n\n${stage.summary}` : ''}`))
  } else if (!stages && steps.length) {
    blocks.push('## 执行计划', ...steps.map((step) =>
      `### ${step.num}. ${inline(step.description)}\n\n**状态：${step.statusLabel}**${step.summary ? `\n\n${step.summary}` : ''}`))
  }
  const answer = ordered && message.blocks!.some((block) => block.kind === 'text' && block.text.trim())
    ? '' : recipeViewReply(message).trim() || recipeOutcomeSummary(message)
  const attempts = recipeAttempts(message)
  const attemptsByKey = new Map(attempts.map((attempt) => [attempt.key, attempt]))
  const workflows = recipeDeliveries(message)
  const workflowViews = new Set(workflows.views.map((artifact) => artifact.key))
  const workflowDeliveries = new Map(workflows.deliveries.map((artifact) => [artifact.key, artifact.progress]))
  const recipeBlocks = message.artifacts.flatMap((artifact) => {
    if (artifact.kind === 'recipe_view') return workflowViews.has(artifact.key) ? [recipeViewMarkdown(artifact.view)] : []
    const delivery = workflowDeliveries.get(artifact.key)
    if (delivery) return [recipeViewMarkdown({
      recipe_id: delivery.saved?.recipe_id || delivery.recipeId || '',
      name: delivery.recipeName || delivery.saved?.name || '处理流程', description: delivery.recipe?.description || '',
      version: delivery.saved?.version || delivery.recipeVersion || 1,
      deleted: delivery.deleted, flow: progressFlow(delivery),
    })]
    if (artifact.kind === 'recipe' && (artifact.progress.file || artifact.progress.saved)) return []
    const attempt = !stages && artifact.kind === 'recipe' ? attemptsByKey.get(artifact.key) : undefined
    if (attempt) {
      const failure = recipeFailure(attempt.progress)
      const name = attempt.progress.recipeName || attempt.progress.saved?.name
      return [[`### ${inline(attempt.label)}${name ? `：${inline(name)}` : ''}`, ...attempt.notes,
        recipeResultSummary(attempt.progress), ...(failure ? [`下一步：${failure.nextStep}`] : [])].join('\n\n')]
    }
    return []
  })
  if (recipeBlocks.length) blocks.push(...(!stages && attempts.length ? ['## 处理经过', ...recipeBlocks] : recipeBlocks))
  if (answer) blocks.push(answer)
  const files = [...deliveredFiles(message), ...standaloneFiles(message, context)]
  const uniqueFiles = [...new Map(files.map((file) => [file.file_id, file])).values()]
  if (uniqueFiles.length) {
    blocks.push('## 交付文件\n\n' + uniqueFiles.map((file) =>
      `- [${inline(file.filename)}](${file.download_url.replace(/[\s()]/g, (char) => encodeURIComponent(char))})`).join('\n'))
  }
  for (const item of message.artifacts) {
    if (item.kind === 'clarify') blocks.push(`### 待确认\n\n${item.question}\n\n${item.options.map((option) => `- ${inline(option)}`).join('\n')}`)
  }
  if (message.error) blocks.push(`> ${inline(message.error)}`)
  return blocks.join('\n\n')
}
