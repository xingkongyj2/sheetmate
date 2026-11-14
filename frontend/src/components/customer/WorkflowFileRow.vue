<script setup lang="ts">
/** 文件页里的一行文件：预览、下载、删除。输入文件和输出文件共用。文件名不能在这里改，要通过流程节点里的 AI 改名。 */
import { Download, FileCheck2, FileSpreadsheet, Trash2 } from '@lucide/vue'

import type { FileInfo } from '@/api/types'
import { timeAgo } from '@/composables/chatModel'

defineProps<{ file: FileInfo; badge?: string; badgeClass?: string }>()
const emit = defineEmits<{ preview: [file: FileInfo]; remove: [file: FileInfo] }>()
</script>

<template>
  <div class="-mx-2 flex items-center gap-3 rounded-xl px-2 py-2 text-sm transition hover:bg-paper">
    <button type="button" class="flex min-w-0 flex-1 items-center gap-2 rounded text-left underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ink" :title="file.filename" :aria-label="`预览 ${file.filename}`" @click="emit('preview', file)">
      <FileCheck2 v-if="file.kind === 'expected'" class="size-4 shrink-0 text-[#b7860b]" />
      <FileSpreadsheet v-else class="size-4 shrink-0 text-fun-green" /><span class="truncate">{{ file.filename }}</span>
    </button>
    <span v-if="badge" class="tag shrink-0 whitespace-nowrap" :class="badgeClass">{{ badge }}</span>
    <span class="shrink-0 text-xs text-faint">{{ timeAgo(file.created_at) }}</span>
    <a :href="file.download_url" class="icon-btn shrink-0" title="下载" :aria-label="`下载 ${file.filename}`"><Download class="size-4" /></a>
    <button type="button" class="icon-btn shrink-0 hover:text-error" title="删除" :aria-label="`删除 ${file.filename}`" @click="emit('remove', file)"><Trash2 class="size-4" /></button>
  </div>
</template>
