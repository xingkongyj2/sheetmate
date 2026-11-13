<script setup lang="ts">
/** A flow stays on its page: shared canvas, fullscreen layer, resource inspector and live replay. */
import { Check, CircleAlert, Focus, LoaderCircle, LocateFixed, Maximize2, MessageSquare, Minus, Play, Plus, X } from '@lucide/vue'
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { conversationApi, recipeApi } from '@/api'
import { ApiError, errorText } from '@/api/http'
import type { FileInfo, RecipeEditRequest, RecipeFlowData, RecipeJSON } from '@/api/types'
import type { StepState } from '@/composables/chatModel'
import { progressFlow } from '@/composables/recipeFlow'
import { progressDeliveryFiles } from '@/composables/deliveryFiles'
import type { FlowFileNode, FlowSelection, RecipeFlowContext } from '@/composables/recipeFlowInteraction'
import { useFlowReplay } from '@/composables/useFlowReplay'
import { conversationMatchesRecipeEdit, recipeEditTarget } from '@/composables/recipeEditing'
import { useToast } from '@/composables/useToast'
import { useWorkspaceStore } from '@/stores/conversations'
import { pendingRecipeEdits } from '@/stores/recipeEdits'

import RecipeFlowCanvas from './RecipeFlowCanvas.vue'
import RecipeFlowInspector from './RecipeFlowInspector.vue'
import RecipeFlowRunSetup from './RecipeFlowRunSetup.vue'

const WorkbookPreview = defineAsyncComponent(() => import('@/features/workbook-preview/WorkbookPreview.vue'))
const props = withDefaults(defineProps<{
  flow: RecipeFlowData
  states?: StepState[]
  direction?: 'horizontal' | 'vertical'
  activeStep?: number | null
  compact?: boolean
  context?: RecipeFlowContext
  toolbarPlacement?: 'inline' | 'card' | 'hidden'
}>(), { states: () => [], direction: 'horizontal', activeStep: null, compact: false, context: () => ({}), toolbarPlacement: 'inline' })
const emit = defineEmits<{ changed: [] }>()
const router = useRouter()
const store = useWorkspaceStore()
const toast = useToast()
const openingEdit = ref(false)
const fullscreen = ref(false)
const selection = ref<FlowSelection | null>(null)
const preview = ref<FileInfo | null>(null)
const dialog = ref<HTMLElement | null>(null)
const viewport = ref<HTMLElement | null>(null)
const canvas = ref<HTMLElement | null>(null)
const zoom = ref(1)
const follow = ref(true)
const dimensions = ref({ width: 1, height: 1, viewportWidth: 1, viewportHeight: 1 })
const replay = useFlowReplay(() => props.context, () => props.flow)
const shownFlow = computed(() => replay.progress.value
  ? progressFlow({ ...replay.progress.value, flow: replay.progress.value.flow ?? replay.flow.value ?? props.flow }) : props.flow)
