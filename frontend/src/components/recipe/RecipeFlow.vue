<script lang="ts">
// 同一页面可能有好几个流程图（对话里每条消息一个）：地址栏要求打开全屏时，只让第一个匹配的打开。
const openedFlows = new Set<symbol>()
</script>

<script setup lang="ts">
/** A flow stays on its page: shared canvas, fullscreen layer, resource inspector; fullscreen executes directly on flowchart nodes and resets on close. */
import { Check, ChevronDown, CircleAlert, Download, Eye, LoaderCircle, LocateFixed, Maximize2, MessageCircleMore, MessageSquare, Minus, Play, Plus, RotateCcw, Square, X } from '@lucide/vue'
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { conversationApi, customerApi, recipeApi, runApi } from '@/api'
import { ApiError, errorText } from '@/api/http'
import type { StreamHandle } from '@/api/sse'
import type { CustomerWorkflow, FileInfo, RecipeEditRequest, RecipeFlowData, RecipeJSON } from '@/api/types'
import type { PickedRunItem } from '@/components/customer/RunRecipeDialog.vue'
import { addProgressFile, applyRecipeEvent, newProgress, type FileArtifact, type RecipeProgress, type StepState } from '@/composables/chatModel'
import type { FlowFileNode, FlowSelection, RecipeFlowContext } from '@/composables/recipeFlowInteraction'
import { conversationMatchesRecipeEdit, recipeEditTarget } from '@/composables/recipeEditing'
import { useFilePreview } from '@/composables/useFilePreview'
import { useToast } from '@/composables/useToast'
import { useWorkspaceStore } from '@/stores/conversations'
import { pendingRecipeEdits } from '@/stores/recipeEdits'

import RecipeFlowCanvas from './RecipeFlowCanvas.vue'
import RecipeFlowInspector from './RecipeFlowInspector.vue'

// 用新文件执行：和客户页里的「执行」是同一个弹窗。全屏模式仅用它选文件，确认后直接在流程图节点上实时执行。
const RunRecipeDialog = defineAsyncComponent(() => import('@/components/customer/RunRecipeDialog.vue'))
const props = withDefaults(defineProps<{
  flow: RecipeFlowData
  states?: StepState[]
  direction?: 'horizontal' | 'vertical'
  activeStep?: number | null
  compact?: boolean
  context?: RecipeFlowContext
  toolbarPlacement?: 'inline' | 'card' | 'hidden'
  inlineCanvas?: boolean  // false：不在页面里画流程图，只提供全屏（由外面的按钮调用 openFullscreen 打开）
}>(), { states: () => [], direction: 'horizontal', activeStep: null, compact: false, context: () => ({}), toolbarPlacement: 'inline', inlineCanvas: true })
const emit = defineEmits<{ changed: [] }>()
const router = useRouter()
const route = useRoute()
const store = useWorkspaceStore()
const toast = useToast()
const openingEdit = ref(false)
const fullscreen = ref(false)
const selection = ref<FlowSelection | null>(null)
const openFilePreview = useFilePreview()
const dialog = ref<HTMLElement | null>(null)
// 右侧节点详情面板：默认与聊天页面工作区面板一致（390px），可以拖左边缘调宽度
const SIDE_DEFAULT = 390
const SIDE_MIN = 280
const SIDE_MAX = 600
const SIDE_CANVAS_MIN = 320   // 至少给流程图留的宽度
const sideWidth = ref(SIDE_DEFAULT)
const workspaceEl = ref<HTMLElement | null>(null)
const resizingSide = ref(false)
function clampSide(width: number): number {
  const room = (workspaceEl.value?.clientWidth ?? Infinity) - SIDE_CANVAS_MIN
  return Math.round(Math.max(SIDE_MIN, Math.min(SIDE_MAX, room, width)))
}
function startSideResize(event: PointerEvent) {
  if (event.button !== 0) return
  ;(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId)
  resizingSide.value = true
  event.preventDefault()
}
function moveSideResize(event: PointerEvent) {
  if (!resizingSide.value || !workspaceEl.value) return
  sideWidth.value = clampSide(workspaceEl.value.getBoundingClientRect().right - event.clientX)
}
function endSideResize() {
  if (!resizingSide.value) return
  resizingSide.value = false
}
function keySideResize(event: KeyboardEvent) {
  const step = event.shiftKey ? 80 : 24
  if (event.key === 'ArrowLeft') sideWidth.value = clampSide(sideWidth.value + step)
  else if (event.key === 'ArrowRight') sideWidth.value = clampSide(sideWidth.value - step)
  else return
  event.preventDefault()
}
function resetSideWidth() {
  sideWidth.value = clampSide(SIDE_DEFAULT)
}
const viewport = ref<HTMLElement | null>(null)
const canvas = ref<HTMLElement | null>(null)
const zoom = ref(1)
const follow = ref(true)
const dimensions = ref({ width: 1, height: 1, viewportWidth: 1, viewportHeight: 1 })
const shownFlow = computed(() => props.flow)
const states = computed(() => {
  const steps = props.states
  return props.context.failed ? steps.map((step) => ['running', 'repairing'].includes(step.status) ? { ...step, status: 'failed' as const } : step) : steps
})
const running = computed(() => props.context.running ?? states.value.some((step) => ['running', 'repairing'].includes(step.status)))
const context = computed<RecipeFlowContext>(() => props.context)
const currentStep = computed(() => running.value ? states.value.find((step) => ['running', 'repairing'].includes(step.status)) : undefined)
const failed = computed(() => props.context.failed || states.value.some((step) => step.status === 'failed'))
const canReplay = computed(() => !context.value.deleted && !!(context.value.recipeId || context.value.recipe?.id || context.value.runId))
const canEdit = computed(() => !context.value.deleted && !!(context.value.recipeId || context.value.recipe?.id))
const dialogOpen = computed(() => fullscreen.value || !!selection.value)

// ---- 全屏独立执行态：每次打开全屏均为未执行状态，点击执行直接在流程图节点上实时跑，关闭全屏即清空重置 ----
type FullscreenItemState = 'wait' | 'run' | 'done' | 'failed' | 'skipped'
interface FullscreenRunItem {
  title: string
  label: string
  mapping: Record<string, string>
  progress: RecipeProgress
  files: FileArtifact[]
  state: FullscreenItemState
}
const fsItems = ref<FullscreenRunItem[]>([])
const fsPool = ref<FileInfo[]>([])
const fsSessionRecipeId = ref<string | null>(null)
const fsSessionRecipe = ref<RecipeJSON | null>(null)
const fsSessionFlow = ref<RecipeFlowData | null>(null)
const fsActiveIndex = ref(0)
const fsViewing = ref<number | null>(null)
const fsRunning = ref(false)
const fsStopping = ref(false)
const fsBatchOpen = ref(false)
let fsGeneration = 0
let fsHandle: StreamHandle | null = null

