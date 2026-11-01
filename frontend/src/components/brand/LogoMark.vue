<script setup lang="ts">
/**
 * SheetMate 标志：三个「单元格」+ 一颗火花（表格 + AI）。原创图形。
 * 单元格从方到圆，寓意杂乱的表格被整理收敛。
 */
withDefaults(defineProps<{ size?: number; withText?: boolean }>(), { size: 28, withText: false })
</script>

<template>
  <span class="inline-flex items-center gap-2 select-none">
    <svg :width="size" :height="size" viewBox="0 0 32 32" aria-hidden="true">
      <rect x="3" y="3" width="12" height="12" rx="3.6" fill="#0b0b0c" />
      <rect x="17" y="3" width="12" height="12" rx="6" fill="#0b0b0c" />
      <rect x="3" y="17" width="12" height="12" rx="6" fill="#0b0b0c" />
      <path
        class="origin-[23px_23px] animate-[spin_9s_linear_infinite]"
        d="M23 16.4l1.7 4.1 4.1 1.7-4.1 1.7-1.7 4.1-1.7-4.1-4.1-1.7 4.1-1.7z"
        fill="#6265DC"
      />
    </svg>
    <span v-if="withText" class="text-[22px] font-semibold tracking-tight">SheetMate</span>
  </span>
</template>
