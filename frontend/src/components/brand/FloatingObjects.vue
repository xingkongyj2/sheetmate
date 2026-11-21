<script setup lang="ts">
/**
 * 登录 / 引导页插画区的漂浮双色图标砖：
 * - 延续原版自然错落的全画幅散落排布（9 枚不同尺寸、微倾角度、分层视差）；
 * - 视觉统一为 SheetMate 暖白极简语言：纯白细描边圆角方砖 + 墨黑 / 翠绿 / 靛蓝双色线条图标，
 *   全部围绕真实业务主题（表格核平、公式 Σ、数据筛选、流程编排、一键复跑、客户库、脱敏盾牌、客户口径、双表对账）；
 * - 底层衬以克制的同心细虚线轨道与微量星火，将散落元素与底部探头的大墨墨自然串联。
 */
import { computed, ref } from 'vue'

import { pointer, usePointer } from '@/composables/usePointer'

type TileKind =
  | 'sheet'
  | 'sigma'
  | 'filter'
  | 'workflow'
  | 'replay'
  | 'customer'
  | 'privacy'
  | 'rule'
  | 'compare'

interface ScatterTile {
  kind: TileKind
  label: string
  /** 在整个左侧舞台的百分比坐标 */
  x: number
  y: number
  size: number
  radius: number
  delay: number
  r0: number
  r1: number
  depth: number
  hero?: boolean
}

const props = withDefaults(
  defineProps<{ preset?: 'auth' | 'hero' | 'empty'; mood?: 'idle' | 'thinking' | 'happy' | 'working' }>(),
  { preset: 'auth', mood: 'idle' },
)

usePointer()

// 延续原版自然错落的散落构图，覆盖上中下全区域（y: 25% ~ 68%），避免上方留白过空
const TILES: ScatterTile[] = [
  { kind: 'replay', label: '一键复跑', x: 62, y: 25, size: 48, radius: 14, delay: -1.4, r0: 4, r1: -5, depth: 16 },
  { kind: 'sigma', label: '公式计算', x: 37, y: 31, size: 60, radius: 17, delay: -0.4, r0: -5, r1: 6, depth: 9 },
  { kind: 'filter', label: '条件筛选', x: 16, y: 35, size: 56, radius: 16, delay: -0.9, r0: -11, r1: -2, depth: 17 },
  { kind: 'workflow', label: '流程编排', x: 76, y: 35, size: 58, radius: 17, delay: -1.7, r0: 5, r1: 12, depth: 12 },
  { kind: 'sheet', label: '表格核平', x: 52, y: 49, size: 70, radius: 20, delay: 0, r0: -7, r1: 4, depth: 10, hero: true },
  { kind: 'privacy', label: '数据脱敏', x: 88, y: 51, size: 56, radius: 16, delay: -2.3, r0: 4, r1: -4, depth: 18 },
  { kind: 'customer', label: '客户库', x: 11, y: 55, size: 54, radius: 15, delay: -1.2, r0: -6, r1: 5, depth: 14 },
  { kind: 'compare', label: '双表对账', x: 79, y: 66, size: 54, radius: 15, delay: -2.8, r0: 7, r1: 13, depth: 13 },
  { kind: 'rule', label: '客户口径', x: 24, y: 68, size: 54, radius: 15, delay: -0.2, r0: -9, r1: -2, depth: 19 },
]

const stage = ref<HTMLElement | null>(null)
const calm = computed(() => props.mood === 'happy')

const parallax = computed(() => {
  if (!pointer.active || typeof window === 'undefined') return { x: 0, y: 0 }
  return {
    x: (pointer.x / window.innerWidth - 0.5) * 2,
    y: (pointer.y / window.innerHeight - 0.5) * 2,
  }
})

// 轻柔避让：鼠标靠近时微幅滑开，保持鲜活手感但不生硬
const DODGE_RADIUS = 104
const DODGE_DISTANCE = 18

const dodges = computed(() => {
  const rect = pointer.active && !calm.value ? stage.value?.getBoundingClientRect() : undefined
  return TILES.map((tile) => {
    if (!rect) return null
    const dx = rect.left + (tile.x / 100) * rect.width - pointer.x
    const dy = rect.top + (tile.y / 100) * rect.height - pointer.y
    const dist = Math.hypot(dx, dy) || 1
    if (dist > DODGE_RADIUS) return null
    const push = (1 - dist / DODGE_RADIUS) * DODGE_DISTANCE
    return { x: (dx / dist) * push, y: (dy / dist) * push }
  })
})

