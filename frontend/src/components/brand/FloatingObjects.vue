<script setup lang="ts">
/**
 * 漂浮的彩色小物件（登录页、引导页的插画区）。
 * 每个物件慢慢上下浮动、轻微旋转，鼠标移动时有一点视差。
 * 带表情的物件比较调皮：鼠标靠近会吓一跳躲开；输密码时（mood = happy）跟墨墨一起闭眼不偷看。
 */
import { computed, ref } from 'vue'

import { pointer, usePointer } from '@/composables/usePointer'

import FunObject, { type FunFace, type FunKind } from './FunObject.vue'

interface Item {
  kind: FunKind
  x: number // 百分比位置
  y: number
  size: number
  delay: number
  r0: number
  r1: number
  depth: number // 视差强度
  face?: FunFace
  flip?: boolean // 时不时翻个面（硬币）
}

const props = withDefaults(
  defineProps<{ preset?: 'auth' | 'hero' | 'empty'; mood?: 'idle' | 'thinking' | 'happy' | 'working' }>(),
  { preset: 'auth', mood: 'idle' },
)
usePointer()

const PRESETS: Record<string, Item[]> = {
  auth: [
    { kind: 'sheet', x: 54, y: 52, size: 70, delay: 0, r0: -8, r1: 4, depth: 10, face: 'smile' },
    { kind: 'funnel', x: 16, y: 36, size: 56, delay: 0.8, r0: -12, r1: -2, depth: 16, face: 'wow' },
    { kind: 'magnet', x: 74, y: 38, size: 58, delay: 1.6, r0: 6, r1: 14, depth: 12, face: 'wink' },
    { kind: 'sigma', x: 38, y: 37, size: 60, delay: 0.4, r0: -4, r1: 8, depth: 8, flip: true },
    { kind: 'cloud', x: 89, y: 52, size: 56, delay: 2.2, r0: 4, r1: -4, depth: 18, face: 'sleepy' },
    { kind: 'folder', x: 10, y: 57, size: 50, delay: 1.2, r0: -6, r1: 6, depth: 14, face: 'smile' },
    { kind: 'donut', x: 80, y: 67, size: 54, delay: 2.8, r0: 8, r1: 16, depth: 12, face: 'happy' },
    { kind: 'cheese', x: 23, y: 69, size: 54, delay: 0.2, r0: -10, r1: -2, depth: 20, face: 'wink' },
  ],
  hero: [
    { kind: 'sheet', x: 6, y: 30, size: 44, delay: 0, r0: -10, r1: 2, depth: 8 },
    { kind: 'magnet', x: 94, y: 24, size: 40, delay: 1.2, r0: 6, r1: 14, depth: 10 },
    { kind: 'sigma', x: 9, y: 72, size: 40, delay: 0.6, r0: -4, r1: 8, depth: 12 },
    { kind: 'spark', x: 92, y: 70, size: 36, delay: 1.8, r0: 0, r1: 20, depth: 14 },
  ],
  empty: [
    { kind: 'folder', x: 18, y: 22, size: 40, delay: 0, r0: -8, r1: 4, depth: 8 },
    { kind: 'spark', x: 80, y: 20, size: 30, delay: 1, r0: 0, r1: 20, depth: 10 },
    { kind: 'sheet', x: 82, y: 84, size: 36, delay: 0.5, r0: 6, r1: 12, depth: 12 },
  ],
}

const items = computed(() => PRESETS[props.preset])
const stage = ref<HTMLElement | null>(null)

/** 视差：鼠标偏离屏幕中心越多，物件反向移动越多 */
const parallax = computed(() => {
  if (!pointer.active || typeof window === 'undefined') return { x: 0, y: 0 }
  return { x: (pointer.x / window.innerWidth - 0.5) * 2, y: (pointer.y / window.innerHeight - 0.5) * 2 }
})

/** 躲鼠标：进入半径后往反方向弹开，越近躲得越远 */
const DODGE_RADIUS = 110
const DODGE_DISTANCE = 28
const dodges = computed(() => {
  const rect = pointer.active ? stage.value?.getBoundingClientRect() : undefined
  return items.value.map((item) => {
    if (!rect || !item.face) return null
    const dx = rect.left + (item.x / 100) * rect.width - pointer.x
    const dy = rect.top + (item.y / 100) * rect.height - pointer.y
    const dist = Math.hypot(dx, dy) || 1
    if (dist > DODGE_RADIUS) return null
    const push = (1 - dist / DODGE_RADIUS) * DODGE_DISTANCE
    return { x: (dx / dist) * push, y: (dy / dist) * push }
  })
})

function faceOf(item: Item, i: number): FunFace | null {
  if (!item.face) return null
  if (props.mood === 'happy') return 'happy'
  return dodges.value[i] ? 'wow' : item.face
}

function offset(item: Item, i: number) {
  const dodge = dodges.value[i]
  return { x: -parallax.value.x * item.depth + (dodge?.x ?? 0), y: -parallax.value.y * item.depth + (dodge?.y ?? 0) }
}
</script>

<template>
  <div ref="stage" class="pointer-events-none absolute inset-0">
    <div
      v-for="(item, i) in items"
      :key="i"
      class="absolute -translate-x-1/2 -translate-y-1/2 transition-transform duration-500 ease-[cubic-bezier(.3,1.45,.5,1)]"
      :style="{
        left: `${item.x}%`,
        top: `${item.y}%`,
        transform: `translate(calc(-50% + ${offset(item, i).x}px), calc(-50% + ${offset(item, i).y}px))`,
      }"
    >
      <div
        class="animate-float drop-shadow-[0_10px_14px_rgba(0,0,0,0.10)]"
        :style="{
          animationDelay: `${item.delay}s`,
          animationDuration: `${5 + (i % 3)}s`,
          '--r0': `${item.r0}deg`,
          '--r1': `${item.r1}deg`,
          perspective: item.flip ? '360px' : undefined,
        }"
      >
        <div :class="{ 'coin-flip': item.flip }">
          <FunObject :kind="item.kind" :size="item.size" :face="faceOf(item, i)" />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.coin-flip {
  animation: coin-flip 5.5s cubic-bezier(0.45, 0, 0.2, 1) 1.5s infinite;
}
@keyframes coin-flip {
  0%,
  82% {
    transform: rotateY(0);
  }
  96%,
  100% {
    transform: rotateY(360deg);
  }
}
@media (prefers-reduced-motion: reduce) {
  .coin-flip {
    animation: none;
  }
}
</style>
