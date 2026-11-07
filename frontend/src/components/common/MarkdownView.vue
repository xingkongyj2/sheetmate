<script setup lang="ts">
/** Markdown 渲染（marked + DOMPurify 防 XSS） */
import DOMPurify from 'dompurify'
import { computed, onBeforeUnmount, ref, shallowRef, watch, watchPostEffect } from 'vue'

import { renderMarkdown } from '@/composables/markdown'
import { createMarkdownTableController } from '@/composables/markdownTables'
import { markdownBlocks, type MarkdownBlock } from '@/composables/streamingMarkdown'

const props = defineProps<{ source: string; streaming?: boolean; stable?: boolean }>()

const root = ref<HTMLElement | null>(null)
const tables = createMarkdownTableController()
const html = computed(() => renderMarkdown(props.source))
const blocks = shallowRef<MarkdownBlock[]>([])
watch(() => props.source, (source) => {
  if (!props.stable) return
  blocks.value = markdownBlocks(source, blocks.value).map((block) =>
    blocks.value.includes(block) ? block : { ...block, html: DOMPurify.sanitize(block.html) },
  )
}, { immediate: true })

// v-html 会替换流式表格的 DOM；渲染后补齐拖动控件并恢复用户已调整的列宽。
watchPostEffect(() => {
  void props.source
  void props.stable
  void blocks.value
  tables.refresh(root.value)
})
onBeforeUnmount(() => tables.destroy())
</script>

<template>
  <div v-if="stable" ref="root" class="prose-sm" :class="{ 'typing-caret': streaming }">
    <div v-for="block in blocks" :key="block.key" class="markdown-block contents" v-html="block.html" />
  </div>
  <div v-else ref="root" class="prose-sm" :class="{ 'typing-caret': streaming }" v-html="html" />
</template>

<style scoped>
.markdown-block:first-child :deep(> :first-child) { margin-top: 0; }
.markdown-block:last-child :deep(> :last-child) { margin-bottom: 0; }
</style>
