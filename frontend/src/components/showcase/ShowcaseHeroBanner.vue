<script setup lang="ts">
/**
 * 首屏全屏 Banner（100svh 全屏 3D 珠光丝绸光雕 + 8 款精简错落的多彩 IP 群像 + 不对称悬浮功能卡 + 居中主标题）：
 * - 保留「无框裸感、高低错落、自然设计感」，同时克制控制数量为 8 只各不相同的核心小团子，给画面留出充裕呼吸感；
 * - 鼠标多层景深视差 + 轻盈错拍浮动 + 悬停弹跳/点击抖动反馈。
 */
import { ArrowRight, ChevronDown, Play, Sparkles } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import Mascot from '@/components/brand/Mascot.vue'
import BannerSilkWave from '@/components/showcase/BannerSilkWave.vue'

defineProps<{
  ctaLabel: string
}>()

const emit = defineEmits<{
  enter: []
  explore: []
}>()

const ready = ref(false)
const pointer = ref({ x: 0, y: 0 })
const scrollRatio = ref(0)
const hoppedId = ref<string | null>(null)
let hopTimer: number | null = null

function triggerHop(id: string) {
  hoppedId.value = null
  requestAnimationFrame(() => {
    hoppedId.value = id
  })
  if (hopTimer) window.clearTimeout(hopTimer)
  hopTimer = window.setTimeout(() => {
    hoppedId.value = null
    hopTimer = null
  }, 560)
}

function onPointerMove(event: PointerEvent) {
  const nx = event.clientX / Math.max(window.innerWidth, 1) - 0.5
  const ny = event.clientY / Math.max(window.innerHeight, 1) - 0.5
  pointer.value = { x: nx, y: ny }
}

function onPointerLeave() {
  pointer.value = { x: 0, y: 0 }
}

function onScroll() {
  const threshold = Math.max(window.innerHeight * 0.72, 520)
  scrollRatio.value = Math.max(0, Math.min(1, window.scrollY / threshold))
}

function floatStyle(side: -1 | 1, depth: number, baseRot: number) {
  const px = pointer.value.x * 24 * depth
  const py = pointer.value.y * 16 * depth
  const sx = side * scrollRatio.value * (46 + depth * 26)
  const sy = -scrollRatio.value * (16 + depth * 18)
  const rot = baseRot + side * scrollRatio.value * 4.5
  const opacity = Math.max(0, 1 - scrollRatio.value * 1.15)
  return {
    transform: `translate3d(${(px + sx).toFixed(1)}px, ${(py + sy).toFixed(1)}px, 0) rotate(${rot.toFixed(1)}deg)`,
    opacity,
  }
}

const silkOpacity = computed(() => Math.max(0, 1 - scrollRatio.value * 1.15))
const scrollCueOpacity = computed(() => (ready.value ? Math.max(0, 1 - scrollRatio.value * 3.2) : 0))

onMounted(() => {
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      ready.value = true
    })
  })
  window.addEventListener('scroll', onScroll, { passive: true })
  onScroll()
})

onBeforeUnmount(() => {
  window.removeEventListener('scroll', onScroll)
  if (hopTimer) window.clearTimeout(hopTimer)
})
</script>