const fsFailedOf = (progress: RecipeProgress) => progress.status === 'failed' || !!progress.failed
  || ['failed', 'escalated', 'incomplete', 'cancelled', 'interrupted'].includes(progress.runStatus ?? '')
const fsHasRun = computed(() => fsItems.value.length > 0)
const fsViewIndex = computed(() => fsViewing.value ?? fsActiveIndex.value)
const fsViewItem = computed<FullscreenRunItem | null>(() => fsItems.value[fsViewIndex.value] ?? null)
const fsViewRunning = computed(() => !!fsViewItem.value && (fsItems.value.length === 1 ? fsRunning.value : fsViewItem.value.state === 'run'))
const fsViewFailed = computed(() => !!fsViewItem.value && (fsViewItem.value.state === 'failed' || fsFailedOf(fsViewItem.value.progress)))
const fsStates = computed<StepState[]>(() => {
  const item = fsViewItem.value
  if (!item) return []
  const steps = item.progress.steps
  return fsViewFailed.value
    ? steps.map((step) => ['running', 'repairing'].includes(step.status) ? { ...step, status: 'failed' as const } : step)
    : steps
})
const fsCurrentStep = computed(() => fsViewRunning.value ? fsStates.value.find((step) => ['running', 'repairing'].includes(step.status)) : undefined)
const fsCompleted = computed(() => fsStates.value.filter((step) => ['success', 'warning', 'repaired'].includes(step.status)).length)
const fsOutputFiles = computed(() => fsViewItem.value?.files ?? [])
const fsBatchInfo = computed(() => (fsItems.value.length > 1 && fsViewItem.value
  ? { index: fsViewIndex.value + 1, total: fsItems.value.length, filename: fsViewItem.value.title } : undefined))
const fsBatchFinished = computed(() => fsItems.value.filter((item) => item.state === 'done' || item.state === 'failed').length)
const fsBatchFailedCount = computed(() => fsItems.value.filter((item) => item.state === 'failed').length)
const fsBatchRetryable = computed(() => fsItems.value.filter((item) => item.state === 'failed' || item.state === 'skipped').length)
const fsErrorText = computed(() => fsViewItem.value?.progress.escalation || fsViewItem.value?.progress.failed || fsStates.value.find((step) => step.error)?.error || '')
const fsFlow = computed<RecipeFlowData>(() => {
  const base = fsSessionFlow.value ?? props.flow
  const item = fsViewItem.value
  if (!item) return base
  const byFile = new Map<string, { name: string; aliases: string[]; file_pattern: string; file_id?: string }>()
  for (const [alias, key] of Object.entries(item.mapping)) {
    const fid = key.split('::')[0]
    const info = fsPool.value.find((file) => file.file_id === fid)
    const runtimeInput = item.progress.inputs.find((inp) => inp.alias === alias)
    const declaredInput = base.inputs.find((inp) => inp.alias === alias)
    const name = runtimeInput?.filename || info?.filename || alias
    const groupKey = fid || name
    const existing = byFile.get(groupKey) ?? { name, aliases: [], file_pattern: declaredInput?.file_pattern ?? '', file_id: fid || undefined }
    existing.aliases.push(alias)
    byFile.set(groupKey, existing)
  }
  const inputFiles = byFile.size ? [...byFile.values()] : base.input_files
  const outputFiles = item.files.length
    ? item.files.map((file) => {
      const declared = base.output_files?.find((o) => o.name === file.filename)
      return { ...(declared ?? { name: file.filename, ref: '', source: '', sources: [] }), name: file.filename, file_id: file.file_id, download_url: file.download_url }
    })
    : base.output_files?.map((o) => ({ ...o, file_id: undefined, download_url: undefined }))
  return { ...base, input_files: inputFiles, output_files: outputFiles }
})
const fsContext = computed<RecipeFlowContext>(() => {
  const item = fsViewItem.value
  const mergedFiles = fsPool.value.length
    ? [...new Map([...(props.context.files ?? []), ...fsPool.value].map((file) => [file.file_id, file])).values()]
    : props.context.files
  if (!item) {
    return {
      ...props.context,
      runId: undefined,
      running: false,
      failed: false,
      resultFiles: [],
      batch: undefined,
    }
  }
  return {
    ...props.context,
    recipe: fsSessionRecipe.value ?? props.context.recipe,
    files: mergedFiles,
    runId: item.progress.runId,
    running: fsViewRunning.value,
    failed: fsViewFailed.value,
    resultFiles: item.files,
    batch: fsBatchInfo.value,
  }
})

function seedSteps(recipe: RecipeJSON | null): StepState[] {
  const steps = recipe?.steps?.filter((step) => step.enabled !== false) ?? []
  if (steps.length) {
    return steps.map((step) => ({
      step_id: step.id, name: step.name, formula: step.formula, formula_name: step.formula,
      kind: step.kind, formula_kind: step.formula_kind, description: step.description,
      output: step.output, status: 'pending', repairs: [],
    }))
  }
  return (fsSessionFlow.value ?? props.flow).steps.map((step) => ({
    step_id: step.id, name: step.name, formula: step.formula, formula_name: step.formula,
    kind: step.kind, formula_kind: step.formula_kind, description: step.description,
    output: step.output, status: 'pending', repairs: [],
  }))
}

function resetFullscreenRun() {
  fsGeneration++
  fsStopping.value = true
  fsHandle?.abort()
  fsHandle = null
  fsItems.value = []
  fsPool.value = []
  fsSessionRecipeId.value = null
  fsSessionRecipe.value = null
  fsSessionFlow.value = null
  fsActiveIndex.value = 0
  fsViewing.value = null
  fsRunning.value = false
  fsStopping.value = false
  fsBatchOpen.value = false
  runRecipe.value = null
}
// stageSize 只用来给画布居中；舞台本身的尺寸由 CSS 解析（stageStyle），不能把 JS 取整后的视口尺寸写进布局：
// Windows 显示缩放 125%/150% 时容器高度是小数像素，clientHeight 取整后比真实高度大，舞台多出零点几像素 → 出滚动条 →
// 经典滚动条占布局、视口变小 → ResizeObserver 重设舞台 → 滚动条消失 → 循环，画面每帧抖动。
const stageSize = computed(() => ({
  width: Math.max(dimensions.value.viewportWidth, dimensions.value.width * zoom.value + 96),
  height: Math.max(dimensions.value.viewportHeight, dimensions.value.height * zoom.value + 96),
}))
const stageStyle = computed(() => ({
  width: `max(100%, ${dimensions.value.width * zoom.value + 96}px)`,
  height: `max(100%, ${dimensions.value.height * zoom.value + 96}px)`,
}))
const canvasStyle = computed(() => ({
  left: `${(stageSize.value.width - dimensions.value.width * zoom.value) / 2}px`,
  top: `${(stageSize.value.height - dimensions.value.height * zoom.value) / 2}px`,
  transform: `scale(${zoom.value})`,
}))
let observer: ResizeObserver | null = null
let returnFocus: HTMLElement | null = null
let previousOverflow = ''
let locked = false
let dragging: { x: number; y: number; left: number; top: number } | null = null

