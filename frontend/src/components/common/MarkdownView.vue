<script setup lang="ts">
/** Markdown 渲染（marked + DOMPurify 防 XSS） */
import DOMPurify from 'dompurify'
import { computed, shallowRef, watch } from 'vue'

import { renderMarkdown } from '@/composables/markdown'
import { markdownBlocks, type MarkdownBlock } from '@/composables/streamingMarkdown'

const props = defineProps<{ source: string; streaming?: boolean; stable?: boolean }>()

const html = computed(() => renderMarkdown(props.source))
const blocks = shallowRef<MarkdownBlock[]>([])
watch(() => props.source, (source) => {
  if (!props.stable) return
  blocks.value = markdownBlocks(source, blocks.value).map((block) =>
    blocks.value.includes(block) ? block : { ...block, html: DOMPurify.sanitize(block.html) },
  )
}, { immediate: true })
</script>

<template>
  <div v-if="stable" class="prose-sm" :class="{ 'typing-caret': streaming }">
    <div v-for="block in blocks" :key="block.key" class="markdown-block contents" v-html="block.html" />
  </div>
  <div v-else class="prose-sm" :class="{ 'typing-caret': streaming }" v-html="html" />
</template>

<style scoped>
.markdown-block:first-child :deep(> :first-child) { margin-top: 0; }
.markdown-block:last-child :deep(> :last-child) { margin-bottom: 0; }
</style>
