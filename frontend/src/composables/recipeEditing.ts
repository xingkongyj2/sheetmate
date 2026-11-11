import type { ConversationDetail, RecipeEditRequest } from '../api/types.ts'
import type { FlowSelection } from './recipeFlowInteraction.ts'

export interface RecipeEditDraft {
  title: string
  request: RecipeEditRequest
}

/** An unsaved snapshot can only be revised in the dialogue that produced it. */
export function conversationMatchesRecipeEdit(conversation: Pick<ConversationDetail, 'recipe_id' | 'messages'>, edit: RecipeEditRequest): boolean {
  if (conversation.recipe_id && conversation.recipe_id !== edit.recipe_id) return false
  return conversation.messages.some((message) => message.events.some((event) => {
    if (!['recipe_generated', 'recipe_run_start', 'recipe_completed', 'recipe_saved', 'recipe_view'].includes(event.type)) return false
    return [event.recipe, event.selected_run?.recipe, { id: event.recipe_id, version: event.version }]
      .some((snapshot) => snapshot?.id === edit.recipe_id && snapshot.version === edit.version)
  }))
}

export function recipeEditTarget(selection?: FlowSelection | null): RecipeEditRequest['target'] {
  if (!selection) return { kind: 'workflow' }
  if (selection.kind === 'step') return { kind: 'step', step_id: selection.id }
  const file = selection.file
  return file.direction === 'input'
    ? { kind: 'input', name: file.name, aliases: [...file.aliases] }
    : { kind: 'output', name: file.name, ...(file.source ? { source: file.source } : {}),
        ...(file.sources?.length ? { sources: [...file.sources] } : {}) }
}

/** Only restore a real persisted target; file names alone cannot select a version. */
export function storedRecipeEdit(value: unknown): RecipeEditRequest | null {
  if (!value || typeof value !== 'object') return null
  const item = value as RecipeEditRequest
  if (typeof item.recipe_id !== 'string' || !item.recipe_id || !Number.isInteger(item.version) || item.version < 1 ||
    !item.target || !['workflow', 'input', 'output', 'step'].includes(item.target.kind) ||
    !['modify', 'add', 'remove', 'replace'].includes(item.operation)) return null
  if (item.target.kind === 'step' && (!Number.isInteger(item.target.step_id) || item.target.step_id! < 1)) return null
  if (item.target.name !== undefined && typeof item.target.name !== 'string') return null
  if (item.target.source !== undefined && typeof item.target.source !== 'string') return null
  for (const key of ['aliases', 'sources'] as const) {
    if (item.target[key] !== undefined && (!Array.isArray(item.target[key]) || !item.target[key]!.every((name) => typeof name === 'string'))) return null
  }
  return item
}

export function recipeEditLabel(edit: RecipeEditRequest): string {
  const action = { modify: '修改', add: '新增', remove: '移除', replace: '替换' }[edit.operation]
  const target = edit.target
  if (target.kind === 'workflow') return '修改流程'
  if (target.kind === 'step') return `修改第 ${target.step_id} 步`
  return `${action}${target.kind === 'input' ? '输入' : '输出'}${target.name ? ` · ${target.name}` : ''}`
}

export function recipeEditPlaceholder(edit: RecipeEditRequest): string {
  if (edit.target.kind === 'step') return '描述这一步要怎么改，其他依赖步骤会重新适配…'
  if (edit.target.kind === 'input') return edit.operation === 'remove'
    ? '说明移除后如何调整处理规则…' : '描述输入变化，可添加新文件或引用已有文件…'
  if (edit.target.kind === 'output') return '描述输出内容、文件、工作表或格式要怎么改…'
  return '描述要修改的输入、输出或处理步骤…'
}
