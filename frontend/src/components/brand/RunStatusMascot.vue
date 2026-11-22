<script setup lang="ts">
/**
 * 运行记录专用小团子（墨墨）IP 状态图标：
 * - 用墨墨的表情、头顶绿芽与右下角状态小徽章直观传达当次执行结果（成功 / 失败 / 有提示 / 运行中 / 已停止）。
 */
import { computed } from 'vue'

type Mood = 'success' | 'failed' | 'warning' | 'running' | 'idle'

const props = withDefaults(
  defineProps<{
    status: string
    size?: number
  }>(),
  { size: 36 },
)

const mood = computed<Mood>(() => {
  if (['success', 'pass', 'repaired'].includes(props.status)) return 'success'
  if (['failed', 'fail', 'escalated'].includes(props.status)) return 'failed'
  if (['warning', 'warn', 'incomplete', 'interrupted'].includes(props.status)) return 'warning'
  if (props.status === 'running') return 'running'
  return 'idle'
})

const PALETTE: Record<Mood, { bg: string; border: string; badge: string }> = {
  success: { bg: '#EFFAF4', border: '#CBEAD7', badge: '#22B573' },
  failed: { bg: '#FEF3F3', border: '#F6CDCD', badge: '#EF4444' },
  warning: { bg: '#FFF9EC', border: '#F2DCAD', badge: '#F2A21B' },
  running: { bg: '#F2F7FF', border: '#D0E2FF', badge: '#2F7BFF' },
  idle: { bg: '#F5F4F2', border: '#E4E2DC', badge: '#A8A49E' },
}

