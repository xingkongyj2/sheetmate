<script setup lang="ts">
/** 状态小圆点 + 文字：成功 / 提示 / 失败 / 运行中 / 草稿 / 已启用 … */
import { computed } from 'vue'

const props = defineProps<{ status: string; label?: string }>()

const MAP: Record<string, { dot: string; text: string; label: string }> = {
  success: { dot: 'bg-success', text: 'text-success', label: '成功' },
  pass: { dot: 'bg-success', text: 'text-success', label: '通过' },
  active: { dot: 'bg-success', text: 'text-success', label: '已启用' },
  repaired: { dot: 'bg-fun-purple', text: 'text-fun-purple', label: '已自动修复' },
  warning: { dot: 'bg-warning', text: 'text-[#b7770c]', label: '有提示' },
  warn: { dot: 'bg-warning', text: 'text-[#b7770c]', label: '有差异' },
  draft: { dot: 'bg-faint', text: 'text-mute', label: '草稿' },
  failed: { dot: 'bg-error', text: 'text-error', label: '失败' },
  fail: { dot: 'bg-error', text: 'text-error', label: '未通过' },
  escalated: { dot: 'bg-error', text: 'text-error', label: '需要人工处理' },
  deprecated: { dot: 'bg-faint', text: 'text-faint', label: '已停用' },
  running: { dot: 'bg-info animate-pulse', text: 'text-info', label: '运行中' },
  pending: { dot: 'bg-line', text: 'text-faint', label: '等待中' },
}

const style = computed(() => MAP[props.status] ?? MAP.pending)
</script>

<template>
  <span class="inline-flex items-center gap-1.5 text-xs font-medium" :class="style.text">
    <span class="size-1.5 rounded-full" :class="style.dot" />
    {{ label ?? style.label }}
  </span>
</template>
