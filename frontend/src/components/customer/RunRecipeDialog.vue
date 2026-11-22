<script setup lang="ts">
/**
 * 用新文件执行一个流程（客户页）。流程已经录制好：
 * 每个输入一个位置，默认是录制时的样例文件，也可以换成已有文件或上传新文件，选好直接执行。
 * 批量：流程只有一个输入时，可以一次上传多个文件，每个文件单独执行一次；有多个输入的流程一次只执行一组文件。
 * 客户页发起的执行，临时上传的输入和每次的结果都只记在各次执行里，不进文件页。
 * 正常情况下不调用 AI；出错时可选让 AI 自动修复。
 */
import { Check, ChevronDown, ChevronRight, CircleX, Download, Eye, Files, FileSpreadsheet, Sheet, LoaderCircle, Play, RotateCcw, Square, TriangleAlert, Upload, X } from '@lucide/vue'
import { computed, onBeforeUnmount, reactive, ref } from 'vue'

import { conversationApi, customerApi, recipeApi } from '@/api'
import { errorText } from '@/api/http'
import type { CustomerWorkflow, FileInfo } from '@/api/types'
import RecipeIcon from '@/components/brand/RecipeIcon.vue'
import BaseModal from '@/components/common/BaseModal.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import { addProgressFile, applyRecipeEvent, newProgress, timeAgo, type FileArtifact, type RecipeProgress, type StepState } from '@/composables/chatModel'
import { inputKey, missingColumns, pickSheet, sampleFile, type RunInput } from '@/composables/runInputs'
import { useFilePreview } from '@/composables/useFilePreview'
import { useToast } from '@/composables/useToast'

import RunDetailDialog from './RunDetailDialog.vue'


export interface PickedRunItem {
  title: string
  label: string
  mapping: Record<string, string>
}

// customerId / conversationId：新上传的文件放到哪里（客户页传客户；对话里的流程传会话；都没有就只能选已有文件）。
// recipe 只用到这几项，客户页的 CustomerWorkflow 和全屏流程图里现取的流程都能直接传进来。
// pickOnly：仅用于选文件（全屏流程图点击执行时，选好文件后关闭弹窗并直接在流程图节点上实时执行）。
const props = defineProps<{
  customerId?: string | null
  conversationId?: string | null
  recipe: Pick<CustomerWorkflow, 'recipe_id' | 'name' | 'version' | 'flow' | 'recipe' | 'files'>
  elevated?: boolean
  pickOnly?: boolean
}>()
const emit = defineEmits<{ close: []; done: []; start: [payload: { items: PickedRunItem[]; pool: FileInfo[] }] }>()
const toast = useToast()

interface Choice { fileId: string; sheet: string }
type ItemState = 'wait' | 'run' | 'done' | 'failed' | 'skipped'
interface RunItem {
  title: string
  label: string
  mapping: Record<string, string>
  progress: RecipeProgress
  files: FileArtifact[]
  state: ItemState
}

const inputs = computed(() => props.recipe.recipe.inputs)
// 默认都是空的，需要上传新文件；也可以一键带入录制时的样例文件
const pool = ref<FileInfo[]>(props.recipe.files.filter((file) => file.kind === 'input'))
// 每个输入选的文件：一个是单次执行，多个是批量
const choice = reactive<Record<string, Choice[]>>(Object.fromEntries(inputs.value.map((input) => [input.alias, []])))
/** 提示用户要传哪个文件：录制时的文件名 */
const expectedName = (input: RunInput) => input.source_file || sampleOf(input)?.filename || input.alias
const sampleOf = (input: RunInput) => sampleFile(props.recipe.recipe, input, pool.value)

const stage = ref<'pick' | 'run'>('pick')
const uploading = ref<{ alias: string; done: number; total: number; batch: boolean } | null>(null)
const canUpload = computed(() => !!(props.customerId || props.conversationId))
const allowRepair = true  // 出错时让 AI 自动修复，始终开启
const items = ref<RunItem[]>([])
const running = ref(false)
const stopping = ref(false)
const detailRunId = ref<string | null>(null)
const activeIndex = ref(0)           // 正在跑（或最后跑过）的那一次：流程图默认跟着它
const viewing = ref<number | null>(null)   // 手动点开查看的某一次；下一次开始执行时回到自动跟随
const openFilePreview = useFilePreview()
onBeforeUnmount(() => { stopping.value = true })   // 关掉窗口：当前这个跑完后不再启动后面的

const entries = (input: RunInput) => choice[input.alias] ?? []
const fileOf = (pick: Choice | undefined) => (pick ? pool.value.find((file) => file.file_id === pick.fileId) ?? null : null)
const keyOf = (input: RunInput, pick: Choice | undefined) => inputKey(input, fileOf(pick), pick?.sheet ?? '')
const missing = (input: RunInput, pick: Choice | undefined) => missingColumns(input, fileOf(pick), pick?.sheet ?? '')
/** 这个输入可以从已上传的文件里选哪些：完整文件输入不限，数据表输入需要有工作表 */
const uploaded = (input: RunInput) => pool.value.filter((file) => input.kind === 'file' || file.sheets.length)
const multi = computed(() => inputs.value.length > 1)
const pendingSamples = computed(() => inputs.value.filter((input) => {
  const sample = sampleOf(input)
  return sample && !(entries(input).length === 1 && entries(input)[0].fileId === sample.file_id)
}))

// 只有一个输入的流程才能批量：每个文件单独执行一次；多个输入的流程一次只执行一组文件
const batchable = computed(() => inputs.value.length === 1)
const total = computed(() => Math.max(1, ...inputs.value.map((input) => entries(input).length)))
const ready = computed(() => inputs.value.length > 0
  && inputs.value.every((input) => entries(input).length > 0 && entries(input).every((pick) => keyOf(input, pick))))