<template>
  <section
    id="banner"
    class="banner-root relative flex min-h-[100svh] w-full flex-col items-center justify-center overflow-hidden bg-gradient-to-b from-[#faf9f6] via-[#fcfcfb] to-white px-4 pb-16 pt-24 sm:px-6"
    :class="{ 'is-ready': ready }"
    @pointermove="onPointerMove"
    @pointerleave="onPointerLeave"
  >
    <!-- 首屏全屏 3D 珠光丝绸光雕 + 马卡龙柔光氛围晕染 -->
    <div
      class="pointer-events-none absolute inset-0 transition-opacity duration-200"
      :style="{ opacity: silkOpacity }"
      aria-hidden="true"
    >
      <div class="absolute -left-24 top-[8%] h-[460px] w-[520px] rounded-full bg-[#ffe6d5]/55 blur-[104px]" />
      <div class="absolute left-[46%] top-[14%] h-[400px] w-[600px] -translate-x-1/2 rounded-full bg-gradient-to-r from-[#dcf7ec]/48 via-[#e8efff]/45 to-[#f7e4ff]/52 blur-[98px]" />
      <div class="absolute -right-24 top-[22%] h-[460px] w-[520px] rounded-full bg-[#ffe0ec]/55 blur-[104px]" />
      <BannerSilkWave />
      <div class="absolute inset-x-0 bottom-0 h-32 bg-gradient-to-t from-white via-white/80 to-transparent" />
      <div class="absolute inset-0 bg-[radial-gradient(ellipse_44%_40%_at_50%_50%,rgba(255,255,255,0.82)_0%,rgba(255,255,255,0.34)_56%,transparent_80%)]" />
    </div>

    <!-- 克制精简的错落 IP 群像 + 对角错位悬浮卡（留白舒展，打破对称） -->
    <div class="pointer-events-none absolute inset-0 mx-auto max-w-[1560px]" aria-hidden="true">
      <!-- 稀疏彩色微星点 -->
      <span class="spark-dot left-[25%] top-[23%] hidden bg-[#f29b72]/65 xl:block" />
      <span class="spark-dot right-[23%] top-[18%] hidden size-2 bg-[#6cb89c]/65 xl:block" />
      <span class="spark-dot left-[24%] bottom-[21%] hidden size-1.5 bg-[#9d8ce8]/65 xl:block" />
      <span class="spark-dot right-[26%] bottom-[29%] hidden bg-[#eb8ca8]/70 xl:block" />

      <!-- 1. 左上功能卡（较高）：流程一键重跑 + 趴在右上角的香芋墨镜团子 -->
      <div class="bloom-el bloom-1 absolute left-[3%] top-[13.5%] hidden lg:block xl:left-[5%]">
        <div class="float-layer" :style="floatStyle(-1, 0.85, -4.5)">
          <div class="idle-drift-a relative w-[238px] rounded-[22px] border border-white/90 bg-white/92 p-4 shadow-[0_22px_50px_-22px_rgba(98,101,220,0.22),0_4px_14px_rgba(15,14,12,0.05),inset_0_1px_0_rgba(255,255,255,0.9)] ring-1 ring-[#e8e5ef]/90 backdrop-blur-md">
            <button
              type="button"
              class="mascot-free pointer-events-auto absolute -right-4 -top-7 rotate-[15deg]"
              :class="{ 'is-hopping': hoppedId === 'taro-coolcat' }"
              @click="triggerHop('taro-coolcat')"
            >
              <Mascot variant="taro-coolcat" :size="58" :shadow="false" class="drop-shadow-[0_10px_14px_rgba(131,112,214,0.34)]" />
            </button>
            <div class="flex items-center justify-between gap-2 pr-7">
              <span class="truncate text-[12px] font-semibold text-ink">顺达月结流程 · v3</span>
            </div>
            <div class="mt-3 flex items-center gap-1.5 text-[10.5px] font-medium">
              <span class="rounded-md bg-[#fff3ec] px-2 py-1 text-[#c25b2a]">读表</span>
              <span class="flex-1 border-t border-dashed border-[#c5c8f5]" />
              <span class="rounded-md bg-[#f1f2ff] px-2 py-1 text-[#5255c8]">核算</span>
              <span class="flex-1 border-t border-dashed border-[#9edec0]" />
              <span class="rounded-md bg-[#ebf9f2] px-2 py-1 text-[#158752]">交付</span>
            </div>
            <div class="mt-3 flex items-center justify-between text-[11px] text-mute">
              <span class="inline-flex items-center gap-1 rounded-full bg-ink px-2 py-0.5 text-[10px] font-medium text-white">
                <Play class="size-2.5 fill-current" /> 一键重跑
              </span>
              <span class="font-mono font-medium text-[#158752]">12s 完成</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 2. 左中前景：单独自由斜浮的大号草莓甜心（与右侧留白形成不对称张力） -->
      <div class="bloom-el bloom-7 absolute left-[3.5%] top-[47%] hidden lg:block xl:left-[5.5%]">
        <div class="float-layer" :style="floatStyle(-1, 1.65, 13)">
          <button
            type="button"
            class="idle-drift-d mascot-free pointer-events-auto"
            :class="{ 'is-hopping': hoppedId === 'rose-sweetie' }"
            @click="triggerHop('rose-sweetie')"
          >
            <Mascot variant="rose-sweetie" :size="78" :shadow="false" class="drop-shadow-[0_18px_24px_rgba(235,140,168,0.36)]" />
          </button>
        </div>
      </div>

      <!-- 3. 左下偏内：对话即处理胶囊卡 + 跳出边缘的鼠尾草绿吐舌团子 -->
      <div class="bloom-el bloom-2 absolute bottom-[14%] left-[7%] hidden lg:block xl:left-[10%]">
        <div class="float-layer" :style="floatStyle(-1, 1.1, 2.6)">
          <div class="idle-drift-b mascot-pill pointer-events-auto relative flex items-center gap-2.5 rounded-full border border-white/90 bg-white/94 py-2 pl-[60px] pr-5 shadow-[0_20px_44px_-20px_rgba(82,161,132,0.28),0_4px_12px_rgba(15,14,12,0.04)] ring-1 ring-[#dff1e9] backdrop-blur-md">
            <button
              type="button"
              class="mascot-free absolute -left-2 -top-2.5 -rotate-[10deg]"
              :class="{ 'is-hopping': hoppedId === 'sage-blep' }"
              @click="triggerHop('sage-blep')"
            >
              <Mascot variant="sage-blep" :size="58" :shadow="false" class="drop-shadow-[0_10px_14px_rgba(82,161,132,0.34)]" />
            </button>
            <div class="leading-tight">
              <div class="text-[12.5px] font-semibold text-ink">对话即处理</div>
              <div class="mt-0.5 text-[11px] text-mute">“按上月口径出表”</div>
            </div>
          </div>
        </div>
      </div>

      <!-- 4. 右上功能卡（刻意错落压低）：公式库 + 趴在左上角的燕麦叼糖小酷 -->
      <div class="bloom-el bloom-3 absolute right-[3%] top-[21.5%] hidden lg:block xl:right-[5%]">
        <div class="float-layer" :style="floatStyle(1, 0.9, 4.2)">
          <div class="idle-drift-b relative w-[240px] rounded-[22px] border border-white/90 bg-white/92 p-4 shadow-[0_22px_50px_-22px_rgba(222,128,85,0.22),0_4px_14px_rgba(15,14,12,0.05),inset_0_1px_0_rgba(255,255,255,0.9)] ring-1 ring-[#efe7dc]/90 backdrop-blur-md">
            <button
              type="button"
              class="mascot-free pointer-events-auto absolute -left-5 -top-8 -rotate-[14deg]"
              :class="{ 'is-hopping': hoppedId === 'oat-coolpop' }"
              @click="triggerHop('oat-coolpop')"
            >
              <Mascot variant="oat-coolpop" :size="58" :shadow="false" class="drop-shadow-[0_10px_14px_rgba(194,167,132,0.38)]" />
            </button>
            <div class="flex items-center justify-end gap-1.5 text-[11.5px]">
              <span class="font-semibold text-ink">公式库 · 阶梯计费</span>
              <span class="inline-flex items-center gap-1 rounded-full bg-[#fff1ea] px-2 py-0.5 text-[10px] font-medium text-[#d4602c]">
                <Sparkles class="size-2.5" /> AI 试算
              </span>
            </div>
            <div class="mt-2.5 rounded-lg bg-[#faf8f4] px-2.5 py-1.5 font-mono text-[10.5px] text-ink ring-1 ring-[#f0ece4]">
              =IF(重&gt;3, 8+(重-3)*2.5, 8)
            </div>
            <div class="mt-2.5 flex items-center justify-between text-[11px] tabular-nums">
              <span class="text-mute">试算 6 kg</span>
              <span class="font-mono font-semibold text-[#158752]">¥ 15.50</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 5. 右下偏高：记住客户规则胶囊卡 + 探出右侧的雾霾冰蓝吃糖笑眯 -->
      <div class="bloom-el bloom-4 absolute bottom-[22%] right-[6%] hidden lg:block xl:right-[8%]">
        <div class="float-layer" :style="floatStyle(1, 1.15, -3.4)">
          <div class="idle-drift-a mascot-pill pointer-events-auto relative flex items-center gap-2.5 rounded-full border border-white/90 bg-white/94 py-2 pl-5 pr-[60px] shadow-[0_20px_44px_-20px_rgba(111,150,232,0.3),0_4px_12px_rgba(15,14,12,0.04)] ring-1 ring-[#dde7fc] backdrop-blur-md">
            <div class="text-right leading-tight">
              <div class="text-[12.5px] font-semibold text-ink">记住客户规则</div>
              <div class="mt-0.5 text-[11px] text-mute">列名别名自动对齐</div>
            </div>
            <button
              type="button"
              class="mascot-free absolute -right-2 -top-2.5 rotate-[11deg]"
              :class="{ 'is-hopping': hoppedId === 'mist-candy' }"
              @click="triggerHop('mist-candy')"
            >
              <Mascot variant="mist-candy" :size="58" :shadow="false" class="drop-shadow-[0_10px_14px_rgba(111,150,232,0.34)]" />
            </button>
          </div>
        </div>
      </div>

      <!-- 6. 右下偏中自由斜浮：叼棒棒糖靛蓝团子（完整露出，与左中草莓甜心形成对角呼应） -->
      <div class="bloom-el bloom-10 absolute bottom-[12%] right-[23%] hidden lg:block xl:right-[25%]">
        <div class="float-layer" :style="floatStyle(1, 1.25, -13)">
          <button
            type="button"
            class="idle-peek-a mascot-free pointer-events-auto"
            :class="{ 'is-hopping': hoppedId === 'indigo-lollipop' }"
            @click="triggerHop('indigo-lollipop')"
          >
            <Mascot variant="indigo-lollipop" :size="66" :shadow="false" class="drop-shadow-[0_14px_20px_rgba(98,101,220,0.34)]" />
          </button>
        </div>
      </div>
    </div>

    <!-- 正中主标题与主行动按钮 -->
    <div class="relative z-10 mx-auto max-w-[740px] text-center">
      <!-- 居中不对称双宝：经典主角墨墨 + 右侧歪头紧贴的蜜桃俏皮虎牙 -->
      <div class="hero-center-1 relative inline-flex items-end justify-center">
        <div
          class="pointer-events-none absolute -inset-x-8 -inset-y-4 rounded-full bg-gradient-to-r from-[#ffd6bf]/42 via-[#d5f5e5]/36 to-[#f5d0ff]/42 blur-2xl"
          aria-hidden="true"
        />
        <button
          type="button"
          class="mascot-free relative z-10 -rotate-[4deg]"
          :class="{ 'is-hopping': hoppedId === 'blob' }"
          @click="triggerHop('blob')"
        >
          <Mascot variant="blob" :size="82" :shadow="false" class="drop-shadow-[0_16px_22px_rgba(17,17,19,0.22)]" />
        </button>
        <button
          type="button"
          class="mascot-free relative z-20 -ml-3.5 mb-0.5 rotate-[15deg]"
          :class="{ 'is-hopping': hoppedId === 'peach-cheeky' }"
          @click="triggerHop('peach-cheeky')"
        >
          <Mascot variant="peach-cheeky" :size="56" :shadow="false" class="drop-shadow-[0_12px_18px_rgba(242,155,114,0.38)]" />
        </button>
      </div>

      <h1 class="hero-center-2 mt-4 text-[34px] font-semibold leading-[1.16] tracking-[-0.03em] text-ink sm:text-[46px] lg:text-[54px]">
        复杂账单，说清一次就够了
      </h1>

      <p class="hero-center-3 mx-auto mt-4 max-w-[540px] text-[15.5px] leading-relaxed text-mute sm:text-[17.5px]">
        丢进表格就能算，跑通自动存流程，下月新表一键复跑。
      </p>

      <div class="hero-center-4 mt-8 flex items-center justify-center">
        <button
          type="button"
          class="group inline-flex items-center gap-2 rounded-full bg-gradient-to-b from-[#1f1f23] to-[#0b0b0c] px-7 py-3.5 text-[15px] font-medium text-white shadow-[0_18px_36px_-12px_rgba(11,11,12,0.48),inset_0_1px_0_rgba(255,255,255,0.16)] transition hover:from-[#2b2b30] hover:to-[#111114] active:scale-[0.98]"
          @click="emit('enter')"
        >
          <span>{{ ctaLabel }}</span>
          <ArrowRight class="size-4 transition-transform duration-200 group-hover:translate-x-0.5" />
        </button>
      </div>
    </div>

    <!-- 首屏底部轻巧的向下滚动引导 -->
    <button
      type="button"
      class="hero-scroll-cue absolute bottom-7 left-1/2 z-10 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-[#e7e5df]/90 bg-white/85 px-4 py-1.5 text-[12px] font-medium text-mute shadow-[0_8px_22px_-12px_rgba(15,14,12,0.16)] backdrop-blur-md transition hover:border-[#d3d0c8] hover:text-ink"
      :style="{ opacity: scrollCueOpacity, pointerEvents: scrollCueOpacity > 0.08 ? 'auto' : 'none' }"
      @click="emit('explore')"
    >
      <span>向下预览工作台</span>
      <ChevronDown class="size-3.5 animate-bounce" />
    </button>
  </section>
