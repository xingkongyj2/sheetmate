import type { UiMessage } from './chatModel'

const TEXT_DIAGRAM_LANGUAGES = new Set(['', 'text', 'plain', 'plaintext', 'ascii', 'diagram', 'flowchart'])
const BOX_LINE = /^\s*(?:[┌┏╔╭└┗╚╰├┣╠┬┳╦┴┻╩┼╋╬][─━═]{2,}|\+(?:[-=]{2,}\+)+)/mu
const FLOW_MARKER = /输入|输出|步骤|流程|[↓↑→←▼▲▶◀]|--?>|<--?/u
const INTERNAL_DIGEST = /^\s*[（(]本轮执行[：:][^\r\n]*[）)]\s*\\?\s*$/u

function isFlowDiagram(language: string, body: string, incomplete: boolean): boolean {
  const text = body.trim()
  if (language === 'mermaid') {
    const directive = text.replace(/^(?:%%[^\n]*\n\s*)+/, '')
    return /^(?:graph|flowchart)(?:\s|$)/i.test(directive) ||
      (incomplete && ['graph', 'flowchart'].some((word) => word.startsWith(directive.toLowerCase())))
  }
  return TEXT_DIAGRAM_LANGUAGES.has(language) && BOX_LINE.test(body) && (incomplete || FLOW_MARKER.test(body))
}

/** Native workflow snapshots replace only clearly identifiable text diagrams, never missing workflow data. */
export function recipeViewReply(message: Pick<UiMessage, 'content' | 'artifacts' | 'streaming'>): string {
  const source = message.content
  if (!message.artifacts.some((artifact) => artifact.kind === 'recipe_view')) return source

  const lines = source.split('\n')
  const kept: string[] = []
  let changed = false
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index]!
    const opening = line.match(/^ {0,3}(`{3,}|~{3,})([^\r\n]*)\r?$/)
    if (!opening) {
      if (INTERNAL_DIGEST.test(line)) changed = true
      else kept.push(line)
      continue
    }

    const fence = opening[1]!
    const language = opening[2]!.trim().toLowerCase().split(/\s+/)[0] ?? ''
    const closing = new RegExp(`^ {0,3}${fence[0]}{${fence.length},}[\\t ]*\\r?$`)
    let end = index + 1
    while (end < lines.length && !closing.test(lines[end]!)) end++
    const complete = end < lines.length
    const body = lines.slice(index + 1, end).join('\n')
    // Wait until a possible text diagram is complete before deciding to show it.
    // Otherwise its opening fence flashes on screen before the first box line arrives.
    const pendingDiagram = !complete && message.streaming && (
      TEXT_DIAGRAM_LANGUAGES.has(language) ||
      (index === lines.length - 1 && ['mermaid', ...TEXT_DIAGRAM_LANGUAGES].some((name) => name.startsWith(language)))
    )
    if (pendingDiagram || isFlowDiagram(language, body, !complete && message.streaming)) changed = true
    else kept.push(...lines.slice(index, complete ? end + 1 : end))
    index = complete ? end : lines.length
  }
  return changed ? kept.join('\n').replace(/\n{3,}/g, '\n\n').trim() : source
}