function select(input: RunInput, file: FileInfo) {
  menuFor.value = null
  choice[input.alias] = [{ fileId: file.file_id, sheet: pickSheet(input, file) }]
}

function clear(input: RunInput) {
  choice[input.alias] = []
}

function removeAt(input: RunInput, index: number) {
  choice[input.alias] = entries(input).filter((_, i) => i !== index)
}

function useAllSamples() {
  for (const input of pendingSamples.value) select(input, sampleOf(input)!)
}

const dragging = ref<string | null>(null)
const menuFor = ref<string | null>(null)  // 正在从已上传文件里选择的输入
const pickerInput = computed(() => inputs.value.find((input) => input.alias === menuFor.value) ?? null)

/** 「上传」：单个文件，替换当前选择（选多个也只取第一个）。 */
function pick(input: RunInput, e: Event) {
  const target = e.target as HTMLInputElement
  const file = target.files?.[0]
  target.value = ''
  if (file) void upload(input, [file])
}

/** 「批量执行」：一次选多个文件，每个文件单独执行一次；已经是批量时继续添加。 */
function pickBatch(input: RunInput, e: Event) {
  const target = e.target as HTMLInputElement
  const files = [...(target.files ?? [])]
  target.value = ''
  if (files.length) void upload(input, files, true)
}

function drop(input: RunInput, e: DragEvent) {
  dragging.value = null
  const file = e.dataTransfer?.files?.[0]
  if (file && canUpload.value && uploading.value === null) void upload(input, [file])
}

async function upload(input: RunInput, files: File[], batch = false) {
  const append = batch && entries(input).length > 1
  const added: FileInfo[] = []
  uploading.value = { alias: input.alias, done: 0, total: files.length, batch }
  try {
    for (const file of files) {
      const info = props.customerId
        ? await customerApi.uploadFile(props.customerId, file, 'input', props.recipe.recipe_id, true)
        : (await conversationApi.upload(props.conversationId!, file, 'input', true)).file
      pool.value.unshift(info)
      added.push(info)
      uploading.value.done++
    }
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    if (added.length) {
      const picks = added.map((file) => ({ fileId: file.file_id, sheet: pickSheet(input, file) }))
      const merged = append ? [...entries(input), ...picks] : picks
      choice[input.alias] = merged.filter((item, i) => merged.findIndex((other) => other.fileId === item.fileId) === i)
    }
    uploading.value = null
  }
}

function previewOutput(file: FileArtifact) {
  openFilePreview(file.file_id)
}

const stem = (name: string) => name.replace(/\.[^.]+$/, '')

/** 单输入流程：一个文件一次执行；多输入流程：只有一次，各输入用各自选的那个文件。 */
function buildItems(): RunItem[] {
  return Array.from({ length: total.value }, (_, i) => {
    const names = inputs.value.map((input) => fileOf(entries(input)[i])?.filename ?? '')
    return {
      title: names.join(' + '),
      label: stem(names[0] ?? ''),
      mapping: Object.fromEntries(inputs.value.map((input) => [input.alias, keyOf(input, entries(input)[i])])),
      progress: newProgress('replay'),
      files: [],
      state: 'wait' as ItemState,
    }
  })
}

const failedOf = (progress: RecipeProgress) => progress.status === 'failed' || !!progress.failed
  || ['failed', 'escalated', 'incomplete', 'cancelled', 'interrupted'].includes(progress.runStatus ?? '')

async function execute(indices: number[]) {
  running.value = true
  stopping.value = false
  try {
    for (const index of indices) {
      const item = items.value[index]
      if (stopping.value) { item.state = 'skipped'; continue }
      item.state = 'run'
      activeIndex.value = index
      viewing.value = null
      Object.assign(item.progress, newProgress('replay'))
      item.files = []
      const fileIds = [...new Set(Object.values(item.mapping).map((key) => key.split('::')[0]))]
      const handle = recipeApi.run(props.recipe.recipe_id, {
        file_ids: fileIds, inputs: item.mapping, allow_repair: allowRepair,
        scoped: true, label: items.value.length > 1 ? item.label || undefined : undefined,
      }, (e) => {
        if (e.type.startsWith('recipe_')) applyRecipeEvent(item.progress, e)
        else if (e.type === 'file') {
          const file = { file_id: e.file_id, filename: e.filename, download_url: e.download_url, sheets: e.sheets }
          addProgressFile(item.progress, file)
          if (!item.files.some((existing) => existing.file_id === file.file_id)) item.files.push(file)
        } else if (e.type === 'error') item.progress.failed = e.content
      })
      try {
        await handle.done
      } catch (err) {
        item.progress.failed = errorText(err)
        item.progress.status = 'failed'
      }
      item.state = failedOf(item.progress) ? 'failed' : 'done'
    }
  } finally {
    running.value = false
    stopping.value = false
    emit('done')
  }
}

function run() {
  if (!ready.value || running.value) return
  const built = buildItems()
  if (props.pickOnly) {
    emit('start', {
      items: built.map(({ title, label, mapping }) => ({ title, label, mapping })),
      pool: [...pool.value],
    })
    return
  }
  items.value = built
  activeIndex.value = 0
  viewing.value = null
  stage.value = 'run'
  void execute(items.value.map((_, i) => i))
}

function retryFailed() {
  if (running.value) return
  const indices = items.value.flatMap((item, i) => (item.state === 'failed' || item.state === 'skipped' ? [i] : []))
  if (indices.length) void execute(indices)
}

// 单次执行：状态卡 → 输出文件 → 处理节点 → 输入文件；批量：状态卡 → 执行列表（含直接下载）→ 当前选中项详情
const single = computed(() => (items.value.length === 1 ? items.value[0] : null))
const viewIndex = computed(() => viewing.value ?? activeIndex.value)
const viewItem = computed<RunItem | null>(() => items.value[viewIndex.value] ?? null)
const progress = computed(() => viewItem.value?.progress ?? newProgress('replay'))
const outputFiles = computed(() => viewItem.value?.files ?? [])
/** 批量执行时标识当前展开的是第几次、哪个文件 */
const batchInfo = computed(() => (items.value.length > 1 && viewItem.value
  ? { index: viewIndex.value + 1, total: items.value.length, filename: viewItem.value.title } : undefined))
