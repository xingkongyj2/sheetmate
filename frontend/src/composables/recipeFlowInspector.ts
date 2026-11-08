import type { FileInfo, RecipeFlowData, RecipeJSON, StepReport } from '../api/types.ts'
import type { StepState } from './chatModel.ts'
import type { FlowFileNode } from './recipeFlowInteraction.ts'

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

/** Public event snapshots omit implementation fields that the versioned recipe includes. */
export function completeInspectorRecipe(recipe: RecipeJSON | undefined): boolean {
  return !!recipe && recipe.steps.every((step) => typeof step.sql === 'string' && typeof step.explanation === 'string' &&
    !!step.params && typeof step.params === 'object' && !Array.isArray(step.params))
}

/** Stored run reports restore fields trimmed from historical chat events. */
export function inspectorStates(current: StepState[], reports: StepReport[]): StepState[] {
  const states = new Map<number, StepState>()
  for (const report of reports) states.set(report.step_id, {
    step_id: report.step_id, name: report.name, formula: report.formula, formula_name: report.formula_name,
    kind: report.kind, formula_kind: report.formula_kind ?? 'builtin', description: report.description,
    output: report.output, status: report.status, repairs: [], report,
  })
  for (const state of current) {
    const stored = states.get(state.step_id)
    const report = state.report && stored?.report ? {
      ...stored.report, ...state.report,
      params: { ...(stored.report.params ?? {}), ...(state.report.params ?? {}) },
      sql: state.report.sql || stored.report.sql,
      preview: state.report.preview ?? stored.report.preview,
    } : state.report ?? stored?.report
    states.set(state.step_id, { ...stored, ...state, report })
  }
  return [...states.values()]
}

function outputArtifacts(node: FlowFileNode, states: StepState[]) {
  const sources = new Set([node.source, ...(node.sources ?? []), ...node.aliases].filter(Boolean))
  const artifacts = states.flatMap((state) => (state.report?.output_files ?? []).map((artifact) => ({ state, artifact })))
  const exact = artifacts.filter(({ artifact }) => artifact.name === node.name || artifact.filename === node.name || sources.has(artifact.ref))
  return exact.length ? exact : artifacts.filter(({ state }) => sources.has(state.output) || sources.has(state.report?.output))
}

export function inspectorFileIds(node: FlowFileNode, states: StepState[] = []): string[] {
  if (node.fileId) return [node.fileId]
  if (node.direction !== 'output') return []
  return [...new Set(outputArtifacts(node, states).flatMap(({ artifact }) => artifact.file_id ? [artifact.file_id] : []))]
}

export function inspectorStep(flow: RecipeFlowData, states: StepState[], recipe: RecipeJSON | undefined, id: number) {
  const flowStep = flow.steps.find((step) => step.id === id)
  const planned = recipe?.steps.find((step) => step.id === id)
  const state = states.find((step) => step.step_id === id)
  const report = state?.report
  const params = { ...(planned?.params ?? {}), ...(flowStep?.params ?? {}), ...(report?.params ?? {}) }
  const kind = report?.kind ?? state?.kind ?? flowStep?.kind ?? planned?.kind
  const formula = report?.formula || state?.formula || flowStep?.formula || planned?.formula || ''
  return {
    id, flowStep, planned, state, report, params,
    name: state?.name || flowStep?.name || planned?.name || `步骤 ${id}`,
    description: report?.description || state?.description || flowStep?.description || planned?.description || '',
    explanation: planned?.explanation || '',
    tool: { kind, formula, formula_kind: report?.formula_kind || state?.formula_kind || flowStep?.formula_kind || planned?.formula_kind },
    formulaName: report?.formula_name || state?.formula_name || formula,
    inputNames: Object.keys(report?.rows_in ?? {}).length ? Object.keys(report!.rows_in!) : flowStep?.inputs ?? [],
    output: report?.output || state?.output || flowStep?.output || planned?.output || '',
    sql: text(report?.sql) || text(params.sql) || text(planned?.sql),
    code: text(params.code),
  }
}

/** Only recorded bindings identify a file; wildcard patterns are requirements, not file IDs. */
export function inspectorFileMatches(node: FlowFileNode, flow: RecipeFlowData, files: FileInfo[], states: StepState[] = []): FileInfo[] {
  if (node.fileId) return files.filter((file) => file.file_id === node.fileId)

  const names = new Set<string>()
  const ids = new Set<string>()
  const inputSources = new Set<string>()
  const addName = (value?: string) => {
    const name = text(value)
    if (name && !/[?*{}]/.test(name)) names.add(name)
  }
  addName(node.name)
  addName(node.pattern)
  node.aliases.forEach(addName)

  if (node.direction === 'input') {
    for (const input of flow.inputs) {
      if (node.aliases.includes(input.alias) || input.alias === node.name ||
        (node.pattern && input.file_pattern === node.pattern) || input.source_file === node.name) {
        addName(input.source_file)
        addName(input.file_pattern)
        if (input.source_file) inputSources.add(input.source_file)
      }
    }
  } else {
    for (const { artifact } of outputArtifacts(node, states)) {
      if (artifact.file_id) ids.add(artifact.file_id)
      addName(artifact.filename)
      addName(artifact.name)
    }
  }

  const candidates = files.filter((file) => node.direction === 'output' ? file.kind === 'output' : file.kind !== 'output' || inputSources.has(file.filename))
  const directIds = candidates.filter((file) => ids.has(file.file_id))
  if (directIds.length) return directIds
  return candidates.filter((file) => names.has(file.filename))
}
