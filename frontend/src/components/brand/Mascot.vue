<script setup lang="ts">
/**
 * 墨墨家族 IP 形象（原创）：圆团子身体 + 90 款独立搞怪表情（预设表情 + 可组合眼/嘴/趣味配饰）+ 标志绿豆芽 / 波点尖帽。
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
const face = computed(() => style.value.face ?? 'normal')
const custom = computed(() => style.value.customFace ?? null)
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

const lineColor = computed(() => (style.value.dark ? '#ffffff' : style.value.line))
const mouthColor = computed(() => (style.value.dark ? 'rgba(255,255,255,.92)' : style.value.line))
const pupilColor = computed(() => style.value.pupil ?? '#111113')
const eyeOutline = computed(() => style.value.stroke ?? null)
const goldAccent = computed(() => (style.value.dark ? '#ffc94a' : '#e09600'))
const glassFrame = computed(() => (style.value.dark ? '#ffffff' : style.value.line))
const partyGlassFrame = computed(() => (style.value.dark ? '#7ea8ff' : style.value.line))
const noteColor = computed(() => (style.value.dark ? '#6ea0ff' : '#4c7cf0'))
const tearColor = computed(() => (style.value.dark ? '#7ec8ff' : '#4fa8f5'))

const moodFaceOverride = computed<'happy' | 'sleep' | null>(() => {
  if (props.mood === 'sleep') return 'sleep'
  if (props.mood === 'happy' && !custom.value && face.value === 'normal') return 'happy'
  return null
})
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

    <!-- 身体后面的配饰（历史猫耳兼容） -->
    <g v-if="acc === 'cat'" stroke-linejoin="round">
      <path d="M32 38 L25 14 L52 25 Z" :fill="style.body" :stroke="style.body" stroke-width="7" />
      <path d="M88 38 L95 14 L68 25 Z" :fill="style.body" :stroke="style.body" stroke-width="7" />
      <path d="M34 31 L30.5 20.5 L43 25.5 Z" :fill="style.accent" :stroke="style.accent" stroke-width="3.5" />
      <path d="M86 31 L89.5 20.5 L77 25.5 Z" :fill="style.accent" :stroke="style.accent" stroke-width="3.5" />
    </g>

    <!-- 小手 -->
    <ellipse
      cx="19" cy="77" rx="6" ry="9" :fill="style.body"
      :stroke="style.stroke || 'none'" :stroke-width="style.stroke ? 2.8 : 0"
      transform="rotate(-24 19 77)"
    />
    <ellipse
      cx="101" cy="77" rx="6" ry="9" :fill="style.body"
      :stroke="style.stroke || 'none'" :stroke-width="style.stroke ? 2.8 : 0"
      transform="rotate(24 101 77)"
    />

    <!-- 身体 -->
    <path :d="BODY" :fill="style.body" />
    <path :d="BODY" :fill="`url(#${shadeId})`" />
    <path v-if="style.stroke" :d="BODY" fill="none" :stroke="style.stroke" stroke-width="3.2" />

    <!-- ==================== 配饰 ==================== -->
    <g v-if="acc === 'sprout'">
      <path d="M60 23 C60 17 60.5 12 62 7" stroke="#159a5d" stroke-width="2.6" fill="none" stroke-linecap="round" />
      <path d="M62 9 C66 1 77 2 77 8 C72 13 65 13 62 9 Z" :fill="style.accent" />
      <path d="M61 12 C57 6 49 7 49 11 C53 15 58 15 61 12 Z" fill="#5fd39a" />
    </g>
    <g v-else-if="acc === 'sprout-tilt'" transform="rotate(14 60 23)">
      <path d="M60 23 C60 17 60.5 12 62 7" stroke="#159a5d" stroke-width="2.6" fill="none" stroke-linecap="round" />
      <path d="M62 9 C66 1 77 2 77 8 C72 13 65 13 62 9 Z" :fill="style.accent" />
      <path d="M61 12 C57 6 49 7 49 11 C53 15 58 15 61 12 Z" fill="#5fd39a" />
    </g>
    <g v-else-if="acc === 'sprout-droop'">
      <path d="M60 23 C61 16 66 13 71 15" stroke="#159a5d" stroke-width="2.6" fill="none" stroke-linecap="round" />
      <path d="M70 14.5 C75 11 81 15 79 20 C74 21 70 18 70 14.5 Z" :fill="style.accent" />
      <path d="M63 15 C59 10 52 12 53 16 C56 18.5 61 18 63 15 Z" fill="#5fd39a" />
    </g>
    <g v-else-if="acc === 'sprout-bandaid'">
      <g transform="rotate(14 60 23)">
        <path d="M60 23 C60 17 60.5 12 62 7" stroke="#159a5d" stroke-width="2.6" fill="none" stroke-linecap="round" />
        <path d="M62 9 C66 1 77 2 77 8 C72 13 65 13 62 9 Z" :fill="style.accent" />
        <path d="M61 12 C57 6 49 7 49 11 C53 15 58 15 61 12 Z" fill="#5fd39a" />
      </g>
      <g transform="rotate(-18 42 40)">
        <rect x="32" y="34" width="22" height="10" rx="4.5" fill="#f3c691" stroke="#c99357" stroke-width="1" />
        <rect x="39" y="34" width="8" height="10" fill="#fbe0bd" />
        <circle cx="35.5" cy="39" r="1" fill="#c99357" />
        <circle cx="50.5" cy="39" r="1" fill="#c99357" />
      </g>
    </g>
    <g v-else-if="acc === 'party'" transform="rotate(11 60 26)">
      <path d="M45 27 L60 4 L75 27 Q60 31 45 27 Z" :fill="style.accent" :stroke="style.accent" stroke-width="2" stroke-linejoin="round" />
      <circle cx="55.5" cy="21" r="2" fill="#ffffff" opacity="0.92" />
      <circle cx="64.5" cy="23" r="2" fill="#ffffff" opacity="0.92" />
      <circle cx="60.5" cy="13.5" r="2" fill="#ffffff" opacity="0.92" />
      <circle cx="60" cy="3.8" r="4.1" fill="#ffc94a" />
    </g>
    <g v-else-if="acc === 'party-tilt'" transform="rotate(-14 60 26)">
      <path d="M45 27 L60 4 L75 27 Q60 31 45 27 Z" :fill="style.accent" :stroke="style.accent" stroke-width="2" stroke-linejoin="round" />
      <circle cx="55.5" cy="21" r="2" fill="#ffffff" opacity="0.92" />
      <circle cx="64.5" cy="23" r="2" fill="#ffffff" opacity="0.92" />
      <circle cx="60.5" cy="13.5" r="2" fill="#ffffff" opacity="0.92" />
      <circle cx="60" cy="3.8" r="4.1" fill="#ffc94a" />
    </g>
    <!-- 历史配饰兼容 -->
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
    <g v-if="style.blush" :fill="style.blush" opacity="0.56">
      <ellipse cx="33" cy="78" rx="6" ry="3.4" />
      <ellipse cx="87" cy="78" rx="6" ry="3.4" />
    </g>

    <!-- ==================== 表情：mood 覆盖 ==================== -->
    <template v-if="moodFaceOverride === 'happy'">
      <g fill="none" :stroke="lineColor" stroke-width="4.5" stroke-linecap="round">
        <path d="M38 64 Q47 54 56 64" />
        <path d="M64 64 Q73 54 82 64" />
      </g>
      <path d="M54 81 Q60 87 66 81" :stroke="mouthColor" stroke-width="3" fill="none" stroke-linecap="round" />
    </template>
    <template v-else-if="moodFaceOverride === 'sleep'">
      <g fill="none" :stroke="lineColor" stroke-width="5" stroke-linecap="round">
        <path d="M38 62 Q47 71 56 62" />
        <path d="M64 62 Q73 71 82 62" />
      </g>
    </template>

    <!-- ==================== 组合表情引擎（8 套彩色系 72 款独立表情） ==================== -->
    <g v-else-if="custom">
      <!-- 眼睛层 -->
      <g v-if="custom.eyes === 'classic'">
        <ellipse cx="47" cy="61" rx="10" ry="12.2" fill="#ffffff" :stroke="eyeOutline || 'none'" :stroke-width="eyeOutline ? 2.2 : 0" />
        <ellipse cx="73" cy="61" rx="10" ry="12.2" fill="#ffffff" :stroke="eyeOutline || 'none'" :stroke-width="eyeOutline ? 2.2 : 0" />
        <circle cx="48" cy="62" r="4.8" :fill="pupilColor" />
        <circle cx="74" cy="62" r="4.8" :fill="pupilColor" />
        <circle cx="46.4" cy="60.2" r="1.5" fill="#ffffff" />
        <circle cx="72.4" cy="60.2" r="1.5" fill="#ffffff" />
      </g>
      <g v-else-if="custom.eyes === 'wide-up'">
        <ellipse cx="46" cy="61" rx="10.4" ry="12.6" fill="#ffffff" :stroke="eyeOutline || 'none'" :stroke-width="eyeOutline ? 2.2 : 0" />
        <ellipse cx="74" cy="61" rx="10.4" ry="12.6" fill="#ffffff" :stroke="eyeOutline || 'none'" :stroke-width="eyeOutline ? 2.2 : 0" />
        <circle cx="48.5" cy="56" r="4.5" :fill="pupilColor" />
        <circle cx="76.5" cy="56" r="4.5" :fill="pupilColor" />
        <circle cx="47.2" cy="54.5" r="1.4" fill="#ffffff" />
        <circle cx="75.2" cy="54.5" r="1.4" fill="#ffffff" />
      </g>
      <g v-else-if="custom.eyes === 'side-eye'">
        <ellipse cx="46" cy="62" rx="10.2" ry="12" fill="#ffffff" :stroke="eyeOutline || 'none'" :stroke-width="eyeOutline ? 2.2 : 0" />
        <ellipse cx="74" cy="62" rx="10.2" ry="12" fill="#ffffff" :stroke="eyeOutline || 'none'" :stroke-width="eyeOutline ? 2.2 : 0" />
        <circle cx="51.5" cy="63" r="4.6" :fill="pupilColor" />
        <circle cx="79.5" cy="63" r="4.6" :fill="pupilColor" />
        <circle cx="50.3" cy="61.2" r="1.4" fill="#ffffff" />
        <circle cx="78.3" cy="61.2" r="1.4" fill="#ffffff" />
      </g>
      <g v-else-if="custom.eyes === 'cross-eyed'">
        <ellipse cx="45.5" cy="62" rx="10.6" ry="12.4" fill="#ffffff" :stroke="eyeOutline || 'none'" :stroke-width="eyeOutline ? 2.2 : 0" />
        <ellipse cx="74.5" cy="62" rx="10.6" ry="12.4" fill="#ffffff" :stroke="eyeOutline || 'none'" :stroke-width="eyeOutline ? 2.2 : 0" />
        <circle cx="51" cy="63.5" r="4.8" :fill="pupilColor" />
        <circle cx="69" cy="63.5" r="4.8" :fill="pupilColor" />
        <circle cx="49.8" cy="61.8" r="1.4" fill="#ffffff" />
        <circle cx="67.8" cy="61.8" r="1.4" fill="#ffffff" />
      </g>
      <g v-else-if="custom.eyes === 'dot'">
        <circle cx="45.5" cy="62" r="5.4" :fill="lineColor" />
        <circle cx="74.5" cy="62" r="5.4" :fill="lineColor" />
        <circle cx="43.8" cy="60.2" r="1.7" fill="#ffffff" />
        <circle cx="72.8" cy="60.2" r="1.7" fill="#ffffff" />
      </g>
      <g v-else-if="custom.eyes === 'dot-wide'">
        <ellipse cx="46" cy="62" rx="10.8" ry="13" fill="#ffffff" :stroke="eyeOutline || 'none'" :stroke-width="eyeOutline ? 2.2 : 0" />
        <ellipse cx="74" cy="62" rx="10.8" ry="13" fill="#ffffff" :stroke="eyeOutline || 'none'" :stroke-width="eyeOutline ? 2.2 : 0" />
        <circle cx="46" cy="62" r="2.9" :fill="pupilColor" />
        <circle cx="74" cy="62" r="2.9" :fill="pupilColor" />
      </g>
      <g v-else-if="custom.eyes === 'arc-up'" fill="none" :stroke="lineColor" stroke-width="5" stroke-linecap="round">
        <path d="M36 64 Q46 52 56 64" />
        <path d="M64 64 Q74 52 84 64" />
      </g>
      <g v-else-if="custom.eyes === 'arc-down'" fill="none" :stroke="lineColor" stroke-width="4.8" stroke-linecap="round">
        <path d="M37 61 Q46 70 55 61" />
        <path d="M65 61 Q74 70 83 61" />
      </g>
      <g v-else-if="custom.eyes === 'wink-left'">
        <path d="M36 62 Q46 53 55 62" fill="none" :stroke="lineColor" stroke-width="4.8" stroke-linecap="round" />
        <ellipse cx="74" cy="62" rx="10" ry="12.2" fill="#ffffff" :stroke="eyeOutline || 'none'" :stroke-width="eyeOutline ? 2.2 : 0" />
        <circle cx="72.5" cy="62" r="4.8" :fill="pupilColor" />
        <circle cx="71.2" cy="60.2" r="1.5" fill="#ffffff" />
      </g>
      <g v-else-if="custom.eyes === 'wink-right'">
        <ellipse cx="46" cy="62" rx="10" ry="12.2" fill="#ffffff" :stroke="eyeOutline || 'none'" :stroke-width="eyeOutline ? 2.2 : 0" />
        <circle cx="47.5" cy="62" r="4.8" :fill="pupilColor" />
        <circle cx="46.2" cy="60.2" r="1.5" fill="#ffffff" />
        <path d="M65 62 Q74 53 84 62" fill="none" :stroke="lineColor" stroke-width="4.8" stroke-linecap="round" />
      </g>
      <g v-else-if="custom.eyes === 'flat'">
        <ellipse cx="47" cy="63" rx="10" ry="11.4" fill="#ffffff" :stroke="eyeOutline || 'none'" :stroke-width="eyeOutline ? 2.2 : 0" />
        <ellipse cx="73" cy="63" rx="10" ry="11.4" fill="#ffffff" :stroke="eyeOutline || 'none'" :stroke-width="eyeOutline ? 2.2 : 0" />
        <circle cx="49" cy="65" r="4.4" :fill="pupilColor" />
        <circle cx="75" cy="65" r="4.4" :fill="pupilColor" />
        <rect x="34" y="48" width="26" height="13" :fill="style.body" />
        <rect x="60" y="48" width="26" height="13" :fill="style.body" />
        <path d="M36 61 H58 M62 61 H84" :stroke="lineColor" stroke-width="2.8" stroke-linecap="round" />
      </g>
      <g v-else-if="custom.eyes === 'squint'" fill="none" :stroke="lineColor" stroke-width="4.8" stroke-linecap="round" stroke-linejoin="round">
        <path d="M36 53 L52 62 L36 71" />
        <path d="M84 53 L68 62 L84 71" />
      </g>
      <g v-else-if="custom.eyes === 'star'">
        <path d="M46 49 L49 58 L58 61 L49 64 L46 73 L43 64 L34 61 L43 58 Z" :fill="goldAccent" :stroke="style.dark ? 'none' : lineColor" :stroke-width="style.dark ? 0 : 1.4" stroke-linejoin="round" />
        <path d="M74 49 L77 58 L86 61 L77 64 L74 73 L71 64 L62 61 L71 58 Z" :fill="goldAccent" :stroke="style.dark ? 'none' : lineColor" :stroke-width="style.dark ? 0 : 1.4" stroke-linejoin="round" />
        <circle cx="46" cy="61" r="2.2" fill="#ffffff" />
        <circle cx="74" cy="61" r="2.2" fill="#ffffff" />
      </g>
      <g v-else-if="custom.eyes === 'heart'">
        <path d="M46 72 C46 72 34 64 34 56.5 C34 52.2 37.4 49.5 41 49.5 C43.4 49.5 45.2 50.8 46 52.6 C46.8 50.8 48.6 49.5 51 49.5 C54.6 49.5 58 52.2 58 56.5 C58 64 46 72 46 72 Z" fill="#f54d7a" />
        <path d="M74 72 C74 72 62 64 62 56.5 C62 52.2 65.4 49.5 69 49.5 C71.4 49.5 73.2 50.8 74 52.6 C74.8 50.8 76.6 49.5 79 49.5 C82.6 49.5 86 52.2 86 56.5 C86 64 74 72 74 72 Z" fill="#f54d7a" />
        <circle cx="40.5" cy="55.5" r="2.2" fill="#ffffff" opacity="0.88" />
        <circle cx="68.5" cy="55.5" r="2.2" fill="#ffffff" opacity="0.88" />
      </g>
      <g v-else-if="custom.eyes === 'x-eyes'" fill="none" :stroke="lineColor" stroke-width="4.6" stroke-linecap="round">
        <path d="M38 54 L53 69 M53 54 L38 69" />
        <path d="M67 54 L82 69 M82 54 L67 69" />
      </g>
      <g v-else-if="custom.eyes === 'sparkle-big'">
        <ellipse cx="46" cy="62" rx="11.2" ry="13.2" fill="#ffffff" :stroke="eyeOutline || 'none'" :stroke-width="eyeOutline ? 2.2 : 0" />
        <ellipse cx="74" cy="62" rx="11.2" ry="13.2" fill="#ffffff" :stroke="eyeOutline || 'none'" :stroke-width="eyeOutline ? 2.2 : 0" />
        <circle cx="46.5" cy="62.5" r="7.2" :fill="pupilColor" />
        <circle cx="73.5" cy="62.5" r="7.2" :fill="pupilColor" />
        <circle cx="43.8" cy="59.2" r="3" fill="#ffffff" />
        <circle cx="70.8" cy="59.2" r="3" fill="#ffffff" />
        <circle cx="49" cy="66.2" r="1.6" fill="#ffffff" opacity="0.88" />
        <circle cx="76" cy="66.2" r="1.6" fill="#ffffff" opacity="0.88" />
      </g>
      <g v-else-if="custom.eyes === 'monocle'">
        <ellipse cx="46" cy="62" rx="9.5" ry="11.6" fill="#ffffff" :stroke="eyeOutline || 'none'" :stroke-width="eyeOutline ? 2.2 : 0" />
        <circle cx="47" cy="62.5" r="4.5" :fill="pupilColor" />
        <circle cx="45.6" cy="60.8" r="1.4" fill="#ffffff" />
        <path d="M36 47 Q46 43 55 48" fill="none" :stroke="lineColor" stroke-width="2.6" stroke-linecap="round" />
        <circle cx="74" cy="62" r="12.8" fill="#ffffff" fill-opacity="0.24" :stroke="goldAccent" stroke-width="3.2" />
        <circle cx="74" cy="62" r="5" :fill="pupilColor" />
        <circle cx="72.4" cy="60" r="1.6" fill="#ffffff" />
        <path d="M86.5 64 Q93 74 88 86" fill="none" :stroke="goldAccent" stroke-width="2" stroke-linecap="round" />
      </g>
      <g v-else-if="custom.eyes === 'round-glasses'">
        <circle cx="45.5" cy="61.5" r="12.5" fill="#ffffff" :stroke="lineColor" stroke-width="3" />
        <circle cx="74.5" cy="61.5" r="12.5" fill="#ffffff" :stroke="lineColor" stroke-width="3" />
        <path d="M58 61.5 H62 M26 58 L33 60 M94 58 L87 60" :stroke="lineColor" stroke-width="2.8" stroke-linecap="round" />
        <circle cx="46.5" cy="62" r="4.5" :fill="pupilColor" />
        <circle cx="75.5" cy="62" r="4.5" :fill="pupilColor" />
        <circle cx="45" cy="60.3" r="1.4" fill="#ffffff" />
        <circle cx="74" cy="60.3" r="1.4" fill="#ffffff" />
      </g>
      <g v-else-if="custom.eyes === 'pixel-shades'">
        <path d="M23 53 H97 V61 H91 V66 H85 V70 H66 V66 H63 V61 H57 V66 H54 V70 H35 V66 H29 V61 H23 Z" fill="#18181f" :stroke="style.dark ? '#ffffff' : 'none'" :stroke-width="style.dark ? 1.8 : 0" />
        <rect x="31" y="57" width="6" height="4" fill="#ffffff" opacity="0.88" />
        <rect x="37" y="61" width="6" height="4" fill="#ffffff" opacity="0.65" />
        <rect x="69" y="57" width="6" height="4" fill="#ffffff" opacity="0.88" />
        <rect x="75" y="61" width="6" height="4" fill="#ffffff" opacity="0.65" />
      </g>
      <g v-else-if="custom.eyes === 'sleep-half'">
        <ellipse cx="46.5" cy="64" rx="10" ry="11" fill="#ffffff" :stroke="eyeOutline || 'none'" :stroke-width="eyeOutline ? 2.2 : 0" />
        <ellipse cx="73.5" cy="64" rx="10" ry="11" fill="#ffffff" :stroke="eyeOutline || 'none'" :stroke-width="eyeOutline ? 2.2 : 0" />
        <circle cx="46.5" cy="67" r="4.2" :fill="pupilColor" />
        <circle cx="73.5" cy="67" r="4.2" :fill="pupilColor" />
        <path d="M34 50 H59 V62 Q46.5 67 34 62 Z" :fill="style.body" />
        <path d="M61 50 H86 V62 Q73.5 67 61 62 Z" :fill="style.body" />
        <path d="M36 62 Q46.5 67 57 62 M63 62 Q73.5 67 84 62" fill="none" :stroke="lineColor" stroke-width="2.8" stroke-linecap="round" />
      </g>
      <g v-else-if="custom.eyes === 'angry-eyes'">
        <ellipse cx="46.5" cy="64" rx="10" ry="11.2" fill="#ffffff" :stroke="eyeOutline || 'none'" :stroke-width="eyeOutline ? 2.2 : 0" />
        <ellipse cx="73.5" cy="64" rx="10" ry="11.2" fill="#ffffff" :stroke="eyeOutline || 'none'" :stroke-width="eyeOutline ? 2.2 : 0" />
        <circle cx="48.5" cy="65.5" r="4.4" :fill="pupilColor" />
        <circle cx="71.5" cy="65.5" r="4.4" :fill="pupilColor" />
        <path d="M34 48 L60 58 L60 50 L34 45 Z" :fill="style.body" />
        <path d="M86 48 L60 58 L60 50 L86 45 Z" :fill="style.body" />
        <path d="M36 52 L57 60 M84 52 L63 60" :stroke="lineColor" stroke-width="3.6" stroke-linecap="round" />
      </g>

      <!-- 嘴巴层 -->
      <path v-if="custom.mouth === 'smile'" d="M52 80 Q60 88 68 80" fill="none" :stroke="mouthColor" stroke-width="3.4" stroke-linecap="round" />
      <g v-else-if="custom.mouth === 'big-grin'">
        <path d="M46 77 Q60 96 74 77 Z" :fill="style.dark ? '#ffffff' : lineColor" />
        <path d="M51.5 83.5 Q60 92.5 68.5 83.5 Q60 80.5 51.5 83.5 Z" fill="#ff7d95" />
      </g>
      <path v-else-if="custom.mouth === 'cat'" d="M49.5 80 Q54.8 87 60 81 Q65.2 87 70.5 80" fill="none" :stroke="mouthColor" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round" />
      <path v-else-if="custom.mouth === 'smirk'" d="M53 82 Q62 86 70 78" fill="none" :stroke="mouthColor" stroke-width="3.4" stroke-linecap="round" />
      <g v-else-if="custom.mouth === 'fang'">
        <path d="M51 79 Q60 87 70 78" fill="none" :stroke="mouthColor" stroke-width="3.4" stroke-linecap="round" />
        <polygon points="64.5,81 68.2,79 67.6,85.2" fill="#ffffff" :stroke="style.dark ? 'none' : lineColor" :stroke-width="style.dark ? 0 : 1.1" />
      </g>
      <g v-else-if="custom.mouth === 'tongue-out'">
        <path d="M51 78 Q60 84 69 78" fill="none" :stroke="mouthColor" stroke-width="3.2" stroke-linecap="round" />
        <path d="M56 80.5 Q60 93 65.5 80.5 Z" fill="#ff7d95" :stroke="style.dark ? 'none' : lineColor" :stroke-width="style.dark ? 0 : 1.3" />
      </g>
      <circle v-else-if="custom.mouth === 'o-small'" cx="60" cy="82" r="4.2" fill="none" :stroke="mouthColor" stroke-width="3.2" />
      <ellipse v-else-if="custom.mouth === 'o-big'" cx="60" cy="83.5" rx="6" ry="7.8" :fill="style.dark ? 'none' : lineColor" :stroke="mouthColor" stroke-width="3.2" />
      <path v-else-if="custom.mouth === 'flat'" d="M52 82 H68" :stroke="mouthColor" stroke-width="3.4" stroke-linecap="round" />
      <path v-else-if="custom.mouth === 'wavy'" d="M49 82 Q54.5 77.5 60 82 T71 82" fill="none" :stroke="mouthColor" stroke-width="3.2" stroke-linecap="round" />
      <path v-else-if="custom.mouth === 'pout'" d="M53 84.5 Q60 78.5 67 84.5" fill="none" :stroke="mouthColor" stroke-width="3.4" stroke-linecap="round" />
      <path v-else-if="custom.mouth === 'kiss'" d="M56 76 Q64 78.5 58 82 Q64 85.5 56 88" fill="none" :stroke="mouthColor" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round" />
      <circle v-else-if="custom.mouth === 'whistle'" cx="65" cy="81.5" r="3.8" fill="none" :stroke="mouthColor" stroke-width="3.2" />
      <g v-else-if="custom.mouth === 'grit'">
        <rect x="46" y="77" width="28" height="10" rx="4.5" fill="#ffffff" :stroke="lineColor" stroke-width="2.2" />
        <path d="M53 77 V87 M60 77 V87 M67 77 V87 M47 82 H73" :stroke="lineColor" stroke-width="1.6" />
      </g>
      <g v-else-if="custom.mouth === 'drool'">
        <path d="M51 79 Q60 88 69 79" fill="none" :stroke="mouthColor" stroke-width="3.4" stroke-linecap="round" />
        <path d="M64.5 83 C63 87 63 93 66 93 C69 93 69 87 67 83 Z" :fill="tearColor" />
        <circle cx="65.2" cy="89" r="1.2" fill="#ffffff" opacity="0.85" />
      </g>
      <path
        v-else-if="custom.mouth === 'mustache'"
        d="M60 79 C55 74 44 76 41 82 C46 86 55 85 60 81 C65 85 74 86 79 82 C76 76 65 74 60 79 Z"
        :fill="lineColor"
      />

      <!-- 趣味附加元素层 -->
      <path v-if="custom.extra === 'sweat'" d="M88 37 C83 44 82 50 88 52 C94 50 93 44 88 37 Z" :fill="tearColor" stroke="#ffffff" stroke-width="1.5" />
      <g v-else-if="custom.extra === 'anger'" stroke="#ff5a6b" stroke-width="3.2" stroke-linecap="round" fill="none">
        <path d="M82 33 Q87 38 82 43 M94 33 Q89 38 94 43 M83 31 Q88 36 93 31 M83 45 Q88 40 93 45" />
      </g>
      <g v-else-if="custom.extra === 'blush-lines'" stroke="#ff6b8b" stroke-width="2.2" stroke-linecap="round" opacity="0.85">
        <path d="M29 76 L32 81 M34 76 L37 81 M83 76 L86 81 M88 76 L91 81" />
      </g>
      <g v-else-if="custom.extra === 'zzz'" fill="none" :stroke="lineColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" opacity="0.82">
        <path d="M88 34 H97 L88 43 H97" />
        <path d="M98 22 H105 L98 29 H105" stroke-width="2.2" />
      </g>
      <g v-else-if="custom.extra === 'bubble'" class="mascot-bubble">
        <circle cx="71" cy="79" r="9.5" fill="#a8d8ff" fill-opacity="0.52" :stroke="style.dark ? '#ffffff' : lineColor" stroke-width="2" />
        <path d="M67 74.5 Q69.5 72.5 72.5 74" fill="none" stroke="#ffffff" stroke-width="1.8" stroke-linecap="round" />
      </g>
      <g v-else-if="custom.extra === 'music'" class="mascot-note" :fill="noteColor" :stroke="noteColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
        <path d="M92 50 V35 L103 32 V45" fill="none" />
        <ellipse cx="89.5" cy="50.5" rx="3.4" ry="2.5" stroke="none" />
        <ellipse cx="100.5" cy="46" rx="3.4" ry="2.5" stroke="none" />
      </g>
      <g v-else-if="custom.extra === 'question'" fill="none" stroke="#ff7a1a" stroke-width="3.6" stroke-linecap="round">
        <path d="M90 31 C90 25 101 25 101 31 C101 35 95 36 95 40" />
        <circle cx="95" cy="46.5" r="2.2" fill="#ff7a1a" stroke="none" />
      </g>
      <g v-else-if="custom.extra === 'exclaim'" fill="#ffc94a" stroke="#d98a00" stroke-width="1.2">
        <rect x="92" y="24" width="5.5" height="17" rx="2.75" transform="rotate(12 95 32)" />
        <circle cx="93" cy="47.5" r="3" />
      </g>
      <path v-else-if="custom.extra === 'heart-float'" d="M95 56 C95 56 86 50 86 44.5 C86 41.5 88.4 39.5 91 39.5 C92.7 39.5 94.2 40.5 95 42 C95.8 40.5 97.3 39.5 99 39.5 C101.6 39.5 104 41.5 104 44.5 C104 50 95 56 95 56 Z" fill="#ff5d87" />
      <g v-else-if="custom.extra === 'sparkle-float'">
        <path d="M94 28 L96 34 L102 36 L96 38 L94 44 L92 38 L86 36 L92 34 Z" :fill="goldAccent" />
        <path d="M26 36 L27.3 40 L31.3 41.3 L27.3 42.6 L26 46.6 L24.7 42.6 L20.7 41.3 L24.7 40 Z" :fill="goldAccent" opacity="0.85" />
      </g>
      <g v-else-if="custom.extra === 'steam'" fill="#ffffff" stroke="#d4d4dc" stroke-width="1.4" opacity="0.95">
        <circle cx="15" cy="52" r="5" />
        <circle cx="9" cy="46" r="6.5" />
        <circle cx="105" cy="52" r="5" />
        <circle cx="111" cy="46" r="6.5" />
      </g>
      <g v-else-if="custom.extra === 'tears'">
        <path d="M35 72 Q31 79 36 81 Q40 79 35 72 Z" :fill="tearColor" />
        <path d="M85 72 Q89 79 84 81 Q80 79 85 72 Z" :fill="tearColor" />
      </g>
      <g v-else-if="custom.extra === 'lollipop'">
        <path d="M67 83 L86 93" stroke="#ffffff" stroke-width="3.2" stroke-linecap="round" />
        <circle cx="88" cy="94" r="9.5" fill="#ff6f91" stroke="#ffffff" stroke-width="2" />
        <path d="M84 91 Q88 88 92 92 Q90 97 85 95" fill="none" stroke="#ffffff" stroke-width="1.8" stroke-linecap="round" />
      </g>
      <g v-else-if="custom.extra === 'leaf-bite'">
        <path d="M67 82 Q78 82 88 76" stroke="#159a5d" stroke-width="2.6" fill="none" stroke-linecap="round" />
        <path d="M85 77 C90 71 99 72 98 78 C93 82 87 81 85 77 Z" fill="#22b573" />
      </g>
    </g>

    <!-- ==================== 预设表情（经典黑前 2 排 18 款） ==================== -->
    <g v-else-if="face === 'laugh'">
      <path d="M36 64 Q46 52 56 64" fill="none" :stroke="lineColor" stroke-width="5.2" stroke-linecap="round" />
      <path d="M64 64 Q74 52 84 64" fill="none" :stroke="lineColor" stroke-width="5.2" stroke-linecap="round" />
      <path d="M51 78 Q60 88 69 78" fill="none" :stroke="mouthColor" stroke-width="3.6" stroke-linecap="round" />
    </g>

    <g v-else-if="face === 'cool'">
      <rect x="27" y="51" width="30" height="19" rx="5.5" fill="#23232a" :stroke="glassFrame" stroke-width="2.4" />
      <rect x="63" y="51" width="30" height="19" rx="5.5" fill="#23232a" :stroke="glassFrame" stroke-width="2.4" />
      <path d="M57 58 H63" :stroke="glassFrame" stroke-width="2.8" stroke-linecap="round" />
      <path d="M33 56 L39 65 M40 55 L44 61" stroke="#ffffff" stroke-width="2.2" stroke-linecap="round" opacity="0.65" />
      <path d="M69 56 L75 65 M76 55 L80 61" stroke="#ffffff" stroke-width="2.2" stroke-linecap="round" opacity="0.65" />
      <path d="M54 81 Q63 85 70 78" fill="none" :stroke="mouthColor" stroke-width="3.2" stroke-linecap="round" />
    </g>

    <g v-else-if="face === 'star'">
      <path d="M46 49 L49 58 L58 61 L49 64 L46 73 L43 64 L34 61 L43 58 Z" :fill="goldAccent" />
      <path d="M74 49 L77 58 L86 61 L77 64 L74 73 L71 64 L62 61 L71 58 Z" :fill="goldAccent" />
      <circle cx="46" cy="61" r="2.2" fill="#ffffff" />
      <circle cx="74" cy="61" r="2.2" fill="#ffffff" />
      <ellipse cx="60" cy="82" rx="5.2" ry="6.2" fill="#ffffff" opacity="0.92" />
      <ellipse cx="60" cy="84" rx="3.2" ry="2.8" fill="#ff7d95" />
    </g>

    <g v-else-if="face === 'tongue'">
      <ellipse cx="46" cy="62" rx="10" ry="12.2" fill="#ffffff" />
      <g :style="{ transform: `translate(${look.x * 0.7}px, ${look.y * 0.7}px)`, transition: 'transform .18s ease-out' }">
        <circle cx="48" cy="62.5" r="4.8" :fill="pupilColor" />
        <circle cx="46.5" cy="60.5" r="1.5" fill="#ffffff" />
      </g>
      <path d="M65 62 Q74 54 83 62" fill="none" :stroke="lineColor" stroke-width="4.8" stroke-linecap="round" />
      <path d="M51 77 Q60 83 69 77" fill="none" :stroke="mouthColor" stroke-width="3.2" stroke-linecap="round" />
      <path d="M56 79.5 Q60 92 65.5 79.5 Z" fill="#ff7d95" />
      <path d="M60.5 80.5 V86" stroke="#e05671" stroke-width="1.4" stroke-linecap="round" />
    </g>

    <g v-else-if="face === 'dizzy'" fill="none" :stroke="lineColor" stroke-width="3" stroke-linecap="round">
      <path d="M46 62 m0 -1 a2 2 0 1 1 2 2 a5 5 0 1 1 -5 -5 a8.5 8.5 0 1 1 9 8.5" />
      <path d="M74 62 m0 -1 a2 2 0 1 1 2 2 a5 5 0 1 1 -5 -5 a8.5 8.5 0 1 1 9 8.5" />
      <path d="M50 82 Q55 77 60 82 T70 82" stroke-width="3.2" />
    </g>

    <g v-else-if="face === 'deadpan'">
      <ellipse cx="47" cy="63" rx="10" ry="11.5" fill="#ffffff" />
      <ellipse cx="73" cy="63" rx="10" ry="11.5" fill="#ffffff" />
      <g :style="{ transform: `translate(${look.x * 0.6}px, ${Math.max(0, look.y * 0.4)}px)`, transition: 'transform .18s ease-out' }">
        <circle cx="51" cy="65" r="4.5" :fill="pupilColor" />
        <circle cx="77" cy="65" r="4.5" :fill="pupilColor" />
      </g>
      <rect x="34" y="48" width="26" height="13" :fill="style.body" />
      <rect x="60" y="48" width="26" height="13" :fill="style.body" />
      <path d="M36 61 H58 M62 61 H84" :stroke="lineColor" stroke-width="2.6" stroke-linecap="round" />
      <path d="M52 82 H68" :stroke="mouthColor" stroke-width="3.2" stroke-linecap="round" />
    </g>

    <g v-else-if="face === 'sleepy'">
      <path d="M37 62 Q46 71 55 62" fill="none" :stroke="lineColor" stroke-width="4.6" stroke-linecap="round" />
      <path d="M65 62 Q74 71 83 62" fill="none" :stroke="lineColor" stroke-width="4.6" stroke-linecap="round" />
      <circle cx="60" cy="79" r="3" :fill="mouthColor" opacity="0.85" />
      <g class="mascot-bubble">
        <circle cx="71" cy="79" r="9.5" fill="#a8d8ff" fill-opacity="0.52" stroke="#ffffff" stroke-width="2" />
        <path d="M67 74.5 Q69.5 72.5 72.5 74" fill="none" stroke="#ffffff" stroke-width="1.8" stroke-linecap="round" />
      </g>
    </g>

    <g v-else-if="face === 'goofy'">
      <ellipse cx="46" cy="64" rx="12" ry="14" fill="#ffffff" />
      <circle cx="75" cy="64" r="6.8" fill="#ffffff" />
      <g :style="{ transform: `translate(${look.x * 0.7}px, ${look.y * 0.7}px)`, transition: 'transform .18s ease-out' }">
        <circle cx="47" cy="64" r="5" :fill="pupilColor" />
        <circle cx="45.4" cy="62.2" r="1.7" fill="#ffffff" />
        <circle cx="74.5" cy="64" r="3.1" :fill="pupilColor" />
        <circle cx="73.5" cy="62.8" r="1.1" fill="#ffffff" />
      </g>
      <path d="M56 83 Q61 80 66 83" fill="none" :stroke="mouthColor" stroke-width="3.2" stroke-linecap="round" />
    </g>

    <g v-else-if="face === 'party-happy'">
      <path d="M37 64 Q47 53 56 64" fill="none" :stroke="lineColor" stroke-width="5" stroke-linecap="round" />
      <path d="M64 64 Q73 53 83 64" fill="none" :stroke="lineColor" stroke-width="5" stroke-linecap="round" />
      <path d="M53 79 Q60 86 67 79" fill="none" :stroke="mouthColor" stroke-width="3.4" stroke-linecap="round" />
    </g>

    <g v-else-if="face === 'wink-fang'">
      <ellipse cx="47" cy="62" rx="10" ry="12.5" fill="#ffffff" />
      <g :style="{ transform: `translate(${look.x * 0.7}px, ${look.y * 0.7}px)`, transition: 'transform .18s ease-out' }">
        <circle cx="49" cy="61.5" r="4.8" :fill="pupilColor" />
        <circle cx="47.4" cy="59.6" r="1.5" fill="#ffffff" />
      </g>
      <path d="M65 61 Q74 52 83 61" fill="none" :stroke="lineColor" stroke-width="4.8" stroke-linecap="round" />
      <path d="M52 79 Q61 86 71 77" fill="none" :stroke="mouthColor" stroke-width="3.4" stroke-linecap="round" />
      <polygon points="65,80.5 68.5,78.5 68,84" fill="#ffffff" />
    </g>

    <g v-else-if="face === 'shock'">
      <ellipse cx="46" cy="62" rx="11" ry="13.5" fill="#ffffff" />
      <ellipse cx="74" cy="62" rx="11" ry="13.5" fill="#ffffff" />
      <g :style="{ transform: `translate(${look.x * 0.8}px, ${look.y * 0.8}px)`, transition: 'transform .18s ease-out' }">
        <circle cx="46" cy="62" r="3" :fill="pupilColor" />
        <circle cx="74" cy="62" r="3" :fill="pupilColor" />
      </g>
      <ellipse cx="60" cy="83" rx="5.5" ry="7.2" fill="none" :stroke="mouthColor" stroke-width="3.4" />
    </g>

    <g v-else-if="face === 'whistle'">
      <ellipse cx="46" cy="61" rx="9.8" ry="12" fill="#ffffff" />
      <ellipse cx="74" cy="61" rx="9.8" ry="12" fill="#ffffff" />
      <circle cx="49.5" cy="57.5" r="4.4" :fill="pupilColor" />
      <circle cx="77.5" cy="57.5" r="4.4" :fill="pupilColor" />
      <circle cx="48.2" cy="56" r="1.4" fill="#ffffff" />
      <circle cx="76.2" cy="56" r="1.4" fill="#ffffff" />
      <circle cx="65" cy="81" r="4" fill="none" :stroke="mouthColor" stroke-width="3.2" />
      <g class="mascot-note" :fill="noteColor" :stroke="noteColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
        <path d="M90 72 V57 L101 54 V67" fill="none" />
        <ellipse cx="87.5" cy="72.5" rx="3.4" ry="2.5" stroke="none" />
        <ellipse cx="98.5" cy="68" rx="3.4" ry="2.5" stroke="none" />
      </g>
    </g>

    <g v-else-if="face === 'money'">
      <g fill="none" :stroke="goldAccent" stroke-width="3.6" stroke-linecap="round" stroke-linejoin="round">
        <path d="M38 51 L46 61 L54 51 M46 61 V73 M40 63.5 H52 M40 68.5 H52" />
        <path d="M66 51 L74 61 L82 51 M74 61 V73 M68 63.5 H80 M68 68.5 H80" />
      </g>
      <path d="M49 80 Q60 91 71 80" fill="none" :stroke="mouthColor" stroke-width="3.6" stroke-linecap="round" />
    </g>

    <g v-else-if="face === 'party-cool'">
      <path d="M24 53 L57 50 L55 68 Q40 73 27 67 Z" fill="#1e2230" :stroke="partyGlassFrame" stroke-width="2.5" stroke-linejoin="round" />
      <path d="M96 53 L63 50 L65 68 Q80 73 93 67 Z" fill="#1e2230" :stroke="partyGlassFrame" stroke-width="2.5" stroke-linejoin="round" />
      <path d="M56 54 H64" :stroke="partyGlassFrame" stroke-width="3" stroke-linecap="round" />
      <path d="M32 56 L38 65 M70 56 L76 65" stroke="#ffffff" stroke-width="2.4" stroke-linecap="round" opacity="0.72" />
      <path d="M53 81 Q61 86 70 78" fill="none" :stroke="mouthColor" stroke-width="3.4" stroke-linecap="round" />
    </g>

    <g v-else-if="face === 'teary'">
      <ellipse cx="46" cy="64" rx="11" ry="12.5" fill="#ffffff" />
      <ellipse cx="74" cy="64" rx="11" ry="12.5" fill="#ffffff" />
      <circle cx="46.5" cy="64.5" r="6.8" :fill="pupilColor" />
      <circle cx="73.5" cy="64.5" r="6.8" :fill="pupilColor" />
      <circle cx="44" cy="61.5" r="2.8" fill="#ffffff" />
      <circle cx="71" cy="61.5" r="2.8" fill="#ffffff" />
      <circle cx="49" cy="68" r="1.7" fill="#9bd5ff" />
      <circle cx="76" cy="68" r="1.7" fill="#9bd5ff" />
      <path d="M35 72 Q32 78 36 80 Q39 78 35 72 Z" :fill="tearColor" />
      <path d="M85 72 Q88 78 84 80 Q81 78 85 72 Z" :fill="tearColor" />
      <path d="M36 49 L49 45 M84 49 L71 45" :stroke="lineColor" stroke-width="2.6" stroke-linecap="round" opacity="0.85" />
      <path d="M53 84 Q60 78 67 84" fill="none" :stroke="mouthColor" stroke-width="3.2" stroke-linecap="round" />
    </g>

    <g v-else-if="face === 'love'">
      <path d="M46 72 C46 72 34 64 34 56.5 C34 52.2 37.4 49.5 41 49.5 C43.4 49.5 45.2 50.8 46 52.6 C46.8 50.8 48.6 49.5 51 49.5 C54.6 49.5 58 52.2 58 56.5 C58 64 46 72 46 72 Z" fill="#f54d7a" />
      <path d="M74 72 C74 72 62 64 62 56.5 C62 52.2 65.4 49.5 69 49.5 C71.4 49.5 73.2 50.8 74 52.6 C74.8 50.8 76.6 49.5 79 49.5 C82.6 49.5 86 52.2 86 56.5 C86 64 74 72 74 72 Z" fill="#f54d7a" />
      <circle cx="40.5" cy="55.5" r="2.2" fill="#ffffff" opacity="0.88" />
      <circle cx="68.5" cy="55.5" r="2.2" fill="#ffffff" opacity="0.88" />
      <path d="M49.5 80 Q54.8 87 60 81 Q65.2 87 70.5 80" fill="none" :stroke="mouthColor" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round" />
    </g>

    <g v-else-if="face === 'xd'">
      <path d="M36 53 L52 62 L36 71" fill="none" :stroke="lineColor" stroke-width="4.8" stroke-linecap="round" stroke-linejoin="round" />
      <path d="M84 53 L68 62 L84 71" fill="none" :stroke="lineColor" stroke-width="4.8" stroke-linecap="round" stroke-linejoin="round" />
      <path d="M45 77 Q60 97 75 77 Z" fill="#ffffff" />
      <path d="M51 84 Q60 93.5 69 84 Q60 80.5 51 84 Z" fill="#ff7d95" />
    </g>

    <!-- 默认：经典大眼 -->
    <template v-else>
      <g class="animate-blink" :style="{ transformOrigin: '60px 62px', transformBox: 'view-box', animationDelay: blinkDelay }">
        <ellipse v-if="!style.dark && !style.stroke" cx="47" cy="63.4" rx="10.5" ry="13" :fill="style.shade" />
        <ellipse v-if="!style.dark && !style.stroke" cx="73" cy="63.4" rx="10.5" ry="13" :fill="style.shade" />
        <ellipse cx="47" cy="62" rx="10.5" ry="13" fill="#ffffff" :stroke="eyeOutline || 'none'" :stroke-width="eyeOutline ? 2.2 : 0" />
        <ellipse cx="73" cy="62" rx="10.5" ry="13" fill="#ffffff" :stroke="eyeOutline || 'none'" :stroke-width="eyeOutline ? 2.2 : 0" />
        <g :style="{ transform: `translate(${look.x}px, ${look.y}px)`, transition: 'transform .18s ease-out' }">
          <circle cx="48" cy="63" r="5" :fill="pupilColor" />
          <circle cx="74" cy="63" r="5" :fill="pupilColor" />
          <circle cx="46.4" cy="61.2" r="1.5" fill="#ffffff" />
          <circle cx="72.4" cy="61.2" r="1.5" fill="#ffffff" />
        </g>
      </g>
      <path v-if="mood === 'working'" d="M54 81 Q60 87 66 81" :stroke="mouthColor" stroke-width="3" fill="none" stroke-linecap="round" />
    </template>

    <!-- 思考气泡 -->
    <g v-if="mood === 'thinking'">
      <circle cx="98" cy="30" r="3" fill="#d4d4d8"><animate attributeName="opacity" values=".3;1;.3" dur="1.2s" repeatCount="indefinite" /></circle>
      <circle cx="106" cy="20" r="4" fill="#d4d4d8"><animate attributeName="opacity" values=".3;1;.3" dur="1.2s" begin=".2s" repeatCount="indefinite" /></circle>
      <circle cx="116" cy="8" r="5" fill="#d4d4d8"><animate attributeName="opacity" values=".3;1;.3" dur="1.2s" begin=".4s" repeatCount="indefinite" /></circle>
    </g>
  </svg>
</template>

<style scoped>
.mascot-bubble {
  transform-box: fill-box;
  transform-origin: center;
  animation: mascot-bubble 2.4s ease-in-out infinite;
}
.mascot-note {
  animation: mascot-note 2.6s ease-in-out infinite;
}

@keyframes mascot-bubble {
  0%, 100% { transform: scale(0.92); opacity: 0.85; }
  50% { transform: scale(1.1); opacity: 1; }
}
@keyframes mascot-note {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-3px); }
}

@media (prefers-reduced-motion: reduce) {
  .mascot-bubble,
  .mascot-note {
    animation: none;
  }
}
</style>