const viewRunning = computed(() => (single.value ? running.value : viewItem.value?.state === 'run'))
const finished = computed(() => items.value.filter((item) => item.state === 'done' || item.state === 'failed').length)
const succeededCount = computed(() => items.value.filter((item) => item.state === 'done').length)
const failedCount = computed(() => items.value.filter((item) => item.state === 'failed').length)
const retryable = computed(() => items.value.filter((item) => item.state === 'failed' || item.state === 'skipped').length)
const batchPercent = computed(() => (items.value.length ? Math.round((finished.value / items.value.length) * 100) : 0))
const stepsDone = (item: RunItem) => item.progress.steps.filter((step) => !['pending', 'running', 'repairing'].includes(step.status)).length
const stepTotal = computed(() => props.recipe.recipe.steps.filter((step) => step.enabled !== false).length || props.recipe.recipe.steps.length)
const stepsFinished = computed(() => progress.value.steps.filter((step) => !['pending', 'running', 'repairing'].includes(step.status)).length)
const stepFailedChecks = (step: StepState) => (step.report?.checks ?? []).filter((check) => !check.ok)
const itemStatus = (item: RunItem) => (item.state === 'failed' ? (item.progress.runStatus === 'escalated' ? 'escalated' : 'failed') : item.progress.runStatus ?? 'success')

const modalTitle = computed(() => {
  if (stage.value === 'pick') return '运行流程'
  if (single.value) return running.value ? '正在执行' : '执行结果'
  return running.value ? '正在批量执行' : '批量执行结果'
})

const displaySteps = computed<StepState[]>(() => {
  if (progress.value.steps.length) return progress.value.steps
  return props.recipe.recipe.steps
    .filter((step) => step.enabled !== false)
    .map((step) => ({
      step_id: step.id,
      name: step.name,
      formula: step.formula,
      formula_name: step.formula,
      kind: step.kind,
      formula_kind: step.formula_kind,
      description: step.description,
      output: step.output,
      status: 'pending' as const,
      repairs: [],
    }))
})

const displayInputs = computed(() => {
  const mapping = viewItem.value?.mapping ?? {}
  const fileIdOf = (alias: string) => {
    const key = mapping[alias]
    return key ? key.split('::')[0] : ''
  }
  if (progress.value.inputs.length) {
    return progress.value.inputs.map((item) => ({
      alias: item.alias,
      filename: item.filename,
      fileId: fileIdOf(item.alias),
      warnings: item.warnings ?? [],
    }))
  }
  if (!viewItem.value) return []
  return inputs.value
    .map((input) => {
      const fid = fileIdOf(input.alias)
      const file = pool.value.find((f) => f.file_id === fid)
      return {
        alias: input.alias,
        filename: file?.filename || expectedName(input),
        fileId: fid,
        warnings: [] as string[],
      }
    })
    .filter((item) => item.filename)
})

/** 仅在存在失败或警告时提取具体异常提示；全部通过时不展示校验明细 */
const validationIssues = computed(() => {
  const validation = progress.value.validation
  if (!validation || validation.status === 'pass') return []
  return (validation.checks ?? [])
    .filter((check) => check.status === 'fail' || check.status === 'warn')
    .map((check) => check.message || check.description)
    .filter(Boolean)
})

const bannerError = computed(() => progress.value.escalation || progress.value.failed || '')

type BannerTone = 'running' | 'success' | 'warning' | 'error'
const singleBanner = computed<{ tone: BannerTone; title: string; desc: string }>(() => {
  const steps = displaySteps.value
  const totalSteps = steps.length
  if (running.value) {
    const repairing = steps.find((step) => step.status === 'repairing')
    const active = steps.find((step) => step.status === 'running')
    return {
      tone: 'running',
      title: repairing ? '正在自动修复并重试…' : '正在执行流程…',
      desc: repairing
        ? `正在修复「${repairing.name}」`
        : active
          ? `正在处理「${active.name}」（已完成 ${stepsFinished.value}/${totalSteps}）`
          : totalSteps
            ? `已完成 ${stepsFinished.value}/${totalSteps} 个节点`
            : '正在准备输入文件…',
    }
  }
  if (failedOf(progress.value)) {
    const status = progress.value.runStatus
    const tone: BannerTone = ['cancelled', 'interrupted', 'incomplete'].includes(status ?? '') ? 'warning' : 'error'
    const title = status === 'escalated'
      ? '执行失败，需人工处理'
      : status === 'cancelled'
        ? '已停止执行'
        : status === 'interrupted'
          ? '执行已中断'
          : status === 'incomplete'
            ? '执行未完成'
            : '执行失败'
    const failedStep = steps.find((step) => step.status === 'failed')
    const desc = failedStep ? `节点「${failedStep.name}」执行出错` : '请查看下方报错信息后调整重试'
    return { tone, title, desc }
  }
  const summaryParts = [
    totalSteps ? `${totalSteps} 个节点全部完成` : '',
    outputFiles.value.length ? `已生成 ${outputFiles.value.length} 个结果文件` : '',
  ].filter(Boolean)
  if (progress.value.runStatus === 'warning' || validationIssues.value.length) {
    return {
      tone: 'warning',
      title: '执行完成，存在提示',
      desc: summaryParts.join(' · ') || '请留意下方提示内容',
    }
  }
  return {
    tone: 'success',
    title: progress.value.runStatus === 'repaired' ? '执行成功（已自动修复）' : '执行成功',
    desc: summaryParts.join(' · ') || '流程已执行完成',
  }
})

