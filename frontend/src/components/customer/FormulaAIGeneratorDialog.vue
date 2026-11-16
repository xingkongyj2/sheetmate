<script setup lang="ts">
import { Check, CircleCheck, LoaderCircle, Paperclip, Sparkles, TriangleAlert } from '@lucide/vue'
import { computed, ref } from 'vue'

import { customerApi, recipeApi } from '@/api'
import { errorText } from '@/api/http'
import type { CustomerFormula, FileInfo, FormulaGenerationResult, FormulaInfo } from '@/api/types'
import BaseModal from '@/components/common/BaseModal.vue'
import { useToast } from '@/composables/useToast'

/**
 * AI 生成 / 修改公式：描述 → 生成 → 检查 → 保存，一个弹窗做完。
 * formula 给了就是修改这个已有公式（保留原 id，保存成新版本）；没给就是新增。
 * takenIds：新增时已被占用的 id（客户已有的、内置的、系统保留的），AI 起的 id 撞了就自动加序号，不会悄悄覆盖。
 */
const props = defineProps<{ customerId?: string; files: FileInfo[]; formula?: Record<string, unknown> | null; takenIds?: string[]; scope?: 'customer' | 'global'; initialDescription?: string }>()
const emit = defineEmits<{ close: []; saved: [formula: CustomerFormula | FormulaInfo]; uploaded: [file: FileInfo] }>()
const toast = useToast()
/** 内置公式不绑定客户：参考表直接上传 */
const global = computed(() => props.scope === 'global')

const description = ref(props.initialDescription ?? '')
const initialRequest = ref('')
const supplements = ref<string[]>([])
const uploadedFiles = ref<FileInfo[]>([])
/** 选中的表：`文件id::工作表名`，按选择先后排序（第几张就是公式里第几个表参数的默认表） */
const selectedKeys = ref<string[]>(initialKeys())
const result = ref<FormulaGenerationResult | null>(null)
const latestValidDraft = ref<Record<string, unknown> | null>(props.formula ?? null)
const saving = ref(false)
const generating = ref(false)
const uploading = ref(false)
const error = ref('')
const problems = ref<string[]>([])
const validated = ref(false)
const lastRequest = ref('')

const files = computed(() => {
  const all = [...props.files, ...uploadedFiles.value]
  return [...new Map(all.filter((file) => /\.(xlsx|xlsm|xls|csv)$/i.test(file.filename)).map((file) => [file.file_id, file])).values()]
})
const busy = computed(() => generating.value || uploading.value || saving.value)
/** 一个文件一个标签（不管它有几张工作表）；选中后，多张工作表的文件在标签里再选具体哪一张 */
const fileOptions = computed(() => files.value.filter((file) => file.sheets.length).map((file) => {
  const key = selectedKeys.value.find((item) => item.startsWith(`${file.file_id}::`))
  return { file, selected: !!key, sheet: key ? splitKey(key).sheet : file.sheets[0].sheet }
}))
const canGenerate = computed(() => !!description.value.trim() && !busy.value)
const canUse = computed(() => validated.value && !!result.value && !result.value.questions.length && requestUnchanged.value && !busy.value)
const formulaName = computed(() => String(result.value?.formula.name ?? '生成的公式'))
const formulaIntent = computed(() => String(result.value?.formula.intent ?? ''))
const requestUnchanged = computed(() => requestKey() === lastRequest.value)
const placeholder = computed(() => result.value?.questions.length ? '回答上面的问题'
  : result.value ? '继续补充，或说说哪里要调整'
  : props.formula ? `说说要怎么改「${String(props.formula.name ?? '')}」，例如：改成大于等于`
  : '说说你想怎样处理表格，例如：只保留金额大于阈值的行，按金额从大到小排')

function keyOf(fileId: string, sheet: string) { return `${fileId}::${sheet}` }
function splitKey(key: string) {
  const at = key.indexOf('::')
  return { file_id: key.slice(0, at), sheet: key.slice(at + 2) }
}

