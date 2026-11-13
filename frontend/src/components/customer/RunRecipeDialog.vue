<script setup lang="ts">
/**
 * 用新文件执行一个流程（客户页）。流程已经录制好：
 * 每个输入一个位置，默认是录制时的样例文件，也可以换成已有文件或上传新文件，选好直接执行。
 * 正常情况下不调用 AI；出错时可选让 AI 自动修复。
 */
import { Check, ChevronDown, Download, Eye, FileSpreadsheet, LoaderCircle, Play, TriangleAlert, Upload, X } from '@lucide/vue'
import { computed, defineAsyncComponent, reactive, ref } from 'vue'

import { customerApi, fileApi, recipeApi } from '@/api'
import { errorText } from '@/api/http'
import type { CustomerWorkflow, FileInfo } from '@/api/types'
import BaseModal from '@/components/common/BaseModal.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import RecipeFlow from '@/components/recipe/RecipeFlow.vue'
import { addProgressFile, applyRecipeEvent, newProgress, timeAgo, type FileArtifact, type RecipeProgress } from '@/composables/chatModel'
import { progressFlow } from '@/composables/recipeFlow'
import { inputKey, missingColumns, pickSheet, sampleFile, type RunInput } from '@/composables/runInputs'
import { useToast } from '@/composables/useToast'

const WorkbookPreview = defineAsyncComponent(() => import('@/features/workbook-preview/WorkbookPreview.vue'))

const props = defineProps<{ customerId: string; recipe: CustomerWorkflow }>()
const emit = defineEmits<{ close: []; done: [] }>()
const toast = useToast()

const inputs = computed(() => props.recipe.recipe.inputs)
// 默认都是空的，需要上传新文件；也可以一键带入录制时的样例文件
const pool = ref<FileInfo[]>(props.recipe.files.filter((file) => file.kind === 'input'))
const choice = reactive<Record<string, { fileId: string; sheet: string }>>(
  Object.fromEntries(inputs.value.map((input) => [input.alias, { fileId: '', sheet: '' }])))
/** 提示用户要传哪个文件：录制时的文件名 */
const expectedName = (input: RunInput) => input.source_file || sampleOf(input)?.filename || input.alias
const sampleOf = (input: RunInput) => sampleFile(props.recipe.recipe, input, pool.value)

const stage = ref<'pick' | 'run'>('pick')
const uploading = ref<string | null>(null)
const allowRepair = true  // 出错时让 AI 自动修复，始终开启
const progress = reactive<RecipeProgress>(newProgress('replay'))
const outputFiles = ref<FileArtifact[]>([])
const running = ref(false)
const preview = ref<FileInfo | null>(null)
const loadingPreview = ref(false)

const fileOf = (alias: string) => pool.value.find((file) => file.file_id === choice[alias]?.fileId) ?? null
const keyOf = (input: RunInput) => inputKey(input, fileOf(input.alias), choice[input.alias]?.sheet ?? '')
const missing = (input: RunInput) => missingColumns(input, fileOf(input.alias), choice[input.alias]?.sheet ?? '')
/** 这个输入可以从已上传的文件里选哪些：完整文件输入不限，数据表输入需要有工作表 */
const uploaded = (input: RunInput) => pool.value.filter((file) => input.kind === 'file' || file.sheets.length)
const multi = computed(() => inputs.value.length > 1)
const pendingSamples = computed(() => inputs.value.filter((input) => sampleOf(input) && sampleOf(input)!.file_id !== choice[input.alias].fileId))
const ready = computed(() => inputs.value.length > 0 && inputs.value.every((input) => keyOf(input)))

function select(input: RunInput, file: FileInfo) {
  menuFor.value = null
  choice[input.alias] = { fileId: file.file_id, sheet: pickSheet(input, file) }
}

function clear(input: RunInput) {
  choice[input.alias] = { fileId: '', sheet: '' }
}

function useAllSamples() {
  for (const input of pendingSamples.value) select(input, sampleOf(input)!)
}

const dragging = ref<string | null>(null)
const menuFor = ref<string | null>(null)  // 正在从已上传文件里选择的输入
const pickerInput = computed(() => inputs.value.find((input) => input.alias === menuFor.value) ?? null)

function pick(input: RunInput, e: Event) {
  const target = e.target as HTMLInputElement
  const file = target.files?.[0]
  target.value = ''
  if (file) void upload(input, file)
}

function drop(input: RunInput, e: DragEvent) {
  dragging.value = null
  const file = e.dataTransfer?.files?.[0]
  if (file && uploading.value === null) void upload(input, file)
}