const batchBanner = computed<{ tone: BannerTone; title: string; desc: string }>(() => {
  if (running.value) {
    return {
      tone: 'running',
      title: `正在批量执行（${finished.value}/${items.value.length}）`,
      desc: failedCount.value ? `已完成 ${finished.value} 个，其中 ${failedCount.value} 个失败` : `共 ${items.value.length} 个文件，正在依次处理`,
    }
  }
  if (!failedCount.value && finished.value === items.value.length) {
    return {
      tone: 'success',
      title: '批量执行成功',
      desc: `全部 ${items.value.length} 个文件均已执行完成`,
    }
  }
  if (failedCount.value === items.value.length) {
    return {
      tone: 'error',
      title: '批量执行失败',
      desc: `${failedCount.value} 个文件均未执行成功，可点击下方条目查看报错`,
    }
  }
  const parts = [
    succeededCount.value ? `${succeededCount.value} 个成功` : '',
    failedCount.value ? `${failedCount.value} 个失败` : '',
    items.value.length - finished.value > 0 ? `${items.value.length - finished.value} 个未执行` : '',
  ].filter(Boolean)
  return {
    tone: 'warning',
    title: '批量执行已结束',
    desc: parts.join(' · '),
  }
})

const STEP_STATUS_TEXT: Record<StepState['status'], string> = {
  pending: '等待中',
  running: '执行中',
  repairing: '修复中',
  success: '已完成',
  repaired: '已自动修复',
  warning: '有提示',
  failed: '失败',
}
const stepStatusText = (status: StepState['status']) => STEP_STATUS_TEXT[status] ?? ''
</script>

