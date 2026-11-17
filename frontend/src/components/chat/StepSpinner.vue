<!-- 步骤标记：品牌 logo 的缩小版（三个圆点加一颗星）。
     active：顺时针依次亮起（进行中）；done / error：静止，展开时圆点变深、星转一个角度。 -->
<script setup lang="ts">
withDefaults(defineProps<{ state?: 'active' | 'done' | 'error' }>(), { state: 'active' })
</script>

<template>
  <span class="step-mark" :class="`is-${state}`" aria-hidden="true"><i /><i /><i /><i /></span>
</template>

<style scoped>
.step-mark {
  display: inline-grid;
  flex: none;
  grid-template: repeat(2, 1fr) / repeat(2, 1fr);
  gap: 2px;
  width: 1rem;
  height: 1rem;
}

.step-mark i {
  border-radius: 50%;
  background: var(--color-ink);
  transition: opacity 180ms, transform 220ms;
}

/* logo 里的那颗星 */
.step-mark i:nth-child(4) {
  border-radius: 0;
  background: var(--color-fun-blue);
  clip-path: polygon(50% 0, 62% 38%, 100% 50%, 62% 62%, 50% 100%, 38% 62%, 0 50%, 38% 38%);
}

/* 进行中：顺时针 左上 → 右上 → 右下（星）→ 左下 */
.is-active i {
  opacity: 0.18;
  transform: scale(0.72);
  animation: step-glow 1.2s ease-in-out infinite;
}
.is-active i:nth-child(1) { animation-delay: 0s; }
.is-active i:nth-child(2) { animation-delay: 0.15s; }
.is-active i:nth-child(4) { animation-delay: 0.3s; }
.is-active i:nth-child(3) { animation-delay: 0.45s; }

@keyframes step-glow {
  0%, 100% { opacity: 0.18; transform: scale(0.72); }
  25% { opacity: 1; transform: scale(1); }
  55% { opacity: 0.18; transform: scale(0.72); }
}

/* 完成 / 出错：静止；整行悬停或展开时更醒目 */
.is-done i, .is-error i { opacity: 0.3; transform: scale(0.82); }
.is-done i:nth-child(4), .is-error i:nth-child(4) { opacity: 0.9; transform: scale(1); }
.is-error i:nth-child(4) { background: var(--color-error, #e5484d); }
:global(summary:hover .is-done i:nth-child(-n + 3)),
:global(summary:hover .is-error i:nth-child(-n + 3)),
:global(details[open] > summary .is-done i:nth-child(-n + 3)),
:global(details[open] > summary .is-error i:nth-child(-n + 3)) { opacity: 0.75; transform: scale(0.9); }
:global(details[open] > summary .step-mark i:nth-child(4)) { transform: rotate(90deg) scale(1.1); }

@media (prefers-reduced-motion: reduce) {
  .is-active i { animation-duration: 2.4s; }
  .step-mark i { transition: none; }
}
</style>