/** 修改已有公式：预选它原来默认的表（参数里记着的 sample），找不到就留空让用户选 */
function initialKeys(): string[] {
  const params = (props.formula?.params ?? {}) as Record<string, { sample?: { file?: string; sheet?: string } | null }>
  const keys: string[] = []
  for (const param of Object.values(params)) {
    const sample = param?.sample
    if (!sample?.file) continue
    for (const file of props.files) {
      if (file.filename !== sample.file) continue
      const sheet = file.sheets.find((item) => item.sheet === sample.sheet) ?? file.sheets[0]
      if (sheet && !keys.includes(keyOf(file.file_id, sheet.sheet))) keys.push(keyOf(file.file_id, sheet.sheet))
      break
    }
  }
  return keys.slice(0, 5)
}

/** 选中 / 取消一个文件：选中时默认用它的第一张工作表 */
function toggleFile(file: FileInfo) {
  const prefix = `${file.file_id}::`
  selectedKeys.value = selectedKeys.value.some((key) => key.startsWith(prefix))
    ? selectedKeys.value.filter((key) => !key.startsWith(prefix))
    : [...selectedKeys.value, keyOf(file.file_id, file.sheets[0].sheet)]
}
/** 换这个文件用的工作表：保持它在已选表里的先后位置（第几张表就是公式里第几个表参数的默认表） */
function pickSheet(file: FileInfo, sheet: string) {
  const prefix = `${file.file_id}::`
  const at = selectedKeys.value.findIndex((key) => key.startsWith(prefix))
  if (at < 0) return
  selectedKeys.value = selectedKeys.value.map((key, index) => (index === at ? keyOf(file.file_id, sheet) : key))
    .filter((key, index) => index === at || !key.startsWith(prefix))
}

function requestKey() {
  return JSON.stringify({ description: description.value.trim(), tables: selectedKeys.value })
}

function fullDescription(input: string) {
  if (!initialRequest.value) return input
  const history = supplements.value.join('\n\n').slice(-1800)
  const questions = result.value?.questions.join('\n').slice(0, 800)
  return [
    `最初的处理要求：\n${initialRequest.value}`,
    history ? `之前的补充：\n${history}` : '',
    questions ? `上一轮需要确认的问题：\n${questions}` : '',
    `本次补充或调整：\n${input}`,
  ].filter(Boolean).join('\n\n')
}

async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  const chosen = Array.from(input.files ?? [])
  input.value = ''
  if (!chosen.length) return
  if (selectedKeys.value.length >= 5) {
    error.value = '最多选择 5 张表，请先取消一些再上传。'
    toast.error(error.value)
    return
  }
  uploading.value = true
  error.value = ''
  try {
    for (const file of chosen) {
      const saved = global.value ? await recipeApi.uploadGlobalFormulaFile(file) : await customerApi.uploadFile(props.customerId!, file, 'input')
      uploadedFiles.value.push(saved)
      // 新上传的文件直接选上，默认用它的第一张工作表（超过 5 张表就不再自动选）
      if (saved.sheets.length && selectedKeys.value.length < 5) selectedKeys.value.push(keyOf(saved.file_id, saved.sheets[0].sheet))
      emit('uploaded', saved)
    }
  } catch (err) {
    error.value = errorText(err)
    toast.error(error.value)
  } finally {
    uploading.value = false
  }
}

