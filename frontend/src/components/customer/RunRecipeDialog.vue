<script setup lang="ts">
/**
 * 用新文件重复执行流程（客户页）：
 * 1) 选流程、上传新文件（或勾选客户资料里的文件）
 * 2) 自动匹配「哪个文件对应哪个输入」，可以手动改
 * 3) 执行：每一步实时显示，正常情况下不调用 AI；出错时可选让 AI 自动修复
 */
import { Download, Eye, FileSpreadsheet, LoaderCircle, Play, Upload } from '@lucide/vue'
import { computed, defineAsyncComponent, reactive, ref } from 'vue'

import { customerApi, fileApi, recipeApi } from '@/api'
import { errorText } from '@/api/http'
import type { FileInfo, RecipeBrief, RecipeFlowData } from '@/api/types'
import BaseModal from '@/components/common/BaseModal.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import RecipeFlow from '@/components/recipe/RecipeFlow.vue'
import { addProgressFile, applyRecipeEvent, newProgress, type FileArtifact, type RecipeProgress } from '@/composables/chatModel'
import { progressFlow } from '@/composables/recipeFlow'
import { useToast } from '@/composables/useToast'

const WorkbookPreview = defineAsyncComponent(() => import('@/features/workbook-preview/WorkbookPreview.vue'))

const props = defineProps<{ customerId: string; recipes: (RecipeBrief & { flow?: unknown })[]; files: FileInfo[]; initialRecipeId?: string | null }>()
const emit = defineEmits<{ close: []; done: [] }>()
const toast = useToast()

const stage = ref<'pick' | 'match' | 'run'>('pick')
const recipeId = ref(props.initialRecipeId ?? props.recipes[0]?.recipe_id ?? '')
const localFiles = ref<FileInfo[]>([...props.files.filter((f) => f.kind === 'input')])
const chosen = ref<string[]>([])
const uploading = ref(false)
const allowRepair = ref(true)
const matching = ref(false)
const match = ref<Awaited<ReturnType<typeof recipeApi.match>> | null>(null)
const mapping = ref<Record<string, string>>({})
const progress = reactive<RecipeProgress>(newProgress('replay'))
const outputFiles = ref<FileArtifact[]>([])
const running = ref(false)
const preview = ref<FileInfo | null>(null)
const loadingPreview = ref(false)

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

const recipe = computed(() => props.recipes.find((r) => r.recipe_id === recipeId.value))

async function upload(e: Event) {
  const input = e.target as HTMLInputElement
  const list = Array.from(input.files ?? [])
  input.value = ''
  uploading.value = true
  try {
    for (const f of list) {
      const info = await customerApi.uploadFile(props.customerId, f, 'input', recipeId.value)
      localFiles.value.unshift(info)
      chosen.value.push(info.file_id)
    }
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    uploading.value = false
  }
}

async function doMatch() {
  if (!recipeId.value || !chosen.value.length) return
  matching.value = true
  try {
    match.value = await recipeApi.match(recipeId.value, chosen.value)
    mapping.value = Object.fromEntries(match.value.inputs.map((i) => [i.alias, i.match?.key ?? '']))
    stage.value = 'match'
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    matching.value = false
  }
}