</template>

<style scoped>
.spark-dot {
  position: absolute;
  width: 6px;
  height: 6px;
  border-radius: 9999px;
  animation: spark-twinkle 4.4s ease-in-out infinite;
}

@keyframes spark-twinkle {
  0%, 100% { opacity: 0.45; transform: scale(0.9); }
  50% { opacity: 0.95; transform: scale(1.25); }
}

.hero-center-1,
.hero-center-2,
.hero-center-3,
.hero-center-4 {
  opacity: 0;
  will-change: transform, opacity, filter;
}

.hero-center-1 {
  transform: translate3d(0, 18px, 0) scale(0.86);
  transition: opacity 0.65s cubic-bezier(0.16, 1, 0.3, 1) 0.08s, transform 0.75s cubic-bezier(0.34, 1.56, 0.64, 1) 0.08s;
}
.hero-center-2 {
  transform: translate3d(0, 22px, 0);
  filter: blur(8px);
  transition: opacity 0.75s cubic-bezier(0.16, 1, 0.3, 1) 0.16s, transform 0.8s cubic-bezier(0.16, 1, 0.3, 1) 0.16s, filter 0.75s cubic-bezier(0.16, 1, 0.3, 1) 0.16s;
}
.hero-center-3 {
  transform: translate3d(0, 16px, 0);
  filter: blur(5px);
  transition: opacity 0.75s cubic-bezier(0.16, 1, 0.3, 1) 0.25s, transform 0.8s cubic-bezier(0.16, 1, 0.3, 1) 0.25s, filter 0.75s cubic-bezier(0.16, 1, 0.3, 1) 0.25s;
}
.hero-center-4 {
  transform: translate3d(0, 14px, 0);
  transition: opacity 0.75s cubic-bezier(0.16, 1, 0.3, 1) 0.34s, transform 0.8s cubic-bezier(0.16, 1, 0.3, 1) 0.34s;
}

