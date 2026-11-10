<script setup lang="ts">
defineProps<{ side: 'left' | 'right'; expanded: boolean }>()
</script>

<template>
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.5"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <path
      v-if="expanded"
      :d="side === 'left'
        ? 'M9 4.5H6A2.5 2.5 0 0 0 3.5 7v10A2.5 2.5 0 0 0 6 19.5h3Z'
        : 'M15 4.5h3A2.5 2.5 0 0 1 20.5 7v10a2.5 2.5 0 0 1-2.5 2.5h-3Z'"
      fill="var(--color-ink)"
      stroke="none"
    />
    <rect x="3.5" y="4.5" width="17" height="15" rx="2.5" />
    <path :d="side === 'left' ? 'M9 4.5v15' : 'M15 4.5v15'" />
  </svg>
</template>