function measure() {
  if (!canvas.value || !viewport.value) return
  dimensions.value = {
    width: canvas.value.scrollWidth, height: canvas.value.scrollHeight,
    viewportWidth: viewport.value.clientWidth, viewportHeight: viewport.value.clientHeight,
  }
}
function fit() {
  measure()
  const d = dimensions.value
  // 上下多留一些：顶部有浮在画布上的标题和按钮。
  zoom.value = Math.max(0.25, Math.min(1, (d.viewportWidth - 96) / d.width, (d.viewportHeight - 168) / d.height))
}
/** 重置：回到刚打开时的样子——整张流程图缩放到刚好放得下，并位于画面正中。 */
async function resetView() {
  fit()
  follow.value = false
  await nextTick()
  const view = viewport.value
  if (!view) return
  view.scrollLeft = Math.max(0, (view.scrollWidth - view.clientWidth) / 2)
  view.scrollTop = Math.max(0, (view.scrollHeight - view.clientHeight) / 2)
}
/** 缩放以画面正中为中心：缩放前画面中心下的那个流程图位置，缩放后仍在画面中心。 */
async function changeZoom(value: number) {
  const next = Math.max(0.25, Math.min(1.6, value))
  follow.value = false
  const view = viewport.value
  if (!view || next === zoom.value) {
    zoom.value = next
    return
  }
  const d = dimensions.value
  const before = { zoom: zoom.value, left: (stageSize.value.width - d.width * zoom.value) / 2, top: (stageSize.value.height - d.height * zoom.value) / 2 }
  const centerX = view.clientWidth / 2
  const centerY = view.clientHeight / 2
  const contentX = (view.scrollLeft + centerX - before.left) / before.zoom
  const contentY = (view.scrollTop + centerY - before.top) / before.zoom
  zoom.value = next
  await nextTick()  // 等画布按新的倍数撑开
  const left = (stageSize.value.width - d.width * next) / 2
  const top = (stageSize.value.height - d.height * next) / 2
  view.scrollLeft = contentX * next + left - centerX
  view.scrollTop = contentY * next + top - centerY
}
// 全屏流程图在地址栏里记一个 ?flow=流程ID：刷新、复制地址、前进后退都停在这个全屏页面。
const FLOW_QUERY = 'flow'
const flowKey = computed(() => props.context.recipeId || props.context.recipe?.id || (props.context.runId ? `run:${props.context.runId}` : ''))
const flowInstance = Symbol('recipe-flow')
let pushedHistory = false  // 这次全屏是我们 push 出来的历史记录：关闭时回退，而不是留下一条多余的记录

function queryWithFlow(value?: string) {
  const { [FLOW_QUERY]: _removed, ...rest } = route.query
  return value ? { ...rest, [FLOW_QUERY]: value } : rest
}
async function openFullscreen() {
  if (fullscreen.value) return
  resetFullscreenRun()
  selection.value = null
  fullscreen.value = true
  openedFlows.add(flowInstance)
  if (flowKey.value && route.query[FLOW_QUERY] !== flowKey.value) {
    pushedHistory = true
    void router.push({ query: queryWithFlow(flowKey.value) })
  }
  follow.value = true
  await nextTick()
  measure()
  fit()
  observer?.disconnect()
  observer = new ResizeObserver(measure)
  if (viewport.value) observer.observe(viewport.value)
  if (canvas.value) observer.observe(canvas.value)
}
function closeLocal() {
  resetFullscreenRun()
  selection.value = null
  fullscreen.value = false
  openedFlows.delete(flowInstance)
  observer?.disconnect()
}
function closeDialog() {
  const wasFullscreen = fullscreen.value
  closeLocal()
  if (!wasFullscreen || route.query[FLOW_QUERY] !== flowKey.value) return
  if (pushedHistory) router.back()
  else void router.replace({ query: queryWithFlow() })  // 刷新后进来的：没有可回退的记录，直接去掉参数
  pushedHistory = false
}
/** 地址栏和全屏状态保持一致：地址里有这个流程就打开，后退去掉参数就关闭。 */
function syncFromRoute() {
  const wanted = route.query[FLOW_QUERY]
  if (wanted && flowKey.value && wanted === flowKey.value && !fullscreen.value && openedFlows.size === 0) void openFullscreen()
  else if (!wanted && fullscreen.value) {
    pushedHistory = false
    closeLocal()
  }
}
function selectStep(id: number) {
  selection.value = { kind: 'step', id }
}
function selectFile(file: FlowFileNode) {
  selection.value = { kind: 'file', file }
}
async function editFlow(operation: RecipeEditRequest['operation'] = 'modify', target = recipeEditTarget(selection.value), focus?: 'sql' | 'python' | 'formula' | 'name') {
  const busy = fullscreen.value ? fsRunning.value : running.value
  if (!canEdit.value || busy || openingEdit.value) return
  openingEdit.value = true
  const current = fullscreen.value ? fsContext.value : context.value
  const activeFlow = fullscreen.value ? fsFlow.value : shownFlow.value
  try {
    const recipeId = current.recipeId || current.recipe!.id
    const route = router.currentRoute.value
    let conversationId = route.name === 'chat' && route.params.id === current.conversationId ? current.conversationId : null
    let savedRecipe: RecipeJSON | undefined
    const displayedVersion = current.version ?? current.recipe?.version
    if (!conversationId || displayedVersion === undefined) {
      try {
        savedRecipe = (await recipeApi.get(recipeId)).recipe
      } catch (err) {
        if (!(err instanceof ApiError && err.status === 404 && current.conversationId && displayedVersion !== undefined)) throw err
      }
    }
    const version = displayedVersion ?? savedRecipe?.version
    if (version === undefined) throw new Error('无法确认流程版本，请刷新流程后重试。')
    // 节点里的「AI 修改」：带上步骤名和要改的是 SQL / 脚本 / 公式，聊天里据此显示和提示
    const stepName = target.kind === 'step' ? activeFlow.steps.find((item) => item.id === target.step_id)?.name : undefined
    const request: RecipeEditRequest = { recipe_id: recipeId, version, operation,
      target: focus && target.kind === 'step' ? { ...target, focus, ...(stepName ? { name: stepName } : {}) }
        : focus === 'name' && target.kind !== 'step' && target.kind !== 'workflow' ? { ...target, focus } : target }
    if (!conversationId) {
      if (savedRecipe && current.customerId) {
        const conversation = await conversationApi.create({ customer_id: current.customerId, recipe_id: recipeId })
        store.upsertConversation(conversation)
        conversationId = conversation.id
      } else {
        if (!current.conversationId) throw new Error('请从生成这个流程的对话发起修改。')
        const conversation = await conversationApi.get(current.conversationId)
        if (!conversationMatchesRecipeEdit(conversation, request)) throw new Error('原对话中的流程与所选版本不匹配，请刷新流程后重试。')
        conversationId = conversation.id
      }
    }
    pendingRecipeEdits.set(conversationId, { title: current.title || '处理流程', request })
    // 只在本地关掉全屏：这里要整页跳去对话页，不能再让 closeDialog 去回退或改写地址栏，否则两次导航互相抢，停在原页面
    pushedHistory = false
    closeLocal()
    await router.push({ name: 'chat', params: { id: conversationId } })
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    openingEdit.value = false
  }
}
const runRecipe = ref<Pick<CustomerWorkflow, 'recipe_id' | 'name' | 'version' | 'flow' | 'recipe' | 'files'> | null>(null)
const preparingRun = ref(false)
/** 用新文件执行：全屏下打开选文件弹窗，选好后关闭弹窗并直接在流程图节点上实时执行；非全屏沿用原弹窗。 */
async function prepareRun() {
  const busy = fullscreen.value ? fsRunning.value : running.value
  if (busy || preparingRun.value) return
  preparingRun.value = true
  try {
    let recipeId = props.context.recipeId || props.context.recipe?.id
    if (!recipeId && props.context.runId) {
      recipeId = (await runApi.get(props.context.runId)).run.recipe_id ?? undefined
      if (!recipeId) throw new Error('这次执行没有可重跑的流程')
    }
    if (!recipeId) throw new Error('这个流程还没有保存，暂时不能用新文件执行')
    const data = await recipeApi.get(recipeId)
    // 文件池和客户页的执行弹窗一致：客户的流程用它自己的资料文件（含录制时的样例），对话里的流程用会话文件。
    let files: FileInfo[] = []
    if (props.context.customerId) {
      const customer = await customerApi.get(props.context.customerId)
      files = [...(customer.recipes.find((item) => item.recipe_id === recipeId)?.files ?? []), ...customer.files]
    } else if (props.context.conversationId) {
      files = (await conversationApi.get(props.context.conversationId)).files
    }
    files = [...new Map([...files, ...(props.context.files ?? [])].map((file) => [file.file_id, file])).values()]
    runRecipe.value = {
      recipe_id: recipeId, name: data.recipe.name || props.context.title || '处理流程', version: data.recipe.version,
      flow: data.flow, recipe: data.recipe, files,
    }
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    preparingRun.value = false
  }
}

