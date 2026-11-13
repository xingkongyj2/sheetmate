<script setup lang="ts">
/**
 * 用新文件执行一个流程（客户页）。流程已经录制好：
 * 每个输入一个位置，默认是录制时的样例文件，也可以换成已有文件或上传新文件，选好直接执行。
 * 正常情况下不调用 AI；出错时可选让 AI 自动修复。
 */
import { Download, Eye, FileSpreadsheet, LoaderCircle, Play, TriangleAlert, Upload } from '@lucide/vue'
import { computed, defineAsyncComponent, reactive, ref } from 'vue'

import { customerApi, fileApi, recipeApi } from '@/api'
import { errorText } from '@/api/http'
import type { CustomerWorkflow, FileInfo } from '@/api/types'
import BaseModal from '@/components/common/BaseModal.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import RecipeFlow from '@/components/recipe/RecipeFlow.vue'
import { addProgressFile, applyRecipeEvent, newProgress, type FileArtifact, type RecipeProgress } from '@/composables/chatModel'
import { progressFlow } from '@/composables/recipeFlow'
import { inputKey, missingColumns, pickSheet, sampleFile, type RunInput } from '@/composables/runInputs'
import { useToast } from '@/composables/useToast'

const WorkbookPreview = defineAsyncComponent(() => import('@/features/workbook-preview/WorkbookPreview.vue'))

const props = defineProps<{ customerId: string; recipe: CustomerWorkflow }>()
const emit = defineEmits<{ close: []; done: [] }>()
const toast = useToast()

const inputs = computed(() => props.recipe.recipe.inputs)
// 可选的文件：这个流程已有的资料（含历史上传），新上传的排在最前
const pool = ref<FileInfo[]>(props.recipe.files.filter((file) => file.kind === 'input'))
const choice = reactive<Record<string, { fileId: string; sheet: string }>>({})
for (const input of inputs.value) {
  const sample = sampleFile(props.recipe.recipe, input, pool.value)
  choice[input.alias] = { fileId: sample?.file_id ?? '', sheet: pickSheet(input, sample) }
}

const stage = ref<'pick' | 'run'>('pick')
const uploading = ref<string | null>(null)
const allowRepair = ref(true)
const progress = reactive<RecipeProgress>(newProgress('replay'))
const outputFiles = ref<FileArtifact[]>([])
const running = ref(false)
const preview = ref<FileInfo | null>(null)
const loadingPreview = ref(false)

const fileOf = (alias: string) => pool.value.find((file) => file.file_id === choice[alias]?.fileId) ?? null
/** 这个输入能用哪些文件：完整文件输入不限，数据表输入需要有工作表 */
const candidates = (input: RunInput) => pool.value.filter((file) => input.kind === 'file' || file.sheets.length)
const keyOf = (input: RunInput) => inputKey(input, fileOf(input.alias), choice[input.alias]?.sheet ?? '')
const missing = (input: RunInput) => missingColumns(input, fileOf(input.alias), choice[input.alias]?.sheet ?? '')
const ready = computed(() => inputs.value.length > 0 && inputs.value.every((input) => keyOf(input)))

function select(input: RunInput, fileId: string) {
  choice[input.alias] = { fileId, sheet: pickSheet(input, pool.value.find((file) => file.file_id === fileId) ?? null) }
}