<template>
  <BaseModal width="max-w-3xl" :elevated="elevated" @close="emit('close')">
    <template #title>
      <div class="flex min-w-0 items-center gap-2.5">
        <RecipeIcon kind="workflow" :size="28" />
        <h3 class="flex min-w-0 items-baseline gap-1.5 text-[15px] font-semibold leading-snug text-ink">
          <span class="shrink-0">{{ modalTitle }}</span>
          <span class="shrink-0" aria-hidden="true">·</span>
          <span class="min-w-0 truncate" :title="recipe.name">{{ recipe.name }}</span>
        </h3>
      </div>
    </template>

    <!-- 1. 每个输入选一个文件；放多个文件就是批量 -->
    <div v-if="stage === 'pick'" class="space-y-3">
      <div v-for="input in inputs" :key="input.alias">
        <div
          class="rounded-2xl border px-4 py-3 transition"
          :class="dragging === input.alias ? 'border-ink bg-paper' : 'border-line'"
          @dragover.prevent="dragging = input.alias" @dragleave="dragging = null" @drop.prevent="drop(input, $event)"
        >
          <div class="flex items-center gap-3">
            <template v-if="entries(input).length === 1">
              <Check class="size-4 shrink-0 text-success" />
              <span class="min-w-0 flex-1 truncate text-sm font-medium">{{ fileOf(entries(input)[0])?.filename }}</span>
              <button type="button" class="icon-btn shrink-0" title="查看文件" :aria-label="`查看文件 ${fileOf(entries(input)[0])?.filename}`" @click="openFilePreview(fileOf(entries(input)[0])!)"><Eye class="size-4" /></button>
              <button type="button" class="icon-btn shrink-0" title="清除选择" :aria-label="`清除 ${input.alias} 的选择`" @click="clear(input)"><X class="size-4" /></button>
            </template>
            <template v-else-if="entries(input).length > 1">
              <Files class="size-4 shrink-0 text-success" />
              <span class="min-w-0 flex-1 truncate text-sm font-medium">{{ entries(input).length }} 个文件，每个单独执行一次</span>
              <button type="button" class="icon-btn shrink-0" title="清除选择" :aria-label="`清除 ${input.alias} 的全部文件`" @click="clear(input)"><X class="size-4" /></button>
            </template>
            <p v-else class="min-w-0 flex-1 text-sm text-mute">请上传新的文件：<span class="break-all font-medium text-ink">{{ expectedName(input) }}</span></p>
            <label v-if="canUpload" class="btn-line h-8 shrink-0 cursor-pointer gap-1.5 px-3 py-0 text-xs">
              <LoaderCircle v-if="uploading?.alias === input.alias" class="size-3.5 animate-spin" /><Upload v-else class="size-3.5" />{{ entries(input).length ? '重新上传' : '上传' }}
              <input type="file" class="hidden" accept=".xlsx,.xlsm,.xls,.csv" :disabled="uploading !== null" @change="pick(input, $event)" />
            </label>
            <button v-if="uploaded(input).length" type="button" class="btn-line h-8 shrink-0 gap-1.5 px-3 py-0 text-xs" aria-haspopup="dialog" @click="menuFor = input.alias">已上传<ChevronDown class="size-3.5" /></button>
            <button v-if="sampleOf(input) && !(entries(input).length === 1 && entries(input)[0].fileId === sampleOf(input)!.file_id)" type="button" class="btn-line h-8 shrink-0 gap-1.5 px-3 py-0 text-xs" @click="select(input, sampleOf(input)!)">样例文件</button>
          </div>
          <ol v-if="entries(input).length > 1" class="mt-2 max-h-44 space-y-0.5 overflow-y-auto pr-1">
            <li v-for="(item, index) in entries(input)" :key="item.fileId" class="group flex items-center gap-2 rounded-lg px-2 py-1 text-[13px] hover:bg-paper">
              <span class="w-5 shrink-0 text-right text-xs tabular-nums text-faint">{{ index + 1 }}</span>
              <span class="min-w-0 flex-1 truncate">{{ fileOf(item)?.filename }}</span>
              <TriangleAlert v-if="missing(input, item).length" class="size-3.5 shrink-0 text-[#8a5a00]" :title="`缺少列：${missing(input, item).join('、')}`" />
              <button type="button" class="icon-btn size-6 shrink-0" title="查看文件" :aria-label="`查看文件 ${fileOf(item)?.filename}`" @click="openFilePreview(fileOf(item)!)"><Eye class="size-3.5" /></button>
              <button type="button" class="icon-btn size-6 shrink-0" title="移除" :aria-label="`移除 ${fileOf(item)?.filename}`" @click="removeAt(input, index)"><X class="size-3.5" /></button>
            </li>
          </ol>
        </div>
        <select v-if="input.kind !== 'file' && entries(input).length === 1 && (fileOf(entries(input)[0])?.sheets.length ?? 0) > 1" v-model="entries(input)[0].sheet" class="select select-sm mt-2 w-full rounded-xl" :aria-label="`${input.alias} 读取的工作表`">
          <option v-for="s in fileOf(entries(input)[0])!.sheets" :key="s.sheet" :value="s.sheet">{{ s.sheet }}（{{ s.rows }} 行）</option>
        </select>
        <p v-if="entries(input).length === 1 && missing(input, entries(input)[0]).length" class="mt-2 flex items-start gap-1.5 px-1 text-xs leading-relaxed text-[#8a5a00]">
          <TriangleAlert class="mt-0.5 size-3.5 shrink-0" /><span>所选文件缺少流程需要的列：{{ missing(input, entries(input)[0]).join('、') }}。请核对是否选错了文件或工作表，列名不同的话执行可能失败。</span>
        </p>
        <p v-else-if="entries(input).length > 1 && entries(input).some((item) => missing(input, item).length)" class="mt-2 flex items-start gap-1.5 px-1 text-xs leading-relaxed text-[#8a5a00]">
          <TriangleAlert class="mt-0.5 size-3.5 shrink-0" /><span>有文件缺少流程需要的列（已在文件名后标出），列名不同的话对应的那次执行可能失败。</span>
        </p>
      </div>
      <p v-if="total > 1" class="px-1 text-xs leading-relaxed text-mute">共 {{ total }} 个文件，每个文件单独执行一次。</p>
    </div>

    <!-- 2. 执行结果：顶部结果总览（状态 + 快捷结果文件）→ 输入文件 → 处理节点 → 输出文件 -->
    <div v-else class="space-y-5">
      <!-- 2.1 顶部执行结果卡（单次 / 批量） -->
      <div v-if="single" class="status-banner" :class="`is-${singleBanner.tone}`" role="status">
        <div class="flex items-center gap-3.5">
          <span class="status-badge" :class="`is-${singleBanner.tone}`" aria-hidden="true">
            <LoaderCircle v-if="singleBanner.tone === 'running'" class="size-4.5 animate-spin" />
            <Check v-else-if="singleBanner.tone === 'success'" class="size-4.5 stroke-[2.4]" />
            <TriangleAlert v-else-if="singleBanner.tone === 'warning'" class="size-4" />
            <X v-else class="size-4.5 stroke-[2.4]" />
          </span>
          <div class="min-w-0 flex-1">
            <div class="text-[15px] font-semibold leading-snug text-ink">{{ singleBanner.title }}</div>
            <p v-if="singleBanner.desc" class="mt-0.5 text-xs leading-5 text-mute">{{ singleBanner.desc }}</p>
          </div>
          <button
            v-if="!elevated && progress.runId && !running"
            type="button"
            class="btn-text shrink-0 gap-0.5 px-2.5 py-1.5 text-xs font-medium"
            aria-haspopup="dialog"
            @click="detailRunId = progress.runId!"
          >
            运行详情<ChevronRight class="size-3.5 text-faint" />
          </button>
        </div>

        <!-- 快捷结果文件：置顶直接查看与下载 -->
        <ul v-if="outputFiles.length" class="quick-results">
          <li v-for="file in outputFiles" :key="file.file_id" class="flex items-center gap-3">
            <span class="output-icon is-sm" aria-hidden="true">
              <FileSpreadsheet class="size-4" />
            </span>
            <button type="button" class="file-name" :title="`查看 ${file.filename}`" @click="previewOutput(file)">{{ file.filename }}</button>
            <div class="flex shrink-0 items-center gap-2">
              <button type="button" class="btn-line h-8 gap-1.5 px-3 py-0 text-xs" @click="previewOutput(file)">
                <Eye class="size-3.5" />查看
              </button>
              <a :href="file.download_url" class="btn-ink h-8 gap-1.5 px-3.5 py-0 text-xs">
                <Download class="size-3.5" />下载
              </a>
            </div>
          </li>
        </ul>

        <div
          v-if="bannerError"
          class="mt-3 whitespace-pre-wrap rounded-xl border border-error/15 bg-white/90 px-3.5 py-2.5 text-[13px] leading-6 text-error"
          role="alert"
        >{{ bannerError }}</div>
        <ul
          v-if="validationIssues.length"
          class="mt-2.5 space-y-1 rounded-xl border border-[#f2d6a2] bg-white/85 px-3.5 py-2.5 text-xs leading-5 text-[#8a5a00]"
        >
          <li v-for="(issue, i) in validationIssues" :key="i" class="flex items-start gap-1.5">
            <TriangleAlert class="mt-0.5 size-3.5 shrink-0" /><span>{{ issue }}</span>
          </li>
        </ul>
      </div>

      <div v-else class="status-banner" :class="`is-${batchBanner.tone}`" role="status">
        <div class="flex items-center gap-3.5">
          <span class="status-badge" :class="`is-${batchBanner.tone}`" aria-hidden="true">
            <LoaderCircle v-if="batchBanner.tone === 'running'" class="size-4.5 animate-spin" />
            <Check v-else-if="batchBanner.tone === 'success'" class="size-4.5 stroke-[2.4]" />
            <TriangleAlert v-else-if="batchBanner.tone === 'warning'" class="size-4" />
            <X v-else class="size-4.5 stroke-[2.4]" />
          </span>
          <div class="min-w-0 flex-1">
            <div class="text-[15px] font-semibold leading-snug text-ink">{{ batchBanner.title }}</div>
            <p class="mt-0.5 text-xs leading-5 text-mute">{{ batchBanner.desc }}</p>
          </div>
        </div>
        <div v-if="running" class="mt-3 h-1.5 overflow-hidden rounded-full bg-black/[0.06]">
          <div class="h-full rounded-full bg-ink transition-all duration-300" :style="{ width: `${batchPercent}%` }" />
        </div>
      </div>

      <!-- 2.2 批量模式：执行列表（置顶，直接可查看/下载每次的结果） -->
      <section v-if="!single">
        <h4 class="sec-title">执行列表<small>{{ items.length }}</small></h4>
        <ol class="card is-batch">
          <li v-for="(item, index) in items" :key="index" class="transition-colors" :class="viewIndex === index ? 'bg-paper/75' : ''">
            <div class="flex items-center gap-3">
              <span class="w-5 shrink-0 text-right text-xs tabular-nums text-faint">{{ index + 1 }}</span>
              <button
                type="button"
                class="min-w-0 flex-1 truncate text-left text-sm font-medium"
                :class="item.state === 'wait' ? 'cursor-default text-mute' : 'text-ink hover:underline underline-offset-4'"
                :title="item.state === 'wait' ? item.title : `查看第 ${index + 1} 次执行情况：${item.title}`"
                :aria-current="viewIndex === index ? 'true' : undefined"
                @click="item.state !== 'wait' && (viewing = index)"
              >{{ item.title }}</button>
              <span v-if="item.state === 'wait'" class="shrink-0 text-xs text-faint">等待中</span>
              <span v-else-if="item.state === 'skipped'" class="shrink-0 text-xs text-faint">已跳过</span>
              <span v-else-if="item.state === 'run'" class="inline-flex shrink-0 items-center gap-1.5 text-xs text-info">
                <LoaderCircle class="size-3.5 animate-spin" /><span class="tabular-nums">{{ stepsDone(item) }}/{{ stepTotal }}</span>
              </span>
              <StatusPill v-else class="shrink-0" :status="itemStatus(item)" />
              <template v-for="file in item.files" :key="file.file_id">
                <button type="button" class="icon-btn size-7 shrink-0" title="查看结果" :aria-label="`查看结果 ${file.filename}`" @click="previewOutput(file)"><Eye class="size-4" /></button>
                <a :href="file.download_url" class="icon-btn size-7 shrink-0" title="下载结果" :aria-label="`下载结果 ${file.filename}`"><Download class="size-4" /></a>
              </template>
              <button v-if="!elevated && item.progress.runId && (item.state === 'done' || item.state === 'failed')" type="button" class="btn-text h-7 shrink-0 px-2 py-0 text-xs" aria-haspopup="dialog" @click="detailRunId = item.progress.runId!">详情</button>
            </div>
            <p v-if="item.state === 'failed' && (item.progress.escalation || item.progress.failed)" class="mt-1.5 line-clamp-3 whitespace-pre-wrap pl-8 text-xs leading-relaxed text-error">{{ item.progress.escalation || item.progress.failed }}</p>
          </li>
        </ol>
      </section>

      <!-- 批量模式：当前选中条目的明细分隔条 -->
      <div v-if="batchInfo && viewItem && viewItem.state !== 'wait'" class="space-y-3 border-t border-line pt-4">
        <div class="flex items-center gap-2 text-xs text-mute">
          <LoaderCircle v-if="viewRunning" class="size-3.5 shrink-0 animate-spin text-info" />
          <span class="rounded-md bg-paper px-2 py-0.5 font-medium tabular-nums text-ink">第 {{ batchInfo.index }}/{{ batchInfo.total }} 次</span>
          <span class="min-w-0 flex-1 truncate">{{ batchInfo.filename }}</span>
        </div>
        <div
          v-if="bannerError"
          class="whitespace-pre-wrap rounded-xl border border-error/15 bg-error/5 px-3.5 py-2.5 text-[13px] leading-6 text-error"
          role="alert"
        >{{ bannerError }}</div>
        <ul
          v-if="validationIssues.length"
          class="space-y-1 rounded-xl border border-[#f2d6a2] bg-[#fffaf0] px-3.5 py-2.5 text-xs leading-5 text-[#8a5a00]"
        >
          <li v-for="(issue, i) in validationIssues" :key="i" class="flex items-start gap-1.5">
            <TriangleAlert class="mt-0.5 size-3.5 shrink-0" /><span>{{ issue }}</span>
          </li>
        </ul>
      </div>

      <!-- 2.3 输入文件 -->
      <section v-if="(single || (viewItem && viewItem.state !== 'wait')) && displayInputs.length">
        <h4 class="sec-title">输入文件<small>{{ displayInputs.length }}</small></h4>
        <ul class="card is-compact">
          <li v-for="item in displayInputs" :key="item.alias">
            <div class="flex items-center gap-2.5">
              <Sheet class="size-4 shrink-0 text-mute" />
              <button
                v-if="item.fileId"
                type="button"
                class="input-name"
                :title="`查看 ${item.filename}`"
                @click="openFilePreview(item.fileId)"
              >{{ item.filename }}</button>
              <span v-else class="min-w-0 flex-1 truncate text-[13.5px] font-medium text-ink" :title="item.filename">{{ item.filename }}</span>
              <button
                v-if="item.fileId"
                type="button"
                class="icon-btn size-7 shrink-0"
                title="查看文件"
                :aria-label="`查看 ${item.filename}`"
                @click="openFilePreview(item.fileId)"
              ><Eye class="size-3.5" /></button>
            </div>
            <ul v-if="item.warnings.length" class="notes is-input">
              <li v-for="w in item.warnings" :key="w" class="is-warn"><TriangleAlert class="mt-0.5 size-3.5 shrink-0" /><span>{{ w }}</span></li>
            </ul>
          </li>
        </ul>
      </section>

      <!-- 2.4 处理节点（看每步执行情况与报错，隐藏技术标签和细节） -->
      <section v-if="(single || (viewItem && viewItem.state !== 'wait')) && displaySteps.length">
        <h4 class="sec-title">处理节点<small>{{ displaySteps.length }}</small></h4>
        <ol class="card">
          <li v-for="step in displaySteps" :key="step.step_id">
            <div class="flex items-center gap-3">
              <span class="step-no" :class="`is-${step.status}`">
                <LoaderCircle v-if="step.status === 'running' || step.status === 'repairing'" class="size-3.5 animate-spin" />
                <Check v-else-if="step.status === 'success' || step.status === 'repaired'" class="size-3.5" />
                <TriangleAlert v-else-if="step.status === 'warning'" class="size-3.5" />
                <X v-else-if="step.status === 'failed'" class="size-3.5" />
                <template v-else>{{ step.step_id }}</template>
              </span>
              <span class="min-w-0 flex-1 truncate text-sm font-medium" :class="step.status === 'pending' ? 'text-mute' : 'text-ink'" :title="step.name">{{ step.name }}</span>
              <span class="step-status" :class="`is-${step.status}`">{{ stepStatusText(step.status) }}</span>
            </div>
            <ul v-if="step.error || step.report?.warnings?.length || stepFailedChecks(step).length" class="notes">
              <li v-if="step.error" class="is-error"><CircleX class="mt-0.5 size-3.5 shrink-0" /><span>{{ step.error }}</span></li>
              <li v-for="(check, i) in stepFailedChecks(step)" :key="`c${i}`" class="is-warn"><TriangleAlert class="mt-0.5 size-3.5 shrink-0" /><span>{{ check.message }}</span></li>
              <li v-for="(w, i) in step.report?.warnings ?? []" :key="`w${i}`" class="is-warn"><TriangleAlert class="mt-0.5 size-3.5 shrink-0" /><span>{{ w }}</span></li>
            </ul>
          </li>
        </ol>
      </section>

      <!-- 2.5 输出文件 -->
      <section v-if="single || (viewItem && viewItem.state !== 'wait')">
        <h4 class="sec-title">输出文件<small v-if="outputFiles.length">{{ outputFiles.length }}</small></h4>
        <ul v-if="outputFiles.length" class="card is-compact">
          <li v-for="file in outputFiles" :key="file.file_id" class="flex items-center gap-2.5">
            <span class="output-icon is-xs" aria-hidden="true">
              <FileSpreadsheet class="size-3.5" />
            </span>
            <button type="button" class="input-name font-semibold" :title="`查看 ${file.filename}`" @click="previewOutput(file)">{{ file.filename }}</button>
            <span class="flex shrink-0 items-center gap-0.5">
              <button type="button" class="icon-btn size-7" title="查看" :aria-label="`查看 ${file.filename}`" @click="previewOutput(file)"><Eye class="size-3.5" /></button>
              <a :href="file.download_url" class="icon-btn size-7" title="下载" :aria-label="`下载 ${file.filename}`"><Download class="size-3.5" /></a>
            </span>
          </li>
        </ul>
        <div v-else-if="viewRunning" class="empty flex items-center justify-center gap-2">
          <LoaderCircle class="size-3.5 animate-spin text-mute" /><span>正在生成结果文件…</span>
        </div>
        <p v-else class="empty">本次执行未生成结果文件</p>
      </section>
    </div>

    <template v-if="stage === 'pick' && batchable && canUpload" #header-actions>
      <label class="btn-line h-8 cursor-pointer gap-1.5 px-3 py-0 text-xs" title="一次选多个文件，每个文件单独执行一次">
        <LoaderCircle v-if="uploading?.batch" class="size-3.5 animate-spin" /><Files v-else class="size-3.5" />
        <template v-if="uploading?.batch">{{ uploading.done }}/{{ uploading.total }}</template>
        <template v-else>{{ entries(inputs[0]).length > 1 ? '继续添加文件' : '批量执行' }}</template>
        <input type="file" multiple class="hidden" accept=".xlsx,.xlsm,.xls,.csv" :disabled="uploading !== null" @change="pickBatch(inputs[0], $event)" />
      </label>
    </template>

    <template #footer>
      <template v-if="stage === 'pick'">
        <button v-if="multi && pendingSamples.length > 1" type="button" class="btn-text mr-auto text-xs" @click="useAllSamples">全部使用样例</button>
        <button class="btn-line" @click="emit('close')">取消</button>
        <button class="btn-ink" :disabled="!ready || uploading !== null" @click="run"><Play class="size-4" />{{ total > 1 ? `执行 ${total} 次` : '开始执行' }}</button>
      </template>
      <template v-else>
        <button v-if="!single && running" class="btn-line mr-auto" :disabled="stopping" @click="stopping = true"><Square class="size-3.5" />{{ stopping ? '当前这个跑完后停止' : '停止' }}</button>
        <button v-else-if="!single && retryable" class="btn-line mr-auto" @click="retryFailed"><RotateCcw class="size-4" />重跑未成功的 {{ retryable }} 个</button>
        <button class="btn-line" :disabled="running" @click="stage = 'pick'">换文件再跑</button>
        <button class="btn-ink" :disabled="running" @click="emit('close')">{{ running ? '执行中…' : '完成' }}</button>
      </template>
    </template>

    <BaseModal v-if="pickerInput" title="选择已上传的文件" width="max-w-md" :elevated="elevated" @close="menuFor = null">
      <ul class="-mx-2 space-y-0.5" role="listbox" aria-label="已上传的文件">
        <li v-for="file in uploaded(pickerInput)" :key="file.file_id" role="option" :aria-selected="entries(pickerInput).some((item) => item.fileId === file.file_id)">
          <button type="button" class="flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left hover:bg-paper focus-visible:bg-paper focus-visible:outline-none" @click="select(pickerInput, file)">
            <Sheet class="size-4 shrink-0 text-[#4a4743]" />
            <span class="min-w-0 flex-1 truncate text-sm">{{ file.filename }}</span>
            <span class="shrink-0 text-xs text-faint">{{ timeAgo(file.created_at) }}</span>
            <Check v-if="entries(pickerInput).some((item) => item.fileId === file.file_id)" class="size-4 shrink-0 text-success" />
          </button>
        </li>
      </ul>
    </BaseModal>
    <RunDetailDialog v-if="detailRunId" :run-id="detailRunId" @close="detailRunId = null" @changed="emit('done')" />
  </BaseModal>