async function executeFullscreen(indices: number[]) {
  const recipeId = fsSessionRecipeId.value
  if (!recipeId || !indices.length) return
  const gen = ++fsGeneration
  fsRunning.value = true
  fsStopping.value = false
  try {
    for (const index of indices) {
      if (gen !== fsGeneration) return
      const item = fsItems.value[index]
      if (!item) continue
      if (fsStopping.value) { item.state = 'skipped'; continue }
      item.state = 'run'
      fsActiveIndex.value = index
      fsViewing.value = null
      const fresh = newProgress('replay')
      fresh.steps = seedSteps(fsSessionRecipe.value)
      Object.assign(item.progress, fresh)
      item.files = []
      await nextTick()
      measure()
      const fileIds = [...new Set(Object.values(item.mapping).map((key) => key.split('::')[0]))]
      const handle = recipeApi.run(recipeId, {
        file_ids: fileIds,
        inputs: item.mapping,
        allow_repair: true,
        scoped: true,
        label: fsItems.value.length > 1 ? item.label || undefined : undefined,
      }, (e) => {
        if (gen !== fsGeneration) return
        if (e.type.startsWith('recipe_')) applyRecipeEvent(item.progress, e)
        else if (e.type === 'file') {
          const file = { file_id: e.file_id, filename: e.filename, download_url: e.download_url, sheets: e.sheets }
          addProgressFile(item.progress, file)
          if (!item.files.some((existing) => existing.file_id === file.file_id)) item.files.push(file)
        } else if (e.type === 'error') {
          item.progress.failed = e.content
        }
      })
      fsHandle = handle
      try {
        await handle.done
      } catch (err) {
        if (gen !== fsGeneration) return
        item.progress.failed = errorText(err)
        item.progress.status = 'failed'
      } finally {
        if (fsHandle === handle) fsHandle = null
      }
      if (gen !== fsGeneration) return
      item.state = fsFailedOf(item.progress) ? 'failed' : 'done'
    }
  } finally {
    if (gen === fsGeneration) {
      fsRunning.value = false
      fsStopping.value = false
      fsHandle = null
      emit('changed')
    }
  }
}

function startFullscreenRun(payload: { items: PickedRunItem[]; pool: FileInfo[] }) {
  const picked = runRecipe.value
  if (!picked || !payload.items.length) return
  fsSessionRecipeId.value = picked.recipe_id
  fsSessionRecipe.value = picked.recipe
  fsSessionFlow.value = picked.flow
  fsPool.value = payload.pool
  fsItems.value = payload.items.map((item) => {
    const progress = newProgress('replay')
    progress.steps = seedSteps(picked.recipe)
    return { ...item, progress, files: [], state: 'wait' }
  })
  fsActiveIndex.value = 0
  fsViewing.value = null
  fsBatchOpen.value = false
  selection.value = null
  runRecipe.value = null
  follow.value = true
  void executeFullscreen(fsItems.value.map((_, i) => i))
}

function retryFullscreenFailed() {
  if (fsRunning.value) return
  const indices = fsItems.value.flatMap((item, i) => (item.state === 'failed' || item.state === 'skipped' ? [i] : []))
  if (indices.length) void executeFullscreen(indices)
}

