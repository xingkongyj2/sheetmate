<!-- 单个步骤的状态徽标：完成是渐变绿底的圆章加白色对勾，出错是红底感叹号，进行中沿用 logo 标记。 -->
<script setup lang="ts">
import StepSpinner from './StepSpinner.vue'

withDefaults(defineProps<{ status: 'ok' | 'error' | 'running' | 'warn' | 'idle' }>(), {})
</script>

<template>
  <StepSpinner v-if="status === 'running'" />
  <span v-else class="status-badge" :class="`is-${status}`" aria-hidden="true">
    <svg v-if="status === 'ok'" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M4.2 8.4l2.5 2.5 5-5.4" />
    </svg>
    <svg v-else-if="status === 'error'" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">
      <path d="M8 4.4v4.2M8 11.4v.1" />
    </svg>
    <svg v-else-if="status === 'warn'" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">
      <path d="M8 4.4v4.2M8 11.4v.1" />
    </svg>
  </span>
</template>

<style scoped>
.status-badge {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 1rem;
  height: 1rem;
  border-radius: 50%;
  color: #fff;
  animation: badge-pop 220ms cubic-bezier(0.34, 1.56, 0.64, 1);
}
.status-badge svg { width: 0.75rem; height: 0.75rem; }

.is-ok {
  background: linear-gradient(145deg, #4ddea0, #1fa971);
  box-shadow: 0 0 0 2.5px rgba(34, 181, 115, 0.14), inset 0 1px 0 rgba(255, 255, 255, 0.35);
}
.is-error {
  background: linear-gradient(145deg, #ff8a8f, #e5484d);
  box-shadow: 0 0 0 2.5px rgba(229, 72, 77, 0.14), inset 0 1px 0 rgba(255, 255, 255, 0.3);
}
.is-warn {
  background: linear-gradient(145deg, #ffcd5c, #e39a12);
  box-shadow: 0 0 0 2.5px rgba(227, 154, 18, 0.14), inset 0 1px 0 rgba(255, 255, 255, 0.35);
}
.is-idle {
  background: transparent;
  box-shadow: inset 0 0 0 1.5px var(--color-faint);
}

@keyframes badge-pop {
  from { opacity: 0; transform: scale(0.5); }
  to { opacity: 1; transform: scale(1); }
}

@media (prefers-reduced-motion: reduce) {
  .status-badge { animation: none; }
}
</style>
