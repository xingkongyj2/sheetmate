import type { RecipeToolKind } from '@/api/types'

import { SCRIPT_TITLE_LIMIT, shortenTitle } from './scriptPresentation'

interface RecipeTool {
  kind?: RecipeToolKind
  formula?: string
  formula_kind?: string
}

/** 兼容尚未携带 kind 的历史流程。 */
export function recipeToolKind(step: RecipeTool): RecipeToolKind {
  if (step.kind) return step.kind
  if (step.formula === 'python_script') return 'python'
  if (step.formula === 'custom_sql') return 'sql'
  return 'formula'
}

export function recipeToolLabel(step: RecipeTool): string {
  const kind = recipeToolKind(step)
  if (kind === 'python') return 'Python 脚本'
  if (kind === 'sql') return 'SQL'
  return step.formula_kind === 'customer' ? '客户公式' : '内置公式'
}

/** 步骤标题：Python 脚本步骤的标题最多 15 个字（过长时在标点处收短），其他步骤原样显示。 */
export function recipeStepTitle(step: RecipeTool & { name: string }): string {
  return recipeToolKind(step) === 'python' ? shortenTitle(step.name, SCRIPT_TITLE_LIMIT, step.name) : step.name
}