async function locateCurrent() {
  follow.value = true
  await nextTick()
  const stepId = fullscreen.value ? fsCurrentStep.value?.step_id : currentStep.value?.step_id
  if (!stepId) return
  const target = canvas.value?.querySelector<HTMLElement>(`[data-flow-step="${stepId}"]`)
  target?.scrollIntoView({ block: 'nearest', inline: 'center', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
}
function startPan(event: PointerEvent) {
  if (event.button !== 0 || !viewport.value || (event.target as HTMLElement).closest('button, a, input, select, textarea')) return
  dragging = { x: event.clientX, y: event.clientY, left: viewport.value.scrollLeft, top: viewport.value.scrollTop }
  viewport.value.setPointerCapture(event.pointerId)
  follow.value = false
}
function pan(event: PointerEvent) {
  if (!dragging || !viewport.value) return
  viewport.value.scrollLeft = dragging.left - (event.clientX - dragging.x)
  viewport.value.scrollTop = dragging.top - (event.clientY - dragging.y)
}
function endPan() { dragging = null }
function wheel(event: WheelEvent) {
  if (!event.ctrlKey && !event.metaKey) return
  event.preventDefault()
  changeZoom(zoom.value + (event.deltaY > 0 ? -0.1 : 0.1))
}
function onKey(event: KeyboardEvent) {
  if (!dialogOpen.value || !dialog.value) return
  const dialogs = document.querySelectorAll('[role="dialog"]')
  if (dialogs[dialogs.length - 1] !== dialog.value) return
  if (event.key === 'Escape' && (event.target as HTMLElement | null)?.tagName === 'INPUT') return
  if (event.key === 'Escape') {
    event.preventDefault()
    event.stopImmediatePropagation()
    closeDialog()
  } else if (event.key === 'Tab') {
    const items = [...dialog.value.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]')].filter((element) => element.getClientRects().length)
    const first = items[0]
    const last = items.at(-1)
    if (!first) { event.preventDefault(); dialog.value.focus() }
    else if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.value || !dialog.value.contains(document.activeElement))) { event.preventDefault(); last?.focus() }
    else if (!event.shiftKey && (document.activeElement === last || !dialog.value.contains(document.activeElement))) { event.preventDefault(); first.focus() }
  }
}
watch(dialogOpen, async (opened) => {
  if (opened) {
    returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    locked = true
    await nextTick()
    dialog.value?.focus()
  } else {
    if (locked) document.body.style.overflow = previousOverflow
    locked = false
    if (returnFocus?.isConnected) returnFocus.focus()
  }
})
watch(() => fsCurrentStep.value?.step_id, () => { if (fullscreen.value && follow.value) void locateCurrent() })
watch(fsFlow, async () => { if (fullscreen.value) { await nextTick(); measure() } })
defineExpose({ openFullscreen })
watch([() => props.context.recipeId || props.context.recipe?.id, () => props.context.runId], () => { selection.value = null })
watch([() => route.query[FLOW_QUERY], flowKey], syncFromRoute)
onMounted(() => {
  window.addEventListener('keydown', onKey, true)
  syncFromRoute()
})
onBeforeUnmount(() => {
  resetFullscreenRun()
  openedFlows.delete(flowInstance)
  observer?.disconnect()
  window.removeEventListener('keydown', onKey, true)
  if (locked) document.body.style.overflow = previousOverflow
})
</script>

