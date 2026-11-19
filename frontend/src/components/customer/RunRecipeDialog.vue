<script setup lang="ts">
/**
 * 用新文件执行一个流程（客户页）。流程已经录制好：
 * 每个输入一个位置，默认是录制时的样例文件，也可以换成已有文件或上传新文件，选好直接执行。
 * 批量：流程只有一个输入时，可以一次上传多个文件，每个文件单独执行一次；有多个输入的流程一次只执行一组文件。
 * 客户页发起的执行，临时上传的输入和每次的结果都只记在各次执行里，不进文件页。
 * 正常情况下不调用 AI；出错时可选让 AI 自动修复。
 */
import { Check, ChevronDown, Download, Eye, Files, Sheet, LoaderCircle, Play, RotateCcw, Square, TriangleAlert, Upload, X } from '@lucide/vue'
import { computed, onBeforeUnmount, reactive, ref } from 'vue'

import { conversationApi, customerApi, recipeApi } from '@/api'
import { errorText } from '@/api/http'
import type { CustomerWorkflow, FileInfo } from '@/api/types'
import BaseModal from '@/components/common/BaseModal.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import RecipeFlow from '@/components/recipe/RecipeFlow.vue'
import { addProgressFile, applyRecipeEvent, newProgress, timeAgo, type FileArtifact, type RecipeProgress } from '@/composables/chatModel'
import { progressFlow } from '@/composables/recipeFlow'
import { inputKey, missingColumns, pickSheet, sampleFile, type RunInput } from '@/composables/runInputs'
import { useFilePreview } from '@/composables/useFilePreview'
import { useToast } from '@/composables/useToast'

import RunDetailDialog from './RunDetailDialog.vue'


// customerId / conversationId：新上传的文件放到哪里（客户页传客户；对话里的流程传会话；都没有就只能选已有文件）。
// recipe 只用到这几项，客户页的 CustomerWorkflow 和全屏流程图里现取的流程都能直接传进来。
const props = defineProps<{
  customerId?: string | null
  conversationId?: string | null
  recipe: Pick<CustomerWorkflow, 'recipe_id' | 'name' | 'version' | 'flow' | 'recipe' | 'files'>
  elevated?: boolean
}>()
const emit = defineEmits<{ close: []; done: [] }>()
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
const uploading = ref<{ alias: string; done: number; total: number } | null>(null)
const canUpload = computed(() => !!(props.customerId || props.conversationId))
const allowRepair = true  // 出错时让 AI 自动修复，始终开启
const items = ref<RunItem[]>([])
const running = ref(false)
const stopping = ref(false)
const detailRunId = ref<string | null>(null)
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

function pick(input: RunInput, e: Event) {
  const target = e.target as HTMLInputElement
  const files = [...(target.files ?? [])]
  target.value = ''
  if (files.length) void upload(input, files)
}

function drop(input: RunInput, e: DragEvent) {
  dragging.value = null
  const files = [...(e.dataTransfer?.files ?? [])]
  if (files.length && canUpload.value && uploading.value === null) void upload(input, files)
}