async function generate() {
  if (!canGenerate.value) return
  // 客户公式一张表都没选就不生成，也就没有可保存的公式：弹个提示；内置公式不针对某张表，参考表可选
  if (!selectedKeys.value.length && !global.value) {
    toast.error(fileOptions.value.length ? '请先选择要处理的表' : '请先上传要处理的表')
    return
  }
  generating.value = true
  validated.value = false
  error.value = ''
  problems.value = []
  const input = description.value.trim()
  try {
    const request = {
      description: fullDescription(input),
      tables: selectedKeys.value.map(splitKey),
      file_ids: [...new Set(selectedKeys.value.map((key) => splitKey(key).file_id))],
      ...(latestValidDraft.value ? { formula: latestValidDraft.value } : {}),
    }
    const generated = global.value ? await recipeApi.generateGlobalFormula(request) : await customerApi.generateFormula(props.customerId!, request)
    result.value = {
      formula: generated.formula ?? {},
      explanation: generated.explanation ?? '',
      warnings: generated.warnings ?? [],
      questions: generated.questions ?? [],
    }
    if (!initialRequest.value) initialRequest.value = input
    else supplements.value = [...supplements.value, input].slice(-4)
    description.value = ''
    lastRequest.value = requestKey()
    if (result.value.questions.length) return
    if (!result.value.formula.id || !result.value.formula.sql) {
      problems.value = ['还没有生成完整的公式，请补充处理规则后再试。']
      return
    }
    problems.value = global.value ? await recipeApi.checkGlobalFormula(result.value.formula) : await customerApi.checkFormula(props.customerId!, result.value.formula)
    validated.value = !problems.value.length
    if (validated.value) latestValidDraft.value = result.value.formula
  } catch (err) {
    error.value = errorText(err)
    toast.error(error.value)
  } finally {
    generating.value = false
  }
}

/** 修改：固定用原来的 id。新增：id 被占用就加序号 */
function finalId(id: string): string {
  if (props.formula) return String(props.formula.id)
  const taken = new Set(props.takenIds ?? [])
  if (!taken.has(id)) return id
  let n = 2
  while (taken.has(`${id.slice(0, 60)}_${n}`)) n++
  return `${id.slice(0, 60)}_${n}`
}

