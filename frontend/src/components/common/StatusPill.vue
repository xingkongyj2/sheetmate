<script setup lang="ts">
/** 状态小圆点 + 文字：成功 / 提示 / 失败 / 运行中 / 草稿 / 已启用 … */
import { computed } from 'vue'

const props = defineProps<{ status: string; label?: string; tag?: boolean }>()

const MAP: Record<string, { dot: string; text: string; bg: string; label: string }> = {
  success: { dot: 'bg-success', text: 'text-success', bg: 'bg-success/10', label: '成功' },
  pass: { dot: 'bg-success', text: 'text-success', bg: 'bg-success/10', label: '通过' },
  active: { dot: 'bg-success', text: 'text-success', bg: 'bg-success/10', label: '已启用' },
  repaired: { dot: 'bg-fun-purple', text: 'text-fun-purple', bg: 'bg-fun-purple/10', label: '已自动修复' },
  warning: { dot: 'bg-warning', text: 'text-[#b7770c]', bg: 'bg-warning/15', label: '有提示' },
  incomplete: { dot: 'bg-warning', text: 'text-[#b7770c]', bg: 'bg-warning/15', label: '未完成' },
  interrupted: { dot: 'bg-warning', text: 'text-[#b7770c]', bg: 'bg-warning/15', label: '已中断' },
  cancelled: { dot: 'bg-faint', text: 'text-mute', bg: 'bg-paper', label: '已停止' },
  waiting: { dot: 'bg-info', text: 'text-info', bg: 'bg-info/10', label: '待确认' },
  warn: { dot: 'bg-warning', text: 'text-[#b7770c]', bg: 'bg-warning/15', label: '有差异' },
  draft: { dot: 'bg-faint', text: 'text-mute', bg: 'bg-paper', label: '草稿' },
  failed: { dot: 'bg-error', text: 'text-error', bg: 'bg-error/10', label: '失败' },
  fail: { dot: 'bg-error', text: 'text-error', bg: 'bg-error/10', label: '未通过' },
  escalated: { dot: 'bg-error', text: 'text-error', bg: 'bg-error/10', label: '需要人工处理' },
  deprecated: { dot: 'bg-faint', text: 'text-faint', bg: 'bg-paper', label: '已停用' },
  running: { dot: 'bg-info animate-pulse', text: 'text-info', bg: 'bg-info/10', label: '运行中' },
  pending: { dot: 'bg-line', text: 'text-faint', bg: 'bg-paper', label: '等待中' },
}

const style = computed(() => MAP[props.status] ?? MAP.pending)
</script>

<template>
  <span class="inline-flex items-center gap-1.5 text-xs font-medium" :class="[style.text, tag ? `rounded-lg px-2.5 py-1 ${style.bg}` : '']">
    <span class="size-1.5 rounded-full" :class="style.dot" />
    {{ label ?? style.label }}
  </span>
</template>
