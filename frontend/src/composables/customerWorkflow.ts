/** Display every workflow, moving only the primary workflow to the front. */
export function primaryFirst<T extends { recipe_id: string }>(
  workflows: readonly T[], primary?: string | null,
): T[] {
  const index = workflows.findIndex((workflow) => workflow.recipe_id === primary)
  if (index < 1) return [...workflows]
  return [workflows[index]!, ...workflows.slice(0, index), ...workflows.slice(index + 1)]
}

export function workflowFiles<T extends { file_id: string }>(files: readonly T[]): T[] {
  return [...new Map(files.map((file) => [file.file_id, file])).values()]
}
