<script setup lang="ts">
/**
 * 墨墨家族 IP 形象（原创）：圆团子身体 + 会眨眼/跟鼠标转的丰富搞怪表情 + 标志绿豆芽 / 蓝波点尖帽配饰。
 * mood: idle 默认（展示头像自带的搞怪表情）/ thinking 思考 / happy 开心 / working 干活（上下晃）/ sleep 闭眼打盹
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
const lineColor = computed(() => (style.value.dark ? 'rgba(255,255,255,.86)' : style.value.line))

/** 仅当外部显式切换为 happy / sleep 状态，且头像本身是标准大眼款时覆盖表情；专属搞怪表情在 idle/working/thinking 下保持原汁原味 */
const moodFaceOverride = computed<'happy' | 'sleep' | null>(() => {
  if (props.mood === 'sleep') return 'sleep'
  if (props.mood === 'happy' && face.value === 'normal') return 'happy'
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
    <ellipse cx="19" cy="77" rx="6" ry="9" :fill="style.body" transform="rotate(-24 19 77)" />
    <ellipse cx="101" cy="77" rx="6" ry="9" :fill="style.body" transform="rotate(24 101 77)" />

    <!-- 身体 -->
    <path :d="BODY" :fill="style.body" />
    <path :d="BODY" :fill="`url(#${shadeId})`" />

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
        <rect x="32" y="34" width="22" height="10" rx="4.5" fill="#f3c691" />
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
    <g v-if="style.blush" :fill="style.blush" opacity="0.55">
      <ellipse cx="34" cy="79" rx="6" ry="3.5" />
      <ellipse cx="86" cy="79" rx="6" ry="3.5" />
    </g>

    <!-- ==================== 表情（眼睛与嘴） ==================== -->
    <!-- 外部 mood 覆盖：happy / sleep -->
    <template v-if="moodFaceOverride === 'happy'">
      <g fill="none" :stroke="style.dark ? '#ffffff' : style.line" stroke-width="4.5" stroke-linecap="round">
        <path d="M38 64 Q47 54 56 64" />
        <path d="M64 64 Q73 54 82 64" />
      </g>
      <path d="M54 81 Q60 87 66 81" :stroke="lineColor" stroke-width="3" fill="none" stroke-linecap="round" />
    </template>
    <template v-else-if="moodFaceOverride === 'sleep'">
      <g fill="none" :stroke="style.dark ? '#ffffff' : style.line" stroke-width="5" stroke-linecap="round">
        <path d="M38 62 Q47 71 56 62" />
        <path d="M64 62 Q73 71 82 62" />
      </g>
    </template>

    <!-- 1. 眯眼大笑 -->
    <g v-else-if="face === 'laugh'">
      <path d="M36 64 Q46 52 56 64" fill="none" stroke="#ffffff" stroke-width="5.2" stroke-linecap="round" />
      <path d="M64 64 Q74 52 84 64" fill="none" stroke="#ffffff" stroke-width="5.2" stroke-linecap="round" />
      <path d="M51 78 Q60 88 69 78" fill="none" stroke="#ffffff" stroke-width="3.6" stroke-linecap="round" opacity="0.9" />
    </g>

    <!-- 2. 拽酷墨镜 -->
    <g v-else-if="face === 'cool'">
      <rect x="27" y="51" width="30" height="19" rx="5.5" fill="#27272e" stroke="#ffffff" stroke-width="2.4" />
      <rect x="63" y="51" width="30" height="19" rx="5.5" fill="#27272e" stroke="#ffffff" stroke-width="2.4" />
      <path d="M57 58 H63" stroke="#ffffff" stroke-width="2.8" stroke-linecap="round" />
      <path d="M33 56 L39 65 M40 55 L44 61" stroke="#ffffff" stroke-width="2.2" stroke-linecap="round" opacity="0.65" />
      <path d="M69 56 L75 65 M76 55 L80 61" stroke="#ffffff" stroke-width="2.2" stroke-linecap="round" opacity="0.65" />
      <path d="M54 81 Q63 85 70 78" fill="none" stroke="#ffffff" stroke-width="3.2" stroke-linecap="round" opacity="0.9" />
    </g>

    <!-- 3. 两眼放光（金色星星眼） -->
    <g v-else-if="face === 'star'">
      <path d="M46 49 L49 58 L58 61 L49 64 L46 73 L43 64 L34 61 L43 58 Z" fill="#ffc94a" />
      <path d="M74 49 L77 58 L86 61 L77 64 L74 73 L71 64 L62 61 L71 58 Z" fill="#ffc94a" />
      <circle cx="46" cy="61" r="2.2" fill="#ffffff" />
      <circle cx="74" cy="61" r="2.2" fill="#ffffff" />
      <ellipse cx="60" cy="82" rx="5.2" ry="6.2" fill="#ffffff" opacity="0.9" />
      <ellipse cx="60" cy="84" rx="3.2" ry="2.8" fill="#ff7d95" />
    </g>

    <!-- 4. 眨眼吐舌 -->
    <g v-else-if="face === 'tongue'">
      <ellipse cx="46" cy="62" rx="10" ry="12.2" fill="#ffffff" />
      <g :style="{ transform: `translate(${look.x * 0.7}px, ${look.y * 0.7}px)`, transition: 'transform .18s ease-out' }">
        <circle cx="48" cy="62.5" r="4.8" fill="#111113" />
        <circle cx="46.5" cy="60.5" r="1.5" fill="#ffffff" />
      </g>
      <path d="M65 62 Q74 54 83 62" fill="none" stroke="#ffffff" stroke-width="4.8" stroke-linecap="round" />
      <path d="M51 77 Q60 83 69 77" fill="none" stroke="#ffffff" stroke-width="3.2" stroke-linecap="round" />
      <path d="M56 79.5 Q60 92 65.5 79.5 Z" fill="#ff7d95" />
      <path d="M60.5 80.5 V86" stroke="#e05671" stroke-width="1.4" stroke-linecap="round" />
    </g>

    <!-- 5. 对账晕晕（蚊香眼） -->
    <g v-else-if="face === 'dizzy'" fill="none" stroke="#ffffff" stroke-width="3" stroke-linecap="round">
      <path d="M46 62 m0 -1 a2 2 0 1 1 2 2 a5 5 0 1 1 -5 -5 a8.5 8.5 0 1 1 9 8.5" />
      <path d="M74 62 m0 -1 a2 2 0 1 1 2 2 a5 5 0 1 1 -5 -5 a8.5 8.5 0 1 1 9 8.5" />
      <path d="M50 82 Q55 77 60 82 T70 82" stroke-width="3.2" />
    </g>

    <!-- 6. 无语盯人（半眯死鱼眼） -->
    <g v-else-if="face === 'deadpan'">
      <ellipse cx="47" cy="63" rx="10" ry="11.5" fill="#ffffff" />
      <ellipse cx="73" cy="63" rx="10" ry="11.5" fill="#ffffff" />
      <g :style="{ transform: `translate(${look.x * 0.6}px, ${Math.max(0, look.y * 0.4)}px)`, transition: 'transform .18s ease-out' }">
        <circle cx="51" cy="65" r="4.5" fill="#111113" />
        <circle cx="77" cy="65" r="4.5" fill="#111113" />
      </g>
      <rect x="35" y="49" width="24" height="12" fill="#111113" />
      <rect x="61" y="49" width="24" height="12" fill="#111113" />
      <path d="M36 61 H58 M62 61 H84" stroke="#ffffff" stroke-width="2.4" stroke-linecap="round" />
      <path d="M52 82 H68" stroke="#ffffff" stroke-width="3.2" stroke-linecap="round" opacity="0.88" />
    </g>

    <!-- 7. 摸鱼打盹（鼻涕泡） -->
    <g v-else-if="face === 'sleepy'">
      <path d="M37 62 Q46 71 55 62" fill="none" stroke="#ffffff" stroke-width="4.6" stroke-linecap="round" />
      <path d="M65 62 Q74 71 83 62" fill="none" stroke="#ffffff" stroke-width="4.6" stroke-linecap="round" />
      <circle cx="60" cy="79" r="3" fill="#ffffff" opacity="0.8" />
      <g class="mascot-bubble">
        <circle cx="71" cy="79" r="9.5" fill="#a8d8ff" fill-opacity="0.45" stroke="#ffffff" stroke-width="2" />
        <path d="M67 74.5 Q69.5 72.5 72.5 74" fill="none" stroke="#ffffff" stroke-width="1.8" stroke-linecap="round" />
      </g>
    </g>

    <!-- 8. 大小眼憨憨 O_o -->
    <g v-else-if="face === 'goofy'">
      <ellipse cx="46" cy="64" rx="12" ry="14" fill="#ffffff" />
      <circle cx="75" cy="64" r="6.8" fill="#ffffff" />
      <g :style="{ transform: `translate(${look.x * 0.7}px, ${look.y * 0.7}px)`, transition: 'transform .18s ease-out' }">
        <circle cx="47" cy="64" r="5" fill="#111113" />
        <circle cx="45.4" cy="62.2" r="1.7" fill="#ffffff" />
        <circle cx="74.5" cy="64" r="3.1" fill="#111113" />
        <circle cx="73.5" cy="62.8" r="1.1" fill="#ffffff" />
      </g>
      <path d="M56 83 Q61 80 66 83" fill="none" stroke="#ffffff" stroke-width="3.2" stroke-linecap="round" opacity="0.86" />
    </g>

    <!-- 9. 蓝帽嘻嘻 -->
    <g v-else-if="face === 'party-happy'">
      <path d="M37 64 Q47 53 56 64" fill="none" stroke="#ffffff" stroke-width="5" stroke-linecap="round" />
      <path d="M64 64 Q73 53 83 64" fill="none" stroke="#ffffff" stroke-width="5" stroke-linecap="round" />
      <path d="M53 79 Q60 86 67 79" fill="none" stroke="#ffffff" stroke-width="3.4" stroke-linecap="round" opacity="0.86" />
    </g>

    <!-- 10. 蓝帽小机灵（单眨眼 + 小虎牙） -->
    <g v-else-if="face === 'wink-fang'">
      <ellipse cx="47" cy="62" rx="10" ry="12.5" fill="#ffffff" />
      <g :style="{ transform: `translate(${look.x * 0.7}px, ${look.y * 0.7}px)`, transition: 'transform .18s ease-out' }">
        <circle cx="49" cy="61.5" r="4.8" fill="#111113" />
        <circle cx="47.4" cy="59.6" r="1.5" fill="#ffffff" />
      </g>
      <path d="M65 61 Q74 52 83 61" fill="none" stroke="#ffffff" stroke-width="4.8" stroke-linecap="round" />
      <path d="M52 79 Q61 86 71 77" fill="none" stroke="#ffffff" stroke-width="3.4" stroke-linecap="round" opacity="0.9" />
      <polygon points="65,80.5 68.5,78.5 68,84" fill="#ffffff" />
    </g>

    <!-- 11. 蓝帽惊呆（豆豆眼 + O 嘴） -->
    <g v-else-if="face === 'shock'">
      <ellipse cx="46" cy="62" rx="11" ry="13.5" fill="#ffffff" />
      <ellipse cx="74" cy="62" rx="11" ry="13.5" fill="#ffffff" />
      <g :style="{ transform: `translate(${look.x * 0.8}px, ${look.y * 0.8}px)`, transition: 'transform .18s ease-out' }">
        <circle cx="46" cy="62" r="3" fill="#111113" />
        <circle cx="74" cy="62" r="3" fill="#111113" />
      </g>
      <ellipse cx="60" cy="83" rx="5.5" ry="7.2" fill="none" stroke="#ffffff" stroke-width="3.4" />
    </g>

    <!-- 12. 蓝帽吹口哨（斜眼装无辜 + 音符 ♪） -->
    <g v-else-if="face === 'whistle'">
      <ellipse cx="46" cy="61" rx="9.8" ry="12" fill="#ffffff" />
      <ellipse cx="74" cy="61" rx="9.8" ry="12" fill="#ffffff" />
      <circle cx="49.5" cy="57.5" r="4.4" fill="#111113" />
      <circle cx="77.5" cy="57.5" r="4.4" fill="#111113" />
      <circle cx="48.2" cy="56" r="1.4" fill="#ffffff" />
      <circle cx="76.2" cy="56" r="1.4" fill="#ffffff" />
      <circle cx="65" cy="81" r="4" fill="none" stroke="#ffffff" stroke-width="3.2" />
      <!-- 漂浮的蓝色小音符 -->
      <g class="mascot-note" fill="#6ea0ff" stroke="#6ea0ff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
        <path d="M90 72 V57 L101 54 V67" fill="none" />
        <ellipse cx="87.5" cy="72.5" rx="3.4" ry="2.5" stroke="none" />
        <ellipse cx="98.5" cy="68" rx="3.4" ry="2.5" stroke="none" />
      </g>
    </g>

    <!-- 13. 蓝帽发财眼（金色 ¥ 财迷眼） -->
    <g v-else-if="face === 'money'">
      <g fill="none" stroke="#ffc94a" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round">
        <path d="M38 51 L46 61 L54 51 M46 61 V73 M40 63.5 H52 M40 68.5 H52" />
        <path d="M66 51 L74 61 L82 51 M74 61 V73 M68 63.5 H80 M68 68.5 H80" />
      </g>
      <path d="M49 80 Q60 91 71 80" fill="none" stroke="#ffffff" stroke-width="3.6" stroke-linecap="round" opacity="0.92" />
    </g>

    <!-- 14. 蓝帽派对拽哥（斜切霓虹蓝边墨镜） -->
    <g v-else-if="face === 'party-cool'">
      <path d="M24 53 L57 50 L55 68 Q40 73 27 67 Z" fill="#1e2230" stroke="#7ea8ff" stroke-width="2.5" stroke-linejoin="round" />
      <path d="M96 53 L63 50 L65 68 Q80 73 93 67 Z" fill="#1e2230" stroke="#7ea8ff" stroke-width="2.5" stroke-linejoin="round" />
      <path d="M56 54 H64" stroke="#7ea8ff" stroke-width="3" stroke-linecap="round" />
      <path d="M32 56 L38 65 M70 56 L76 65" stroke="#ffffff" stroke-width="2.4" stroke-linecap="round" opacity="0.72" />
      <path d="M53 81 Q61 86 70 78" fill="none" stroke="#ffffff" stroke-width="3.4" stroke-linecap="round" opacity="0.92" />
    </g>

    <!-- 15. 蓝帽委屈巴巴（水汪汪星星泪眼 + 委屈小嘴） -->
    <g v-else-if="face === 'teary'">
      <ellipse cx="46" cy="64" rx="11" ry="12.5" fill="#ffffff" />
      <ellipse cx="74" cy="64" rx="11" ry="12.5" fill="#ffffff" />
      <circle cx="46.5" cy="64.5" r="6.8" fill="#1d2538" />
      <circle cx="73.5" cy="64.5" r="6.8" fill="#1d2538" />
      <circle cx="44" cy="61.5" r="2.8" fill="#ffffff" />
      <circle cx="71" cy="61.5" r="2.8" fill="#ffffff" />
      <circle cx="49" cy="68" r="1.7" fill="#9bd5ff" />
      <circle cx="76" cy="68" r="1.7" fill="#9bd5ff" />
      <!-- 眼角小泪滴 -->
      <path d="M35 72 Q32 78 36 80 Q39 78 35 72 Z" fill="#7ec8ff" />
      <path d="M85 72 Q88 78 84 80 Q81 78 85 72 Z" fill="#7ec8ff" />
      <!-- 八字委屈眉与波浪小嘴 -->
      <path d="M36 49 L49 45 M84 49 L71 45" stroke="#ffffff" stroke-width="2.6" stroke-linecap="round" opacity="0.82" />
      <path d="M53 84 Q60 78 67 84" fill="none" stroke="#ffffff" stroke-width="3.2" stroke-linecap="round" opacity="0.9" />
    </g>

    <!-- 默认：经典大眼（可眨眼、可跟鼠标转） -->
    <template v-else>
      <g class="animate-blink" :style="{ transformOrigin: '60px 62px', transformBox: 'view-box', animationDelay: blinkDelay }">
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
      <path v-if="mood === 'working'" d="M54 81 Q60 87 66 81" :stroke="lineColor" stroke-width="3" fill="none" stroke-linecap="round" />
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