function tileStyle(tile: ScatterTile, i: number) {
  const factor = calm.value ? 0.35 : 1
  const dodge = dodges.value[i]
  const tx = -parallax.value.x * tile.depth * factor + (dodge?.x ?? 0)
  const ty = -parallax.value.y * tile.depth * factor + (dodge?.y ?? 0)
  return {
    left: `${tile.x}%`,
    top: `${tile.y}%`,
    transform: `translate(calc(-50% + ${tx}px), calc(-50% + ${ty}px))`,
  }
}

const orbitStyle = computed(() => {
  const factor = calm.value ? 0.3 : 1
  return {
    transform: `translate(${-parallax.value.x * 4 * factor}px, ${-parallax.value.y * 4 * factor}px)`,
  }
})
</script>

<template>
  <div ref="stage" class="pointer-events-none absolute inset-0 overflow-hidden select-none" aria-hidden="true">
    <!-- 柔和的同心细虚线轨道底纹 + 零星微星，连接全屏散落元素与底部墨墨 -->
    <svg
      class="absolute inset-0 h-full w-full transition-transform duration-700 ease-out"
      :style="orbitStyle"
      viewBox="0 0 720 900"
      preserveAspectRatio="xMidYMax slice"
      fill="none"
    >
      <circle cx="360" cy="835" r="245" stroke="#DDDAD2" stroke-width="1.1" stroke-dasharray="3 6" opacity="0.72" />
      <circle cx="360" cy="835" r="415" stroke="#D8D5CD" stroke-width="1.15" stroke-dasharray="4 6.5" opacity="0.78" />
      <circle cx="360" cy="835" r="590" stroke="#DDDAD2" stroke-width="1.1" stroke-dasharray="3.5 7" opacity="0.62" />

      <!-- 极克制的散落小星火与微点 -->
      <path
        class="orbit-twinkle"
        d="M192 222 L194.2 227.4 L199.6 229.6 L194.2 231.8 L192 237.2 L189.8 231.8 L184.4 229.6 L189.8 227.4 Z"
        fill="#6265DC"
      />
      <path
        class="orbit-twinkle orbit-twinkle-delay"
        d="M524 468 L525.8 472.2 L530 474 L525.8 475.8 L524 480 L522.2 475.8 L518 474 L522.2 472.2 Z"
        fill="#22B573"
      />
      <path
        class="orbit-twinkle orbit-twinkle-slow"
        d="M312 586 L313.5 589.6 L317.1 591.1 L313.5 592.6 L312 596.2 L310.5 592.6 L306.9 591.1 L310.5 589.6 Z"
        fill="#6265DC"
        opacity="0.7"
      />
      <circle cx="512" cy="242" r="3" fill="#22B573" opacity="0.52" />
      <circle cx="240" cy="436" r="2.8" fill="#6265DC" opacity="0.4" />
      <circle cx="436" cy="366" r="2.4" fill="#18181B" opacity="0.18" />
      <circle cx="630" cy="358" r="2.6" fill="#18181B" opacity="0.14" />
      <circle cx="96" cy="616" r="2.6" fill="#22B573" opacity="0.44" />
    </svg>

    <!-- 9 枚自然错落的白底圆角双色图标砖 -->
    <div
      v-for="(tile, i) in TILES"
      :key="tile.kind"
      class="absolute transition-transform duration-500 ease-[cubic-bezier(.3,1.4,.5,1)]"
      :style="tileStyle(tile, i)"
    >
      <div
        class="orbit-float"
        :class="{ 'orbit-calm': calm }"
        :style="{
          animationDelay: `${tile.delay}s`,
          animationDuration: `${5.2 + (i % 3) * 0.85}s`,
          '--r0': `${tile.r0}deg`,
          '--r1': `${tile.r1}deg`,
        }"
      >
        <div
          class="orbit-tile flex items-center justify-center"
          :class="tile.hero ? 'orbit-tile-hero' : ''"
          :style="{
            width: `${tile.size}px`,
            height: `${tile.size}px`,
            borderRadius: `${tile.radius}px`,
          }"
        >
          <!-- 1. 中心主砖：绿顶表格 + 翠绿核平对勾 -->
          <svg v-if="tile.kind === 'sheet'" width="36" height="36" viewBox="0 0 28 28" fill="none">
            <rect x="3" y="4" width="21" height="19" rx="4" fill="#FFFFFF" stroke="#18181B" stroke-width="1.8" />
            <path d="M3.9 8A3.1 3.1 0 0 1 7 4.9h13a3.1 3.1 0 0 1 3.1 3.1v2.2H3.9V8Z" fill="#22B573" fill-opacity="0.24" />
            <path d="M3 10.2h21" stroke="#18181B" stroke-width="1.6" />
            <path d="M10.2 10.2V23M16.8 10.2V18.5M3 16.2h13.5" stroke="#18181B" stroke-width="1.45" stroke-opacity="0.46" stroke-linecap="round" />
            <circle cx="21.2" cy="20.8" r="5.2" fill="#22B573" stroke="#FFFFFF" stroke-width="1.8" />
            <path d="M19 20.8l1.5 1.5 3-3.1" stroke="#FFFFFF" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" />
          </svg>

          <!-- 2. 公式汇总 Σ -->
          <svg v-else-if="tile.kind === 'sigma'" width="30" height="30" viewBox="0 0 24 24" fill="none">
            <rect x="2.5" y="2.5" width="19" height="19" rx="5.5" fill="#F2F3FD" />
            <path d="M17.2 7H7.6l4.9 5-4.9 5h9.6" stroke="#6265DC" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" />
          </svg>

          <!-- 3. 数据筛选漏斗 -->
          <svg v-else-if="tile.kind === 'filter'" width="27" height="27" viewBox="0 0 24 24" fill="none">
            <path
              d="M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 19.6 6.5l-5.1 6.1a1.5 1.5 0 0 0-.35.97v4.93a1 1 0 0 1-1.45.9l-2.3-1.15a1 1 0 0 1-.55-.9v-3.78a1.5 1.5 0 0 0-.35-.97L4.4 6.5A1.5 1.5 0 0 1 4 5.5Z"
              fill="#EEF0FF"
              stroke="#18181B"
              stroke-width="1.75"
              stroke-linejoin="round"
            />
            <circle cx="18.5" cy="17.5" r="2.6" fill="#6265DC" />
          </svg>

          <!-- 4. 流程节点编排 -->
          <svg v-else-if="tile.kind === 'workflow'" width="29" height="29" viewBox="0 0 24 24" fill="none">
            <rect x="3" y="3.5" width="7.5" height="7.5" rx="2.2" fill="#EEF0FF" stroke="#6265DC" stroke-width="1.8" />
            <rect x="13.5" y="13" width="7.5" height="7.5" rx="2.2" fill="#E8F8F0" stroke="#22B573" stroke-width="1.8" />
            <path d="M10.5 7.25h4.2a2.5 2.5 0 0 1 2.5 2.5V13" stroke="#18181B" stroke-width="1.8" stroke-linecap="round" />
            <path d="M6.75 11v3.25a2.5 2.5 0 0 0 2.5 2.5H13.5" stroke="#18181B" stroke-width="1.5" stroke-opacity="0.32" stroke-linecap="round" stroke-dasharray="2 2.5" />
          </svg>

          <!-- 5. 一键复跑 -->
          <svg v-else-if="tile.kind === 'replay'" width="24" height="24" viewBox="0 0 24 24" fill="none">
            <path d="M20 12a8 8 0 1 1-2.34-5.66" stroke="#18181B" stroke-width="1.9" stroke-linecap="round" />
            <path d="M20 4.5v4.5h-4.5" stroke="#18181B" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" />
            <path d="M10.3 9.2 15 12l-4.7 2.8V9.2Z" fill="#22B573" />
          </svg>

          <!-- 6. 客户库（呼应左侧菜单 CircleUserRound） -->
          <svg v-else-if="tile.kind === 'customer'" width="27" height="27" viewBox="0 0 24 24" fill="none">
            <circle cx="12" cy="12" r="9" fill="#F4F3EE" stroke="#18181B" stroke-width="1.8" />
            <circle cx="12" cy="10.2" r="3.1" stroke="#18181B" stroke-width="1.8" />
            <path d="M6.8 18.8a5.6 5.6 0 0 1 10.4 0" stroke="#18181B" stroke-width="1.8" stroke-linecap="round" />
            <circle cx="18.6" cy="5.8" r="2.6" fill="#22B573" stroke="#FFFFFF" stroke-width="1.4" />
          </svg>

          <!-- 7. 数据脱敏盾牌 -->
          <svg v-else-if="tile.kind === 'privacy'" width="27" height="27" viewBox="0 0 24 24" fill="none">
            <path
              d="M19.5 12.2c0 4.6-3.2 7-7.1 8.4a1.1 1.1 0 0 1-.8 0c-3.9-1.4-7.1-3.8-7.1-8.4V6.3a1 1 0 0 1 1-1c1.9 0 4.2-1 5.9-2.4a1 1 0 0 1 1.2 0c1.7 1.4 4 2.4 5.9 2.4a1 1 0 0 1 1 1v5.9Z"
              fill="#E8F8F0"
              stroke="#22B573"
              stroke-width="1.8"
              stroke-linejoin="round"
            />
            <path d="m9.3 12 1.9 1.9 3.7-3.8" stroke="#159A5D" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" />
          </svg>

          <!-- 8. 客户口径便签 -->
          <svg v-else-if="tile.kind === 'rule'" width="27" height="27" viewBox="0 0 24 24" fill="none">
            <path
              d="M6.5 3.5h11A2.5 2.5 0 0 1 20 6v9.6L15.6 20H6.5A2.5 2.5 0 0 1 4 17.5V6a2.5 2.5 0 0 1 2.5-2.5Z"
              fill="#FFFFFF"
              stroke="#18181B"
              stroke-width="1.75"
              stroke-linejoin="round"
            />
            <path d="M15.4 20v-3.2a1.4 1.4 0 0 1 1.4-1.4H20" fill="#E9EAFC" stroke="#6265DC" stroke-width="1.6" stroke-linejoin="round" />
            <path d="M8 8.5h6.2" stroke="#6265DC" stroke-width="2" stroke-linecap="round" />
            <path d="M8 12.5h8M8 16h4.2" stroke="#18181B" stroke-width="1.6" stroke-opacity="0.45" stroke-linecap="round" />
          </svg>

          <!-- 9. 双表对账比对 -->
          <svg v-else width="27" height="27" viewBox="0 0 24 24" fill="none">
            <rect x="3" y="4.5" width="11" height="12.5" rx="2.5" fill="#F4F3EE" stroke="#18181B" stroke-width="1.7" />
            <path d="M3.5 8.5h10" stroke="#18181B" stroke-width="1.4" stroke-opacity="0.45" />
            <rect x="10" y="7.5" width="11" height="12.5" rx="2.5" fill="#FFFFFF" stroke="#18181B" stroke-width="1.75" />
            <path d="M10.7 10a1.8 1.8 0 0 1 1.8-1.8h6a1.8 1.8 0 0 1 1.8 1.8v1.3h-9.6V10Z" fill="#22B573" fill-opacity="0.25" />
            <path d="M10 11.4h11" stroke="#18181B" stroke-width="1.4" />
            <path d="m13.3 15.6 1.5 1.5 3-3" stroke="#22B573" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.orbit-tile {
  background: #ffffff;
  border: 1px solid #e5e3de;
  box-shadow:
    0 12px 26px -14px rgba(24, 24, 27, 0.14),
    0 2px 6px -2px rgba(24, 24, 27, 0.05),
    inset 0 1px 0 rgba(255, 255, 255, 0.9);
  transition: opacity 0.35s ease, transform 0.35s ease;
}