const states = computed(() => {
  const steps = replay.progress.value?.steps ?? props.states
  const stoppedWithError = replay.progress.value ? replay.progress.value.status === 'failed' : props.context.failed
  return stoppedWithError ? steps.map((step) => ['running', 'repairing'].includes(step.status) ? { ...step, status: 'failed' as const } : step) : steps
})
const running = computed(() => replay.running.value || (!replay.progress.value && (props.context.running ?? states.value.some((step) => ['running', 'repairing'].includes(step.status)))))
const context = computed<RecipeFlowContext>(() => {
  const progress = replay.progress.value
  if (!progress) return props.context
  return {
    ...props.context,
    title: progress.recipeName || replay.executionRecipe.value?.name || props.context.title,
    recipeId: progress.saved?.recipe_id ?? progress.recipeId ?? props.context.recipeId,
    version: progress.saved?.version ?? progress.recipeVersion ?? replay.executionRecipe.value?.version ?? props.context.version,
    recipe: replay.executionRecipe.value ?? progress.recipe ?? props.context.recipe,
    runId: progress.runId,
    resultFiles: progressDeliveryFiles(progress),
    files: replay.executionFiles.value,
    running: running.value,
  }
})
const currentStep = computed(() => running.value ? states.value.find((step) => ['running', 'repairing'].includes(step.status)) : undefined)
const completed = computed(() => states.value.filter((step) => ['success', 'warning', 'repaired'].includes(step.status)).length)
const failed = computed(() => (replay.progress.value ? replay.progress.value.status === 'failed' : props.context.failed) || states.value.some((step) => step.status === 'failed'))
const canReplay = computed(() => !context.value.deleted && !!(context.value.recipeId || context.value.recipe?.id || context.value.runId))
const canEdit = computed(() => !context.value.deleted && !!(context.value.recipeId || context.value.recipe?.id))
const dialogOpen = computed(() => fullscreen.value || !!selection.value)
const stageSize = computed(() => ({
  width: Math.max(dimensions.value.viewportWidth, dimensions.value.width * zoom.value + 96),
  height: Math.max(dimensions.value.viewportHeight, dimensions.value.height * zoom.value + 96),
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
  zoom.value = Math.max(0.25, Math.min(1, (d.viewportWidth - 96) / d.width, (d.viewportHeight - 96) / d.height))
}
function changeZoom(value: number) {
  zoom.value = Math.max(0.25, Math.min(1.6, value))
  follow.value = false
}
async function openFullscreen() {
  fullscreen.value = true
  follow.value = true
  await nextTick()
  measure()
  fit()
  observer?.disconnect()
  observer = new ResizeObserver(measure)
  if (viewport.value) observer.observe(viewport.value)
  if (canvas.value) observer.observe(canvas.value)
}
function closeDialog() {
  selection.value = null
  fullscreen.value = false
  replay.closeSetup()
  observer?.disconnect()
}
function selectStep(id: number) {
  replay.closeSetup()
  selection.value = { kind: 'step', id }
}
function selectFile(file: FlowFileNode) {
  replay.closeSetup()
  selection.value = { kind: 'file', file }
}
async function editFlow(operation: RecipeEditRequest['operation'] = 'modify', target = recipeEditTarget(selection.value), focus?: 'sql' | 'python' | 'formula') {
  if (!canEdit.value || running.value || openingEdit.value) return
  openingEdit.value = true
  const current = context.value
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
    const stepName = target.kind === 'step' ? shownFlow.value.steps.find((item) => item.id === target.step_id)?.name : undefined
    const request: RecipeEditRequest = { recipe_id: recipeId, version, operation,
      target: focus && target.kind === 'step' ? { ...target, focus, ...(stepName ? { name: stepName } : {}) } : target }
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
    closeDialog()
    await router.push({ name: 'chat', params: { id: conversationId } })
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    openingEdit.value = false
  }
}
async function prepareRun() {
  if (running.value) return
  if (!fullscreen.value) await openFullscreen()
  selection.value = null
  await replay.prepare()
}
function runStarted() {
  selection.value = null
  follow.value = true
}
async function locateCurrent() {
  follow.value = true
  await nextTick()
  const target = canvas.value?.querySelector<HTMLElement>(`[data-flow-step="${currentStep.value?.step_id}"]`)
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
  if (!dialogOpen.value || preview.value || !dialog.value) return
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
watch(() => currentStep.value?.step_id, () => { if (fullscreen.value && follow.value) void locateCurrent() })
defineExpose({ openFullscreen })
watch(replay.running, (value, previous) => { if (previous && !value) emit('changed') })
watch([() => props.context.recipeId || props.context.recipe?.id, () => props.context.runId], ([recipeId, runId], previous) => {
  selection.value = null
  replay.closeSetup()
  if (!replay.running.value && (recipeId !== previous[0] || runId !== replay.progress.value?.runId)) replay.progress.value = null
})
watch(() => props.context.running, (value) => {
  if (value && !replay.running.value) replay.progress.value = null
})
onMounted(() => window.addEventListener('keydown', onKey, true))
onBeforeUnmount(() => {
  observer?.disconnect()
  window.removeEventListener('keydown', onKey, true)
  if (locked) document.body.style.overflow = previousOverflow
})
</script>

<template>
  <div class="recipe-flow-viewer min-w-0">
    <div v-if="toolbarPlacement !== 'hidden'" class="recipe-flow-toolbar flex min-h-6 items-center justify-end gap-1" :class="toolbarPlacement === 'inline' ? 'mb-2' : 'recipe-flow-toolbar--card'">
      <span v-if="running && toolbarPlacement === 'inline'" class="mr-auto flex min-w-0 items-center gap-1.5 text-[11px] text-[#6265DC]" role="status"><LoaderCircle class="size-3 shrink-0 animate-spin" /><span class="truncate">{{ currentStep?.name || '执行中' }}</span></span>
      <button v-if="canEdit" type="button" class="btn-text gap-1 px-2 py-1 text-[11px]" :disabled="running || openingEdit" @click="editFlow('modify', { kind: 'workflow' })"><MessageSquare class="size-3.5" />对话修改</button>
      <button v-if="canReplay" type="button" class="btn-text gap-1 px-2 py-1 text-[11px]" :disabled="running" @click="prepareRun"><Play class="size-3.5" />重新执行</button>
      <button type="button" class="btn-text gap-1 px-2 py-1 text-[11px]" aria-label="全屏查看流程图" aria-haspopup="dialog" @click="openFullscreen"><Maximize2 class="size-3.5" />全屏</button>
    </div>
    <RecipeFlowCanvas :flow="shownFlow" :states="states" :direction="direction" :compact="compact" :active-step="selection?.kind === 'step' ? selection.id : activeStep" :selected-file-key="selection?.kind === 'file' ? selection.file.key : null" :busy="running" :failed="failed" @select="selectStep" @select-file="selectFile" />
  </div>

  <Teleport v-if="dialogOpen" to="body">
    <div class="flow-layer" :class="{ 'flow-layer--detail': !fullscreen }" @click.self="closeDialog">
      <section ref="dialog" role="dialog" aria-modal="true" :aria-label="fullscreen ? `${context.title || '处理流程'} · 全屏流程图` : '流程节点详情'" tabindex="-1" class="flow-dialog" :class="fullscreen ? 'flow-dialog--full' : 'flow-dialog--detail'">
        <template v-if="fullscreen">
          <header class="flow-header">
            <div class="flow-header-title flex min-w-0 flex-1 items-center gap-2.5">
              <span class="flex size-8 shrink-0 items-center justify-center rounded-xl bg-[#6265DC]/10 text-[#6265DC]"><Focus class="size-4" /></span>
              <h2 class="truncate text-[15px] font-semibold">{{ context.title || '处理流程' }}</h2>
              <span v-if="context.version" class="tag shrink-0 !bg-[#6265DC]/10 !text-[#6265DC]">v{{ context.version }}</span>
            </div>
            <button v-if="canEdit" type="button" class="btn-text gap-1 px-2 text-xs" :disabled="running || openingEdit" @click="editFlow('add', { kind: 'input' })"><Plus class="size-3.5" />输入</button>
            <button v-if="canEdit" type="button" class="btn-text gap-1 px-2 text-xs" :disabled="running || openingEdit" @click="editFlow('add', { kind: 'output' })"><Plus class="size-3.5" />输出</button>
            <button v-if="canEdit" type="button" class="btn-text gap-1 px-2 text-xs" :disabled="running || openingEdit" @click="editFlow('modify', { kind: 'workflow' })"><MessageSquare class="size-3.5" />对话修改</button>
            <button v-if="canReplay" type="button" class="flow-run-button" :disabled="running || replay.loading.value" @click="prepareRun"><LoaderCircle v-if="running" class="size-3.5 animate-spin" /><Play v-else class="size-3.5" /><span>{{ running ? '执行中' : '重新执行' }}</span></button>
            <button type="button" class="flow-header-close icon-btn ml-1 shrink-0" aria-label="退出全屏" title="退出全屏 · Esc" @click="closeDialog"><X class="size-5" /></button>
          </header>
          <div class="flow-workspace">
            <div class="flow-canvas-area">
              <div ref="viewport" class="flow-viewport" tabindex="0" aria-label="流程画布，可滚动和拖动" @pointerdown="startPan" @pointermove="pan" @pointerup="endPan" @pointercancel="endPan" @lostpointercapture="endPan" @wheel="wheel">
                <div class="flow-stage" :style="{ width: `${stageSize.width}px`, height: `${stageSize.height}px` }">
                  <div ref="canvas" class="flow-canvas" :style="canvasStyle">
                    <RecipeFlowCanvas :flow="shownFlow" :states="states" :active-step="selection?.kind === 'step' ? selection.id : null" :selected-file-key="selection?.kind === 'file' ? selection.file.key : null" :busy="running" :failed="failed" @select="selectStep" @select-file="selectFile" />
                  </div>
                </div>
              </div>
              <div class="flow-tools">
                <button class="icon-btn size-8" type="button" aria-label="缩小流程图" :disabled="zoom <= 0.25" @click="changeZoom(zoom - 0.1)"><Minus class="size-4" /></button>
                <span class="w-11 text-center text-[11px] tabular-nums text-mute">{{ Math.round(zoom * 100) }}%</span>
                <button class="icon-btn size-8" type="button" aria-label="放大流程图" :disabled="zoom >= 1.6" @click="changeZoom(zoom + 0.1)"><Plus class="size-4" /></button>
                <span class="mx-1 h-4 w-px bg-line" />
                <button class="icon-btn size-8" type="button" aria-label="适应画布" title="适应画布" @click="fit"><Maximize2 class="size-4" /></button>
                <button v-if="currentStep" class="icon-btn size-8" :class="{ '!text-[#6265DC] !bg-[#6265DC]/10': follow }" type="button" aria-label="跟随执行步骤" title="跟随执行" @click="locateCurrent"><LocateFixed class="size-4" /></button>
              </div>
              <div v-if="states.length || running || failed" class="flow-run-status" role="status" aria-live="polite">
                <LoaderCircle v-if="running" class="size-3.5 shrink-0 animate-spin text-[#6265DC]" /><CircleAlert v-else-if="failed" class="size-3.5 shrink-0 text-error" /><Check v-else-if="completed === shownFlow.steps.length" class="size-3.5 shrink-0 text-success" />
                <span class="truncate">{{ currentStep?.name || (failed ? '执行失败' : running ? '正在生成结果' : completed === shownFlow.steps.length ? '执行完成' : '等待执行') }}</span>
                <span class="shrink-0 tabular-nums text-faint">{{ completed }}/{{ shownFlow.steps.length }}</span>
              </div>
            </div>
            <aside v-if="replay.setupOpen.value || selection" class="flow-side-panel" :aria-label="replay.setupOpen.value ? '执行设置' : '节点详情'">
              <RecipeFlowRunSetup v-if="replay.setupOpen.value" :controller="replay" @close="replay.closeSetup()" @started="runStarted" @preview="(file) => (preview = file)" />
              <RecipeFlowInspector v-else-if="selection" :selection="selection" :flow="shownFlow" :states="states" :context="context" :recipe="context.recipe" :files="context.files" :editable="canEdit && !running && !openingEdit" @edit="(operation, options) => editFlow(operation, undefined, options?.focus)" @renamed="emit('changed')" @close="selection = null" @preview="(file) => (preview = file)" />
            </aside>
          </div>
          <div v-if="replay.error.value && !replay.setupOpen.value" class="border-t border-error/10 bg-error/5 px-5 py-2 text-xs text-error" role="alert">{{ replay.error.value }}</div>
        </template>
        <RecipeFlowInspector v-else-if="selection" :selection="selection" :flow="shownFlow" :states="states" :context="context" :recipe="context.recipe" :files="context.files" :editable="canEdit && !running && !openingEdit" @edit="(operation, options) => editFlow(operation, undefined, options?.focus)" @renamed="emit('changed')" @close="closeDialog" @preview="(file) => (preview = file)" />
      </section>
    </div>
  </Teleport>
  <WorkbookPreview v-if="preview" :key="preview.file_id" :file="preview" @close="preview = null" />
</template>

<style scoped>
.recipe-flow-toolbar--card { position: absolute; top: 12px; right: 12px; z-index: 2; }
.flow-layer { position: fixed; inset: 0; z-index: 4000; background: white; }
.flow-layer--detail { display: flex; align-items: center; justify-content: center; padding: 24px; background: #0b0b0c35; backdrop-filter: blur(3px); }
.flow-dialog { min-width: 0; background: white; outline: none; }
.flow-dialog--full { display: flex; height: 100dvh; flex-direction: column; }
.flow-dialog--detail { display: flex; flex-direction: column; width: clamp(360px, 42vw, 560px); max-width: 100%; max-height: 88dvh; overflow: hidden; border: 1px solid #ececef; border-radius: 24px; box-shadow: 0 24px 90px #22224225; }
.flow-header { display: flex; min-height: 68px; flex: none; align-items: center; gap: 10px; border-bottom: 1px solid #ececef; padding: 12px 24px; }
.flow-run-button { display: inline-flex; height: 34px; flex: none; align-items: center; justify-content: center; gap: 7px; border-radius: 10px; padding: 0 14px; background: #6265dc; color: white; font-size: 12px; font-weight: 500; transition: background .2s; }
.flow-run-button:hover { background: #5356c6; }
.flow-run-button:disabled { cursor: default; opacity: .55; }
.flow-workspace { display: flex; min-height: 0; flex: 1; }
.flow-canvas-area { position: relative; min-width: 0; min-height: 0; flex: 1; background-color: #fafafd; background-image: radial-gradient(#6265dc18 .8px, transparent .8px); background-size: 20px 20px; }
.flow-viewport { width: 100%; height: 100%; overflow: auto; overscroll-behavior: contain; cursor: grab; touch-action: pan-x pan-y; }
.flow-viewport:active { cursor: grabbing; }
.flow-viewport:focus-visible { outline: 2px solid #6265dc; outline-offset: -3px; }
.flow-stage { position: relative; }
.flow-canvas { position: absolute; width: max-content; transform-origin: top left; padding: 4px; }
.flow-tools { position: absolute; left: 20px; bottom: 20px; display: flex; align-items: center; padding: 4px; border: 1px solid #ececef; border-radius: 13px; background: white; box-shadow: 0 3px 18px #22224208; }
.flow-run-status { position: absolute; right: 20px; bottom: 26px; display: flex; max-width: calc(100% - 280px); align-items: center; gap: 8px; border: 1px solid #ececef; border-radius: 12px; padding: 9px 12px; background: white; font-size: 12px; }
.flow-side-panel { width: 390px; min-height: 0; flex: none; overflow-y: auto; border-left: 1px solid #ececef; background: white; }
@media (max-width: 760px) {
  .recipe-flow-toolbar--card { top: 10px; right: 10px; }
  .flow-header { min-height: 60px; gap: 6px; padding: 10px 12px; }
  .flow-run-button { padding: 0 10px; }
  .flow-header h2 { font-size: 13px; }
  .flow-workspace { position: relative; }
  .flow-side-panel { position: absolute; inset: 0 0 0 auto; z-index: 2; width: min(390px, 100%); box-shadow: -12px 0 40px #22224210; }
  .flow-tools { left: 12px; bottom: 12px; }
  .flow-run-status { top: 12px; right: 12px; bottom: auto; max-width: calc(100% - 24px); }
  .flow-layer--detail { padding: 12px; }
}
@media (max-width: 640px) {
  .recipe-flow-toolbar--card { top: 46px; left: 12px; right: 12px; }
  .flow-header { flex-wrap: wrap; }
  .flow-header-title { flex-basis: calc(100% - 42px); }
  .flow-header > button { order: 2; }
  .flow-header > .flow-header-close { order: 1; }
}
</style>