.is-ready .hero-center-1,
.is-ready .hero-center-2,
.is-ready .hero-center-3,
.is-ready .hero-center-4 {
  opacity: 1;
  transform: none;
  filter: blur(0);
}

.hero-scroll-cue {
  transition:
    opacity 0.55s cubic-bezier(0.22, 1, 0.36, 1),
    border-color 0.2s ease,
    color 0.2s ease;
}

/* 开场从中心自然错拍散开 */
.bloom-el {
  opacity: 0;
  will-change: transform, opacity, filter;
  filter: blur(6px);
}
.bloom-1 {
  transform: translate3d(84px, 46px, 0) scale(0.82);
  transition: opacity 0.85s cubic-bezier(0.16, 1, 0.3, 1) 0.18s, transform 1s cubic-bezier(0.16, 1, 0.3, 1) 0.18s, filter 0.85s ease 0.18s;
}
.bloom-2 {
  transform: translate3d(92px, -22px, 0) scale(0.82);
  transition: opacity 0.85s cubic-bezier(0.16, 1, 0.3, 1) 0.28s, transform 1s cubic-bezier(0.16, 1, 0.3, 1) 0.28s, filter 0.85s ease 0.28s;
}
.bloom-3 {
  transform: translate3d(-86px, 34px, 0) scale(0.82);
  transition: opacity 0.85s cubic-bezier(0.16, 1, 0.3, 1) 0.22s, transform 1s cubic-bezier(0.16, 1, 0.3, 1) 0.22s, filter 0.85s ease 0.22s;
}
.bloom-4 {
  transform: translate3d(-94px, -18px, 0) scale(0.82);
  transition: opacity 0.85s cubic-bezier(0.16, 1, 0.3, 1) 0.32s, transform 1s cubic-bezier(0.16, 1, 0.3, 1) 0.32s, filter 0.85s ease 0.32s;
}
.bloom-7 {
  transform: translate3d(0, 26px, 0) scale(0.74);
  transition: opacity 0.9s cubic-bezier(0.16, 1, 0.3, 1) 0.26s, transform 1.05s cubic-bezier(0.34, 1.56, 0.64, 1) 0.26s, filter 0.85s ease 0.26s;
}
.bloom-10 {
  transform: translate3d(0, 46px, 0) scale(0.86);
  transition: opacity 0.85s cubic-bezier(0.16, 1, 0.3, 1) 0.42s, transform 1s cubic-bezier(0.34, 1.56, 0.64, 1) 0.42s, filter 0.85s ease 0.42s;
}