<template>
  <div class="recipe-flow-viewer min-w-0">
    <div v-if="toolbarPlacement !== 'hidden'" class="recipe-flow-toolbar flex min-h-6 items-center justify-end gap-1" :class="toolbarPlacement === 'inline' ? 'mb-2' : 'recipe-flow-toolbar--card'">
      <span v-if="running && toolbarPlacement === 'inline'" class="mr-auto flex min-w-0 items-center gap-1.5 text-[11px] text-info" role="status"><LoaderCircle class="size-3 shrink-0 animate-spin" /><span class="truncate"><template v-if="context.batch">第 {{ context.batch.index }}/{{ context.batch.total }} 次 · </template>{{ currentStep?.name || '执行中' }}</span></span>
      <!-- 对话消息里的流程卡片只留全屏，修改和执行都在全屏里 -->
      <button v-if="canEdit && toolbarPlacement !== 'card'" type="button" class="btn-text gap-1 px-2 py-1 text-[11px]" :disabled="running || openingEdit" @click="editFlow('modify', { kind: 'workflow' })"><MessageSquare class="size-3.5" />对话修改</button>
      <button v-if="canReplay && toolbarPlacement !== 'card'" type="button" class="btn-text gap-1 px-2 py-1 text-[11px]" :disabled="running" @click="prepareRun"><Play class="size-3.5" />重新执行</button>
      <button type="button" class="btn-text gap-1 px-2 py-1 text-[11px]" aria-label="全屏查看流程图" aria-haspopup="dialog" @click="openFullscreen"><Maximize2 class="size-3.5" />全屏</button>
    </div>
    <RecipeFlowCanvas v-if="inlineCanvas" :flow="shownFlow" :states="states" :direction="direction" :compact="compact" :active-step="selection?.kind === 'step' ? selection.id : activeStep" :selected-file-key="selection?.kind === 'file' ? selection.file.key : null" :busy="running" :failed="failed" @select="selectStep" @select-file="selectFile" />
  </div>

  <Teleport v-if="dialogOpen" to="body">
    <div class="flow-layer" :class="{ 'flow-layer--detail': !fullscreen }" @click.self="closeDialog">
      <section ref="dialog" role="dialog" aria-modal="true" :aria-label="fullscreen ? `${context.title || '处理流程'} · 全屏流程图` : '流程节点详情'" tabindex="-1" class="flow-dialog" :class="fullscreen ? 'flow-dialog--full' : 'flow-dialog--detail'">
        <template v-if="fullscreen">
          <div ref="workspaceEl" class="flow-workspace" :class="{ 'is-resizing': resizingSide }">
            <div class="flow-canvas-area app-panel" :class="{ 'has-side': !!selection }">
              <header class="flow-header">
                <div class="flow-header-title">
                  <div class="flow-title-pill">
                    <h2 class="truncate text-[15px] font-semibold tracking-tight text-ink" :title="context.title || '处理流程'">{{ context.title || '处理流程' }}</h2>
                    <span v-if="context.version" class="shrink-0 rounded-full border border-line bg-paper px-2 py-0.5 text-[11px] font-medium tabular-nums text-mute">v{{ context.version }}</span>
                    <span v-if="fsBatchInfo" class="flow-batch-tag" role="status" :title="`第 ${fsBatchInfo.index}/${fsBatchInfo.total} 次 · ${fsBatchInfo.filename}`">
                      <LoaderCircle v-if="fsViewRunning" class="size-3.5 shrink-0 animate-spin text-info" />
                      <span class="shrink-0 tabular-nums">第 {{ fsBatchInfo.index }}/{{ fsBatchInfo.total }} 次</span>
                      <span class="truncate text-mute">{{ fsBatchInfo.filename }}</span>
                    </span>
                  </div>
                </div>
                <div class="flow-header-actions">
                  <button
                    v-if="canEdit"
                    type="button"
                    class="flow-action-pill"
                    aria-label="对话修改"
                    title="对话修改"
                    :disabled="fsRunning || openingEdit"
                    @click="editFlow('modify', { kind: 'workflow' })"
                  >
                    <MessageCircleMore class="size-4 shrink-0" aria-hidden="true" />
                    <span class="hidden sm:inline">对话修改</span>
                  </button>
                  <button
                    v-if="canReplay"
                    type="button"
                    class="flow-action-pill flow-action-pill--primary"
                    :aria-label="fsRunning ? '执行中' : fsHasRun ? '重新执行' : '执行'"
                    :title="fsRunning ? '执行中' : fsHasRun ? '重新执行' : '执行'"
                    :disabled="fsRunning || preparingRun"
                    @click="prepareRun"
                  >
                    <LoaderCircle v-if="fsRunning || preparingRun" class="size-4 shrink-0 animate-spin" aria-hidden="true" />
                    <Play v-else class="size-3.5 shrink-0" aria-hidden="true" />
                    <span class="hidden sm:inline">{{ fsRunning ? '执行中' : fsHasRun ? '重新执行' : '执行' }}</span>
                  </button>
                  <button
                    type="button"
                    class="flow-action flow-header-close"
                    aria-label="退出全屏"
                    title="退出全屏 · Esc"
                    @click="closeDialog"
                  >
                    <X class="size-4" aria-hidden="true" />
                  </button>
                </div>
              </header>
              <div ref="viewport" class="flow-viewport" tabindex="0" aria-label="流程画布，可滚动和拖动" @pointerdown="startPan" @pointermove="pan" @pointerup="endPan" @pointercancel="endPan" @lostpointercapture="endPan" @wheel="wheel">
                <div class="flow-stage" :style="stageStyle">
                  <div ref="canvas" class="flow-canvas" :style="canvasStyle">
                    <RecipeFlowCanvas :flow="fsFlow" :states="fsStates" :active-step="selection?.kind === 'step' ? selection.id : null" :selected-file-key="selection?.kind === 'file' ? selection.file.key : null" :busy="fsViewRunning" :failed="fsViewFailed" @select="selectStep" @select-file="selectFile" />
                  </div>
                </div>
              </div>
              <div class="flow-tools">
                <button class="icon-btn size-8" type="button" aria-label="缩小流程图" :disabled="zoom <= 0.25" @click="changeZoom(zoom - 0.1)"><Minus class="size-4" /></button>
                <span class="w-11 text-center text-[11px] tabular-nums text-mute">{{ Math.round(zoom * 100) }}%</span>
                <button class="icon-btn size-8" type="button" aria-label="放大流程图" :disabled="zoom >= 1.6" @click="changeZoom(zoom + 0.1)"><Plus class="size-4" /></button>
                <span class="mx-1 h-4 w-px bg-line" />
                <button class="icon-btn size-8" type="button" aria-label="重置视图" title="重置视图" @click="resetView"><RotateCcw class="size-4" /></button>
                <button v-if="fsCurrentStep" class="icon-btn size-8" :class="{ '!text-info !bg-info/10': follow }" type="button" aria-label="跟随执行步骤" title="跟随执行" @click="locateCurrent"><LocateFixed class="size-4" /></button>
              </div>

              <!-- 右下角执行情况与快捷结果（未执行时不显示，关闭全屏即重置） -->
              <div v-if="fsHasRun" class="flow-status-dock">
                <div v-if="fsItems.length > 1 && fsBatchOpen" class="flow-batch-popover" role="region" aria-label="批量执行列表">
                  <div class="flex items-center justify-between border-b border-line px-3 py-2 text-xs">
                    <span class="font-semibold text-ink">批量执行 · {{ fsBatchFinished }}/{{ fsItems.length }}</span>
                    <div class="flex items-center gap-1.5">
                      <button v-if="fsRunning" type="button" class="btn-text h-6 px-2 py-0 text-[11px]" :disabled="fsStopping" @click="fsStopping = true">
                        <Square class="size-3" />{{ fsStopping ? '停止中' : '停止' }}
                      </button>
                      <button v-else-if="fsBatchRetryable" type="button" class="btn-text h-6 px-2 py-0 text-[11px]" @click="retryFullscreenFailed">
                        <RotateCcw class="size-3" />重跑未成功
                      </button>
                    </div>
                  </div>
                  <ol class="max-h-52 divide-y divide-[#f2f1ee] overflow-y-auto">
                    <li
                      v-for="(item, index) in fsItems"
                      :key="index"
                      class="flex items-center gap-2 px-3 py-2 text-xs transition-colors"
                      :class="fsViewIndex === index ? 'bg-paper/80' : 'hover:bg-paper/45'"
                    >
                      <span class="w-4 shrink-0 text-right tabular-nums text-faint">{{ index + 1 }}</span>
                      <button
                        type="button"
                        class="min-w-0 flex-1 truncate text-left font-medium"
                        :class="item.state === 'wait' ? 'cursor-default text-mute' : 'text-ink'"
                        :title="item.title"
                        @click="item.state !== 'wait' && (fsViewing = index)"
                      >{{ item.title }}</button>
                      <LoaderCircle v-if="item.state === 'run'" class="size-3.5 shrink-0 animate-spin text-info" />
                      <Check v-else-if="item.state === 'done'" class="size-3.5 shrink-0 text-success" />
                      <CircleAlert v-else-if="item.state === 'failed'" class="size-3.5 shrink-0 text-error" />
                      <span v-else class="shrink-0 text-[11px] text-faint">{{ item.state === 'skipped' ? '已跳过' : '等待' }}</span>
                      <template v-if="item.files.length">
                        <button type="button" class="icon-btn size-6 shrink-0" title="查看结果" @click="openFilePreview(item.files[0]!.file_id)"><Eye class="size-3.5" /></button>
                        <a :href="item.files[0]!.download_url" class="icon-btn size-6 shrink-0" title="下载结果"><Download class="size-3.5" /></a>
                      </template>
                    </li>
                  </ol>
                </div>

                <div class="flow-run-status" :class="{ 'is-failed': fsViewFailed && !fsViewRunning, 'is-success': !fsViewRunning && !fsViewFailed && fsCompleted === fsFlow.steps.length }" role="status" aria-live="polite">
                  <LoaderCircle v-if="fsViewRunning" class="size-3.5 shrink-0 animate-spin text-info" />
                  <CircleAlert v-else-if="fsViewFailed" class="size-3.5 shrink-0 text-error" />
                  <Check v-else-if="fsCompleted === fsFlow.steps.length" class="size-3.5 shrink-0 text-success" />
                  <span class="truncate font-medium text-ink">
                    {{ fsCurrentStep?.name || (fsViewFailed ? '执行失败' : fsViewRunning ? '正在生成结果' : fsCompleted === fsFlow.steps.length ? '执行完成' : '等待执行') }}
                  </span>
                  <span v-if="fsViewFailed && !fsViewRunning && fsErrorText" class="hidden max-w-[200px] truncate text-error sm:inline" :title="fsErrorText">{{ fsErrorText }}</span>
                  <span class="shrink-0 tabular-nums text-faint">{{ fsCompleted }}/{{ fsFlow.steps.length }}</span>

                  <template v-if="fsItems.length > 1">
                    <span class="h-3.5 w-px shrink-0 bg-line" />
                    <button type="button" class="flow-hud-btn" @click="fsBatchOpen = !fsBatchOpen">
                      <span class="tabular-nums">批量 {{ fsBatchFinished }}/{{ fsItems.length }}</span>
                      <span v-if="fsBatchFailedCount" class="text-error">({{ fsBatchFailedCount }}失败)</span>
                      <ChevronDown class="size-3.5 transition-transform" :class="{ 'rotate-180': fsBatchOpen }" />
                    </button>
                  </template>

                  <template v-if="!fsViewRunning && fsOutputFiles.length">
                    <span class="h-3.5 w-px shrink-0 bg-line" />
                    <button type="button" class="flow-hud-btn" title="查看结果文件" @click="openFilePreview(fsOutputFiles[0]!.file_id)">
                      <Eye class="size-3.5" />查看
                    </button>
                    <a :href="fsOutputFiles[0]!.download_url" class="flow-hud-btn is-primary" title="下载结果文件">
                      <Download class="size-3.5" />下载
                    </a>
                  </template>
                </div>
              </div>
            </div>
            <div v-if="selection" class="flow-side" :style="{ '--side-w': `${sideWidth}px` }">
              <div
                class="flow-side-resizer" :class="{ 'is-active': resizingSide }" role="separator" aria-orientation="vertical" aria-label="调整详情面板宽度"
                :aria-valuenow="sideWidth" :aria-valuemin="SIDE_MIN" :aria-valuemax="SIDE_MAX" tabindex="0" title="拖动调整宽度，双击恢复默认"
                @pointerdown="startSideResize" @pointermove="moveSideResize" @pointerup="endSideResize" @pointercancel="endSideResize" @keydown="keySideResize" @dblclick="resetSideWidth"
              />
              <aside class="flow-side-panel app-panel" aria-label="节点详情">
                <RecipeFlowInspector v-if="selection" :selection="selection" :flow="fsFlow" :states="fsStates" :context="fsContext" :recipe="fsContext.recipe" :files="fsContext.files" :editable="canEdit && !fsRunning && !openingEdit" @edit="(operation, options) => editFlow(operation, undefined, options?.focus)" @close="selection = null" @preview="openFilePreview" />
              </aside>
            </div>
          </div>
        </template>
        <RecipeFlowInspector v-else-if="selection" :selection="selection" :flow="shownFlow" :states="states" :context="context" :recipe="context.recipe" :files="context.files" :editable="canEdit && !running && !openingEdit" @edit="(operation, options) => editFlow(operation, undefined, options?.focus)" @close="closeDialog" @preview="openFilePreview" />
      </section>
    </div>
  </Teleport>
  <RunRecipeDialog
    v-if="runRecipe"
    :customer-id="context.customerId" :conversation-id="context.conversationId" :recipe="runRecipe" :pick-only="fullscreen" elevated
    @close="runRecipe = null" @start="startFullscreenRun" @done="emit('changed')"
  />
