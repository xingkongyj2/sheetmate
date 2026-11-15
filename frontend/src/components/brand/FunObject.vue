<script setup lang="ts">
/**
 * 彩色小物件插画（原创），每一个都对应一种表格操作：
 * sheet 表格 · funnel 筛选 · magnet 匹配 · sigma 汇总 · puzzle 合并 · rocket 执行 · folder 文件
 * crown 排名 · plane 导出 · pie 分析 · spark AI · broom 清洗 · blocks 重塑 · calc 计算 · code 自定义
 *
 * cheese 奶酪（缺失值）· donut 甜甜圈（环形图）· cloud 云朵 —— 偏装饰的可爱小物件
 *
 * 风格：柔和立体——左上受光的渐变面 + 右下侧面厚度 + 少量白色高光，配色与墨墨家族同一套色板。
 * face：装饰场景（登录页、空状态）可以给物件加表情，眼睛会眨、会跟着鼠标转；功能图标位置不要加。
 */
import { computed, onMounted, ref, useId } from 'vue'

import { usePointer } from '@/composables/usePointer'

export type FunKind =
  | 'sheet' | 'funnel' | 'magnet' | 'sigma' | 'puzzle' | 'rocket' | 'folder' | 'crown'
  | 'plane' | 'pie' | 'spark' | 'broom' | 'blocks' | 'calc' | 'code'
  | 'cheese' | 'donut' | 'cloud'

/** smile 微笑 · wink 眨眼吐舌 · happy 眯眼笑 · wow 惊讶 · sleepy 打瞌睡 */
export type FunFace = 'smile' | 'wink' | 'happy' | 'wow' | 'sleepy'

const props = withDefaults(defineProps<{ kind: FunKind; size?: number; face?: FunFace | null }>(), { size: 48, face: null })

/** 每个色系：亮面 / 本色 / 渐变暗端 / 侧面厚度 */
const TONES = {
  green: ['#74d99e', '#38aa68', '#279356', '#17703c'],
  blue: ['#8fb4ff', '#3a72e9', '#2a5fd6', '#1c46a8'],
  red: ['#ff9d92', '#e85d53', '#d24840', '#a8302a'],
  yellow: ['#ffe79c', '#f9c647', '#ebaf2a', '#c98a0c'],
  sky: ['#a2ddf8', '#46b1e3', '#2f9dd2', '#1a7cad'],
  pink: ['#ffb3d0', '#e573a3', '#d45d91', '#ab4277'],
  orange: ['#ffbb7d', '#f38230', '#e06d18', '#b4520a'],
  purple: ['#bdabff', '#8667db', '#7354cc', '#5139a8'],
  ink: ['#5a5a63', '#2c2c32', '#1f1f24', '#0b0b0c'],
  silver: ['#ffffff', '#eef0f4', '#dfe2e9', '#b9bdc8'],
  wood: ['#e6b781', '#c78a4a', '#b2763a', '#8a5628'],
  mint: ['#f0fff6', '#c9f6de', '#aeebca', '#86d6ab'],
  dough: ['#f9d6a6', '#e8a862', '#d4914c', '#a8682c'],
  cloud: ['#ffffff', '#eef8ff', '#cfeafc', '#9fd0f0'],
} as const
type Tone = keyof typeof TONES

const KIND_TONES: Record<FunKind, Tone[]> = {
  sheet: ['green'],
  funnel: ['blue'],
  magnet: ['red', 'silver'],
  sigma: ['yellow'],
  puzzle: ['sky'],
  rocket: ['pink', 'orange', 'yellow', 'blue', 'silver'],
  folder: ['yellow', 'silver'],
  crown: ['purple', 'yellow', 'pink'],
  plane: ['orange'],
  pie: ['purple', 'orange'],
  spark: ['yellow', 'pink'],
  broom: ['wood', 'yellow', 'orange'],
  blocks: ['blue', 'red', 'green'],
  calc: ['ink', 'mint', 'silver', 'orange'],
  code: ['ink'],
  cheese: ['yellow'],
  donut: ['dough', 'pink'],
  cloud: ['cloud'],
}

