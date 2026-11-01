<script setup lang="ts">
import { ArrowRight, CircleCheck, FileSpreadsheet, LoaderCircle, Paperclip, Sparkles, TriangleAlert } from '@lucide/vue'
import { computed, ref } from 'vue'

import { customerApi } from '@/api'
import { errorText } from '@/api/http'
import type { FileInfo, FormulaGenerationResult } from '@/api/types'
import BaseModal from '@/components/common/BaseModal.vue'
import { useToast } from '@/composables/useToast'

const props = defineProps<{ customerId: string; files: FileInfo[] }>()
const emit = defineEmits<{ close: []; generated: [formula: Record<string, unknown>]; uploaded: [file: FileInfo] }>()
const toast = useToast()

const description = ref('')
const initialRequest = ref('')
const supplements = ref<string[]>([])
const uploadedFiles = ref<FileInfo[]>([])
const selectedIds = ref<string[]>([])
const result = ref<FormulaGenerationResult | null>(null)
const latestValidDraft = ref<Record<string, unknown> | null>(null)
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
const busy = computed(() => generating.value || uploading.value)
const canGenerate = computed(() => !!description.value.trim() && selectedIds.value.length <= 5 && !busy.value)
const canUse = computed(() => validated.value && !!result.value && !result.value.questions.length && requestUnchanged.value && !busy.value)
const formulaName = computed(() => String(result.value?.formula.name ?? '生成的公式'))
const formulaIntent = computed(() => String(result.value?.formula.intent ?? ''))
const requestUnchanged = computed(() => requestKey() === lastRequest.value)

