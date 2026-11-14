<script setup lang="ts">
import { FileCheck2, FileSpreadsheet, LoaderCircle, Upload } from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'

import { customerApi, fileApi } from '@/api'
import { errorText } from '@/api/http'
import type { FileInfo } from '@/api/types'
import { isDeliveredOutput } from '@/composables/deliveryFiles'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'

import WorkflowFileRow from './WorkflowFileRow.vue'

const props = defineProps<{
  customerId: string
  recipeId?: string
  files: FileInfo[]
  outputs: FileInfo[]
  refresh: () => Promise<void>
}>()
const emit = defineEmits<{ preview: [file: FileInfo] }>()
// 只展示最终的输出文件，各环节的过程文件不在这里列出
const results = computed(() => props.outputs.filter(isDeliveredOutput))
const toast = useToast()
const { confirmDelete } = useConfirm()
const uploading = ref(false)
const uploadMenuOpen = ref(false)
const uploadInput = ref<HTMLInputElement | null>(null)
const uploadButton = ref<HTMLButtonElement | null>(null)
const uploadFirstChoice = ref<HTMLButtonElement | null>(null)
let active = true
let uploadContext: {
  customerId: string
  recipeId?: string
  kind: 'input' | 'expected'
  refresh: () => Promise<void>
} | null = null
onBeforeUnmount(() => { active = false })

async function toggleUploadMenu() {
  if (uploading.value) return
  uploadMenuOpen.value = !uploadMenuOpen.value
  if (uploadMenuOpen.value) {
    await nextTick()
    uploadFirstChoice.value?.focus({ preventScroll: true })
  }
}

function closeUploadMenu() {
  uploadMenuOpen.value = false
  uploadButton.value?.focus({ preventScroll: true })
}

function chooseUpload(kind: 'input' | 'expected') {
  if (uploading.value) return
  uploadContext = {
    customerId: props.customerId,
    recipeId: props.recipeId,
    kind,
    refresh: props.refresh,
  }
  closeUploadMenu()
  uploadInput.value?.click()
}

async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''
  const context = uploadContext
  uploadContext = null
  if (!files.length || uploading.value || !context) return
  uploading.value = true
  let uploaded = 0
  try {
    for (const file of files) {
      await customerApi.uploadFile(context.customerId, file, context.kind, context.recipeId)
      uploaded++
    }
    if (active) toast.success(`已上传 ${uploaded} 个文件`)
  } catch (err) {
    if (active) toast.error(errorText(err))
  } finally {
    try {
      if (uploaded) await context.refresh()
    } catch (err) {
      if (active) toast.error(errorText(err))
    } finally {
      uploading.value = false
    }
  }
}

async function remove(file: FileInfo) {
  const customerId = props.customerId
  const recipeId = props.recipeId
  const refresh = props.refresh
  await confirmDelete({
    title: recipeId ? '从这个流程移除文件？' : '从客户资料移除这个文件？',
    target: file.filename,
    description: '历史运行和其他流程中使用的文件会保留。',
    confirmText: '移除文件',
    action: async () => {
      await fileApi.remove(file.file_id, customerId, recipeId)
      await refresh()
    },
  })
}
</script>

<template>
  <div class="card-soft p-5">
    <div>
      <div class="mb-3 flex items-center gap-3">
        <h3 class="text-[15px] font-semibold">输入文件</h3>
        <div class="relative ml-auto shrink-0" @keydown.esc.stop.prevent="closeUploadMenu">
          <button
            ref="uploadButton"
            type="button"
            class="btn-ink h-8 gap-1.5 whitespace-nowrap px-3 py-0 text-xs"
            :disabled="uploading"
            :aria-expanded="uploadMenuOpen"
            @click="toggleUploadMenu"
          >
            <LoaderCircle v-if="uploading" class="size-3.5 shrink-0 animate-spin" /><Upload v-else class="size-3.5 shrink-0" />
            <span>{{ uploading ? '上传中…' : '上传' }}</span>
          </button>
          <div v-if="uploadMenuOpen" class="fixed inset-0 z-30" @click="closeUploadMenu" />
          <div v-if="uploadMenuOpen" class="absolute right-0 top-full z-40 mt-2 w-max max-w-[calc(100vw-3rem)] animate-pop rounded-2xl border border-line bg-white p-1.5 shadow-xl" role="group" aria-label="选择上传文件类型">
            <button ref="uploadFirstChoice" type="button" class="flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left hover:bg-paper focus-visible:bg-paper focus-visible:outline-none" @click="chooseUpload('input')">
              <FileSpreadsheet class="size-4 shrink-0 text-fun-green" />
              <span class="text-sm font-medium">要处理的文件</span>
            </button>
            <button type="button" class="flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left hover:bg-paper focus-visible:bg-paper focus-visible:outline-none" @click="chooseUpload('expected')">
              <FileCheck2 class="size-4 shrink-0 text-[#b7860b]" />
              <span class="text-sm font-medium">人工做好的文件</span>
            </button>
          </div>
          <input ref="uploadInput" type="file" class="hidden" multiple accept=".xlsx,.xlsm,.xls,.csv" @change="upload" />
        </div>
      </div>
      <WorkflowFileRow
        v-for="file in files" :key="file.file_id" :file="file"
        :badge="file.kind === 'expected' ? file.kind_label : undefined" badge-class="!bg-fun-yellow/20 !text-[#8a6400]"
        @preview="emit('preview', $event)" @remove="remove"
      />
      <p v-if="!files.length" class="py-6 text-center text-sm text-faint">还没有文件</p>

      <h3 class="mb-3 mt-6 border-t border-line pt-6 text-[15px] font-semibold">输出文件</h3>
      <WorkflowFileRow
        v-for="file in results" :key="file.file_id" :file="file"
        :badge="file.delivery_status === 'candidate' ? '待核验文件' : undefined"
        @preview="emit('preview', $event)" @remove="remove"
      />
      <p v-if="!results.length" class="py-6 text-center text-sm text-faint">还没有输出文件</p>
    </div>
  </div>
</template>