</template>

<style scoped>
/* 顶部执行状态卡 */
.status-banner {
  border: 1px solid var(--color-line);
  border-radius: 16px;
  padding: 14px 16px;
  background: var(--color-paper);
}
.status-banner.is-success {
  border-color: #cdecd9;
  background: #f3fbf6;
}
.status-banner.is-warning {
  border-color: #f3ddb2;
  background: #fffaf0;
}
.status-banner.is-error {
  border-color: #f7cfcf;
  background: #fef5f5;
}

.status-badge {
  display: inline-flex;
  width: 36px;
  height: 36px;
  flex: none;
  align-items: center;
  justify-content: center;
  border-radius: 9999px;
}
.status-badge.is-running {
  border: 1px solid var(--color-line);
  background: #fff;
  color: var(--color-ink);
}
.status-badge.is-success {
  background: #22b573;
  color: #fff;
}
.status-badge.is-warning {
  background: #f2a21b;
  color: #fff;
}
.status-badge.is-error {
  background: var(--color-error);
  color: #fff;
}

/* 区块标题与卡片 */
.sec-title {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin: 0 2px 8px;
  color: var(--color-ink);
  font-size: 13.5px;
  font-weight: 600;
  line-height: 1.4;
}
.sec-title small {
  color: var(--color-faint);
  font-size: 12px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}

