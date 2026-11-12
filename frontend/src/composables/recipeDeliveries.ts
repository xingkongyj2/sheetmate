import type { Artifact, RecipeProgress, UiMessage } from './chatModel'
import { progressDeliveryFiles } from './deliveryFiles'

type RecipeDelivery = Extract<Artifact, { kind: 'recipe' }>
type RecipeView = Extract<Artifact, { kind: 'recipe_view' }>

function recipeId(progress: RecipeProgress): string | undefined {
  return progress.saved?.recipe_id || progress.recipeId || progress.recipe?.id
}

/** A run mode or recipe ID does not prove that its processing definition was explicitly saved. */
export function isWorkflowArtifact(progress: RecipeProgress): boolean {
  return !!progress.saved || progress.recipePersisted === true
}

function hasExecution(progress: RecipeProgress): boolean {
  return !!(progress.runId || progress.steps.length || progress.inputs.length || progress.file || progress.files?.length)
}

function filesOf(progress: RecipeProgress) {
  return progress.files?.length ? progress.files : progress.file ? [progress.file] : []
}

/** A later save updates the workflow without removing the result of its execution. */
function mergeDelivery(previous: RecipeDelivery, current: RecipeDelivery): RecipeDelivery {
  const base = hasExecution(current.progress) ? current.progress : previous.progress
  const latest = current.progress
  const saved = latest.saved && (!previous.progress.saved || latest.saved.version >= previous.progress.saved.version)
    ? latest.saved : previous.progress.saved
  const files = [...new Map([...filesOf(previous.progress), ...filesOf(latest)].map((file) => [file.file_id, file])).values()]
  return {
    ...current,
    key: previous.key,
    progress: {
      ...base,
      recipeId: recipeId(latest) || recipeId(previous.progress),
      recipeName: latest.recipeName || saved?.name || base.recipeName,
      saved,
      flow: saved?.flow || latest.flow || base.flow,
      deleted: latest.deleted ?? previous.progress.deleted,
      files,
      file: latest.file || previous.progress.file,
    },
  }
}

/** Deliver each workflow once; execution/save cards contain the same graph as a read-only view. */
export function recipeDeliveries(message: Pick<UiMessage, 'artifacts'>): { deliveries: RecipeDelivery[]; views: RecipeView[] } {
  const deliveries = new Map<string, RecipeDelivery>()
  const executions = new Map<string, RecipeDelivery>()
  const views = new Map<string, RecipeView>()
  for (const artifact of message.artifacts) {
    if (artifact.kind === 'recipe_view') {
      const previous = views.get(artifact.view.recipe_id)
      if (!previous || artifact.view.version >= previous.view.version) views.set(artifact.view.recipe_id, artifact)
    } else if (artifact.kind === 'recipe') {
      const id = recipeId(artifact.progress)
      const key = id ? `recipe:${id}` : artifact.progress.file ? `file:${artifact.progress.file.file_id}` : artifact.key
      if (!isWorkflowArtifact(artifact.progress)) {
        // Retain execution context for a later explicit save without publishing its internal graph.
        if (hasExecution(artifact.progress)) executions.set(key, artifact)
        continue
      }
      if (!progressDeliveryFiles(artifact.progress).length && !artifact.progress.saved) continue
      const previous = executions.get(key) ?? deliveries.get(key)
      deliveries.set(key, previous ? mergeDelivery(previous, artifact) : artifact)
      executions.delete(key)
    }
  }

  const deliveredIds = new Set([...deliveries.values()].flatMap((artifact) => {
    const id = recipeId(artifact.progress)
    return id ? [id] : []
  }))
  return {
    deliveries: [...deliveries.values()].map((artifact) => {
      const id = recipeId(artifact.progress)
      const view = id ? views.get(id)?.view : undefined
      if (!view || artifact.progress.flow || artifact.progress.saved?.flow) return artifact
      return { ...artifact, progress: { ...artifact.progress, flow: view.flow } }
    }),
    views: [...views.values()].filter((artifact) => !deliveredIds.has(artifact.view.recipe_id)),
  }
}
