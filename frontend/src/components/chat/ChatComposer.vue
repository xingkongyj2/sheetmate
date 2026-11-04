<script setup lang="ts">
/**
 * 输入框：文字 + 附件（要处理的表 / 人工做好的结果）+ 先出计划 + 发送/停止。
 * 中文输入法组字时按回车不会发送；支持把文件直接拖进来。
 */
import { ArrowUp, Eye, FileCheck2, FileSpreadsheet, ListChecks, LoaderCircle, Plus, Square, X } from '@lucide/vue'
import { computed, ref } from 'vue'

import { referenceSourceKey, type FileReference, type TableReference } from '@/composables/tableReferences'
import ReferenceEditor from './ReferenceEditor.vue'

export interface Attachment {
  key: string
  sourceId?: string
  name: string
  kind: 'input' | 'expected'
  status: 'pending' | 'uploading' | 'done' | 'error'
  error?: string
  tables?: string[]
}

const props = withDefaults(
  defineProps<{
    modelValue: string
    plan?: boolean
    attachments?: Attachment[]
    tableReferences?: TableReference[]
    fileReferences?: FileReference[]
    referencesLoading?: boolean
    referencesError?: string
    busy?: boolean
    stoppable?: boolean
    placeholder?: string
    autofocus?: boolean
    large?: boolean
    appearance?: 'outlined' | 'card'
  }>(),
  { plan: false, attachments: () => [], tableReferences: () => [], fileReferences: () => [], referencesLoading: false, referencesError: '', busy: false, stoppable: true, placeholder: '描述你要对表格做什么…', autofocus: false, large: false, appearance: 'outlined' },
)

const emit = defineEmits<{
  'update:modelValue': [value: string]
  'update:plan': [value: boolean]
  attach: [files: File[], kind: 'input' | 'expected']
  remove: [key: string]
  preview: [key: string]
  send: []
  stop: []
}>()

const editor = ref<InstanceType<typeof ReferenceEditor> | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const pendingKind = ref<'input' | 'expected'>('input')
const menuOpen = ref(false)
const dragging = ref(false)
const knownFiles = computed<FileReference[]>(() => {
  const files = new Map<string, FileReference>()
  for (const reference of props.fileReferences) {
    files.set(referenceSourceKey(reference), reference)
  }
  for (const attachment of props.attachments) {
    if (attachment.status === 'uploading') continue
    const reference = attachmentReference(attachment)
    const key = referenceSourceKey(reference)
    if (!files.has(key)) files.set(key, reference)
  }
  return [...files.values()]
})

function attachmentReference(attachment: Attachment): FileReference {
  return { filename: attachment.name, kind: attachment.kind, sourceId: attachment.sourceId ?? attachment.key }
}

function captureSelection() {
  editor.value?.captureSelection()
}

function insertFile(attachment: Attachment) {
  menuOpen.value = false
  editor.value?.insertReference(attachmentReference(attachment))
}

function preview(key: string) {
  captureSelection()
  menuOpen.value = false
  emit('preview', key)
}

function pick(kind: 'input' | 'expected') {
  pendingKind.value = kind
  menuOpen.value = false
  fileInput.value?.click()
}

function onFiles(e: Event) {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  if (files.length) emit('attach', files, pendingKind.value)
  input.value = ''
}

function onDrop(e: DragEvent) {
  dragging.value = false
  const files = Array.from(e.dataTransfer?.files ?? []).filter((f) => /\.(xlsx|xlsm|xls|csv)$/i.test(f.name))
  if (files.length) emit('attach', files, 'input')
}

defineExpose({ focus: () => editor.value?.focus(), captureSelection })
</script>

