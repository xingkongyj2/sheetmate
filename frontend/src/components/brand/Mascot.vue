<script setup lang="ts">
/**
 * 墨墨家族 IP 形象（原创）：圆团子身体 + 会眨眼、会跟着鼠标转的大眼睛 + 小配饰。
 * mood: idle 发呆 / thinking 思考（眼睛往上看 + 冒泡）/ happy 开心（眯眼笑）/ working 干活（上下晃）
 */
import { computed, onMounted, ref } from 'vue'

import { usePointer } from '@/composables/usePointer'

import { mascotStyle } from './mascots'

const props = withDefaults(
  defineProps<{
    variant?: string | null
    size?: number
    mood?: 'idle' | 'thinking' | 'happy' | 'working'
    track?: boolean
    shadow?: boolean
  }>(),
  { variant: 'blob', size: 96, mood: 'idle', track: true, shadow: true },
)

const root = ref<SVGSVGElement | null>(null)
const pointer = usePointer()
const style = computed(() => mascotStyle(props.variant))
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

const dark = computed(() => style.value.body === '#111113')
const mouthColor = computed(() => (dark.value ? 'rgba(255,255,255,.75)' : 'rgba(17,17,19,.7)'))
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
    <ellipse v-if="shadow" cx="60" cy="113" rx="30" ry="4" fill="#000" opacity="0.08" />

    <!-- 小手 -->
    <ellipse cx="19" cy="76" rx="6" ry="9" :fill="style.shade" transform="rotate(-24 19 76)" />
    <ellipse cx="101" cy="76" rx="6" ry="9" :fill="style.shade" transform="rotate(24 101 76)" />

    <!-- 身体 -->
    <path
      d="M60 22 C84 21 101 40 102 64 C103 90 88 107 60 107 C32 107 17 90 18 64 C19 40 36 23 60 22 Z"
      :fill="style.body"
    />
    <path d="M30 92 C40 103 80 103 90 92 C84 104 72 107 60 107 C48 107 36 104 30 92 Z" :fill="style.shade" opacity="0.55" />

    <!-- 配饰 -->
    <g v-if="style.accessory === 'sprout'">
      <path d="M60 23 C60 17 60.5 12 62 7" stroke="#159a5d" stroke-width="2.6" fill="none" stroke-linecap="round" />
      <path d="M62 9 C66 1 77 2 77 8 C72 13 65 13 62 9 Z" :fill="style.accent" />
      <path d="M61 12 C57 6 49 7 49 11 C53 15 58 15 61 12 Z" fill="#5fd39a" />
    </g>
    <g v-else-if="style.accessory === 'antenna'">
      <path d="M60 23 L60 9" stroke="#111113" stroke-width="2.6" stroke-linecap="round" />
      <circle cx="60" cy="8" r="5" :fill="style.accent">
        <animate attributeName="r" values="4.2;5.6;4.2" dur="1.6s" repeatCount="indefinite" />
      </circle>
    </g>
    <g v-else-if="style.accessory === 'cap'">
      <path d="M33 37 C36 20 84 20 87 37 Z" :fill="style.accent" />
      <path d="M78 34 C88 33 98 35 100 39 C92 40 84 39 78 38 Z" :fill="style.accent" />
      <circle cx="60" cy="22" r="2.6" fill="#ffffff" opacity="0.9" />
    </g>
    <g v-else-if="style.accessory === 'headphones'">
      <path d="M24 56 C24 22 96 22 96 56" stroke="#111113" stroke-width="5" fill="none" stroke-linecap="round" />
      <rect x="15" y="50" width="12" height="20" rx="6" fill="#111113" />
      <rect x="93" y="50" width="12" height="20" rx="6" fill="#111113" />
    </g>
    <g v-else-if="style.accessory === 'bow'">
      <path d="M72 26 L85 17 Q88 26 85 35 Z" :fill="style.accent" />
      <path d="M72 26 L59 17 Q56 26 59 35 Z" :fill="style.accent" />
      <circle cx="72" cy="26" r="3.6" fill="#d94f8c" />
    </g>
    <g v-else-if="style.accessory === 'glasses'">
      <circle cx="47" cy="62" r="14.5" fill="none" stroke="#111113" stroke-width="3" />
      <circle cx="73" cy="62" r="14.5" fill="none" stroke="#111113" stroke-width="3" />
      <path d="M61.5 60 Q60 57.5 58.5 60" stroke="#111113" stroke-width="3" fill="none" />
    </g>
    <g v-else-if="style.accessory === 'crown'">
      <path d="M42 30 L44 12 L53 21 L60 9 L67 21 L76 12 L78 30 Z" :fill="style.accent" stroke="#e0a50a" stroke-width="1.5" stroke-linejoin="round" />
      <circle cx="60" cy="24" r="2.6" fill="#ff5a5f" />
    </g>
    <g v-else-if="style.accessory === 'beret'">
      <ellipse cx="58" cy="27" rx="27" ry="8.5" fill="#111113" />
      <path d="M58 19 L60 13" stroke="#111113" stroke-width="3" stroke-linecap="round" />
    </g>
    <g v-else-if="style.accessory === 'scarf'">
      <path d="M26 86 C44 96 76 96 94 86 L94 93 C76 103 44 103 26 93 Z" :fill="style.accent" />
      <path d="M78 94 L84 108 L74 106 Z" :fill="style.accent" />
    </g>

    <!-- 眼睛 -->
    <g v-if="mood === 'happy'">
      <path d="M38 64 Q47 54 56 64" stroke="#ffffff" stroke-width="4.5" fill="none" stroke-linecap="round" />
      <path d="M64 64 Q73 54 82 64" stroke="#ffffff" stroke-width="4.5" fill="none" stroke-linecap="round" />
    </g>
    <g v-else class="animate-blink" :style="{ transformOrigin: '60px 62px', transformBox: 'view-box', animationDelay: blinkDelay }">
      <ellipse cx="47" cy="62" rx="10.5" ry="13" fill="#ffffff" />
      <ellipse cx="73" cy="62" rx="10.5" ry="13" fill="#ffffff" />
      <g :style="{ transform: `translate(${look.x}px, ${look.y}px)`, transition: 'transform .18s ease-out' }">
        <circle cx="48" cy="63" r="5" fill="#111113" />
        <circle cx="74" cy="63" r="5" fill="#111113" />
        <circle cx="46.4" cy="61.2" r="1.5" fill="#ffffff" />
        <circle cx="72.4" cy="61.2" r="1.5" fill="#ffffff" />
      </g>
    </g>

    <!-- 嘴巴 / 腮红 -->
    <path v-if="mood === 'happy' || mood === 'working'" d="M54 81 Q60 87 66 81" :stroke="mouthColor" stroke-width="3" fill="none" stroke-linecap="round" />
    <g v-if="!dark" opacity="0.28">
      <ellipse cx="35" cy="78" rx="6" ry="3.5" fill="#ffffff" />
      <ellipse cx="85" cy="78" rx="6" ry="3.5" fill="#ffffff" />
    </g>

    <!-- 思考气泡 -->
    <g v-if="mood === 'thinking'">
      <circle cx="98" cy="30" r="3" fill="#d4d4d8"><animate attributeName="opacity" values=".3;1;.3" dur="1.2s" repeatCount="indefinite" /></circle>
      <circle cx="106" cy="20" r="4" fill="#d4d4d8"><animate attributeName="opacity" values=".3;1;.3" dur="1.2s" begin=".2s" repeatCount="indefinite" /></circle>
      <circle cx="116" cy="8" r="5" fill="#d4d4d8"><animate attributeName="opacity" values=".3;1;.3" dur="1.2s" begin=".4s" repeatCount="indefinite" /></circle>
    </g>
  </svg>
</template>
