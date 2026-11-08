<script setup lang="ts">
import { ArrowRight, Check, Eye, FileSpreadsheet, LoaderCircle, Play, Upload, X } from '@lucide/vue'

import type { FileInfo } from '@/api/types'
import type { FlowReplayController } from '@/composables/useFlowReplay'

const props = defineProps<{ controller: FlowReplayController }>()
const emit = defineEmits<{ close: []; started: []; preview: [file: FileInfo] }>()
const {
  recipe, files, chosen, mapping, match, allowRepair, loading, matching, uploading, running,
  error, mode, viewedVersion, versionChanged, canUpload, canRun,
} = props.controller

function uploadFiles(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''
  void props.controller.upload(files)
}

function start() {
  if (!canRun.value) return
  emit('started')
  void props.controller.run()
}
</script>

<template>
  <section class="flow-run-setup flex h-full min-h-0 flex-col bg-white" aria-label="执行设置">
    <header class="flex items-center gap-3 border-b border-line px-5 py-4">
      <div class="min-w-0 flex-1">
        <h3 class="text-sm font-semibold">重新执行</h3>
        <p v-if="recipe" class="mt-1 truncate text-xs text-mute">{{ recipe.name }} <span class="ml-1 text-[#6265DC]">v{{ recipe.version }}</span></p>
      </div>
      <button type="button" class="icon-btn shrink-0" aria-label="关闭执行设置" @click="emit('close')"><X class="size-4" /></button>
    </header>
    <div class="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4">
      <div v-if="loading" role="status" class="flex justify-center py-10"><LoaderCircle class="size-5 animate-spin text-[#6265DC]" /></div>
      <template v-else>
        <p v-if="versionChanged" class="rounded-xl bg-[#6265DC]/8 px-3 py-2 text-xs leading-5 text-[#6265DC]">当前查看 v{{ viewedVersion }}，将执行最新 v{{ recipe?.version }}。</p>
        <div v-if="mode === 'original'" class="rounded-xl border border-line p-4">
          <div class="flex items-center gap-2 text-sm font-medium"><FileSpreadsheet class="size-4 text-[#6265DC]" />使用原文件重新执行</div>
          <p class="mt-2 text-xs text-mute">从第一步开始，使用本次运行的流程与输入。</p>
        </div>
        <template v-else-if="recipe">
          <div v-if="recipe.inputs.length" class="space-y-3">
            <div class="flex items-center justify-between gap-2">
              <span class="text-xs font-medium">输入文件</span>
              <label v-if="canUpload" class="btn-text cursor-pointer text-xs text-[#6265DC]" :class="{ 'pointer-events-none opacity-50': uploading }">
                <LoaderCircle v-if="uploading" class="size-3.5 animate-spin" /><Upload v-else class="size-3.5" />上传
                <input type="file" multiple class="hidden" accept=".xlsx,.xlsm,.xls,.csv" :disabled="uploading || running" @change="uploadFiles" />
              </label>
            </div>
            <div v-if="files.length" class="max-h-60 space-y-2 overflow-y-auto">
              <label v-for="file in files" :key="file.file_id" class="flex cursor-pointer items-center gap-2.5 rounded-xl border px-3 py-2.5 transition" :class="chosen.includes(file.file_id) ? 'border-[#6265DC]/35 bg-[#6265DC]/5' : 'border-line hover:bg-paper'">
                <input v-model="chosen" type="checkbox" class="size-3.5 shrink-0 accent-[#6265DC]" :value="file.file_id" :disabled="uploading || running" />
                <FileSpreadsheet class="size-4 shrink-0 text-[#6265DC]" />
                <span class="min-w-0 flex-1 truncate text-xs" :title="file.filename">{{ file.filename }}</span>
                <button type="button" class="icon-btn size-6 shrink-0" :aria-label="`查看文件 ${file.filename}`" @click.prevent.stop="emit('preview', file)"><Eye class="size-3.5" /></button>
              </label>
            </div>
            <p v-else class="rounded-xl border border-dashed border-line px-3 py-5 text-center text-xs text-faint">{{ canUpload ? '上传本次要处理的文件' : '暂无可用文件' }}</p>
            <button type="button" class="flex w-full items-center justify-center gap-2 rounded-xl border border-[#6265DC]/20 px-3 py-2 text-xs font-medium text-[#6265DC] transition hover:bg-[#6265DC]/5 disabled:cursor-not-allowed disabled:opacity-40" :disabled="!chosen.length || matching || uploading || running" @click="controller.matchInputs()">
              <LoaderCircle v-if="matching" class="size-3.5 animate-spin" /><Check v-else-if="match" class="size-3.5" /><ArrowRight v-else class="size-3.5" />{{ match ? '重新匹配输入' : '匹配输入' }}
            </button>
          </div>
          <div v-if="match?.inputs.length" class="space-y-3">
            <div v-for="input in match.inputs" :key="input.alias">
              <label :for="`flow-input-${input.alias}`" class="mb-1.5 block text-xs font-medium">{{ input.alias }}</label>
              <select :id="`flow-input-${input.alias}`" v-model="mapping[input.alias]" class="w-full rounded-xl border border-line bg-white px-3 py-2 text-xs outline-none focus:border-[#6265DC]">
                <option value="">选择对应文件{{ input.kind === 'file' ? '' : ' / 工作表' }}</option>
                <option v-for="candidate in match.candidates.filter((item) => (item.kind ?? 'table') === (input.kind ?? 'table'))" :key="candidate.key" :value="candidate.key">{{ candidate.filename }}{{ candidate.sheet ? ` · ${candidate.sheet}` : '' }}</option>
              </select>
              <p v-if="input.required_columns.length" class="mt-1.5 text-[11px] leading-5 text-faint">{{ input.required_columns.join('、') }}</p>
            </div>
          </div>
          <label class="flex items-center justify-between gap-3 border-t border-line pt-4 text-xs text-mute">
            出错时自动修复
            <input v-model="allowRepair" type="checkbox" class="toggle toggle-sm text-[#6265DC]" />
          </label>
        </template>
        <p v-if="error" role="alert" class="rounded-xl bg-error/5 px-3 py-2 text-xs leading-5 text-error">{{ error }}</p>
      </template>
    </div>
    <footer class="flex items-center gap-2 border-t border-line px-5 py-4">
      <button type="button" class="btn-line flex-1 py-2 text-xs" @click="emit('close')">取消</button>
      <button type="button" class="flex flex-1 items-center justify-center gap-2 rounded-full bg-[#6265DC] px-4 py-2 text-xs font-medium text-white transition hover:bg-[#5558C6] disabled:cursor-not-allowed disabled:opacity-40" :disabled="!canRun" @click="start"><Play class="size-3.5" />开始执行</button>
    </footer>
  </section>
</template>