async function run() {
  stage.value = 'run'
  running.value = true
  Object.assign(progress, newProgress('replay'))
  outputFiles.value = []
  const fileIds = [...new Set(Object.values(mapping.value).filter(Boolean).map((k) => k.split('::')[0]))]
  const handle = recipeApi.run(recipeId.value, { file_ids: fileIds, inputs: mapping.value, allow_repair: allowRepair.value }, (e) => {
    if (e.type.startsWith('recipe_')) applyRecipeEvent(progress, e)
    else if (e.type === 'file') {
      const file = { file_id: e.file_id, filename: e.filename, download_url: e.download_url, sheets: e.sheets }
      addProgressFile(progress, file)
      if (!outputFiles.value.some((item) => item.file_id === file.file_id)) outputFiles.value.push(file)
    }
    else if (e.type === 'error') progress.failed = e.content
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

const flow = computed(() => {
  const snapshot = recipe.value?.flow as RecipeFlowData | undefined
  return snapshot ? progressFlow({ ...progress, flow: snapshot }) : null
})
</script>

<template>
  <BaseModal title="用新文件执行流程" width="max-w-5xl" @close="emit('close')">
    <!-- 1. 选流程和文件 -->
    <div v-if="stage === 'pick'" class="space-y-5">
      <div>
        <div class="label-mini mb-2">选择流程</div>
        <div class="grid gap-2 sm:grid-cols-2">
          <button
            v-for="r in recipes"
            :key="r.recipe_id"
            class="rounded-2xl border p-3 text-left transition"
            :class="recipeId === r.recipe_id ? 'border-ink' : 'border-line hover:bg-paper'"
            @click="recipeId = r.recipe_id"
          >
            <div class="flex items-center gap-2"><span class="font-medium">{{ r.name }}</span><span class="tag !bg-[#6265DC]/10 !text-[#6265DC]">v{{ r.version }}</span></div>
            <div class="mt-1 line-clamp-2 text-xs text-mute">{{ r.description }}</div>
            <div class="mt-2 text-[11px] text-faint">需要：{{ r.input_patterns.map((i) => `${i.alias}（${i.file_pattern || '任意文件'}）`).join('、') }}</div>
          </button>
        </div>
      </div>
      <div>
        <div class="mb-2 flex items-center justify-between">
          <span class="label-mini">选择这次要处理的文件</span>
          <label class="btn-line cursor-pointer py-1.5">
            <LoaderCircle v-if="uploading" class="size-4 animate-spin" /><Upload v-else class="size-4" />上传新文件
            <input type="file" class="hidden" multiple accept=".xlsx,.xlsm,.xls,.csv" @change="upload" />
          </label>
        </div>
        <div class="max-h-64 space-y-1 overflow-y-auto">
          <label v-for="f in localFiles" :key="f.file_id" class="flex cursor-pointer items-center gap-3 rounded-xl border border-line px-3 py-2 hover:bg-paper">
            <input v-model="chosen" type="checkbox" class="checkbox checkbox-sm" :value="f.file_id" />
            <FileSpreadsheet class="size-4 text-fun-green" />
            <span class="min-w-0 flex-1 truncate text-sm">{{ f.filename }}</span>
            <span class="text-[11px] text-faint">{{ f.sheets.map((s) => `${s.sheet} ${s.rows} 行`).join(' / ') }}</span>
            <button type="button" class="icon-btn shrink-0" :aria-label="`查看文件 ${f.filename}`" title="查看文件" @click.prevent.stop="preview = f"><Eye class="size-4" /></button>
          </label>
          <p v-if="!localFiles.length" class="py-6 text-center text-sm text-faint">上传这个月的新文件</p>
        </div>
      </div>
    </div>

    <!-- 2. 匹配输入 -->
    <div v-else-if="stage === 'match' && match" class="space-y-4">
      <p class="text-sm text-mute">已按输入要求自动匹配。数据表按列名和文件名匹配，完整文件按文件名匹配；可以调整对应关系：</p>
      <div v-for="inp in match.inputs" :key="inp.alias" class="rounded-2xl border border-line p-3">
        <div class="flex items-center gap-2">
          <span class="font-medium">{{ inp.alias }}</span>
          <span class="text-xs text-faint">{{ inp.description }}</span>
          <span v-if="inp.match" class="ml-auto text-[11px] text-success">自动匹配 {{ Math.round(inp.match.score * 100) }} 分</span>
          <span v-else class="ml-auto text-[11px] text-error">没有找到合适的文件</span>
        </div>
        <select v-model="mapping[inp.alias]" class="select select-sm mt-2 w-full rounded-xl">
          <option value="">— 请选择 —</option>
          <option v-for="c in match.candidates.filter((candidate) => (candidate.kind ?? 'table') === (inp.kind ?? 'table'))" :key="c.key" :value="c.key">{{ c.filename }}{{ c.kind === 'file' ? '（完整文件）' : ` · ${c.sheet}（${c.rows} 行）` }}</option>
        </select>
        <div v-if="inp.kind !== 'file'" class="mt-1.5 text-[11px] text-faint">必需的列：{{ inp.required_columns.join('、') || '无' }}</div>
      </div>
      <label class="flex items-center gap-2 text-sm">
        <input v-model="allowRepair" type="checkbox" class="toggle toggle-sm" />
        出错时允许 AI 自动修复（修复用于本次执行，业务规则不变）
      </label>
    </div>

    <!-- 3. 执行 -->
    <div v-else class="space-y-4">
      <div class="flex items-center gap-2">
        <span class="font-medium">{{ recipe?.name }}</span>
        <StatusPill :status="running ? 'running' : progress.status === 'failed' ? 'failed' : progress.runStatus ?? 'running'" />
      </div>
      <div v-if="progress.inputs.length" class="space-y-1">
        <div v-for="i in progress.inputs" :key="i.alias" class="flex flex-wrap items-center gap-1.5 text-xs text-mute">
          <FileSpreadsheet class="size-3.5" /><b class="font-medium text-ink">{{ i.alias }}</b> ← {{ i.filename }}<template v-if="i.kind !== 'file'"> · {{ i.sheet }}（{{ i.rows }} 行）</template><span v-else>（完整文件）</span>
          <span v-for="(std, raw) in i.renames" :key="raw" class="rounded bg-info/10 px-1.5 text-info">{{ raw }} → {{ std }}</span>
          <span v-for="w in i.warnings" :key="w" class="rounded bg-warning/10 px-1.5 text-[#8a5a00]">{{ w }}</span>
        </div>
      </div>
      <RecipeFlow v-if="flow" :flow="flow" :states="progress.steps" :context="{
        title: recipe?.name, recipeId, version: recipe?.version, customerId,
        recipe: recipe?.recipe, files: localFiles, resultFiles: outputFiles,
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
        <button class="btn-line" @click="emit('close')">取消</button>
        <button class="btn-ink" :disabled="!recipeId || !chosen.length || matching" @click="doMatch">
          <LoaderCircle v-if="matching" class="size-4 animate-spin" />下一步：匹配输入
        </button>
      </template>
      <template v-else-if="stage === 'match'">
        <button class="btn-line" @click="stage = 'pick'">上一步</button>
        <button class="btn-ink" :disabled="Object.values(mapping).some((v) => !v)" @click="run"><Play class="size-4" />开始执行</button>
      </template>
      <template v-else>
        <button class="btn-line" :disabled="running" @click="emit('close')">{{ running ? '执行中…' : '完成' }}</button>
      </template>
    </template>

    <WorkbookPreview v-if="preview" :key="preview.file_id" :file="preview" @close="preview = null" />
  </BaseModal>
</template>