</template>

<style scoped>
.recipe-flow-toolbar--card { position: absolute; top: 12px; right: 12px; z-index: 2; }
.flow-layer { position: fixed; inset: 0; z-index: 4000; background: var(--color-canvas); }
.flow-layer--detail { display: flex; align-items: center; justify-content: center; padding: 24px; background: #0b0b0c35; backdrop-filter: blur(3px); }
.flow-dialog { min-width: 0; background: var(--color-canvas); outline: none; }
.flow-dialog--full { display: flex; height: 100dvh; flex-direction: column; }
.flow-dialog--detail { display: flex; flex-direction: column; width: clamp(360px, 42vw, 560px); max-width: 100%; max-height: 88dvh; overflow: hidden; border: 1px solid #e4e3e1; border-radius: 20px; background: var(--color-panel); box-shadow: 0 24px 80px -20px rgba(32, 28, 24, 0.22); }
/* 浮在画布上的轻量标题与操作区：空白处点击穿透到画布，拖动不受影响 */
.flow-header { position: absolute; inset: 0 0 auto 0; z-index: 3; display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 12px; padding: 16px 20px; pointer-events: none; }
.flow-header-title { display: flex; min-width: 0; max-width: min(52vw, 540px); grid-column: 2; align-items: center; justify-content: center; }
.flow-canvas-area.has-side .flow-header { grid-template-columns: minmax(0, 1fr) auto; }
.flow-canvas-area.has-side .flow-header-title { grid-column: 1; justify-content: flex-start; max-width: 100%; }
.flow-canvas-area.has-side .flow-header-actions { grid-column: 2; }
.flow-title-pill { display: inline-flex; min-width: 0; max-width: 100%; align-items: center; gap: 8px; border: 1px solid #e4e3e1; border-radius: 9999px; background: rgba(255, 255, 255, 0.92); backdrop-filter: blur(10px); padding: 7px 16px; box-shadow: 0 4px 16px -6px rgba(60, 50, 40, 0.08); pointer-events: auto; }
.flow-batch-tag { display: inline-flex; min-width: 0; max-width: 220px; align-items: center; gap: 6px; border-radius: 9999px; background: var(--color-paper); padding: 3px 10px; font-size: 11px; line-height: 1.4; color: var(--color-ink); }
.flow-header-actions { display: flex; grid-column: 3; align-items: center; justify-content: flex-end; gap: 8px; }
.flow-action-pill { display: inline-flex; height: 36px; flex: none; align-items: center; justify-content: center; gap: 6px; border: 1px solid #e4e3e1; border-radius: 9999px; background: rgba(255, 255, 255, 0.94); backdrop-filter: blur(10px); padding: 0 14px; font-size: 12.5px; font-weight: 500; color: var(--color-ink); box-shadow: 0 4px 14px -6px rgba(60, 50, 40, 0.08); pointer-events: auto; transition: background-color .18s, border-color .18s, color .18s, transform .15s; }
.flow-action-pill:hover { border-color: #d4d1cb; background: var(--color-paper); }
.flow-action-pill:active { transform: scale(0.98); }
.flow-action-pill:focus-visible { outline: 2px solid var(--color-ink); outline-offset: 2px; }
.flow-action-pill:disabled { cursor: default; opacity: .45; }
.flow-action-pill--primary { border-color: var(--color-ink); background: var(--color-ink); color: #ffffff; box-shadow: 0 6px 16px -8px rgba(11, 11, 12, 0.35); }
.flow-action-pill--primary:hover { border-color: #26262a; background: #26262a; color: #ffffff; }
.flow-action { display: inline-flex; width: 36px; height: 36px; flex: none; align-items: center; justify-content: center; border: 1px solid #e4e3e1; border-radius: 9999px; background: rgba(255, 255, 255, 0.94); backdrop-filter: blur(10px); color: var(--color-mute); box-shadow: 0 4px 14px -6px rgba(60, 50, 40, 0.08); pointer-events: auto; transition: background-color .18s, border-color .18s, color .18s; }
.flow-action:hover { border-color: #d4d1cb; background: var(--color-paper); color: var(--color-ink); }
.flow-action:focus-visible { outline: 2px solid var(--color-ink); outline-offset: 2px; }
.flow-action:disabled { cursor: default; opacity: .45; }
/* 与主界面 AppShell / ChatView 完全一致的暖灰底 + 悬浮圆角白面板 */
.flow-workspace { display: flex; min-height: 0; flex: 1; gap: 8px; padding: 8px; background: var(--color-canvas); }
.flow-canvas-area { position: relative; min-width: 0; min-height: 0; flex: 1; overflow: hidden; border-radius: 14px; background-color: var(--color-panel); background-image: radial-gradient(rgba(120, 113, 104, 0.16) 1px, transparent 1px); background-size: 24px 24px; }
.flow-viewport { width: 100%; height: 100%; overflow: auto; scrollbar-gutter: stable both-edges; overscroll-behavior: contain; cursor: grab; touch-action: pan-x pan-y; }
.flow-viewport:active { cursor: grabbing; }
.flow-viewport:focus-visible { outline: 2px solid var(--color-ink); outline-offset: -3px; }
.flow-stage { position: relative; }
.flow-canvas { position: absolute; width: max-content; transform-origin: top left; padding: 4px; }
.flow-canvas :deep(.recipe-flow-input:not(.is-selected)),
.flow-canvas :deep(.recipe-flow-step:not(.is-active):not(.is-completed):not(.is-warning):not(.is-failed):not(.is-selected)) { border-color: #e4e3e1; box-shadow: 0 2px 10px -4px rgba(60, 50, 40, 0.07), 0 1px 2px rgba(60, 50, 40, 0.03); }
.flow-canvas :deep(.recipe-flow-output) { box-shadow: 0 10px 22px -12px rgba(11, 11, 12, 0.42); }
.flow-tools { position: absolute; left: 18px; bottom: 18px; display: flex; align-items: center; padding: 4px; border: 1px solid #e4e3e1; border-radius: 12px; background: rgba(255, 255, 255, 0.94); backdrop-filter: blur(10px); box-shadow: 0 4px 16px -6px rgba(60, 50, 40, 0.09); }
.flow-status-dock { position: absolute; right: 18px; bottom: 18px; z-index: 3; display: flex; max-width: calc(100% - 260px); flex-direction: column; align-items: flex-end; gap: 8px; }
.flow-batch-popover { width: min(360px, 82vw); overflow: hidden; border: 1px solid #e4e3e1; border-radius: 14px; background: rgba(255, 255, 255, 0.96); backdrop-filter: blur(12px); box-shadow: 0 12px 32px -10px rgba(32, 28, 24, 0.16); }
.flow-run-status { display: flex; max-width: 100%; align-items: center; gap: 8px; border: 1px solid #e4e3e1; border-radius: 12px; padding: 6px 12px; min-height: 40px; background: rgba(255, 255, 255, 0.95); backdrop-filter: blur(10px); box-shadow: 0 4px 16px -6px rgba(60, 50, 40, 0.09); font-size: 12px; }
.flow-run-status.is-success { border-color: #cbead7; background: rgba(246, 253, 249, 0.96); }
.flow-run-status.is-failed { border-color: #f6cdcd; background: rgba(254, 246, 246, 0.96); }
.flow-hud-btn { display: inline-flex; height: 26px; flex: none; align-items: center; gap: 4px; border-radius: 8px; padding: 0 8px; font-size: 12px; font-weight: 500; color: var(--color-ink); transition: background-color .15s, color .15s; }
.flow-hud-btn:hover { background: rgba(20, 20, 23, 0.06); }
.flow-hud-btn.is-primary { border-radius: 9999px; background: var(--color-ink); padding: 0 10px; color: #fff; }
.flow-hud-btn.is-primary:hover { background: #26262a; }
.flow-workspace.is-resizing { cursor: col-resize; user-select: none; }
.flow-side { position: relative; width: var(--side-w, 390px); min-width: 280px; max-width: calc(100% - 320px); min-height: 0; flex: none; }
.flow-side-panel { width: 100%; height: 100%; min-height: 0; overflow-y: auto; border-radius: 14px; background: var(--color-panel); }
/* 拖动条：落在两块悬浮面板的 8px 缝隙里，悬停和拖动时亮起柔和细线 */
.flow-side-resizer { position: absolute; top: 12px; bottom: 12px; left: -9px; z-index: 3; width: 10px; cursor: col-resize; touch-action: none; }
.flow-side-resizer::after { content: ''; position: absolute; top: 0; bottom: 0; left: 4px; width: 2px; border-radius: 9999px; background: transparent; transition: background .15s; }
.flow-side-resizer:hover::after, .flow-side-resizer.is-active::after, .flow-side-resizer:focus-visible::after { background: var(--color-ink); }
.flow-side-resizer:focus-visible { outline: none; }
@media (max-width: 760px) {
  .recipe-flow-toolbar--card { top: 10px; right: 10px; }
  .flow-workspace { position: relative; padding: 0; gap: 0; }
  .flow-canvas-area { border-radius: 0; border: none; box-shadow: none; }
  .flow-header { padding: 12px; }
  .flow-title-pill { padding: 6px 12px; }
  .flow-side { position: absolute; inset: 0 0 0 auto; z-index: 2; width: min(390px, 100%); min-width: 0; max-width: 100%; box-shadow: -12px 0 40px rgba(32, 28, 24, 0.12); }
  .flow-side-panel { border-radius: 0; border-right: none; border-top: none; border-bottom: none; }
  .flow-side-resizer { display: none; }
  .flow-tools { left: 12px; bottom: 12px; }
  .flow-status-dock { top: 62px; right: 12px; bottom: auto; max-width: calc(100% - 24px); }
  .flow-layer--detail { padding: 12px; }
}
@media (max-width: 640px) {
  .recipe-flow-toolbar--card { top: 46px; left: 12px; right: 12px; }
  .flow-header { grid-template-columns: 1fr auto; }
  .flow-header-title { grid-column: 1; justify-content: flex-start; max-width: 100%; }
  .flow-header-actions { grid-column: 2; gap: 6px; }
  .flow-action-pill { width: 36px; padding: 0; }
}
</style>