.card {
  margin: 0;
  border: 1px solid var(--color-line);
  border-radius: 16px;
  background: #fff;
  padding: 2px 16px;
  list-style: none;
}
.card > li {
  padding: 11px 0;
}
.card > li + li {
  border-top: 1px solid #f2f1ee;
}
.card.is-result > li {
  padding: 12px 0;
}
.card.is-compact {
  padding: 2px 14px;
}
.card.is-compact > li {
  padding: 8px 0;
}
.card.is-batch {
  overflow: hidden;
  padding: 0;
}
.card.is-batch > li {
  padding: 10px 16px;
}

.output-icon {
  display: inline-flex;
  width: 36px;
  height: 36px;
  flex: none;
  align-items: center;
  justify-content: center;
  border: 1px solid #d2eedf;
  border-radius: 10px;
  background: #effaf4;
  color: #1a9c5b;
}
.output-icon.is-sm {
  width: 30px;
  height: 30px;
  border-radius: 8px;
}
.output-icon.is-xs {
  width: 22px;
  height: 22px;
  border-radius: 6px;
}

.quick-results {
  margin: 12px 0 0;
  border: 1px solid rgba(20, 20, 23, 0.07);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.92);
  padding: 2px 12px;
  list-style: none;
}
.quick-results > li {
  padding: 9px 0;
}
.quick-results > li + li {
  border-top: 1px solid #f2f1ee;
}