async function upload(input: RunInput, file: File) {
  uploading.value = input.alias
  try {
    const info = await customerApi.uploadFile(props.customerId, file, 'input', props.recipe.recipe_id)
    pool.value.unshift(info)
    select(input, info)
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    uploading.value = null
  }
}

async function previewOutput(file: FileArtifact) {
  if (loadingPreview.value) return
  loadingPreview.value = true
  try {
    preview.value = await fileApi.get(file.file_id)
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    loadingPreview.value = false
  }
}

async function run() {
  if (!ready.value || running.value) return
  const mapping = Object.fromEntries(inputs.value.map((input) => [input.alias, keyOf(input)]))
  const fileIds = [...new Set(Object.values(mapping).map((key) => key.split('::')[0]))]
  stage.value = 'run'
  running.value = true
  Object.assign(progress, newProgress('replay'))
  outputFiles.value = []
  const handle = recipeApi.run(props.recipe.recipe_id, { file_ids: fileIds, inputs: mapping, allow_repair: allowRepair }, (e) => {
    if (e.type.startsWith('recipe_')) applyRecipeEvent(progress, e)
    else if (e.type === 'file') {
      const file = { file_id: e.file_id, filename: e.filename, download_url: e.download_url, sheets: e.sheets }
      addProgressFile(progress, file)
      if (!outputFiles.value.some((item) => item.file_id === file.file_id)) outputFiles.value.push(file)
    } else if (e.type === 'error') progress.failed = e.content
  })
  try {
    await handle.done
  } catch (err) {
    progress.failed = errorText(err)
    progress.status = 'failed'
  } finally {
    running.value = false
    emit('done')
  }
}

const flow = computed(() => progressFlow({ ...progress, flow: props.recipe.flow }))
</script>

