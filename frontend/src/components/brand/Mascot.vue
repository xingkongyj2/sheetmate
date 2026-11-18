<script setup lang="ts">
/**
 * 墨墨家族 IP 形象（原创）：圆团子身体 + 会眨眼、会跟着鼠标转的大眼睛 + 小配饰。
 * mood: idle 发呆 / thinking 思考（眼睛往上看 + 冒泡）/ happy 开心（眯眼笑）/ working 干活（上下晃）/ sleep 闭眼（弯弯的睡眼）
 */
import { computed, onMounted, ref, useId } from 'vue'

import { usePointer } from '@/composables/usePointer'

import { mascotStyle } from './mascots'

const props = withDefaults(
  defineProps<{
    variant?: string | null
    size?: number
    mood?: 'idle' | 'thinking' | 'happy' | 'working' | 'sleep'
    track?: boolean
    shadow?: boolean
  }>(),
  { variant: 'blob', size: 96, mood: 'idle', track: true, shadow: true },
)

const BODY = 'M60 22 C84 21 101 40 102 64 C103 90 88 107 60 107 C32 107 17 90 18 64 C19 40 36 23 60 22 Z'

const root = ref<SVGSVGElement | null>(null)
const pointer = usePointer()
const style = computed(() => mascotStyle(props.variant))
const acc = computed(() => style.value.accessory)
const uid = useId()
const clipId = `mascot-body-${uid}`
const shadeId = `mascot-shade-${uid}`
const blinkDelay = `${(Math.random() * 3).toFixed(2)}s`
const mounted = ref(false)
onMounted(() => (mounted.value = true))

/** 瞳孔偏移：朝鼠标方向最多移动 4 个单位 */
const look = computed(() => {
  if (props.mood === 'thinking') return { x: 3, y: -4 }
  if (!props.track || !pointer.active || !mounted.value || !root.value) return { x: 0, y: 0 }
  const rect = root.value.getBoundingClientRect()
  const cx = rect.left + rect.width / 2
  const cy = rect.top + rect.height * 0.52
  const dx = pointer.x - cx
  const dy = pointer.y - cy
  const dist = Math.hypot(dx, dy) || 1
  const reach = Math.min(1, dist / 260)
  return { x: (dx / dist) * 4 * reach, y: (dy / dist) * 4 * reach }
})

/** 深色身体上用白色线条，彩色身体用同色相的深色 */
const lineColor = computed(() => (style.value.dark ? 'rgba(255,255,255,.8)' : style.value.line))
</script>

