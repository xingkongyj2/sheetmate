<script setup lang="ts">
/**
 * 对话图标：
 * - 常规态：极简纯圆线条气泡，随 currentColor 渲染；
 * - 运行态（running）：保留气泡轮廓一致感，改为柔和底晕 + 沿气泡边缘循环流动的微光弧 + 气泡内三点错峰律动。
 */
withDefaults(defineProps<{ active?: boolean; running?: boolean }>(), { active: false, running: false })

const BUBBLE_PATH =
  'M10 2.75A7.15 7.15 0 1 1 6.65 16.21L3.55 16.95A.55.55 0 0 1 2.95 16.35L3.69 13.25A7.15 7.15 0 0 1 10 2.75Z'
</script>

<template>
  <svg
    viewBox="0 0 20 20"
    fill="none"
    :aria-hidden="!running"
    :aria-label="running ? '运行中' : undefined"
    :role="running ? 'status' : undefined"
    focusable="false"
  >
    <template v-if="running">
      <!-- 轻盈半透底色与柔和外轨 -->
      <path
        :d="BUBBLE_PATH"
        fill="currentColor"
        fill-opacity="0.1"
        stroke="currentColor"
        stroke-opacity="0.26"
        stroke-width="1.5"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
      <!-- 沿气泡轮廓顺滑游走的流光弧 -->
      <path
        :d="BUBBLE_PATH"
        class="bubble-trace"
        pathLength="100"
        stroke="currentColor"
        stroke-width="1.8"
        stroke-linecap="round"
        stroke-linejoin="round"
        stroke-dasharray="32 68"
      />
      <!-- 气泡内错峰律动的思考三点 -->
      <circle class="bubble-dot dot-1" cx="6.85" cy="10.1" r="1.15" fill="currentColor" />
      <circle class="bubble-dot dot-2" cx="10" cy="10.1" r="1.15" fill="currentColor" />
      <circle class="bubble-dot dot-3" cx="13.15" cy="10.1" r="1.15" fill="currentColor" />
    </template>
    <path
      v-else
      :d="BUBBLE_PATH"
      stroke="currentColor"
      :stroke-width="active ? 1.7 : 1.55"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
  </svg>
</template>

<style scoped>
.bubble-trace {
  animation: bubble-orbit 1.4s linear infinite;
}

.bubble-dot {
  transform-box: fill-box;
  transform-origin: center;
  animation: bubble-wave 1.15s ease-in-out infinite;
}
.dot-1 { animation-delay: 0s; }
.dot-2 { animation-delay: 0.16s; }
.dot-3 { animation-delay: 0.32s; }

@keyframes bubble-orbit {
  from { stroke-dashoffset: 100; }
  to { stroke-dashoffset: 0; }
}

@keyframes bubble-wave {
  0%, 70%, 100% {
    opacity: 0.35;
    transform: translateY(0) scale(0.86);
  }
  35% {
    opacity: 1;
    transform: translateY(-1.5px) scale(1.1);
  }
}

@media (prefers-reduced-motion: reduce) {
  .bubble-trace {
    animation-duration: 3s;
  }
  .bubble-dot {
    animation-duration: 2.4s;
  }
}
</style>