function requestKey() {
  return JSON.stringify({ description: description.value.trim(), files: [...selectedIds.value].sort() })
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
  if (chosen.length + selectedIds.value.length > 5) {
    error.value = '最多选择 5 个参考文件，请减少文件后再上传。'
    toast.error(error.value)
    return
  }
  uploading.value = true
  error.value = ''
  try {
    for (const file of chosen) {
      const saved = await customerApi.uploadFile(props.customerId, file, 'input')
      uploadedFiles.value.push(saved)
      selectedIds.value.push(saved.file_id)
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
  generating.value = true
  validated.value = false
  error.value = ''
  problems.value = []
  const input = description.value.trim()
  try {
    const generated = await customerApi.generateFormula(props.customerId, {
      description: fullDescription(input),
      file_ids: [...selectedIds.value],
      ...(latestValidDraft.value ? { formula: latestValidDraft.value } : {}),
    })
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
    problems.value = await customerApi.checkFormula(props.customerId, result.value.formula)
    validated.value = !problems.value.length
    if (validated.value) latestValidDraft.value = result.value.formula
  } catch (err) {
    error.value = errorText(err)
    toast.error(error.value)
  } finally {
    generating.value = false
  }
}

function useFormula() {
  if (canUse.value && result.value) emit('generated', result.value.formula)
}
</script>

<template>
  <BaseModal title="AI 配置公式" width="max-w-3xl" @close="busy ? undefined : emit('close')">
    <div class="space-y-5" data-testid="formula-ai-generator">
      <label class="block">
        <span class="mb-2 block text-sm font-medium">{{ result?.questions.length ? '补充回答' : result ? '继续补充或调整要求' : '描述你想怎样处理表格' }}</span>
        <textarea
          v-model="description"
          class="field min-h-32 resize-y text-sm leading-7"
          :disabled="busy"
          :maxlength="initialRequest ? 3000 : 6000"
          data-testid="formula-ai-description"
        />
      </label>

      <details v-if="initialRequest" class="rounded-xl bg-paper/60 px-4 py-3 text-xs leading-6 text-mute">
        <summary class="cursor-pointer font-medium">最初的处理要求</summary>
        <p class="mt-2 whitespace-pre-wrap break-words">{{ initialRequest }}</p>
      </details>

      <section class="rounded-2xl bg-paper/70 p-4">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <h4 class="text-[13px] font-medium">参考文件<span class="ml-2 text-xs font-normal text-faint">{{ selectedIds.length }}/5</span></h4>
          <label class="btn-text cursor-pointer !py-1 text-xs" :class="busy ? 'pointer-events-none opacity-50' : ''">
            <LoaderCircle v-if="uploading" class="size-3.5 animate-spin" /><Paperclip v-else class="size-3.5" />上传文件
            <input type="file" multiple accept=".xlsx,.xlsm,.xls,.csv" class="hidden" :disabled="busy" data-testid="formula-ai-upload" @change="upload" />
          </label>
        </div>
        <p v-if="!files.length" class="mt-2 text-xs leading-6 text-faint">可以上传表格，让 AI 参考实际列名和数据。文件也会加入该客户的文件列表。</p>
        <div v-else class="mt-3 max-h-40 space-y-1.5 overflow-y-auto">
          <label v-for="file in files" :key="file.file_id" class="flex cursor-pointer items-center gap-2.5 rounded-xl bg-white px-3 py-2.5 text-xs">
            <input v-model="selectedIds" type="checkbox" class="checkbox checkbox-xs" :value="file.file_id" :disabled="busy || (selectedIds.length >= 5 && !selectedIds.includes(file.file_id))" />
            <FileSpreadsheet class="size-4 shrink-0 text-[#3a9a86]" />
            <span class="min-w-0 flex-1 break-all">{{ file.filename }}</span>
          </label>
        </div>
      </section>

      <p v-if="error" class="rounded-xl bg-error/5 px-4 py-3 text-[13px] leading-6 text-error" role="alert">{{ error }}</p>

      <section v-if="result" class="space-y-4 rounded-2xl bg-[#f0f8f5] p-4 sm:p-5" data-testid="formula-ai-result">
        <div class="flex items-start gap-3">
          <span class="flex size-9 shrink-0 items-center justify-center rounded-xl bg-white text-[#318b76]"><Sparkles class="size-4" /></span>
          <div class="min-w-0 flex-1">
            <h4 class="break-words text-sm font-semibold">{{ formulaName }}</h4>
            <p v-if="formulaIntent" class="mt-1 break-words text-[13px] leading-6 text-mute">{{ formulaIntent }}</p>
          </div>
        </div>
        <p v-if="result.explanation" class="whitespace-pre-wrap break-words text-[13px] leading-7 text-mute">{{ result.explanation }}</p>
        <div v-if="result.questions.length" class="rounded-xl bg-white/80 p-3 text-[13px] leading-7">
          <div class="mb-1 font-medium text-ink">还需要你确认</div>
          <ul class="list-disc space-y-1 pl-4 text-mute"><li v-for="question in result.questions" :key="question">{{ question }}</li></ul>
        </div>
        <ul v-if="result.warnings.length" class="space-y-1 text-xs leading-6 text-[#8b6a39]">
          <li v-for="warning in result.warnings" :key="warning" class="flex items-start gap-2"><TriangleAlert class="mt-1 size-3.5 shrink-0" />{{ warning }}</li>
        </ul>
        <ul v-if="problems.length" class="space-y-1 rounded-xl bg-error/5 px-3 py-2 text-xs leading-6 text-error"><li v-for="problem in problems" :key="problem">{{ problem }}</li></ul>
        <p v-if="validated" class="flex items-center gap-1.5 text-xs text-[#318b76]"><CircleCheck class="size-3.5" />公式检查通过</p>
      </section>
    </div>

    <template #footer>
      <div class="flex w-full flex-wrap justify-end gap-2">
        <button class="btn-line" :disabled="busy" @click="emit('close')">取消</button>
        <button
          class="btn-ink !bg-[#278675] disabled:opacity-40"
          :disabled="!canGenerate || (!!result && requestUnchanged && !error)"
          data-testid="formula-ai-generate"
          @click="generate"
        ><LoaderCircle v-if="generating" class="size-4 animate-spin" /><Sparkles v-else class="size-4" />{{ generating ? '正在生成' : result ? '继续调整' : '生成公式' }}</button>
        <button v-if="result" class="btn-ink" :disabled="!canUse" data-testid="formula-ai-use" @click="useFormula">检查并保存<ArrowRight class="size-4" /></button>
      </div>
    </template>
  </BaseModal>
</template>
