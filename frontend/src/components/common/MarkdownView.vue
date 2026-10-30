<script setup lang="ts">
/** Markdown 渲染（marked + DOMPurify 防 XSS） */
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { computed } from 'vue'

const props = defineProps<{ source: string; streaming?: boolean }>()

marked.setOptions({ gfm: true, breaks: true })

const html = computed(() => DOMPurify.sanitize(marked.parse(props.source || '', { async: false }) as string))
</script>

<template>
  <div class="prose-sm" :class="{ 'typing-caret': streaming }" v-html="html" />
</template>