async function upload(input: RunInput, e: Event) {
  const target = e.target as HTMLInputElement
  const file = target.files?.[0]
  target.value = ''
  if (!file) return
  uploading.value = input.alias
  try {
    const info = await customerApi.uploadFile(props.customerId, file, 'input', props.recipe.recipe_id)
    pool.value.unshift(info)
    select(input, info.file_id)
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
  const handle = recipeApi.run(props.recipe.recipe_id, { file_ids: fileIds, inputs: mapping, allow_repair: allowRepair.value }, (e) => {
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
      <p class="text-sm text-mute">流程已经录好，为每份资料选一个文件：默认是录制时的样例，也可以上传新文件。</p>
      <div v-for="input in inputs" :key="input.alias" class="rounded-2xl border border-line p-3.5">
        <div class="flex flex-wrap items-baseline gap-x-2">
          <span class="font-medium">{{ input.alias }}</span>
          <span class="text-xs text-faint">{{ input.kind === 'file' ? '完整文件' : input.required_columns.length ? `需要列：${input.required_columns.join('、')}` : '数据表' }}</span>
        </div>
        <div class="mt-2.5 flex items-center gap-2">
          <select class="select select-sm min-w-0 flex-1 rounded-xl" :value="choice[input.alias].fileId" :aria-label="`${input.alias} 使用的文件`" @change="select(input, ($event.target as HTMLSelectElement).value)">
            <option value="" disabled>请选择文件，或上传新文件</option>
            <option v-for="file in candidates(input)" :key="file.file_id" :value="file.file_id">
              {{ file.filename }}{{ file.file_id === sampleFile(recipe.recipe, input, pool)?.file_id ? '（样例）' : '' }}
            </option>
          </select>
          <button v-if="fileOf(input.alias)" type="button" class="icon-btn shrink-0" title="查看文件" :aria-label="`查看文件 ${fileOf(input.alias)?.filename}`" @click="preview = fileOf(input.alias)"><Eye class="size-4" /></button>
          <label class="btn-line shrink-0 cursor-pointer py-1.5">
            <LoaderCircle v-if="uploading === input.alias" class="size-4 animate-spin" /><Upload v-else class="size-4" />上传新文件
            <input type="file" class="hidden" accept=".xlsx,.xlsm,.xls,.csv" :disabled="uploading !== null" @change="upload(input, $event)" />
          </label>
        </div>
        <div v-if="input.kind !== 'file' && fileOf(input.alias)" class="mt-2 flex flex-wrap items-center gap-2 text-xs text-mute">
          <template v-if="fileOf(input.alias)!.sheets.length > 1">
            读取工作表
            <select v-model="choice[input.alias].sheet" class="select select-xs rounded-lg" :aria-label="`${input.alias} 读取的工作表`">
              <option v-for="s in fileOf(input.alias)!.sheets" :key="s.sheet" :value="s.sheet">{{ s.sheet }}（{{ s.rows }} 行）</option>
            </select>
          </template>
          <span v-else>{{ choice[input.alias].sheet }} · {{ fileOf(input.alias)!.sheets[0]?.rows }} 行</span>
        </div>
        <p v-if="missing(input).length" class="mt-2 flex items-start gap-1.5 text-xs text-[#8a5a00]">
          <TriangleAlert class="mt-0.5 size-3.5 shrink-0" />这张表里没找到：{{ missing(input).join('、') }}。列名不一样时系统会按已有的对应关系处理，仍可能执行失败。
        </p>
      </div>
      <p v-if="!inputs.length" class="py-6 text-center text-sm text-faint">这个流程没有需要上传的资料</p>
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
        <label class="mr-auto flex items-center gap-2 text-xs text-mute" title="修复只用于本次执行，业务规则不变">
          <input v-model="allowRepair" type="checkbox" class="toggle toggle-xs" />出错时让 AI 自动修复
        </label>
        <button class="btn-line" @click="emit('close')">取消</button>
        <button class="btn-ink" :disabled="!ready || uploading !== null" @click="run"><Play class="size-4" />开始执行</button>
      </template>
      <template v-else>
        <button class="btn-line" :disabled="running" @click="stage = 'pick'">换文件再跑</button>
        <button class="btn-ink" :disabled="running" @click="emit('close')">{{ running ? '执行中…' : '完成' }}</button>
      </template>
    </template>

    <WorkbookPreview v-if="preview" :key="preview.file_id" :file="preview" @close="preview = null" />
  </BaseModal>
</template>