/** 上传一个或多个文件：已经是批量（多个文件）时继续添加，否则替换当前选择。 */
async function upload(input: RunInput, picked: File[]) {
  const files = batchable.value ? picked : picked.slice(0, 1)
  const append = entries(input).length > 1
  const added: FileInfo[] = []
  uploading.value = { alias: input.alias, done: 0, total: files.length }
  try {
    for (const file of files) {
      const info = props.customerId
        ? await customerApi.uploadFile(props.customerId, file, 'input', props.recipe.recipe_id, true)
        : (await conversationApi.upload(props.conversationId!, file, 'input')).file
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
      Object.assign(item.progress, newProgress('replay'))
      item.files = []
      const fileIds = [...new Set(Object.values(item.mapping).map((key) => key.split('::')[0]))]
      const handle = recipeApi.run(props.recipe.recipe_id, {
        file_ids: fileIds, inputs: item.mapping, allow_repair: allowRepair,
        scoped: !!props.customerId, label: items.value.length > 1 ? item.label || undefined : undefined,
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
  items.value = buildItems()
  stage.value = 'run'
  void execute(items.value.map((_, i) => i))
}

function retryFailed() {
  if (running.value) return
  const indices = items.value.flatMap((item, i) => (item.state === 'failed' || item.state === 'skipped' ? [i] : []))
  if (indices.length) void execute(indices)
}

// 单次执行：直接展示流程图的进度；批量：一行一次执行
const single = computed(() => (items.value.length === 1 ? items.value[0] : null))
const progress = computed(() => single.value?.progress ?? newProgress('replay'))
const outputFiles = computed(() => single.value?.files ?? [])
const flow = computed(() => progressFlow({ ...progress.value, flow: props.recipe.flow }))
const finished = computed(() => items.value.filter((item) => item.state === 'done' || item.state === 'failed').length)
const failedCount = computed(() => items.value.filter((item) => item.state === 'failed').length)
const retryable = computed(() => items.value.filter((item) => item.state === 'failed' || item.state === 'skipped').length)
const stepsDone = (item: RunItem) => item.progress.steps.filter((step) => !['pending', 'running', 'repairing'].includes(step.status)).length
const stepTotal = computed(() => props.recipe.recipe.steps.length)
const itemStatus = (item: RunItem) => (item.state === 'failed' ? (item.progress.runStatus === 'escalated' ? 'escalated' : 'failed') : item.progress.runStatus ?? 'success')
</script>

<template>
  <BaseModal :title="`执行「${recipe.name}」`" width="max-w-3xl" :elevated="elevated" @close="emit('close')">
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
            <label v-if="canUpload" class="btn-line h-8 shrink-0 cursor-pointer gap-1.5 px-3 py-0 text-xs" :title="!batchable ? '上传文件' : entries(input).length > 1 ? '继续添加文件' : '可以一次选多个文件，批量执行'">
              <LoaderCircle v-if="uploading?.alias === input.alias" class="size-3.5 animate-spin" /><Upload v-else class="size-3.5" />
              <template v-if="uploading?.alias === input.alias && uploading.total > 1">{{ uploading.done }}/{{ uploading.total }}</template>
              <template v-else>{{ entries(input).length > 1 ? '添加' : entries(input).length ? '重新上传' : '上传' }}</template>
              <input type="file" :multiple="batchable" class="hidden" accept=".xlsx,.xlsm,.xls,.csv" :disabled="uploading !== null" @change="pick(input, $event)" />
            </label>
            <button v-if="uploaded(input).length" type="button" class="btn-text h-8 shrink-0 gap-1 px-2 py-0 text-xs" aria-haspopup="dialog" @click="menuFor = input.alias">已上传<ChevronDown class="size-3.5" /></button>
            <button v-if="sampleOf(input) && !(entries(input).length === 1 && entries(input)[0].fileId === sampleOf(input)!.file_id)" type="button" class="btn-text h-8 shrink-0 px-2 py-0 text-xs" @click="select(input, sampleOf(input)!)">样例文件</button>
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

    <!-- 2. 执行：一次是流程图进度，批量是一行一次 -->
    <div v-else-if="single" class="space-y-4">
      <div class="flex items-center gap-2">
        <span class="font-medium">{{ recipe.name }}</span>
        <StatusPill :status="running ? 'running' : progress.status === 'failed' ? 'failed' : progress.runStatus ?? 'running'" />
      </div>
      <div v-if="progress.inputs.length" class="space-y-1">
        <div v-for="i in progress.inputs" :key="i.alias" class="flex flex-wrap items-center gap-1.5 text-xs text-mute">
          <Sheet class="size-3.5" /><b class="font-medium text-ink">{{ i.alias }}</b> ← {{ i.filename }}<template v-if="i.kind !== 'file'"> · {{ i.sheet }}（{{ i.rows }} 行）</template><span v-else>（完整文件）</span>
          <span v-for="(std, raw) in i.renames" :key="raw" class="rounded bg-info/10 px-1.5 text-info">{{ raw }} → {{ std }}</span>
          <span v-for="w in i.warnings" :key="w" class="rounded bg-warning/10 px-1.5 text-[#8a5a00]">{{ w }}</span>
        </div>
      </div>
      <RecipeFlow :flow="flow" :states="progress.steps" :context="{
        title: recipe.name, recipeId: recipe.recipe_id, version: recipe.version, customerId, conversationId,
        recipe: recipe.recipe, files: pool, resultFiles: outputFiles,
        runId: progress.runId, running, failed: progress.status === 'failed' || !!progress.failed,
      }" @changed="emit('done')" />
      <p v-if="progress.validation" class="text-xs text-mute">运行校验：{{ progress.validation.summary }}</p>
      <div v-if="progress.escalation || progress.failed" class="whitespace-pre-wrap rounded-xl bg-error/5 px-3 py-2.5 text-[13px] leading-relaxed text-error">{{ progress.escalation || progress.failed }}</div>
      <p v-else-if="progress.runSummary" class="text-sm text-mute">{{ progress.runSummary }}</p>
      <div v-if="outputFiles.length" class="space-y-2">
        <div v-for="file in outputFiles" :key="file.file_id" class="flex items-center gap-2 rounded-xl border border-line px-3 py-2">
          <Sheet class="size-4 shrink-0 text-[#4a4743]" />
          <span class="min-w-0 flex-1 truncate text-sm">{{ file.filename }}</span>
          <button class="btn-text text-xs" @click="previewOutput(file)"><Eye class="size-3.5" />查看</button>
          <a :href="file.download_url" class="btn-text text-xs"><Download class="size-3.5" />下载</a>
        </div>
      </div>
    </div>

    <div v-else class="space-y-3">
      <div class="flex items-center gap-2">
        <span class="font-medium">{{ recipe.name }}</span>
        <span class="text-xs tabular-nums text-mute">{{ finished }}/{{ items.length }}<template v-if="failedCount">，{{ failedCount }} 个失败</template></span>
        <LoaderCircle v-if="running" class="size-3.5 animate-spin text-info" />
      </div>
      <ol class="divide-y divide-line rounded-2xl border border-line">
        <li v-for="(item, index) in items" :key="index" class="px-4 py-2.5">
          <div class="flex items-center gap-3">
            <span class="w-5 shrink-0 text-right text-xs tabular-nums text-faint">{{ index + 1 }}</span>
            <span class="min-w-0 flex-1 truncate text-sm" :title="item.title">{{ item.title }}</span>
            <span v-if="item.state === 'wait'" class="shrink-0 text-xs text-faint">等待</span>
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
    </div>

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