/** 表情画在物件的哪里：中心点 x、y + 缩放 */
const FACE_AT: Partial<Record<FunKind, [number, number, number]>> = {
  sheet: [22, 28, 1.1],
  funnel: [24, 20.5, 0.95],
  magnet: [24, 34.6, 0.78],
  puzzle: [24, 27, 1.05],
  folder: [24, 31.2, 1.12],
  blocks: [14, 31.5, 0.8],
  cheese: [23, 30.5, 1.1],
  donut: [24, 29, 0.85],
  cloud: [24, 30.2, 1.12],
}
const FACE_EYES: Record<FunFace, 'open' | 'wink' | 'up' | 'down'> = { smile: 'open', wink: 'wink', happy: 'up', wow: 'open', sleepy: 'down' }
const INK = '#1d1d1f'

/** 渐变方向：d 左上→右下 · h 左→右 · v 上→下 · r 右下→左上（凹面） */
const DIRS = { d: [0, 0, 1, 1], h: [0, 0, 1, 0], v: [0, 0, 0, 1], r: [1, 1, 0, 0] } as const
type Dir = keyof typeof DIRS

const uid = useId()
const tones = computed(() => KIND_TONES[props.kind])
const fill = (tone: Tone, dir: Dir = 'd') => `url(#${uid}-${tone}-${dir})`
const light = (tone: Tone) => TONES[tone][0]
const dark = (tone: Tone) => TONES[tone][3]

const faceAt = computed(() => FACE_AT[props.kind] ?? [24, 26, 1])
const eyes = computed(() => FACE_EYES[props.face ?? 'smile'])
const blinkDelay = `${(Math.random() * 4).toFixed(2)}s`

/** 眼睛朝鼠标方向最多移动 1 个单位 */
const root = ref<SVGSVGElement | null>(null)
const mounted = ref(false)
onMounted(() => (mounted.value = true))
const pointer = usePointer()
const look = computed(() => {
  if (!props.face || !pointer.active || !mounted.value || !root.value) return { x: 0, y: 0 }
  const rect = root.value.getBoundingClientRect()
  const dx = pointer.x - (rect.left + rect.width / 2)
  const dy = pointer.y - (rect.top + rect.height / 2)
  const dist = Math.hypot(dx, dy) || 1
  const reach = Math.min(1, dist / 220)
  return { x: (dx / dist) * reach, y: (dy / dist) * reach }
})
</script>

