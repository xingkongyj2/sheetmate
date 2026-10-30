import type { PlanStatus, TableArtifact, TimelineItem, UiMessage } from './chatModel'

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

export function lastResultTable(message: UiMessage): TableArtifact | undefined {
  return message.artifacts.flatMap((item) => item.kind === 'table' && item.table.role !== 'query' ? [item.table] : []).at(-1)
}

function inline(value: unknown): string {
  const text = typeof value === 'object' && value !== null ? JSON.stringify(value) : String(value ?? '')
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/([\\`*_{}[\]()#+.!|~-])/g, '\\$1').replace(/\r?\n/g, '<br>')
}

export function messageMarkdown(message: UiMessage): string {
  const blocks: string[] = []
  const steps = planSteps(message)
  if (steps.length) {
    blocks.push('## 执行计划', ...steps.map((step) =>
      `### ${step.num}. ${inline(step.description)}\n\n**状态：${step.statusLabel}**${step.summary ? `\n\n${step.summary}` : ''}`))
  }
  if (message.content.trim()) blocks.push(message.content.trim())
  const table = lastResultTable(message)
  if (table?.columns.length) {
    const row = (values: unknown[]) => `| ${values.map(inline).join(' | ')} |`
    blocks.push(`### ${inline(table.label || table.name || '结果表')}\n\n` +
      [row(table.columns), `| ${table.columns.map(() => '---').join(' | ')} |`,
        ...table.preview.map((values) => row(table.columns.map((_, index) => values[index])))].join('\n') +
      (table.rows > table.preview.length ? `\n\n仅复制当前预览 ${table.preview.length} 行，共 ${table.rows} 行。` : ''))
  }
  const files = message.artifacts.flatMap((item) => item.kind === 'file' ? [item.file] : item.kind === 'recipe' && item.progress.file ? [item.progress.file] : [])
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
