<script setup lang="ts">
import { Download, FileCheck2, FileSpreadsheet, Image } from '@lucide/vue'
import { computed } from 'vue'

import type { FileInfo, PlanStep } from '@/api/types'
import type { UiMessage } from '@/composables/chatModel'
import { isImageFile } from '@/composables/attachmentFiles'
import { parseTableReferences } from '@/composables/tableReferences'

const props = defineProps<{ message: UiMessage; files: FileInfo[] }>()
const emit = defineEmits<{ preview: [file: FileInfo] }>()
const content = computed(() => parseTableReferences(props.message.content))

const attachments = computed(() => {
  const ids = props.message.meta.attachments
  if (!Array.isArray(ids)) return []
  const files = new Map(props.files.map((file) => [file.file_id, file]))
  return ids.filter((id): id is string => typeof id === 'string').map((id) => ({ id, file: files.get(id) }))
})

function fileDetails(file: FileInfo) {
  const extension = file.filename.includes('.') ? file.filename.split('.').pop()!.toUpperCase() : '文件'
  const size = file.size < 1024 ? `${file.size} B` : file.size < 1024 * 1024
    ? `${(file.size / 1024).toFixed(1)} KB` : `${(file.size / (1024 * 1024)).toFixed(1)} MB`
  return `${file.kind === 'expected' ? '人工结果' : '上传文件'} · ${extension} · ${size}`
}
</script>

<template>
  <div class="flex justify-end">
    <div class="flex max-w-[85%] min-w-0 flex-col items-end gap-2">
      <div class="max-w-full whitespace-pre-wrap break-words rounded-[20px] rounded-br-md bg-paper px-4 py-2.5 text-[15px] leading-relaxed [overflow-wrap:anywhere]"><template v-for="(segment, index) in content" :key="index"><span v-if="segment.type === 'table'" class="mx-0.5 rounded-md bg-[#e0ece5] px-1.5 py-0.5 text-[#3f6152]" :title="`${segment.reference.filename} / ${segment.reference.sheet}`">{{ segment.reference.sheet }}</span><span v-else-if="segment.type === 'file'" class="mx-0.5 rounded-md bg-[#e4eaf2] px-1.5 py-0.5 text-[#42566f]" :title="segment.reference.filename">{{ segment.reference.filename }}</span><template v-else>{{ segment.text }}</template></template></div>
      <div v-if="attachments.length" class="flex w-[320px] max-w-full flex-col gap-2" aria-label="消息附件">
        <div v-for="attachment in attachments" :key="attachment.id" class="flex min-w-0 items-center gap-1 rounded-2xl border border-line bg-white p-1.5 shadow-[0_2px_8px_rgba(0,0,0,0.03)]">
          <template v-if="attachment.file">
            <button
              type="button"
              class="flex min-w-0 flex-1 items-center gap-3 rounded-xl px-2 py-1.5 text-left transition hover:bg-paper focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
              :aria-label="`预览文件 ${attachment.file.filename}`"
              :title="attachment.file.filename"
              @click="emit('preview', attachment.file)"
            >
              <span class="flex size-10 shrink-0 items-center justify-center rounded-xl" :class="attachment.file.kind === 'expected' ? 'bg-fun-yellow/15 text-[#b7860b]' : 'bg-fun-green/10 text-fun-green'">
                <img v-if="isImageFile(attachment.file.filename, attachment.file.media_type) && attachment.file.preview_url" :src="attachment.file.preview_url" alt="" loading="lazy" class="size-10 rounded-xl object-cover" />
                <Image v-else-if="isImageFile(attachment.file.filename, attachment.file.media_type)" class="size-5" />
                <FileCheck2 v-else-if="attachment.file.kind === 'expected'" class="size-5" />
                <FileSpreadsheet v-else class="size-5" />
              </span>
              <span class="min-w-0 flex-1">
                <span class="block break-words text-[13px] font-semibold leading-5 text-ink [overflow-wrap:anywhere]">{{ attachment.file.filename }}</span>
                <span class="mt-0.5 block truncate text-[11px] text-mute">{{ fileDetails(attachment.file) }}</span>
              </span>
            </button>
            <a :href="attachment.file.download_url" class="icon-btn mr-1 shrink-0" :aria-label="`下载文件 ${attachment.file.filename}`" title="下载文件"><Download class="size-4" /></a>
          </template>
          <div v-else class="flex min-w-0 items-center gap-3 px-2 py-1.5 text-faint">
            <span class="flex size-10 shrink-0 items-center justify-center rounded-xl bg-paper"><FileSpreadsheet class="size-5" /></span>
            <span class="min-w-0"><span class="block text-[13px] font-medium">文件不可用</span><span class="mt-0.5 block text-[11px]">可能已被删除</span></span>
          </div>
        </div>
      </div>
      <div v-if="message.meta.plan" class="text-right text-[11px] text-faint">自动规划 · {{ (message.meta.plan as PlanStep[]).length }} 步</div>
    </div>
  </div>
</template>
