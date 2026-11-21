<script setup lang="ts">
/** 对话图标：极简纯圆线条气泡，纯随 currentColor 渲染，轻盈留白。 */
withDefaults(defineProps<{ active?: boolean }>(), { active: false })
</script>

<template>
  <svg viewBox="0 0 20 20" fill="none" aria-hidden="true" focusable="false">
    <path
      d="M10 2.75A7.15 7.15 0 1 1 6.65 16.21L3.55 16.95A.55.55 0 0 1 2.95 16.35L3.69 13.25A7.15 7.15 0 0 1 10 2.75Z"
      stroke="currentColor"
      :stroke-width="active ? 1.7 : 1.55"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
  </svg>
</template>