<template>
  <BaseModal :title="`执行「${recipe.name}」`" width="max-w-3xl" @close="emit('close')">
    <!-- 1. 每个输入选一个文件 -->
    <div v-if="stage === 'pick'" class="space-y-3">
      <div v-for="input in inputs" :key="input.alias">
        <div
          class="flex items-center gap-3 rounded-2xl border px-4 py-3 transition"
          :class="dragging === input.alias ? 'border-ink bg-paper' : 'border-line'"
          @dragover.prevent="dragging = input.alias" @dragleave="dragging = null" @drop.prevent="drop(input, $event)"
        >
          <template v-if="fileOf(input.alias)">
            <Check class="size-4 shrink-0 text-success" />
            <span class="min-w-0 flex-1 truncate text-sm font-medium">{{ fileOf(input.alias)!.filename }}</span>
            <button type="button" class="icon-btn shrink-0" title="查看文件" :aria-label="`查看文件 ${fileOf(input.alias)!.filename}`" @click="preview = fileOf(input.alias)"><Eye class="size-4" /></button>
            <button type="button" class="icon-btn shrink-0" title="清除选择" :aria-label="`清除 ${input.alias} 的选择`" @click="clear(input)"><X class="size-4" /></button>
          </template>
          <p v-else class="min-w-0 flex-1 text-sm text-mute">请上传新的文件：<span class="break-all font-medium text-ink">{{ expectedName(input) }}</span></p>
          <label class="btn-line h-8 shrink-0 cursor-pointer gap-1.5 px-3 py-0 text-xs">
            <LoaderCircle v-if="uploading === input.alias" class="size-3.5 animate-spin" /><Upload v-else class="size-3.5" />{{ fileOf(input.alias) ? '重新上传' : '上传' }}
            <input type="file" class="hidden" accept=".xlsx,.xlsm,.xls,.csv" :disabled="uploading !== null" @change="pick(input, $event)" />
          </label>
          <button v-if="uploaded(input).length" type="button" class="btn-text h-8 shrink-0 gap-1 px-2 py-0 text-xs" aria-haspopup="dialog" @click="menuFor = input.alias">已上传<ChevronDown class="size-3.5" /></button>
          <button v-if="sampleOf(input) && sampleOf(input)!.file_id !== choice[input.alias].fileId" type="button" class="btn-text h-8 shrink-0 px-2 py-0 text-xs" @click="select(input, sampleOf(input)!)">样例文件</button>
        </div>
        <select v-if="input.kind !== 'file' && (fileOf(input.alias)?.sheets.length ?? 0) > 1" v-model="choice[input.alias].sheet" class="select select-sm mt-2 w-full rounded-xl" :aria-label="`${input.alias} 读取的工作表`">
          <option v-for="s in fileOf(input.alias)!.sheets" :key="s.sheet" :value="s.sheet">{{ s.sheet }}（{{ s.rows }} 行）</option>
        </select>
        <p v-if="missing(input).length" class="mt-2 flex items-start gap-1.5 px-1 text-xs leading-relaxed text-[#8a5a00]">
          <TriangleAlert class="mt-0.5 size-3.5 shrink-0" /><span>所选文件缺少流程需要的列：{{ missing(input).join('、') }}。请核对是否选错了文件或工作表，列名不同的话执行可能失败。</span>
        </p>
      </div>
    </div>

    <!-- 2. 执行 -->
    <div v-else class="space-y-4">
      <div class="flex items-center gap-2">
        <span class="font-medium">{{ recipe.name }}</span>
        <StatusPill :status="running ? 'running' : progress.status === 'failed' ? 'failed' : progress.runStatus ?? 'running'" />
      </div>
      <div v-if="progress.inputs.length" class="space-y-1">
        <div v-for="i in progress.inputs" :key="i.alias" class="flex flex-wrap items-center gap-1.5 text-xs text-mute">
          <FileSpreadsheet class="size-3.5" /><b class="font-medium text-ink">{{ i.alias }}</b> ← {{ i.filename }}<template v-if="i.kind !== 'file'"> · {{ i.sheet }}（{{ i.rows }} 行）</template><span v-else>（完整文件）</span>
          <span v-for="(std, raw) in i.renames" :key="raw" class="rounded bg-info/10 px-1.5 text-info">{{ raw }} → {{ std }}</span>
          <span v-for="w in i.warnings" :key="w" class="rounded bg-warning/10 px-1.5 text-[#8a5a00]">{{ w }}</span>
        </div>
      </div>
      <RecipeFlow :flow="flow" :states="progress.steps" :context="{
        title: recipe.name, recipeId: recipe.recipe_id, version: recipe.version, customerId,
        recipe: recipe.recipe, files: pool, resultFiles: outputFiles,
        runId: progress.runId, running, failed: progress.status === 'failed' || !!progress.failed,
      }" @changed="emit('done')" />
      <p v-if="progress.validation" class="text-xs text-mute">运行校验：{{ progress.validation.summary }}</p>
      <div v-if="progress.escalation || progress.failed" class="whitespace-pre-wrap rounded-xl bg-error/5 px-3 py-2.5 text-[13px] leading-relaxed text-error">{{ progress.escalation || progress.failed }}</div>
      <p v-else-if="progress.runSummary" class="text-sm text-mute">{{ progress.runSummary }}</p>
      <div v-if="outputFiles.length" class="space-y-2">
        <div v-for="file in outputFiles" :key="file.file_id" class="flex items-center gap-2 rounded-xl border border-line px-3 py-2">
          <FileSpreadsheet class="size-4 shrink-0 text-fun-green" />
          <span class="min-w-0 flex-1 truncate text-sm">{{ file.filename }}</span>
          <button class="btn-text text-xs" :disabled="loadingPreview" @click="previewOutput(file)"><Eye class="size-3.5" />查看</button>
          <a :href="file.download_url" class="btn-text text-xs"><Download class="size-3.5" />下载</a>
        </div>
      </div>
    </div>

    <template #footer>
      <template v-if="stage === 'pick'">
        <button v-if="multi && pendingSamples.length > 1" type="button" class="btn-text mr-auto text-xs" @click="useAllSamples">全部使用样例</button>
        <button class="btn-line" @click="emit('close')">取消</button>
        <button class="btn-ink" :disabled="!ready || uploading !== null" @click="run"><Play class="size-4" />开始执行</button>
      </template>
      <template v-else>
        <button class="btn-line" :disabled="running" @click="stage = 'pick'">换文件再跑</button>
        <button class="btn-ink" :disabled="running" @click="emit('close')">{{ running ? '执行中…' : '完成' }}</button>
      </template>
    </template>

    <BaseModal v-if="pickerInput" title="选择已上传的文件" width="max-w-md" @close="menuFor = null">
      <ul class="-mx-2 space-y-0.5" role="listbox" aria-label="已上传的文件">
        <li v-for="file in uploaded(pickerInput)" :key="file.file_id" role="option" :aria-selected="file.file_id === choice[pickerInput.alias].fileId">
          <button type="button" class="flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left hover:bg-paper focus-visible:bg-paper focus-visible:outline-none" @click="select(pickerInput, file)">
            <FileSpreadsheet class="size-4 shrink-0 text-fun-green" />
            <span class="min-w-0 flex-1 truncate text-sm">{{ file.filename }}</span>
            <span class="shrink-0 text-xs text-faint">{{ timeAgo(file.created_at) }}</span>
            <Check v-if="file.file_id === choice[pickerInput.alias].fileId" class="size-4 shrink-0 text-success" />
          </button>
        </li>
      </ul>
    </BaseModal>
    <WorkbookPreview v-if="preview" :key="preview.file_id" :file="preview" @close="preview = null" />
  </BaseModal>
</template>