<template>
  <svg
    ref="root"
    :width="size"
    :height="size"
    viewBox="0 0 120 120"
    class="overflow-visible"
    :class="{ 'animate-bob': mood === 'working' }"
    aria-hidden="true"
  >
    <defs>
      <clipPath :id="clipId"><path :d="BODY" /></clipPath>
      <linearGradient :id="shadeId" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0.5" :stop-color="style.shade" stop-opacity="0" />
        <stop offset="1" :stop-color="style.shade" stop-opacity="0.85" />
      </linearGradient>
    </defs>
    <ellipse v-if="shadow" cx="60" cy="113" rx="30" ry="4" fill="#000" opacity="0.08" />

    <!-- 身体后面的配饰 -->
    <g v-if="acc === 'cat'" stroke-linejoin="round">
      <path d="M32 38 L25 14 L52 25 Z" :fill="style.body" :stroke="style.body" stroke-width="7" />
      <path d="M88 38 L95 14 L68 25 Z" :fill="style.body" :stroke="style.body" stroke-width="7" />
      <path d="M34 31 L30.5 20.5 L43 25.5 Z" :fill="style.accent" :stroke="style.accent" stroke-width="3.5" />
      <path d="M86 31 L89.5 20.5 L77 25.5 Z" :fill="style.accent" :stroke="style.accent" stroke-width="3.5" />
    </g>

    <!-- 小手 -->
    <ellipse cx="19" cy="77" rx="6" ry="9" :fill="style.body" transform="rotate(-24 19 77)" />
    <ellipse cx="101" cy="77" rx="6" ry="9" :fill="style.body" transform="rotate(24 101 77)" />

    <!-- 身体 -->
    <path :d="BODY" :fill="style.body" />
    <path :d="BODY" :fill="`url(#${shadeId})`" />

    <!-- 配饰 -->
    <g v-if="acc === 'sprout'">
      <path d="M60 23 C60 17 60.5 12 62 7" stroke="#159a5d" stroke-width="2.6" fill="none" stroke-linecap="round" />
      <path d="M62 9 C66 1 77 2 77 8 C72 13 65 13 62 9 Z" :fill="style.accent" />
      <path d="M61 12 C57 6 49 7 49 11 C53 15 58 15 61 12 Z" fill="#5fd39a" />
    </g>
    <g v-else-if="acc === 'glasses'" fill="none" :stroke="style.accent" stroke-width="2.6" stroke-linecap="round">
      <ellipse cx="47" cy="62" rx="13.4" ry="15.4" />
      <ellipse cx="73" cy="62" rx="13.4" ry="15.4" />
      <path d="M57.5 52 Q60 49.5 62.5 52" />
      <path d="M33.6 58 L24 55" />
      <path d="M86.4 58 L96 55" />
    </g>
    <g v-else-if="acc === 'headphones'">
      <path d="M21 62 C21 5 99 5 99 62" fill="none" :stroke="style.accent" stroke-width="4.6" stroke-linecap="round" />
      <rect x="12.5" y="52" width="14" height="23" rx="7" :fill="style.accent" />
      <rect x="93.5" y="52" width="14" height="23" rx="7" :fill="style.accent" />
    </g>
    <g v-else-if="acc === 'ninja'">
      <path d="M93 36 C101 30 108 31 116 23 C116 33 108 40 96 41 Z" :fill="style.accent" />
      <path d="M95 40 C103 44 108 50 114 55 C105 57 99 51 93 44 Z" :fill="style.accent" />
      <path d="M95 40 C103 44 108 50 114 55 C105 57 99 51 93 44 Z" fill="#000" opacity="0.18" />
      <path :clip-path="`url(#${clipId})`" d="M10 29 Q60 40 110 29 L110 40 Q60 51 10 40 Z" :fill="style.accent" />
      <circle cx="93" cy="38" r="4.4" :fill="style.accent" />
      <circle cx="93" cy="38" r="4.4" fill="#000" opacity="0.18" />
    </g>
    <g v-else-if="acc === 'party'" transform="rotate(12 60 26)">
      <path d="M46 27 L60 5 L74 27 Q60 30.5 46 27 Z" :fill="style.accent" :stroke="style.accent" stroke-width="2" stroke-linejoin="round" />
      <circle cx="55.5" cy="21" r="1.8" fill="#ffffff" opacity="0.9" />
      <circle cx="64.5" cy="23" r="1.8" fill="#ffffff" opacity="0.9" />
      <circle cx="60.5" cy="14" r="1.8" fill="#ffffff" opacity="0.9" />
      <circle cx="60" cy="4.2" r="3.8" fill="#ffc94a" />
    </g>
    <g v-else-if="acc === 'halo'">
      <ellipse cx="60" cy="11" rx="17" ry="4.6" fill="none" :stroke="style.accent" stroke-width="3.2">
        <animateTransform attributeName="transform" type="translate" values="0 0;0 -2;0 0" dur="2.8s" repeatCount="indefinite" />
      </ellipse>
    </g>
    <g v-else-if="acc === 'flower'" transform="translate(76 35) rotate(12)">
      <ellipse v-for="i in 6" :key="i" cy="-5.2" rx="3.3" ry="5" fill="#ffffff" :transform="`rotate(${i * 60})`" />
      <circle r="3.6" :fill="style.accent" />
    </g>
    <g v-else-if="acc === 'bowtie'" stroke-linejoin="round">
      <path d="M60 97 L47 90 Q44.5 97 47 104 Z" :fill="style.accent" :stroke="style.accent" stroke-width="2.4" />
      <path d="M60 97 L73 90 Q75.5 97 73 104 Z" :fill="style.accent" :stroke="style.accent" stroke-width="2.4" />
      <rect x="56.4" y="93" width="7.2" height="8" rx="2.4" :fill="style.accent" />
      <rect x="56.4" y="93" width="7.2" height="8" rx="2.4" fill="#000" opacity="0.2" />
    </g>
    <g v-else-if="acc === 'antenna'">
      <path d="M60 23 L60 11" :stroke="style.line" stroke-width="2.4" stroke-linecap="round" />
      <circle cx="60" cy="8.5" r="4.8" :fill="style.accent">
        <animate attributeName="r" values="4.3;5.4;4.3" dur="1.6s" repeatCount="indefinite" />
      </circle>
    </g>
    <g v-else-if="acc === 'calyx'" :fill="style.accent">
      <path d="M60 25 C54 20 46 20 38 25 C46 29.5 54 29.5 60 27 Z" />
      <path d="M60 25 C66 20 74 20 82 25 C74 29.5 66 29.5 60 27 Z" />
      <path d="M59 26 C56 30 51 34 45 37 C50 31 54 27 58 25 Z" />
      <path d="M61 26 C64 30 69 34 75 37 C70 31 66 27 62 25 Z" />
      <path d="M60 25 C59 21 59 17 61 13" fill="none" :stroke="style.accent" stroke-width="3" stroke-linecap="round" />
    </g>
    <g v-else-if="acc === 'bow'" transform="rotate(-14 76 25)">
      <path d="M76 25 C70 16 61 17 61.5 24 C62 31 70 31 76 25 Z" :fill="style.accent" />
      <path d="M76 25 C82 16 91 17 90.5 24 C90 31 82 31 76 25 Z" :fill="style.accent" />
      <ellipse cx="76" cy="25" rx="3.4" ry="3.9" :fill="style.accent" />
      <ellipse cx="76" cy="25" rx="3.4" ry="3.9" fill="#000" opacity="0.15" />
    </g>
    <g v-else-if="acc === 'crown'">
      <path d="M46 29 L44 15 L52.5 21 L60 11 L67.5 21 L76 15 L74 29 Z" :fill="style.accent" :stroke="style.accent" stroke-width="2.4" stroke-linejoin="round" />
      <circle cx="60" cy="23.5" r="2.3" fill="#ffffff" opacity="0.85" />
    </g>
    <g v-else-if="acc === 'scarf'">
      <g :clip-path="`url(#${clipId})`">
        <path d="M10 80 Q60 97 110 80 L110 92 Q60 110 10 92 Z" :fill="style.accent" />
        <path d="M10 87 Q60 104.5 110 87 L110 92 Q60 110 10 92 Z" fill="#000" opacity="0.12" />
      </g>
      <path d="M70.5 96 C72 99.5 71.8 103.5 70 108 L79.5 108.5 C80.2 104 80 99.5 78.5 94 Z" :fill="style.accent" />
      <path d="M70.5 96 C72 99.5 71.8 103.5 70 108 L79.5 108.5 C80.2 104 80 99.5 78.5 94 Z" fill="#000" opacity="0.08" />
    </g>
    <g v-else-if="acc === 'beret'">
      <path d="M31 31 C31 20 50 13 68 14 C84 15 93 22 91 29 C88 33 72 33 58 33 C46 33 34 34 31 31 Z" :fill="style.accent" />
      <path d="M63 14.5 L64.5 9" :stroke="style.accent" stroke-width="3" stroke-linecap="round" />
    </g>
    <g v-else-if="acc === 'beanie'">
      <circle cx="60" cy="9" r="6.5" :fill="style.accent" />
      <circle cx="60" cy="9" r="6.5" fill="#ffffff" opacity="0.28" />
      <path d="M26 42 C26 4 94 4 94 42 Z" :fill="style.accent" />
      <rect x="23.5" y="35" width="73" height="11" rx="5.5" :fill="style.accent" />
      <path d="M33 38v5 M40 38v5 M47 38v5 M54 38v5 M61 38v5 M68 38v5 M75 38v5 M82 38v5 M89 38v5" stroke="#000" stroke-opacity="0.16" stroke-width="2" stroke-linecap="round" />
    </g>

    <!-- 腮红 -->
    <g v-if="style.blush" :fill="style.blush" opacity="0.55">
      <ellipse cx="34" cy="79" rx="6" ry="3.5" />
      <ellipse cx="86" cy="79" rx="6" ry="3.5" />
    </g>

    <!-- 眼睛 -->
    <g v-if="mood === 'happy'" fill="none" :stroke="style.dark ? '#ffffff' : style.line" stroke-width="4.5" stroke-linecap="round">
      <path d="M38 64 Q47 54 56 64" />
      <path d="M64 64 Q73 54 82 64" />
    </g>
    <!-- 闭眼：两道向下弯的弧线（和开心的向上弯相反） -->
    <g v-else-if="mood === 'sleep'" fill="none" :stroke="style.dark ? '#ffffff' : style.line" stroke-width="5" stroke-linecap="round">
      <path d="M38 62 Q47 71 56 62" />
      <path d="M64 62 Q73 71 82 62" />
    </g>
    <g v-else class="animate-blink" :style="{ transformOrigin: '60px 62px', transformBox: 'view-box', animationDelay: blinkDelay }">
      <ellipse v-if="!style.dark" cx="47" cy="63.4" rx="10.5" ry="13" :fill="style.shade" />
      <ellipse v-if="!style.dark" cx="73" cy="63.4" rx="10.5" ry="13" :fill="style.shade" />
      <ellipse cx="47" cy="62" rx="10.5" ry="13" fill="#ffffff" />
      <ellipse cx="73" cy="62" rx="10.5" ry="13" fill="#ffffff" />
      <g :style="{ transform: `translate(${look.x}px, ${look.y}px)`, transition: 'transform .18s ease-out' }">
        <circle cx="48" cy="63" r="5" fill="#111113" />
        <circle cx="74" cy="63" r="5" fill="#111113" />
        <circle cx="46.4" cy="61.2" r="1.5" fill="#ffffff" />
        <circle cx="72.4" cy="61.2" r="1.5" fill="#ffffff" />
      </g>
    </g>

    <!-- 嘴巴 -->
    <path v-if="mood === 'happy' || mood === 'working'" d="M54 81 Q60 87 66 81" :stroke="lineColor" stroke-width="3" fill="none" stroke-linecap="round" />

    <!-- 思考气泡 -->
    <g v-if="mood === 'thinking'">
      <circle cx="98" cy="30" r="3" fill="#d4d4d8"><animate attributeName="opacity" values=".3;1;.3" dur="1.2s" repeatCount="indefinite" /></circle>
      <circle cx="106" cy="20" r="4" fill="#d4d4d8"><animate attributeName="opacity" values=".3;1;.3" dur="1.2s" begin=".2s" repeatCount="indefinite" /></circle>
      <circle cx="116" cy="8" r="5" fill="#d4d4d8"><animate attributeName="opacity" values=".3;1;.3" dur="1.2s" begin=".4s" repeatCount="indefinite" /></circle>
    </g>
  </svg>
</template>
