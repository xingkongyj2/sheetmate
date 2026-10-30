<script setup lang="ts">
/** Markdown 渲染（marked + DOMPurify 防 XSS） */
import { computed } from 'vue'

import { renderMarkdown } from '@/composables/markdown'

const props = defineProps<{ source: string; streaming?: boolean }>()

const html = computed(() => renderMarkdown(props.source))
</script>

<template>
  <div class="prose-sm" :class="{ 'typing-caret': streaming }" v-html="html" />
</template>
