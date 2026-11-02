import type { RecipeFlowData } from '../api/types.ts'
import type { RecipeProgress } from './chatModel.ts'

/** File boundaries stay separate from the tables passed between processing steps. */
export function flowInputFiles(flow: RecipeFlowData): NonNullable<RecipeFlowData['input_files']> {
  if (flow.input_files?.length) return flow.input_files
  const files = new Map<string, NonNullable<RecipeFlowData['input_files']>[number]>()
  for (const input of flow.inputs) {
    const key = input.source_file ? `file:${input.source_file}` : `alias:${input.alias}`
    const file = files.get(key) ?? {
      name: input.source_file || input.file_pattern || input.alias,
      aliases: [], file_pattern: input.file_pattern,
    }
    file.aliases.push(input.alias)
    files.set(key, file)
  }
  return [...files.values()]
}

export function flowOutputFiles(flow: RecipeFlowData): NonNullable<RecipeFlowData['output_files']> {
  // Missing file metadata in old messages must not turn a sheet name into a filename.
  return flow.output_files ?? []
}

/** Use actual run bindings to fill older snapshots that only contain processing steps. */
export function progressFlow(progress: RecipeProgress): RecipeFlowData {
  const snapshot = progress.flow ?? progress.saved?.flow
  const inputs = progress.inputs.length
    ? progress.inputs.map((input) => ({
      alias: input.alias, kind: input.kind, description: input.filename,
      file_pattern: '', source_file: input.filename, sheet: input.sheet,
    }))
    : snapshot?.inputs.length ? snapshot.inputs : progress.recipe?.inputs ?? []
  const files = snapshot?.output_files
  const delivered = progress.files?.length ? progress.files : progress.file ? [progress.file] : []
  return {
    ...snapshot,
    inputs,
    input_files: !progress.inputs.length && snapshot?.input_files?.length ? snapshot.input_files : undefined,
    steps: snapshot?.steps ?? progress.steps.map((step) => ({
      id: step.step_id, name: step.name, action: '', formula: step.formula, kind: step.kind,
      formula_kind: step.formula_kind as 'builtin' | 'customer',
      description: step.description, output: step.output, inputs: [],
    })),
    outputs: snapshot?.outputs ?? (progress.outputs ?? []).map((output) => ({ name: output.sheet, source: '' })),
    output_files: delivered.length ? delivered.map((file) => {
      const declared = files?.find((item) => item.name === file.filename)
      return declared ?? { name: file.filename, ref: '', source: '', sources: [] }
    }) : files,
  }
}