const theme = computed(() => PALETTE[mood.value])
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 40 40"
    fill="none"
    class="shrink-0 select-none"
    aria-hidden="true"
    focusable="false"
  >
    <!-- 柔和状态底框 -->
    <rect
      x="1"
      y="1"
      width="38"
      height="38"
      rx="11"
      :fill="theme.bg"
      :stroke="theme.border"
      stroke-width="1.2"
    />

    <!-- 小团子墨墨本体 -->
    <g :class="{ 'run-mascot-bob': mood === 'running' }">
      <!-- 头顶招牌绿芽（失败时微微歪头耷拉） -->
      <g :transform="mood === 'failed' ? 'rotate(22 19 13.5)' : undefined">
        <path
          d="M19 13.6 C19 11.2 19.6 9.3 20.6 7.8"
          :stroke="mood === 'failed' ? '#7E9187' : '#159A5D'"
          stroke-width="1.8"
          stroke-linecap="round"
        />
        <path
          d="M20.4 8.6 C22.8 5.9 27.1 6.6 26.5 9.3 C24.2 11.2 21.6 10.6 20.4 8.6 Z"
          :fill="mood === 'failed' ? '#9CB0A5' : '#22B573'"
        />
        <path
          d="M19.5 10 C17.6 7.7 14 8.3 14.3 10.2 C16 11.8 18.2 11.5 19.5 10 Z"
          :fill="mood === 'failed' ? '#BAC9C1' : '#5FD39A'"
        />
      </g>

      <!-- 小圆手 -->
      <ellipse cx="7.4" cy="24.6" rx="1.9" ry="2.8" :fill="mood === 'idle' ? '#2C2C31' : '#141417'" transform="rotate(-18 7.4 24.6)" />
      <ellipse cx="30.6" cy="24.6" rx="1.9" ry="2.8" :fill="mood === 'idle' ? '#2C2C31' : '#141417'" transform="rotate(18 30.6 24.6)" />

      <!-- 圆润墨黑身体 -->
      <path
        d="M19 13 C26.2 13 30.2 17.6 30.2 23.2 C30.2 28.6 26 31.8 19 31.8 C12 31.8 7.8 28.6 7.8 23.2 C7.8 17.6 11.8 13 19 13 Z"
        :fill="mood === 'idle' ? '#2C2C31' : '#141417'"
      />

      <!-- 成功：开心弯弯眼 + 腮红 + 微笑 -->
      <template v-if="mood === 'success'">
        <path d="M13.2 22.3 Q15.2 19.8 17.2 22.3" stroke="#FFFFFF" stroke-width="2" stroke-linecap="round" />
        <path d="M20.8 22.3 Q22.8 19.8 24.8 22.3" stroke="#FFFFFF" stroke-width="2" stroke-linecap="round" />
        <ellipse cx="12.2" cy="24.7" rx="1.7" ry="1.05" fill="#FF8DA6" opacity="0.58" />
        <ellipse cx="25.8" cy="24.7" rx="1.7" ry="1.05" fill="#FF8DA6" opacity="0.58" />
        <path d="M17.2 25.4 Q19 27.1 20.8 25.4" stroke="#FFFFFF" stroke-width="1.5" stroke-linecap="round" opacity="0.92" />
      </template>

      <!-- 失败：晕晕 × × 眼 + 委屈嘴 + 小汗滴 -->
      <template v-else-if="mood === 'failed'">
        <path d="M13.6 20.4 L16.6 23.4 M16.6 20.4 L13.6 23.4" stroke="#FFFFFF" stroke-width="1.8" stroke-linecap="round" />
        <path d="M21.4 20.4 L24.4 23.4 M24.4 20.4 L21.4 23.4" stroke="#FFFFFF" stroke-width="1.8" stroke-linecap="round" />
        <path d="M17 26.8 Q19 25 21 26.8" stroke="#FFFFFF" stroke-width="1.5" stroke-linecap="round" opacity="0.88" />
        <path d="M29.4 14.4 C29.4 14.4 31.3 16.7 31.3 17.9 A1.9 1.9 0 0 1 27.5 17.9 C27.5 16.7 29.4 14.4 29.4 14.4 Z" fill="#63B6FF" />
      </template>

      <!-- 有提示 / 未完成：圆溜溜留意大眼 + 小惊讶嘴 -->
      <template v-else-if="mood === 'warning'">
        <circle cx="15.3" cy="21.7" r="2.45" fill="#FFFFFF" />
        <circle cx="22.7" cy="21.7" r="2.45" fill="#FFFFFF" />
        <circle cx="15.3" cy="21.7" r="1.2" fill="#141417" />
        <circle cx="22.7" cy="21.7" r="1.2" fill="#141417" />
        <circle cx="19" cy="26.1" r="1.2" stroke="#FFFFFF" stroke-width="1.4" opacity="0.9" />
      </template>

      <!-- 运行中：元气大眼斜向上看 -->
      <template v-else-if="mood === 'running'">
        <ellipse cx="15.3" cy="21.7" rx="2.2" ry="2.65" fill="#FFFFFF" />
        <ellipse cx="22.7" cy="21.7" rx="2.2" ry="2.65" fill="#FFFFFF" />
        <circle cx="15.9" cy="21.2" r="1.15" fill="#141417" />
        <circle cx="23.3" cy="21.2" r="1.15" fill="#141417" />
        <circle cx="16.3" cy="20.6" r="0.45" fill="#FFFFFF" />
        <circle cx="23.7" cy="20.6" r="0.45" fill="#FFFFFF" />
        <path d="M17.5 25.6 Q19 26.8 20.5 25.6" stroke="#FFFFFF" stroke-width="1.45" stroke-linecap="round" opacity="0.88" />
      </template>

      <!-- 已停止 / 等待中：安静闭眼 -->
      <template v-else>
        <path d="M13.5 22.3 L17 22.3 M21 22.3 L24.5 22.3" stroke="#FFFFFF" stroke-width="1.8" stroke-linecap="round" opacity="0.88" />
        <path d="M17.6 25.7 Q19 26.4 20.4 25.7" stroke="#FFFFFF" stroke-width="1.4" stroke-linecap="round" opacity="0.72" />
      </template>
    </g>

    <!-- 右下角状态小徽章 -->
    <g>
      <circle cx="32.2" cy="32.2" r="6" :fill="theme.badge" :stroke="theme.bg" stroke-width="2" />
      <path
        v-if="mood === 'success'"
        d="M29.7 32.2 L31.4 33.9 L34.9 30.4"
        stroke="#FFFFFF"
        stroke-width="1.8"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
      <path
        v-else-if="mood === 'failed'"
        d="M30.2 30.2 L34.2 34.2 M34.2 30.2 L30.2 34.2"
        stroke="#FFFFFF"
        stroke-width="1.8"
        stroke-linecap="round"
      />
      <g v-else-if="mood === 'warning'">
        <path d="M32.2 29.6 L32.2 32.7" stroke="#FFFFFF" stroke-width="1.8" stroke-linecap="round" />
        <circle cx="32.2" cy="34.8" r="0.95" fill="#FFFFFF" />
      </g>
      <circle
        v-else-if="mood === 'running'"
        cx="32.2"
        cy="32.2"
        r="2.1"
        fill="#FFFFFF"
        class="run-badge-pulse"
      />
      <path
        v-else
        d="M30.1 32.2 L34.3 32.2"
        stroke="#FFFFFF"
        stroke-width="1.8"
        stroke-linecap="round"
      />
    </g>
  </svg>
</template>

<style scoped>
.run-mascot-bob {
  transform-box: fill-box;
  transform-origin: center;
  animation: run-bob 1.5s ease-in-out infinite;
}

.run-badge-pulse {
  transform-box: fill-box;
  transform-origin: center;
  animation: run-pulse 1.3s ease-in-out infinite;
}

@keyframes run-bob {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-1.6px); }
}

@keyframes run-pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.45; transform: scale(0.7); }
}

@media (prefers-reduced-motion: reduce) {
  .run-mascot-bob,
  .run-badge-pulse {
    animation: none;
  }
}
</style>
