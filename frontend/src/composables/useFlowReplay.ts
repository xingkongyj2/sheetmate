import { computed, onScopeDispose, ref, watch } from 'vue'

import { conversationApi, customerApi, recipeApi, runApi } from '@/api'
import { ApiError, errorText } from '@/api/http'
import type { StreamHandle } from '@/api/sse'
import type { AgentEvent, FileInfo, RecipeFlowData, RecipeJSON } from '@/api/types'
import { addProgressFile, applyRecipeEvent, newProgress, type RecipeProgress } from './chatModel'
import { flowInputFiles } from './recipeFlow'
import type { RecipeFlowContext } from './recipeFlowInteraction'

type InputMatch = Awaited<ReturnType<typeof recipeApi.match>>

function uniqueFiles(items: FileInfo[]): FileInfo[] {
  return [...new Map(items.map((file) => [file.file_id, file])).values()]
}

function snapshot(value: unknown): RecipeJSON | null {
  if (!value || typeof value !== 'object') return null
  const candidate = value as Partial<RecipeJSON>
  return typeof candidate.id === 'string' && Array.isArray(candidate.steps) && Array.isArray(candidate.inputs)
    ? candidate as RecipeJSON : null
}

/** Lives with the embedded flow, so closing its fullscreen layer does not interrupt a run. */
export function useFlowReplay(context: () => RecipeFlowContext, sourceFlow: () => RecipeFlowData) {
  const progress = ref<RecipeProgress | null>(null)
  const recipe = ref<RecipeJSON | null>(null)
  const flow = ref<RecipeFlowData | null>(null)
  const files = ref<FileInfo[]>([])
  const executionRecipe = ref<RecipeJSON | null>(null)
  const executionFiles = ref<FileInfo[]>([])
  const chosen = ref<string[]>([])
  const mapping = ref<Record<string, string>>({})
  const match = ref<InputMatch | null>(null)
  const allowRepair = ref(true)
  const setupOpen = ref(false)
  const loading = ref(false)
  const matching = ref(false)
  const uploading = ref(false)
  const running = ref(false)
  const error = ref('')
  const mode = ref<'files' | 'original'>('files')
  const viewedVersion = ref<number | undefined>()
  const originalRunId = ref<string | null>(null)
  const uploadCustomerId = ref<string | null>(null)
  const uploadConversationId = ref<string | null>(null)
  const preparedSource = ref('')
  let disposed = false
  let request = 0
  let matchRequest = 0
  let stream: StreamHandle | null = null

  const sourceIdentity = computed(() => {
    const current = context()
    return JSON.stringify([current.recipeId || current.recipe?.id, current.version ?? current.recipe?.version, current.runId, current.customerId, current.conversationId])
  })
  const canUpload = computed(() => !!(uploadCustomerId.value || uploadConversationId.value))
  const canPrepare = computed(() => !running.value && !context().running && !context().deleted &&
    !!(context().recipeId || context().recipe?.id || context().runId))
  const firstStep = computed(() => flow.value?.steps[0]?.id ?? sourceFlow().steps[0]?.id)
  const versionChanged = computed(() => mode.value === 'files' && viewedVersion.value != null &&
    recipe.value != null && viewedVersion.value !== recipe.value.version)
  const canRun = computed(() => {
    if (running.value || loading.value || matching.value || uploading.value || context().running || context().deleted || preparedSource.value !== sourceIdentity.value) return false
    if (mode.value === 'original') return !!originalRunId.value && firstStep.value != null
    if (!recipe.value) return false
    if (!recipe.value.inputs.length) return true
    if (!match.value || match.value.inputs.length !== recipe.value.inputs.length) return false
    return match.value.inputs.every((input) => {
      const key = mapping.value[input.alias]
      const candidate = match.value?.candidates.find((item) => item.key === key)
      return candidate && (candidate.kind ?? 'table') === (input.kind ?? 'table') && chosen.value.includes(key!.split('::')[0]!)
    })
  })

  watch(chosen, () => {
    matchRequest += 1
    match.value = null
    mapping.value = {}
    matching.value = false
  }, { deep: true, flush: 'sync' })

  function closeSetup() {
    setupOpen.value = false
    request += 1
    matchRequest += 1
    loading.value = false
    matching.value = false
  }

  async function prepare() {
    if (!canPrepare.value) return
    const token = ++request
    const current = { ...context() }
    preparedSource.value = sourceIdentity.value
    setupOpen.value = true
    loading.value = true
    error.value = ''
    recipe.value = null
    flow.value = null
    match.value = null
    mapping.value = {}
    chosen.value = []
    files.value = uniqueFiles(current.files ?? [])
    mode.value = 'files'
    viewedVersion.value = current.version ?? current.recipe?.version
    originalRunId.value = null
    uploadCustomerId.value = current.customerId ?? null
    uploadConversationId.value = current.conversationId ?? null
    const recipeId = current.recipeId || current.recipe?.id
    try {
      if (recipeId) {
        try {
          // Matching uses the latest saved version. Show that version before the user starts it.
          const data = await recipeApi.get(recipeId)
          if (disposed || token !== request) return
          recipe.value = data.recipe
          flow.value = data.flow
          uploadCustomerId.value = data.recipe.customer_id || current.customerId || null
          const conversationId = data.recipe.learned_from?.conversation_id
          uploadConversationId.value = current.conversationId || (typeof conversationId === 'string' ? conversationId : null)
        } catch (reason) {
          if (!(reason instanceof ApiError && reason.status === 404 && current.runId)) throw reason
        }
      }
      if (!recipe.value && current.runId) {
        const data = await runApi.get(current.runId)
        if (disposed || token !== request) return
        if (!data.run.recipe_id) throw new Error('这次执行没有可重跑的流程')
        recipe.value = snapshot(data.run.validation?.recipe_snapshot) ?? current.recipe ?? null
        flow.value = sourceFlow()
        originalRunId.value = current.runId
        mode.value = 'original'
        return
      }
      if (!recipe.value) throw new Error('保存流程后即可重新执行')

      const sources: Promise<FileInfo[]>[] = []
      if (uploadCustomerId.value) sources.push(customerApi.get(uploadCustomerId.value).then((data) => [...data.files, ...data.outputs]))
      if (uploadConversationId.value) sources.push(conversationApi.get(uploadConversationId.value).then((data) => data.files))
      const resources = await Promise.allSettled(sources)
      if (disposed || token !== request) return
      files.value = uniqueFiles([...files.value, ...resources.flatMap((item) => item.status === 'fulfilled' ? item.value : [])])
      const bindings = recipe.value.learned_from?.input_bindings
      const boundIds = bindings && typeof bindings === 'object'
        ? Object.values(bindings).flatMap((value) => value && typeof value === 'object' && typeof value.file_id === 'string' ? [value.file_id] : []) : []
      const names = new Set(flowInputFiles(sourceFlow()).map((file) => file.name))
      chosen.value = files.value.filter((file) => boundIds.includes(file.file_id) || names.has(file.filename)).map((file) => file.file_id)
      if (!chosen.value.length && files.value.length === 1) chosen.value = [files.value[0]!.file_id]
      if (!files.value.length) {
        const failed = resources.find((item) => item.status === 'rejected')
        if (failed?.status === 'rejected') error.value = errorText(failed.reason)
      }
    } catch (reason) {
      if (!disposed && token === request) error.value = errorText(reason)
    } finally {
      if (!disposed && token === request) loading.value = false
    }
  }

  async function upload(items: File[]) {
    if (!items.length || uploading.value || running.value || !canUpload.value) return
    uploading.value = true
    error.value = ''
    const token = request
    try {
      for (const item of items) {
        const file = uploadCustomerId.value
          ? await customerApi.uploadFile(uploadCustomerId.value, item, 'input')
          : (await conversationApi.upload(uploadConversationId.value!, item, 'input')).file
        if (disposed || token !== request) return
        files.value = uniqueFiles([file, ...files.value])
        chosen.value = [...new Set([...chosen.value, file.file_id])]
      }
    } catch (reason) {
      if (!disposed && token === request) error.value = errorText(reason)
    } finally { if (!disposed) uploading.value = false }
  }

  async function matchInputs() {
    if (!recipe.value || matching.value || uploading.value || running.value) return
    if (!chosen.value.length && recipe.value.inputs.length) { error.value = '请选择输入文件'; return }
    const token = ++matchRequest
    const prepareToken = request
    const ids = [...chosen.value]
    matching.value = true
    error.value = ''
    try {
      const result = await recipeApi.match(recipe.value.id, ids)
      if (disposed || token !== matchRequest || prepareToken !== request) return
      // A new saved version could have changed the input aliases since opening the setup.
      const expected = recipe.value.inputs
      if (result.inputs.length !== expected.length || result.inputs.some((input) => {
        const definition = expected.find((item) => item.alias === input.alias)
        return !definition || (definition.kind ?? 'table') !== (input.kind ?? 'table') ||
          (definition.file_pattern ?? '') !== (input.file_pattern ?? '') ||
          JSON.stringify([...(definition.required_columns ?? [])].sort()) !== JSON.stringify([...(input.required_columns ?? [])].sort())
      })) {
        throw new Error('流程已更新，请重新打开执行设置')
      }
      match.value = result
      mapping.value = Object.fromEntries(result.inputs.map((input) => [input.alias, input.match?.key ?? '']))
    } catch (reason) {
      if (!disposed && token === matchRequest && prepareToken === request) error.value = errorText(reason)
    } finally { if (!disposed && token === matchRequest) matching.value = false }
  }

  function fail(message: string) {
    error.value = message
    if (!progress.value) return
    progress.value.failed = message
    progress.value.status = 'failed'
    progress.value.runStatus = 'failed'
    for (const step of progress.value.steps) if (step.status === 'running' || step.status === 'repairing') step.status = 'failed'
  }

  async function run(): Promise<boolean> {
    if (!canRun.value) return false
    const selectedRecipe = recipe.value
    const selectedFlow = flow.value ?? sourceFlow()
    const original = mode.value === 'original'
    const runId = originalRunId.value
    const stepId = firstStep.value
    const inputMapping = { ...mapping.value }
    const inputIds = [...new Set(Object.values(inputMapping).map((key) => key.split('::')[0]!).filter(Boolean))]
    running.value = true
    setupOpen.value = false
    error.value = ''
    executionRecipe.value = selectedRecipe ? JSON.parse(JSON.stringify(selectedRecipe)) as RecipeJSON : null
    executionFiles.value = [...files.value]
    progress.value = {
      ...newProgress('replay'), status: 'running', recipe: selectedRecipe ?? undefined,
      flow: selectedFlow, recipeId: selectedRecipe?.id, recipeVersion: selectedRecipe?.version,
      recipeName: selectedRecipe?.name || context().title,
      steps: selectedFlow.steps.map((step) => ({
        step_id: step.id, name: step.name, formula: step.formula, formula_name: step.formula,
        kind: step.kind, formula_kind: step.formula_kind, description: step.description,
        output: step.output, status: 'pending', repairs: [],
      })),
    }
    let finished = false
    const receive = (event: AgentEvent) => {
      if (disposed || !progress.value) return
      if (event.type.startsWith('recipe_')) applyRecipeEvent(progress.value, event)
      if (event.type === 'recipe_run_done' || event.type === 'done') {
        finished = true
        if (event.status) progress.value.runStatus = event.status
        progress.value.status = progress.value.failed || ['failed', 'escalated'].includes(progress.value.runStatus ?? '') ? 'failed' : 'done'
      }
      if (event.type === 'file' && event.file_id) addProgressFile(progress.value, {
        file_id: event.file_id, filename: event.filename, download_url: event.download_url,
        kind: 'output', sheets: event.sheets, run_id: event.run_id,
      })
      if (event.type === 'error') fail(event.content || event.error || '执行失败')
      if (event.type === 'recipe_failed') error.value = progress.value.failed || '执行失败'
    }
    try {
      stream = original
        ? runApi.rerun(runId!, stepId!, null, receive)
        : recipeApi.run(selectedRecipe!.id, {
          file_ids: inputIds, inputs: inputMapping, allow_repair: allowRepair.value, version: selectedRecipe!.version,
        }, receive)
      await stream.done
      if (disposed) return false
      if (!finished && progress.value?.status !== 'failed') fail('执行连接已结束，未收到完成状态')
      return progress.value?.status === 'done' && !progress.value.failed
    } catch (reason) {
      if (!disposed) fail(errorText(reason))
      return false
    } finally {
      stream = null
      if (!disposed) running.value = false
    }
  }

  onScopeDispose(() => {
    disposed = true
    request += 1
    matchRequest += 1
    stream?.abort()
  })

  return {
    progress, recipe, flow, files, executionRecipe, executionFiles, chosen, mapping, match, allowRepair, setupOpen, loading, matching,
    uploading, running, error, mode, viewedVersion, versionChanged, canUpload, canPrepare, canRun,
    prepare, closeSetup, matchInputs, upload, run,
  }
}

export type FlowReplayController = ReturnType<typeof useFlowReplay>
