export type RecipeStatus = 'active' | 'deprecated'

/** Older chat snapshots may still contain the retired draft status. */
export function recipeStatus(status: unknown): RecipeStatus {
  return status === 'deprecated' ? 'deprecated' : 'active'
}

export function recipeStatusLabel(status: unknown): string {
  return recipeStatus(status) === 'deprecated' ? '已停用' : '已启用'
}
