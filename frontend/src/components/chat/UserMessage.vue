<script setup lang="ts">
import { Brain, Download, FileCheck2, FileSpreadsheet, Image, MessageSquare } from '@lucide/vue'
import { computed } from 'vue'

import type { FileInfo, PlanStep } from '@/api/types'
import type { UiMessage } from '@/composables/chatModel'
import { isImageFile } from '@/composables/attachmentFiles'
import { renderMarkdown } from '@/composables/markdown'
import { fillChips, looksLikeMarkdown, markdownWithChips } from '@/composables/messageMarkdown'
import { parseTableReferences } from '@/composables/tableReferences'
import { recipeEditLabel, recipeEditReruns, storedRecipeEdit } from '@/composables/recipeEditing'

const props = defineProps<{ message: UiMessage; files: FileInfo[] }>()
const emit = defineEmits<{ preview: [file: FileInfo] }>()
const content = computed(() => parseTableReferences(props.message.content))
// 带标题、列表等写法的消息按 Markdown 渲染（比如导入流程包时写进输入框的处理逻辑）；普通的一两句话保持原样。
const markdownHtml = computed(() => {
  if (!looksLikeMarkdown(props.message.content)) return ''
  const { source, chips } = markdownWithChips(props.message.content)
  return fillChips(renderMarkdown(source), chips)
})
const recipeEdit = computed(() => storedRecipeEdit(props.message.meta.recipe_edit))
const adopted = computed(() => {
  const steering = props.message.meta.steering
  return !!steering && typeof steering === 'object' && (steering as Record<string, unknown>).status === 'committed'
})

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
      <div v-if="markdownHtml" class="prose-sm max-w-full break-words rounded-[20px] rounded-br-md bg-paper px-4 py-2.5 [overflow-wrap:anywhere]" v-html="markdownHtml" />
      <div v-else class="max-w-full whitespace-pre-wrap break-words rounded-[20px] rounded-br-md bg-paper px-4 py-2.5 text-[15px] leading-relaxed [overflow-wrap:anywhere]"><template v-for="(segment, index) in content" :key="index"><span v-if="segment.type === 'table'" class="mx-0.5 rounded-md bg-[#e0ece5] px-1.5 py-0.5 text-[#3f6152]" :title="`${segment.reference.filename} / ${segment.reference.sheet}`">{{ segment.reference.sheet }}</span><span v-else-if="segment.type === 'file'" class="mx-0.5 rounded-md bg-[#e4eaf2] px-1.5 py-0.5 text-[#42566f]" :title="segment.reference.filename">{{ segment.reference.filename }}</span><template v-else>{{ segment.text }}</template></template></div>
      <p v-if="adopted" class="text-[11px] text-success">追加要求 · 已采纳</p>
      <p v-if="recipeEdit" class="flex max-w-full items-center gap-1.5 break-words text-[11px] text-[#6265dc]"><MessageSquare class="size-3 shrink-0" />{{ recipeEditLabel(recipeEdit) }} · v{{ recipeEdit.version }}<template v-if="recipeEditReruns(recipeEdit)"> · 修改后重跑</template></p>
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
      <div v-if="message.meta.thinking" class="flex items-center justify-end gap-1 text-[11px] text-faint"><Brain class="size-3" />思考模式</div>
      <div v-else-if="message.meta.plan" class="text-right text-[11px] text-faint">按计划执行（{{ (message.meta.plan as PlanStep[]).length }} 步）</div>
    </div>
  </div>
</template>