.is-ready .bloom-el {
  opacity: 1;
  transform: none;
  filter: blur(0);
}

.float-layer {
  transition: transform 0.28s cubic-bezier(0.22, 1, 0.36, 1), opacity 0.22s ease-out;
  will-change: transform, opacity;
}

.idle-drift-a {
  animation: drift-y 6.4s ease-in-out infinite;
}
.idle-drift-b {
  animation: drift-y 7.4s ease-in-out 1.1s infinite;
}
.idle-drift-d {
  animation: drift-sway 6.1s ease-in-out 1.5s infinite;
}
.idle-peek-a {
  animation: peek-bob 5.8s ease-in-out 0.6s infinite;
}

@keyframes drift-y {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-7px); }
}
@keyframes drift-sway {
  0%, 100% { transform: translateY(0) rotate(0deg); }
  50% { transform: translateY(-8px) rotate(-4.5deg); }
}
@keyframes peek-bob {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-9px); }
}

.mascot-free {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  background: transparent;
  border: 0;
  padding: 0;
  transition: transform 0.34s cubic-bezier(0.34, 1.56, 0.64, 1), filter 0.28s ease;
}
.mascot-free:hover {
  transform: translateY(-7px) scale(1.13) rotate(0deg);
}
.mascot-free.is-hopping {
  animation: mascot-hop 0.54s cubic-bezier(0.34, 1.56, 0.64, 1);
}

@keyframes mascot-hop {
  0% { transform: scale(1) translateY(0); }
  25% { transform: scale(1.16, 0.88) translateY(4px); }
  55% { transform: scale(0.94, 1.12) translateY(-16px) rotate(-6deg); }
  80% { transform: scale(1.04, 0.96) translateY(0) rotate(3deg); }
  100% { transform: scale(1) translateY(0) rotate(0deg); }
}

.mascot-pill {
  transition: transform 0.32s cubic-bezier(0.34, 1.56, 0.64, 1), box-shadow 0.28s ease;
}
.mascot-pill:hover {
  transform: translateY(-3px) scale(1.02);
}

@media (prefers-reduced-motion: reduce) {
  .hero-center-1,
  .hero-center-2,
  .hero-center-3,
  .hero-center-4,
  .bloom-el {
    opacity: 1;
    transform: none;
    filter: none;
    transition: none;
  }
  .spark-dot,
  .idle-drift-a,
  .idle-drift-b,
  .idle-drift-d,
  .idle-peek-a {
    animation: none;
  }
}
</style>