async function save() {
  if (!canUse.value || !result.value) return
  saving.value = true
  error.value = ''
  try {
    const body = { ...result.value.formula, id: finalId(String(result.value.formula.id)) }
    emit('saved', global.value ? await recipeApi.saveGlobalFormula(body) : await customerApi.saveFormula(props.customerId!, body))
  } catch (err) {
    error.value = errorText(err)
    toast.error(error.value)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <BaseModal :title="formula ? 'AI 修改公式' : scope === 'global' ? 'AI 生成内置公式' : 'AI 生成公式'" width="max-w-2xl" @close="busy ? undefined : emit('close')">
    <div class="space-y-5" data-testid="formula-ai-generator">
      <!-- 描述 + 参考文件放在同一个输入区里：写在上面，文件标签在下面 -->
      <div class="rounded-2xl border border-line bg-white transition focus-within:border-ink/30">
        <textarea
          v-model="description"
          class="block min-h-32 w-full resize-y rounded-t-2xl bg-transparent px-4 pb-1 pt-3.5 text-sm leading-7 outline-none placeholder:text-faint"
          :placeholder="placeholder"
          :aria-label="placeholder"
          :disabled="busy"
          :maxlength="initialRequest ? 3000 : 6000"
          data-testid="formula-ai-description"
        />
        <div class="flex flex-wrap items-center gap-2 px-3 pb-3 pt-1">
          <label class="inline-flex size-8 shrink-0 cursor-pointer items-center justify-center rounded-full border border-line text-mute transition hover:border-ink/30 hover:text-ink" :class="busy ? 'pointer-events-none opacity-50' : ''" title="上传参考文件，让 AI 看到真实的列名和数据" aria-label="上传参考文件">
            <LoaderCircle v-if="uploading" class="size-4 animate-spin" /><Paperclip v-else class="size-4" />
            <input type="file" multiple accept=".xlsx,.xlsm,.xls,.csv" class="hidden" :disabled="busy" data-testid="formula-ai-upload" @change="upload" />
          </label>
          <div
            v-for="item in fileOptions"
            :key="item.file.file_id"
            class="inline-flex max-w-full items-center rounded-full border text-xs transition"
            :class="[item.selected ? 'border-ink bg-ink text-white' : 'border-line text-mute hover:border-ink/30', busy || (selectedKeys.length >= 5 && !item.selected) ? 'opacity-40' : '']"
          >
            <button
              type="button"
              class="max-w-[16rem] truncate rounded-full px-3 py-1"
              :title="item.file.filename"
              :aria-pressed="item.selected"
              :disabled="busy || (selectedKeys.length >= 5 && !item.selected)"
              data-testid="formula-ai-table"
              @click="toggleFile(item.file)"
            >{{ item.file.filename }}</button>
            <select
              v-if="item.selected && item.file.sheets.length > 1"
              class="mr-1 max-w-[9rem] cursor-pointer truncate border-l border-white/30 bg-transparent py-1 pl-2 pr-1 text-xs text-white/85 outline-none"
              :value="item.sheet"
              :title="`工作表：${item.sheet}`"
              :aria-label="`${item.file.filename} 用哪张工作表`"
              :disabled="busy"
              data-testid="formula-ai-sheet"
              @change="pickSheet(item.file, ($event.target as HTMLSelectElement).value)"
            >
              <option v-for="sheet in item.file.sheets" :key="sheet.sheet" :value="sheet.sheet" class="bg-white text-ink">{{ sheet.sheet }}</option>
            </select>
          </div>
        </div>
      </div>

      <details v-if="initialRequest" class="px-1 text-xs leading-6 text-mute">
        <summary class="cursor-pointer">最初的要求</summary>
        <p class="mt-1.5 whitespace-pre-wrap break-words">{{ initialRequest }}</p>
      </details>

      <p v-if="error" class="rounded-xl bg-error/5 px-4 py-3 text-[13px] leading-6 text-error" role="alert">{{ error }}</p>

      <section v-if="result" class="space-y-3 rounded-2xl bg-[#f0f8f5] p-4 sm:p-5" data-testid="formula-ai-result">
        <div class="flex items-start gap-3">
          <span class="flex size-9 shrink-0 items-center justify-center rounded-xl bg-white text-[#318b76]"><Sparkles class="size-4" /></span>
          <div class="min-w-0 flex-1">
            <h4 class="break-words text-sm font-semibold">{{ formulaName }}</h4>
            <p v-if="formulaIntent" class="mt-1 break-words text-[13px] leading-6 text-mute">{{ formulaIntent }}</p>
          </div>
          <CircleCheck v-if="validated" class="mt-1 size-4 shrink-0 text-[#318b76]" title="公式检查通过" aria-label="公式检查通过" />
        </div>
        <details v-if="result.explanation" class="text-[13px] leading-7 text-mute">
          <summary class="cursor-pointer text-xs">AI 的说明</summary>
          <p class="mt-1.5 whitespace-pre-wrap break-words">{{ result.explanation }}</p>
        </details>
        <div v-if="result.questions.length" class="rounded-xl bg-white/80 p-3 text-[13px] leading-7">
          <ul class="list-disc space-y-1 pl-4 text-ink"><li v-for="question in result.questions" :key="question">{{ question }}</li></ul>
        </div>
        <ul v-if="result.warnings.length" class="space-y-1 text-xs leading-6 text-[#8b6a39]">
          <li v-for="warning in result.warnings" :key="warning" class="flex items-start gap-2"><TriangleAlert class="mt-1 size-3.5 shrink-0" />{{ warning }}</li>
        </ul>
        <ul v-if="problems.length" class="space-y-1 rounded-xl bg-error/5 px-3 py-2 text-xs leading-6 text-error"><li v-for="problem in problems" :key="problem">{{ problem }}</li></ul>
      </section>
    </div>

    <template #footer>
      <button class="btn-line" :disabled="busy" @click="emit('close')">取消</button>
      <button
        class="btn-ink !bg-[#278675] disabled:opacity-40"
        :disabled="!canGenerate || (!!result && requestUnchanged && !error)"
        data-testid="formula-ai-generate"
        @click="generate"
      ><LoaderCircle v-if="generating" class="size-4 animate-spin" /><Sparkles v-else class="size-4" />{{ generating ? '生成中' : result ? '再调整' : '生成' }}</button>
      <button v-if="result" class="btn-ink" :disabled="!canUse" data-testid="formula-ai-use" @click="save"><LoaderCircle v-if="saving" class="size-4 animate-spin" /><Check v-else class="size-4" />保存</button>
    </template>
  </BaseModal>
</template>