<template>
  <div
    class="relative rounded-[22px] bg-white transition"
    :class="[
      appearance === 'card'
        ? 'shadow-[0_4px_24px_rgba(0,0,0,0.06),0_1px_4px_rgba(0,0,0,0.03)] focus-within:shadow-[0_6px_28px_rgba(0,0,0,0.09),0_2px_6px_rgba(0,0,0,0.04)]'
        : ['border', dragging ? 'border-ink' : 'border-line shadow-[0_8px_30px_-18px_rgba(0,0,0,0.25)] focus-within:border-ink/30'],
      dragging && 'ring-4 ring-black/5',
    ]"
    @dragover.prevent="dragging = true"
    @dragleave.prevent="dragging = false"
    @drop.prevent="onDrop"
  >
    <!-- 附件 -->
    <div v-if="attachments.length" class="flex flex-wrap gap-2 px-4 pt-3">
      <div
        v-for="a in attachments"
        :key="a.key"
        class="group flex max-w-[260px] items-center gap-2 rounded-xl border px-2.5 py-1.5 text-xs"
        :class="a.status === 'error' ? 'border-error/40 bg-error/5' : a.kind === 'expected' ? 'border-fun-yellow/60 bg-fun-yellow/10' : 'border-line bg-paper'"
        :title="a.error || a.name"
      >
        <LoaderCircle v-if="a.status === 'uploading'" class="size-4 shrink-0 animate-spin text-mute" />
        <FileCheck2 v-else-if="a.kind === 'expected'" class="size-4 shrink-0 text-[#b7860b]" />
        <FileSpreadsheet v-else class="size-4 shrink-0 text-fun-green" />
        <div class="min-w-0">
          <button
            type="button"
            class="block max-w-full cursor-pointer truncate rounded-sm text-left font-medium text-ink transition hover:text-[#42566f] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#8fa2ba]"
            :aria-label="`插入文件名 ${a.name}`"
            title="点击插入文件名"
            :disabled="a.status === 'uploading'"
            @pointerdown.prevent="captureSelection"
            @click="insertFile(a)"
          >{{ a.name }}</button>
          <div class="truncate text-[10px] text-faint">
            {{ a.status === 'error' ? a.error : a.kind === 'expected' ? '人工结果 · 让 AI 照着学' : a.tables?.length ? `已加载：${a.tables.join('、')}` : '要处理的表' }}
          </div>
        </div>
        <button
          type="button"
          class="ml-1 flex size-6 shrink-0 items-center justify-center rounded-lg text-mute transition hover:bg-white hover:text-ink disabled:opacity-40"
          :aria-label="`查看文件 ${a.name}`"
          title="查看表格"
          :disabled="a.status === 'uploading'"
          @pointerdown.prevent="captureSelection"
          @click="preview(a.key)"
        ><Eye class="size-3.5" /></button>
        <button type="button" class="shrink-0 rounded p-0.5 text-faint hover:bg-white hover:text-ink" :aria-label="`移除文件 ${a.name}`" :disabled="busy" @click="emit('remove', a.key)"><X class="size-3" /></button>
      </div>
    </div>

    <ReferenceEditor
      ref="editor"
      :model-value="modelValue"
      :table-references="tableReferences"
      :file-references="knownFiles"
      :references-loading="referencesLoading"
      :references-error="referencesError"
      :placeholder="placeholder"
      :large="large"
      :autofocus="autofocus"
      :busy="busy"
      @update:model-value="emit('update:modelValue', $event)"
      @send="emit('send')"
    />

    <div class="flex items-center gap-1.5 px-3 pb-3 pt-1">
      <div class="relative">
        <button class="icon-btn" title="上传表格" @click="menuOpen = !menuOpen"><Plus class="size-[18px]" /></button>
        <div v-if="menuOpen" class="absolute bottom-10 left-0 z-30 w-max max-w-[calc(100vw-3rem)] animate-pop rounded-2xl border border-line bg-white p-1.5 shadow-xl" @mouseleave="menuOpen = false">
          <button class="flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left hover:bg-paper" @click="pick('input')">
            <FileSpreadsheet class="size-4 text-fun-green" />
            <span class="text-sm font-medium">要处理的文件</span>
          </button>
          <button class="flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left hover:bg-paper" @click="pick('expected')">
            <FileCheck2 class="size-4 text-[#b7860b]" />
            <span class="text-sm font-medium">人工做好的文件</span>
          </button>
        </div>
      </div>
      <input ref="fileInput" type="file" class="hidden" multiple accept=".xlsx,.xlsm,.xls,.csv" @change="onFiles" />

      <slot name="tools" />

      <button
        class="flex items-center gap-1 rounded-full px-2.5 py-1 text-xs transition"
        :class="plan ? 'bg-ink text-white' : 'text-mute hover:bg-paper hover:text-ink'"
        title="先生成执行计划，确认后再执行"
        @click="emit('update:plan', !plan)"
      >
        <ListChecks class="size-3.5" />先出计划
      </button>

      <div class="ml-auto">
        <button v-if="busy && stoppable" class="flex size-9 items-center justify-center rounded-full bg-ink text-white transition hover:bg-black/80" title="停止" @click="emit('stop')">
          <Square class="size-3.5 fill-current" />
        </button>
        <button v-else-if="busy" type="button" class="flex size-9 items-center justify-center rounded-full bg-ink text-white opacity-60" aria-label="正在上传并创建对话" disabled>
          <LoaderCircle class="size-4 animate-spin" />
        </button>
        <button
          v-else
          class="flex size-9 items-center justify-center rounded-full bg-ink text-white transition hover:bg-black/80 disabled:bg-line disabled:text-faint"
          :disabled="!modelValue.trim()"
          title="发送（Enter）"
          @click="emit('send')"
        >
          <ArrowUp class="size-4" />
        </button>
      </div>
    </div>
    <div v-if="dragging" class="pointer-events-none absolute inset-0 flex items-center justify-center rounded-[22px] bg-white/80 text-sm font-medium">
      松开即可上传表格
    </div>
  </div>
</template>

