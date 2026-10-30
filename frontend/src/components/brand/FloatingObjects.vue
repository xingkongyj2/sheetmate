<script setup lang="ts">
/**
 * 漂浮的彩色小物件（登录页、引导页、首页的右侧插画区）。
 * 每个物件慢慢上下浮动、轻微旋转，鼠标移动时有一点视差。
 */
import { computed } from 'vue'

import { pointer, usePointer } from '@/composables/usePointer'

import FunObject, { type FunKind } from './FunObject.vue'

interface Item {
  kind: FunKind
  x: number // 百分比位置
  y: number
  size: number
  delay: number
  r0: number
  r1: number
  depth: number // 视差强度
}

const props = withDefaults(defineProps<{ preset?: 'auth' | 'hero' | 'empty' }>(), { preset: 'auth' })
usePointer()

const PRESETS: Record<string, Item[]> = {
  auth: [
    { kind: 'sheet', x: 54, y: 52, size: 70, delay: 0, r0: -8, r1: 4, depth: 10 },
    { kind: 'funnel', x: 16, y: 36, size: 56, delay: 0.8, r0: -12, r1: -2, depth: 16 },
    { kind: 'magnet', x: 74, y: 38, size: 58, delay: 1.6, r0: 6, r1: 14, depth: 12 },
    { kind: 'sigma', x: 38, y: 37, size: 60, delay: 0.4, r0: -4, r1: 8, depth: 8 },
    { kind: 'puzzle', x: 89, y: 53, size: 44, delay: 2.2, r0: 10, r1: -4, depth: 18 },
    { kind: 'folder', x: 9, y: 56, size: 48, delay: 1.2, r0: -6, r1: 6, depth: 14 },
    { kind: 'crown', x: 80, y: 66, size: 48, delay: 2.8, r0: 8, r1: 16, depth: 12 },
    { kind: 'rocket', x: 24, y: 68, size: 54, delay: 0.2, r0: -14, r1: -4, depth: 20 },
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

/** 视差：鼠标偏离屏幕中心越多，物件反向移动越多 */
const parallax = computed(() => {
  if (!pointer.active || typeof window === 'undefined') return { x: 0, y: 0 }
  return { x: (pointer.x / window.innerWidth - 0.5) * 2, y: (pointer.y / window.innerHeight - 0.5) * 2 }
})
</script>

<template>
  <div class="pointer-events-none absolute inset-0">
    <div
      v-for="(item, i) in items"
      :key="i"
      class="absolute -translate-x-1/2 -translate-y-1/2 transition-transform duration-300 ease-out"
      :style="{
        left: `${item.x}%`,
        top: `${item.y}%`,
        transform: `translate(calc(-50% + ${-parallax.x * item.depth}px), calc(-50% + ${-parallax.y * item.depth}px))`,
      }"
    >
      <div
        class="animate-float drop-shadow-[0_10px_14px_rgba(0,0,0,0.10)]"
        :style="{ animationDelay: `${item.delay}s`, animationDuration: `${5 + (i % 3)}s`, '--r0': `${item.r0}deg`, '--r1': `${item.r1}deg` }"
      >
        <FunObject :kind="item.kind" :size="item.size" />
      </div>
    </div>
  </div>
</template>