.orbit-tile-hero {
  border-color: #dfddd7;
  box-shadow:
    0 16px 32px -16px rgba(24, 24, 27, 0.18),
    0 3px 8px -2px rgba(24, 24, 27, 0.06),
    inset 0 1px 0 rgba(255, 255, 255, 0.95);
}

.orbit-float {
  animation: orbit-bob 5.8s ease-in-out infinite;
  transition: opacity 0.35s ease;
}

.orbit-calm {
  opacity: 0.7;
}

.orbit-twinkle {
  transform-box: fill-box;
  transform-origin: center;
  animation: twinkle-soft 3.4s ease-in-out infinite;
}

.orbit-twinkle-delay {
  animation-delay: 1.4s;
}

.orbit-twinkle-slow {
  animation-duration: 4.2s;
  animation-delay: 0.7s;
}

@keyframes orbit-bob {
  0%, 100% {
    transform: translate3d(0, 0, 0) rotate(var(--r0, -4deg));
  }
  50% {
    transform: translate3d(0, -10px, 0) rotate(var(--r1, 4deg));
  }
}

@keyframes twinkle-soft {
  0%, 100% {
    opacity: 0.42;
    transform: scale(0.85);
  }
  50% {
    opacity: 0.94;
    transform: scale(1.12);
  }
}

@media (prefers-reduced-motion: reduce) {
  .orbit-float,
  .orbit-twinkle {
    animation: none;
  }
}
</style>
