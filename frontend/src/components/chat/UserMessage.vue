<script setup lang="ts">
import { Brain, Check, Copy, Download, FileCheck2, Sheet, Image, MessageSquare } from '@lucide/vue'
import { computed, onBeforeUnmount, ref } from 'vue'

import type { FileInfo, PlanStep } from '@/api/types'
import type { UiMessage } from '@/composables/chatModel'
import { isImageFile } from '@/composables/attachmentFiles'
import { renderMarkdown } from '@/composables/markdown'
import { copyMarkdown } from '@/composables/markdown'
import { fillChips, looksLikeMarkdown, markdownWithChips } from '@/composables/messageMarkdown'
import { parseTableReferences } from '@/composables/tableReferences'
import { recipeEditLabel, recipeEditReruns, storedRecipeEdit } from '@/composables/recipeEditing'
import { useToast } from '@/composables/useToast'

const props = defineProps<{ message: UiMessage; files: FileInfo[] }>()
const emit = defineEmits<{ preview: [file: FileInfo] }>()
const content = computed(() => parseTableReferences(props.message.content))
const toast = useToast()

/** 发送时间，只显示几点几分（本地时间） */
const sentAt = computed(() => {
  const date = new Date(props.message.createdAt ?? '')
  if (Number.isNaN(date.getTime())) return ''
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
})

// 复制：引用的表格、文件换成名字，其余保持原文
const plainText = computed(() => content.value.map((segment) =>
  segment.type === 'table' ? segment.reference.sheet : segment.type === 'file' ? segment.reference.filename : segment.text).join(''))
const copied = ref(false)
let copiedTimer = 0
async function copyMessage() {
  try {
    await copyMarkdown(plainText.value, 'markdown')
    copied.value = true
    window.clearTimeout(copiedTimer)
    copiedTimer = window.setTimeout(() => { copied.value = false }, 2000)
  } catch (error) {
    toast.error(error instanceof Error ? error.message : '复制失败，请重试')
  }
}
onBeforeUnmount(() => window.clearTimeout(copiedTimer))
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
  <div class="group/user flex justify-end">
    <div class="flex max-w-[85%] min-w-0 flex-col items-end gap-2">
      <time v-if="sentAt" class="-mb-1 text-[11px] tabular-nums text-faint" :datetime="message.createdAt">{{ sentAt }}</time>
      <div v-if="markdownHtml" class="user-bubble prose-sm max-w-full break-words rounded-[14px] bg-ink px-3.5 py-2 text-white [overflow-wrap:anywhere]" v-html="markdownHtml" />
      <div v-else class="user-bubble max-w-full whitespace-pre-wrap break-words rounded-[14px] bg-ink px-3.5 py-2 text-[15px] leading-6 text-white [overflow-wrap:anywhere]"><template v-for="(segment, index) in content" :key="index"><span v-if="segment.type === 'table'" class="msg-chip mx-0.5 rounded-md px-1.5 py-0.5" :title="`${segment.reference.filename} / ${segment.reference.sheet}`">{{ segment.reference.sheet }}</span><span v-else-if="segment.type === 'file'" class="msg-chip mx-0.5 rounded-md px-1.5 py-0.5" :title="segment.reference.filename">{{ segment.reference.filename }}</span><template v-else>{{ segment.text }}</template></template></div>
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
              <span class="flex size-10 shrink-0 items-center justify-center rounded-xl bg-paper text-[#4a4743]">
                <img v-if="isImageFile(attachment.file.filename, attachment.file.media_type) && attachment.file.preview_url" :src="attachment.file.preview_url" alt="" loading="lazy" class="size-10 rounded-xl object-cover" />
                <Image v-else-if="isImageFile(attachment.file.filename, attachment.file.media_type)" class="size-5" />
                <FileCheck2 v-else-if="attachment.file.kind === 'expected'" class="size-5" />
                <Sheet v-else class="size-5" />
              </span>
              <span class="min-w-0 flex-1">
                <span class="block break-words text-[13px] font-semibold leading-5 text-ink [overflow-wrap:anywhere]">{{ attachment.file.filename }}</span>
                <span class="mt-0.5 block truncate text-[11px] text-mute">{{ fileDetails(attachment.file) }}</span>
              </span>
            </button>
            <a :href="attachment.file.download_url" class="icon-btn mr-1 shrink-0" :aria-label="`下载文件 ${attachment.file.filename}`" title="下载文件"><Download class="size-4" /></a>
          </template>
          <div v-else class="flex min-w-0 items-center gap-3 px-2 py-1.5 text-faint">
            <span class="flex size-10 shrink-0 items-center justify-center rounded-xl bg-paper"><Sheet class="size-5" /></span>
            <span class="min-w-0"><span class="block text-[13px] font-medium">文件不可用</span><span class="mt-0.5 block text-[11px]">可能已被删除</span></span>
          </div>
        </div>
      </div>
      <div v-if="message.meta.thinking" class="flex items-center justify-end gap-1 text-[11px] text-faint"><Brain class="size-3" />思考模式</div>
      <div v-else-if="message.meta.plan" class="text-right text-[11px] text-faint">按计划执行（{{ (message.meta.plan as PlanStep[]).length }} 步）</div>
      <button
        type="button"
        class="icon-btn -mt-1 !size-6 pointer-events-none opacity-0 transition-opacity group-hover/user:pointer-events-auto group-hover/user:opacity-100 focus-visible:pointer-events-auto focus-visible:opacity-100"
        :class="copied ? 'pointer-events-auto !opacity-100' : ''"
        aria-label="复制消息"
        title="复制"
        @click="copyMessage"
      >
        <Check v-if="copied" class="size-3.5 text-success" /><Copy v-else class="size-3.5" />
      </button>
    </div>
  </div>
</template>

<style scoped>
/* 用户消息：黑底白字气泡，里面的 Markdown、引用标签、选中文字都换成适合深色底的颜色 */
.user-bubble.prose-sm { color: #fff; }
.user-bubble ::selection,
.user-bubble::selection { background: rgb(255 255 255 / 0.3); color: #fff; }
.user-bubble :deep(.msg-chip),
.user-bubble .msg-chip { background: rgb(255 255 255 / 0.16); color: #fff; }
.user-bubble :deep(h1),
.user-bubble :deep(h2),
.user-bubble :deep(h3),
.user-bubble :deep(h4) { color: #fff; }
.user-bubble :deep(code),
.user-bubble :deep(pre) { background: rgb(255 255 255 / 0.12); }
.user-bubble :deep(pre code) { background: transparent; }
.user-bubble :deep(blockquote) { border-color: rgb(255 255 255 / 0.3); color: rgb(255 255 255 / 0.75); }
.user-bubble :deep(hr) { border-color: rgb(255 255 255 / 0.2); }
.user-bubble :deep(.markdown-table-scroll),
.user-bubble :deep(table) { border-color: rgb(255 255 255 / 0.2); background: transparent; }
.user-bubble :deep(th),
.user-bubble :deep(td) { border-color: rgb(255 255 255 / 0.16); background: transparent; color: inherit; }
.user-bubble.prose-sm :deep(tbody tr:nth-child(n)),
.user-bubble.prose-sm :deep(tbody tr:nth-child(n) td) { background: transparent; }
.user-bubble :deep(th) { background: rgb(255 255 255 / 0.08); color: rgb(255 255 255 / 0.8); }
</style>