<template>
  <svg ref="root" :width="size" :height="size" viewBox="0 0 48 48" :class="face ? 'overflow-visible' : undefined" aria-hidden="true">
    <defs>
      <template v-for="tone in tones" :key="tone">
        <linearGradient v-for="(v, dir) in DIRS" :id="`${uid}-${tone}-${dir}`" :key="dir" :x1="v[0]" :y1="v[1]" :x2="v[2]" :y2="v[3]">
          <stop offset="0" :stop-color="TONES[tone][0]" />
          <stop offset="0.48" :stop-color="TONES[tone][1]" />
          <stop offset="1" :stop-color="TONES[tone][2]" />
        </linearGradient>
      </template>
    </defs>

    <!-- 表格：绿色卡片 + 选中的单元格 -->
    <g v-if="kind === 'sheet'">
      <rect x="8.5" y="9.5" width="34" height="32" rx="7" :fill="dark('green')" />
      <rect x="5" y="6" width="34" height="32" rx="7" :fill="fill('green')" />
      <path d="M5 13a7 7 0 0 1 7-7h20a7 7 0 0 1 7 7v2H5z" fill="#000" opacity="0.12" />
      <path d="M16.5 15v23M27.5 15v23M5 22.7h34M5 30.3h34" stroke="#fff" :stroke-opacity="face ? 0.22 : 0.45" stroke-width="1.3" />
      <rect v-if="!face" x="16.5" y="22.7" width="11" height="7.6" rx="1.2" fill="#fff" :stroke="dark('green')" stroke-width="0.9" />
      <path d="M10.5 9.6h14" stroke="#fff" stroke-opacity="0.55" stroke-width="1.6" stroke-linecap="round" />
    </g>

    <!-- 漏斗：蓝色，带开口的立体锥体 -->
    <g v-else-if="kind === 'funnel'">
      <path
        d="M5.5 11C5.5 13.5 7 15.5 9 17.6L19.5 29v12.2c0 1.6 1.5 2.4 2.9 1.7l4.6-2.3c1-.5 1.5-1.4 1.5-2.5V29L39 17.6c2-2.1 3.5-4.1 3.5-6.6z"
        :fill="fill('blue', 'h')"
      />
      <ellipse cx="24" cy="11" rx="18.5" ry="5.2" :fill="light('blue')" />
      <ellipse cx="24" cy="11.5" rx="15.4" ry="3.5" :fill="dark('blue')" />
      <path d="M11 19.5l8 8.6" stroke="#fff" stroke-opacity="0.5" stroke-width="2" stroke-linecap="round" />
    </g>

    <!-- 磁铁：红色 U 形 + 银色磁极 -->
    <g v-else-if="kind === 'magnet'">
      <g transform="translate(2 2)">
        <path d="M8 7h10v17a6 6 0 0 0 12 0V7h10v17a16 16 0 0 1-32 0z" :fill="dark('red')" />
        <path d="M8 7h10v7H8zM30 7h10v7H30z" :fill="dark('silver')" />
      </g>
      <path d="M8 7h10v17a6 6 0 0 0 12 0V7h10v17a16 16 0 0 1-32 0z" :fill="fill('red')" />
      <path d="M8 9a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v5H8zM30 9a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v5H30z" :fill="fill('silver', 'v')" />
      <path d="M11.2 17v7a12.8 12.8 0 0 0 3.6 8.9" stroke="#fff" stroke-opacity="0.45" stroke-width="2" fill="none" stroke-linecap="round" />
    </g>

    <!-- 求和硬币：黄色，内圈凹进去 + 浮雕 Σ -->
    <g v-else-if="kind === 'sigma'">
      <circle cx="26" cy="26" r="18" :fill="dark('yellow')" />
      <circle cx="23" cy="23" r="18" :fill="fill('yellow')" />
      <circle cx="23" cy="23" r="13.5" :fill="fill('yellow', 'r')" />
      <path d="M29.6 15.8H17.6l7 7.2-7 7.2h12" stroke="#fff" stroke-opacity="0.6" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round" />
      <path d="M29 15H17l7 8-7 8h12" stroke="#a36d06" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round" />
      <path d="M9.6 17.5a14.6 14.6 0 0 1 7.8-7.9" stroke="#fff" stroke-opacity="0.65" stroke-width="2" fill="none" stroke-linecap="round" />
    </g>

    <!-- 拼图：天蓝 -->
    <g v-else-if="kind === 'puzzle'">
      <path d="M11 13h8a5 5 0 1 1 10 0h8v8a5 5 0 1 1 0 10v8H29a5 5 0 1 0-10 0h-8z" :fill="dark('sky')" transform="translate(2.5 2.5)" />
      <path d="M11 13h8a5 5 0 1 1 10 0h8v8a5 5 0 1 1 0 10v8H29a5 5 0 1 0-10 0h-8z" :fill="fill('sky')" />
      <path d="M14 16.5v7" stroke="#fff" stroke-opacity="0.5" stroke-width="2" stroke-linecap="round" />
      <path d="M21.6 11.2a2.6 2.6 0 0 1 3.4-1.3" stroke="#fff" stroke-opacity="0.55" stroke-width="1.6" fill="none" stroke-linecap="round" />
    </g>

    <!-- 火箭：粉色 -->
    <g v-else-if="kind === 'rocket'">
      <g transform="translate(17.6 30.4) rotate(45)">
        <path d="M0 0C-4.2 0-5.4 6 0 13.5 5.4 6 4.2 0 0 0z" :fill="fill('orange', 'v')" />
        <path d="M0 1.2C-2.2 1.2-2.8 4.6 0 8.6 2.8 4.6 2.2 1.2 0 1.2z" :fill="light('yellow')" />
      </g>
      <path d="M14 24l-6-1 6-7 6 2zM24 34l1 6 7-6-2-6z" :fill="dark('pink')" />
      <path d="M33 9c6-1 7 0 6 6-1 7-7 15-15 19l-10-10C18 16 26 10 33 9z" :fill="fill('pink')" />
      <path d="M33 9c6-1 7 0 6 6-.3 2-1 4.2-2 6.4C34.5 16 31 12.5 26.6 11 28.8 9.9 31 9.3 33 9z" fill="#fff" opacity="0.18" />
      <path d="M21.5 17C24.5 14 28 12 31.5 11.2" stroke="#fff" stroke-opacity="0.5" stroke-width="1.8" fill="none" stroke-linecap="round" />
      <circle cx="29.5" cy="18.5" r="4.8" :fill="fill('silver', 'v')" />
      <circle cx="29.5" cy="18.5" r="3" :fill="fill('blue')" />
      <circle cx="28.5" cy="17.4" r="1" fill="#fff" opacity="0.9" />
    </g>

    <!-- 文件夹：黄色，里面露出一张纸 -->
    <g v-else-if="kind === 'folder'">
      <path d="M5 13a4 4 0 0 1 4-4h8.3a3 3 0 0 1 2.2 1l3 3H39a4 4 0 0 1 4 4v19a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4z" :fill="dark('yellow')" />
      <rect x="11" y="12.5" width="25" height="19" rx="2.2" :fill="fill('silver', 'v')" transform="rotate(-5 23.5 22)" />
      <path d="M15 18.2l14-1.2M15.4 22.4l9.5-.8" stroke="#c5c8d2" stroke-width="1.5" stroke-linecap="round" />
      <path d="M4.2 21a3 3 0 0 1 3-3.2h33.6a3 3 0 0 1 3 3.2L42.6 36.7a4 4 0 0 1-4 3.3H9.4a4 4 0 0 1-4-3.3z" :fill="fill('yellow', 'v')" />
      <path d="M7.6 20.4h32.8" stroke="#fff" stroke-opacity="0.6" stroke-width="1.4" stroke-linecap="round" />
      <rect v-if="!face" x="12" y="26" width="12" height="3" rx="1.5" fill="#fff" opacity="0.75" />
    </g>

    <!-- 皇冠：紫色 + 金色宝珠 -->
    <g v-else-if="kind === 'crown'">
      <path d="M8 33L5 13l11 8 8-12 8 12 11-8-3 20z" :fill="fill('purple', 'v')" :stroke="fill('purple', 'v')" stroke-width="2.4" stroke-linejoin="round" />
      <path d="M9.4 17.5l1.7 11.6" stroke="#fff" stroke-opacity="0.45" stroke-width="1.8" stroke-linecap="round" />
      <rect x="7" y="32" width="34" height="7.5" rx="2.6" :fill="dark('purple')" />
      <path d="M10 34.2h28" stroke="#fff" stroke-opacity="0.25" stroke-width="1.2" stroke-linecap="round" />
      <circle cx="5" cy="12.5" r="2.7" :fill="fill('yellow')" />
      <circle cx="24" cy="8.5" r="2.7" :fill="fill('yellow')" />
      <circle cx="43" cy="12.5" r="2.7" :fill="fill('yellow')" />
      <circle cx="16" cy="27.5" r="2.3" :fill="fill('yellow')" />
      <circle cx="32" cy="27.5" r="2.3" :fill="fill('yellow')" />
      <circle cx="24" cy="25.5" r="2.9" :fill="fill('pink')" />
      <circle cx="23.1" cy="24.6" r="0.9" fill="#fff" opacity="0.85" />
    </g>

    <!-- 纸飞机：橙色 -->
    <g v-else-if="kind === 'plane'">
      <path d="M5 22L43 6 33 42l-9-11z" :fill="fill('orange')" />
      <path d="M24 31l19-25-23 21z" :fill="dark('orange')" />
      <path d="M20 27l4 4-5 8z" fill="#a24a07" />
      <path d="M9 21.2L38.5 8.8" stroke="#fff" stroke-opacity="0.5" stroke-width="1.5" stroke-linecap="round" />
    </g>

    <!-- 饼图：带厚度，橙色一块被拉出来 -->
    <g v-else-if="kind === 'pie'">
      <circle cx="22" cy="29" r="16" :fill="dark('purple')" />
      <circle cx="22" cy="26" r="16" :fill="fill('purple')" />
      <path d="M22 26V10a16 16 0 0 0-11.3 27.3z" :fill="light('purple')" opacity="0.75" />
      <path d="M26 9a16 16 0 0 1 16 16H26z" :fill="dark('orange')" />
      <path d="M26 6a16 16 0 0 1 16 16H26z" :fill="fill('orange')" />
      <path d="M11.5 16.5a13 13 0 0 1 5-4.6" stroke="#fff" stroke-opacity="0.55" stroke-width="1.8" fill="none" stroke-linecap="round" />
    </g>

    <!-- 火花：切面宝石感 -->
    <g v-else-if="kind === 'spark'" stroke-linejoin="round">
      <path d="M24 4l4.5 13.5L42 22l-13.5 4.5L24 40l-4.5-13.5L6 22l13.5-4.5z" :fill="fill('yellow')" :stroke="fill('yellow')" stroke-width="1.6" />
      <path d="M24 22h18l-13.5 4.5zM24 22v18l-4.5-13.5zM24 22H6l13.5-4.5zM24 22V4l4.5 13.5z" fill="#b87a00" opacity="0.14" />
      <path d="M38 34l1.6 4.4L44 40l-4.4 1.6L38 46l-1.6-4.4L32 40l4.4-1.6z" :fill="fill('pink')" :stroke="fill('pink')" stroke-width="1" />
      <path d="M38 40h6l-4.4 1.6zM38 40v6l-1.6-4.4zM38 40h-6l4.4-1.6zM38 40v-6l1.6 4.4z" fill="#000" opacity="0.1" />
    </g>

    <!-- 扫帚：清洗 -->
    <g v-else-if="kind === 'broom'">
      <path d="M33 6l4 3-11 15-4-3z" :fill="fill('wood', 'h')" />
      <path d="M20 20l9 7c-2 8-8 14-17 15 3-3 4-6 4-9-4 1-7 1-10 0 6-3 10-8 14-13z" :fill="fill('yellow')" />
      <path d="M21 30.5l-4.5 6M24.5 32l-5 7" stroke="#c98a0a" stroke-opacity="0.45" stroke-width="1.3" stroke-linecap="round" />
      <path d="M20 20l9 7-2 3-9-7z" :fill="fill('orange')" />
    </g>

    <!-- 积木：重塑 -->
    <g v-else-if="kind === 'blocks'">
      <rect x="8" y="26" width="16" height="16" rx="3.5" :fill="dark('blue')" />
      <rect x="6" y="24" width="16" height="16" rx="3.5" :fill="fill('blue')" />
      <rect x="26" y="26" width="16" height="16" rx="3.5" :fill="dark('red')" />
      <rect x="24" y="24" width="16" height="16" rx="3.5" :fill="fill('red')" />
      <g transform="rotate(12 23 15)">
        <rect x="17" y="9" width="16" height="16" rx="3.5" :fill="dark('green')" />
        <rect x="15" y="7" width="16" height="16" rx="3.5" :fill="fill('green')" />
        <path d="M18.5 10.3h7" stroke="#fff" stroke-opacity="0.5" stroke-width="1.6" stroke-linecap="round" />
      </g>
      <path d="M9.5 27.3h7M27.5 27.3h7" stroke="#fff" stroke-opacity="0.5" stroke-width="1.6" stroke-linecap="round" />
    </g>

    <!-- 计算器：计算 -->
    <g v-else-if="kind === 'calc'">
      <rect x="11.5" y="6.5" width="28" height="38" rx="6" :fill="dark('ink')" />
      <rect x="9" y="4" width="28" height="38" rx="6" :fill="fill('ink')" />
      <rect x="13.5" y="8.5" width="19" height="9" rx="2.4" :fill="fill('mint', 'v')" />
      <rect x="22" y="12" width="8" height="2.4" rx="1.2" fill="#1c7f45" opacity="0.55" />
      <g :fill="fill('silver', 'v')">
        <circle cx="17" cy="24" r="2.3" /><circle cx="23" cy="24" r="2.3" />
        <circle cx="17" cy="31" r="2.3" /><circle cx="23" cy="31" r="2.3" />
        <circle cx="17" cy="37" r="2.3" /><circle cx="23" cy="37" r="2.3" />
      </g>
      <g :fill="fill('orange', 'v')">
        <circle cx="29" cy="24" r="2.3" /><circle cx="29" cy="31" r="2.3" /><circle cx="29" cy="37" r="2.3" />
      </g>
      <path d="M13 6.8h8" stroke="#fff" stroke-opacity="0.22" stroke-width="1.4" stroke-linecap="round" />
    </g>

    <!-- 代码：自定义 -->
    <g v-else-if="kind === 'code'">
      <rect x="7.5" y="11.5" width="38" height="30" rx="6" fill="#0b0b0c" />
      <rect x="5" y="9" width="38" height="30" rx="6" :fill="fill('ink')" />
      <circle cx="10" cy="14" r="1.5" fill="#ff6b62" />
      <circle cx="14.5" cy="14" r="1.5" fill="#f9c647" />
      <circle cx="19" cy="14" r="1.5" fill="#38c172" />
      <path d="M18 22.5l-4.5 4.5 4.5 4.5M30 22.5l4.5 4.5-4.5 4.5M26 20.5l-4 13" stroke="#5fd39a" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round" />
    </g>
    <!-- 奶酪：洞洞 = 缺失值 -->
    <g v-else-if="kind === 'cheese'">
      <path d="M6.5 23H44.5V39a4 4 0 0 1-4 4H10.5a4 4 0 0 1-4-4z" :fill="dark('yellow')" />
      <path d="M5 21H43V37a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4z" :fill="fill('yellow', 'v')" />
      <path d="M5 21L35.6 8.4a2.2 2.2 0 0 1 2.9 1.2L43 21z" :fill="light('yellow')" />
      <path d="M7.5 21.2H40.5" stroke="#fff" stroke-opacity="0.7" stroke-width="1.3" stroke-linecap="round" />
      <ellipse cx="30.5" cy="15.2" rx="2.6" ry="1.4" fill="#f2c24c" />
      <g fill="#e2a21e">
        <circle cx="10.6" cy="35.4" r="2.5" />
        <circle cx="38.2" cy="27.5" r="1.8" />
        <circle cx="36.6" cy="36" r="1.3" />
      </g>
      <path d="M43 28.4a2.6 2.6 0 0 0 0 5.2z" fill="#e2a21e" />
    </g>

    <!-- 甜甜圈：粉色糖霜 + 彩色糖针 -->
    <g v-else-if="kind === 'donut'">
      <ellipse cx="24" cy="29.5" rx="19" ry="15" :fill="dark('dough')" />
      <ellipse cx="24" cy="27" rx="19" ry="15" :fill="fill('dough', 'v')" />
      <path
        d="M8 24C8 15.5 16 10.5 24 10.5S40 15.5 40 24c0 3.8-1.5 6.4-3.5 7.4-1.5.8-2 3-3.5 3-1.7 0-1.8-1.9-3.5-1.5-2.5.6-3 3.5-5 3.5-2.1 0-1.9-3-4.5-3.3-2.2-.2-3 1.8-4.8 1.3-1.8-.5-1.4-2.9-3.2-3.5C9.5 30 8 27.5 8 24z"
        :fill="fill('pink')"
      />
      <ellipse cx="24" cy="20.4" rx="5.6" ry="3.3" :fill="dark('pink')" opacity="0.55" />
      <ellipse cx="24" cy="20.8" rx="4.3" ry="2.4" :fill="dark('dough')" />
      <path d="M12.6 19.2C14.6 15 18 13 21.5 12.4" stroke="#fff" stroke-opacity="0.6" stroke-width="1.6" fill="none" stroke-linecap="round" />
      <g stroke-width="1.3" stroke-linecap="round">
        <path d="M15.5 22.5l1.6-1" stroke="#f9c647" />
        <path d="M19.4 15.6l1.8.4" stroke="#ffffff" />
        <path d="M28.6 15.2l1.4 1.2" stroke="#8fb4ff" />
        <path d="M33.6 20.4l1.2-1.4" stroke="#74d99e" />
        <path d="M35.4 25.4l1.7.5" stroke="#f9c647" />
        <path d="M11.4 26.2l.6-1.6" stroke="#8fb4ff" />
        <path d="M24.6 16l.2-1.6" stroke="#74d99e" />
      </g>
    </g>

    <!-- 云朵 -->
    <g v-else-if="kind === 'cloud'">
      <path d="M13 38A8 8 0 0 1 12.5 22A10.5 10.5 0 0 1 33.5 22.1A8 8 0 0 1 35 38Z" :fill="dark('cloud')" transform="translate(1.5 2)" />
      <path d="M13 38A8 8 0 0 1 12.5 22A10.5 10.5 0 0 1 33.5 22.1A8 8 0 0 1 35 38Z" :fill="fill('cloud', 'v')" />
      <path d="M16 15.8a8 8 0 0 1 6.5-3.2" stroke="#fff" stroke-width="1.8" fill="none" stroke-linecap="round" />
    </g>

    <!-- 表情 -->
    <g v-if="face" :transform="`translate(${faceAt[0]} ${faceAt[1]}) scale(${faceAt[2]})`">
      <ellipse cx="-7.4" cy="3.2" rx="2.1" ry="1.25" fill="#ff6f8e" opacity="0.42" />
      <ellipse cx="7.4" cy="3.2" rx="2.1" ry="1.25" fill="#ff6f8e" opacity="0.42" />
      <g v-if="eyes === 'open' || eyes === 'wink'" class="animate-blink" :style="{ transformBox: 'fill-box', transformOrigin: 'center', animationDelay: blinkDelay }">
        <g :style="{ transform: `translate(${look.x}px, ${look.y}px)`, transition: 'transform .18s ease-out' }">
          <ellipse cx="-4.2" cy="0" rx="1.6" ry="2.1" :fill="INK" />
          <circle cx="-4.7" cy="-0.7" r="0.6" fill="#fff" />
          <template v-if="eyes === 'open'">
            <ellipse cx="4.2" cy="0" rx="1.6" ry="2.1" :fill="INK" />
            <circle cx="3.7" cy="-0.7" r="0.6" fill="#fff" />
          </template>
        </g>
      </g>
      <g fill="none" :stroke="INK" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round">
        <path v-if="eyes === 'wink'" d="M5.6 -1.5L3 0L5.6 1.5" />
        <template v-else-if="eyes === 'up'">
          <path d="M-5.8 0.6Q-4.2 -1.6 -2.6 0.6" />
          <path d="M2.6 0.6Q4.2 -1.6 5.8 0.6" />
        </template>
        <template v-else-if="eyes === 'down'">
          <path d="M-5.8 -0.2Q-4.2 1.6 -2.6 -0.2" />
          <path d="M2.6 -0.2Q4.2 1.6 5.8 -0.2" />
        </template>
      </g>
      <path v-if="face === 'smile'" d="M-1.8 2.7Q0 4.6 1.8 2.7" fill="none" :stroke="INK" stroke-width="1.1" stroke-linecap="round" />
      <g v-else-if="face === 'wink'">
        <path d="M-0.2 3.4Q-0.4 6.2 0.9 6.2Q2.3 6.2 1.8 3.2Z" fill="#ff6f8e" />
        <path d="M-2 2.6Q0 4.2 2 2.6" fill="none" :stroke="INK" stroke-width="1.1" stroke-linecap="round" />
      </g>
      <g v-else-if="face === 'happy'">
        <path d="M-2.3 2.4H2.3Q2.2 5.8 0 5.8Q-2.2 5.8 -2.3 2.4Z" :fill="INK" />
        <ellipse cx="0" cy="4.9" rx="1.15" ry="0.75" fill="#ff6f8e" />
      </g>
      <ellipse v-else-if="face === 'wow'" cx="0" cy="3.7" rx="1" ry="1.3" :fill="INK" />
      <g v-else-if="face === 'sleepy'">
        <ellipse cx="0" cy="3.4" rx="0.75" ry="0.85" :fill="INK" />
        <text x="7" y="-4" font-size="4.6" font-weight="700" :fill="INK" opacity="0">
          z
          <animate attributeName="opacity" values="0;0.8;0" dur="2.6s" repeatCount="indefinite" />
          <animateTransform attributeName="transform" type="translate" values="0 0;2.5 -3.5" dur="2.6s" repeatCount="indefinite" />
        </text>
      </g>
    </g>
  </svg>
</template>