.file-name {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  color: var(--color-ink);
  font-size: 14px;
  font-weight: 600;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-underline-offset: 4px;
}
.file-name:hover {
  text-decoration: underline;
}

.input-name {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  color: var(--color-ink);
  font-size: 13.5px;
  font-weight: 500;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-underline-offset: 4px;
}
.input-name:hover {
  text-decoration: underline;
}

.empty {
  margin: 0;
  border: 1px dashed var(--color-line);
  border-radius: 14px;
  padding: 16px;
  color: var(--color-faint);
  font-size: 13px;
  text-align: center;
}

/* 处理节点状态 */
.step-no {
  display: inline-flex;
  width: 22px;
  height: 22px;
  flex: none;
  align-items: center;
  justify-content: center;
  border-radius: 9999px;
  background: var(--color-paper);
  color: var(--color-mute);
  font-size: 11.5px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.step-no.is-running,
.step-no.is-repairing {
  background: color-mix(in srgb, var(--color-info) 12%, transparent);
  color: var(--color-info);
}
.step-no.is-success,
.step-no.is-repaired {
  background: color-mix(in srgb, var(--color-success) 14%, transparent);
  color: var(--color-success);
}
.step-no.is-warning {
  background: color-mix(in srgb, var(--color-warning) 18%, transparent);
  color: #8a5a00;
}
.step-no.is-failed {
  background: color-mix(in srgb, var(--color-error) 12%, transparent);
  color: var(--color-error);
}

.step-status {
  flex: none;
  font-size: 12px;
  font-weight: 500;
  color: var(--color-faint);
}
.step-status.is-running,
.step-status.is-repairing {
  color: var(--color-info);
}
.step-status.is-success,
.step-status.is-repaired {
  color: var(--color-success);
}
.step-status.is-warning {
  color: #8a5a00;
}
.step-status.is-failed {
  color: var(--color-error);
}

.notes {
  margin: 6px 0 0 34px;
  padding: 0;
  list-style: none;
}
.notes.is-input {
  margin-left: 26px;
}
.notes li {
  display: flex;
  gap: 6px;
  padding: 2px 0;
  font-size: 12.5px;
  line-height: 1.55;
  overflow-wrap: anywhere;
}
.notes .is-error {
  color: var(--color-error);
}
.notes .is-warn {
  color: #8a5a00;
}
</style>
