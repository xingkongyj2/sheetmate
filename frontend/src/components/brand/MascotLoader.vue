<script setup lang="ts">
/**
 * SheetMate 统一小团子（墨墨）模块/页面加载器：
 * - 主 IP 头顶绿芽的小团子轻盈弹性蹦跳 + 地面阴影呼吸 + 绿芽惯性摇曳 + 好奇张望眨眼 + 底部三点波浪；
 * - 默认自带垂直与水平居中，适用于整页、侧栏面板、Tab 内容区及弹窗加载态。
 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    size?: 'sm' | 'md' | 'lg'
    label?: string
  }>(),
  { size: 'md', label: '' },
)

const px = computed(() => (props.size === 'sm' ? 48 : props.size === 'lg' ? 72 : 60))
</script>

<template>
  <div
    class="flex flex-1 flex-col items-center justify-center gap-2.5 select-none"
    role="status"
    :aria-label="label || '加载中'"
  >
    <svg
      :width="px"
      :height="px"
      viewBox="0 0 88 88"
      fill="none"
      class="overflow-visible"
      aria-hidden="true"
      focusable="false"
    >
      <!-- 柔和暖白圆底光晕 -->
      <circle class="loader-halo" cx="44" cy="46" r="30" fill="#F3F2EE" />

      <!-- 随跳跃呼吸的地面软阴影 -->
      <ellipse class="loader-shadow" cx="44" cy="74" rx="16.5" ry="3.2" fill="#18181B" />

      <!-- 右上角靛蓝四芒小星火与左上角微芽点 -->
      <path
        class="loader-spark"
        d="M68 24 L69.3 27.2 L72.5 28.5 L69.3 29.8 L68 33 L66.7 29.8 L63.5 28.5 L66.7 27.2 Z"
        fill="#6265DC"
      />
      <circle class="loader-dot-accent" cx="20" cy="30" r="2.2" fill="#22B573" />

      <!-- 弹性蹦跳的小团子（墨墨） -->
      <g class="loader-hop">
        <!-- 惯性摆动的招牌绿芽 -->
        <g class="loader-sprout">
          <path d="M44 33 C44 28.2 44.8 24.8 46.2 21.6" stroke="#159A5D" stroke-width="2.3" stroke-linecap="round" />
          <path d="M46 23.4 C49.4 18 57.6 18.8 56.8 23.4 C52.8 26.8 48 26.4 46 23.4 Z" fill="#22B573" />
          <path d="M45 25.4 C42.2 21 36 22 36.5 25.2 C39.6 27.8 43.2 27.6 45 25.4 Z" fill="#5FD39A" />
        </g>

        <!-- 小圆耳/手点缀 -->
        <ellipse cx="23.5" cy="54.5" rx="3.4" ry="5" fill="#111113" transform="rotate(-18 23.5 54.5)" />
        <ellipse cx="64.5" cy="54.5" rx="3.4" ry="5" fill="#111113" transform="rotate(18 64.5 54.5)" />

        <!-- 圆润墨黑身体 -->
        <path
          d="M44 31.5 C57.2 31.5 65 40.4 65 52 C65 62.6 57.2 68.5 44 68.5 C30.8 68.5 23 62.6 23 52 C23 40.4 30.8 31.5 44 31.5 Z"
          fill="#111113"
        />

        <!-- 好奇左右张望并自然眨眼的大眼睛 -->
        <g class="loader-eyes">
          <ellipse cx="37.2" cy="49.5" rx="4.8" ry="5.8" fill="#FFFFFF" />
          <ellipse cx="50.8" cy="49.5" rx="4.8" ry="5.8" fill="#FFFFFF" />
          <g class="loader-pupils">
            <circle cx="37.2" cy="49.5" r="2.45" fill="#111113" />
            <circle cx="50.8" cy="49.5" r="2.45" fill="#111113" />
            <circle cx="38.2" cy="48.1" r="0.9" fill="#FFFFFF" />
            <circle cx="51.8" cy="48.1" r="0.9" fill="#FFFFFF" />
          </g>
        </g>

        <!-- 柔和微笑 -->
        <path d="M41.2 58.6 Q44 61.1 46.8 58.6" stroke="#FFFFFF" stroke-width="1.7" stroke-linecap="round" opacity="0.82" />
      </g>
    </svg>

    <!-- 可选简短提示文案或极简三点律动 -->
    <div v-if="label" class="flex items-center gap-1.5 text-xs text-faint">
      <span>{{ label }}</span>
      <span class="inline-flex items-center gap-0.5" aria-hidden="true">
        <i class="loader-wave-dot dot-a" />
        <i class="loader-wave-dot dot-b" />
        <i class="loader-wave-dot dot-c" />
      </span>
    </div>
    <div v-else class="flex items-center gap-1.5" aria-hidden="true">
      <i class="loader-wave-dot dot-a" />
      <i class="loader-wave-dot dot-b" />
      <i class="loader-wave-dot dot-c" />
    </div>
  </div>
</template>

<style scoped>
.loader-hop {
  transform-box: fill-box;
  transform-origin: 50% 100%;
  animation: mascot-hop 1.18s cubic-bezier(0.45, 0.05, 0.55, 0.95) infinite;
}

.loader-shadow {
  transform-box: fill-box;
  transform-origin: center;
  animation: mascot-shadow 1.18s cubic-bezier(0.45, 0.05, 0.55, 0.95) infinite;
}

.loader-sprout {
  transform-box: fill-box;
  transform-origin: 50% 100%;
  animation: sprout-wiggle 1.18s ease-in-out infinite;
}

.loader-eyes {
  transform-box: fill-box;
  transform-origin: center;
  animation: loader-blink 3.8s ease-in-out infinite;
}

.loader-pupils {
  animation: loader-glance 2.36s ease-in-out infinite;
}

.loader-halo {
  transform-box: fill-box;
  transform-origin: center;
  animation: halo-breath 2.36s ease-in-out infinite;
}

.loader-spark {
  transform-box: fill-box;
  transform-origin: center;
  animation: spark-twinkle 1.8s ease-in-out infinite;
}

.loader-dot-accent {
  transform-box: fill-box;
  transform-origin: center;
  animation: spark-twinkle 2.1s ease-in-out 0.6s infinite;
}

.loader-wave-dot {
  display: inline-block;
  width: 4px;
  height: 4px;
  border-radius: 9999px;
  background-color: #18181b;
  opacity: 0.2;
  animation: dot-bounce 1.18s ease-in-out infinite;
}
.dot-a { animation-delay: 0s; }
.dot-b { animation-delay: 0.16s; }
.dot-c { animation-delay: 0.32s; }

@keyframes mascot-hop {
  0%, 100% {
    transform: translateY(0) scaleX(1.07) scaleY(0.92);
  }
  16% {
    transform: translateY(-2px) scaleX(0.96) scaleY(1.05);
  }
  46% {
    transform: translateY(-11px) scaleX(0.95) scaleY(1.05);
  }
  78% {
    transform: translateY(0.5px) scaleX(1.06) scaleY(0.93);
  }
}

@keyframes mascot-shadow {
  0%, 100% {
    opacity: 0.14;
    transform: scaleX(1.08);
  }
  46% {
    opacity: 0.06;
    transform: scaleX(0.68);
  }
  78% {
    opacity: 0.13;
    transform: scaleX(1.04);
  }
}

@keyframes sprout-wiggle {
  0%, 100% { transform: rotate(-6deg); }
  46% { transform: rotate(8deg); }
}

@keyframes loader-blink {
  0%, 88%, 100% { transform: scaleY(1); }
  94% { transform: scaleY(0.1); }
}

@keyframes loader-glance {
  0%, 100% { transform: translateX(-1.1px); }
  50% { transform: translateX(1.1px); }
}

@keyframes halo-breath {
  0%, 100% { transform: scale(0.96); opacity: 0.78; }
  50% { transform: scale(1.04); opacity: 1; }
}

@keyframes spark-twinkle {
  0%, 100% { opacity: 0.35; transform: scale(0.8); }
  50% { opacity: 0.95; transform: scale(1.15); }
}

@keyframes dot-bounce {
  0%, 70%, 100% { opacity: 0.22; transform: translateY(0); }
  35% { opacity: 0.72; transform: translateY(-2.5px); }
}

@media (prefers-reduced-motion: reduce) {
  .loader-hop,
  .loader-shadow,
  .loader-sprout,
  .loader-eyes,
  .loader-pupils,
  .loader-halo,
  .loader-spark,
  .loader-dot-accent,
  .loader-wave-dot {
    animation: none;
  }
  .loader-shadow { opacity: 0.11; }
  .loader-wave-dot { opacity: 0.4; }
}
</style>
