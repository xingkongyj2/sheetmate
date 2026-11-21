<script setup lang="ts">
/**
 * 全屏首屏 Banner（100svh）：
 * - 干净留白的纯白/极浅纸感舞台，不铺 AI 炫彩背景，而是在全屏四角与外沿编排实体感「账单卡片 + 墨墨家族小团子」特效元素；
 * - 打开瞬间：元素从中心轻盈绽放散开落位 + 细线条草图弧线顺滑描绘；
 * - 互动体验：三层景深鼠标微视差 + 向下滚动时元素向左右优雅退场，自然衔接下方纯白页面。
 */
import { ArrowRight, ChevronDown, GitCompareArrows, Play, ShieldCheck, Sparkles } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import Mascot from '@/components/brand/Mascot.vue'
import BannerSilkWave from '@/components/showcase/BannerSilkWave.vue'

defineProps<{
  ctaLabel: string
}>()

const emit = defineEmits<{
  enter: []
  scrollDown: []
}>()

const ready = ref(false)
const pointer = ref({ x: 0, y: 0 })
const scrollRatio = ref(0)

function onPointerMove(event: PointerEvent) {
  const nx = event.clientX / Math.max(window.innerWidth, 1) - 0.5
  const ny = event.clientY / Math.max(window.innerHeight, 1) - 0.5
  pointer.value = { x: nx, y: ny }
}

function onPointerLeave() {
  pointer.value = { x: 0, y: 0 }
}

function onScroll() {
  const vh = Math.max(window.innerHeight, 600)
  scrollRatio.value = Math.max(0, Math.min(1, window.scrollY / (vh * 0.72)))
}

/** 根据元素所在侧（左/右）与景深系数，合成「鼠标视差 + 滚离退场」位移 */
function floatStyle(side: -1 | 1, depth: number, baseRot: number) {
  const px = pointer.value.x * 26 * depth
  const py = pointer.value.y * 18 * depth
  const sx = side * scrollRatio.value * (56 + depth * 28)
  const sy = -scrollRatio.value * (24 + depth * 18)
  const rot = baseRot + side * scrollRatio.value * 4
  const opacity = Math.max(0, 1 - scrollRatio.value * 1.15)
  return {
    transform: `translate3d(${(px + sx).toFixed(1)}px, ${(py + sy).toFixed(1)}px, 0) rotate(${rot.toFixed(1)}deg)`,
    opacity,
  }
}

const centerOpacity = computed(() => Math.max(0, 1 - scrollRatio.value * 1.35))
const centerShift = computed(() => `translate3d(0, ${(-scrollRatio.value * 36).toFixed(1)}px, 0)`)

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
})
</script>

<template>
  <section
    id="banner"
    class="banner-root relative flex min-h-[100svh] w-full flex-col items-center justify-center overflow-hidden bg-gradient-to-b from-[#faf9f6] via-[#fcfcfb] to-white px-6 pb-16 pt-24"
    :class="{ 'is-ready': ready }"
    @pointermove="onPointerMove"
    @pointerleave="onPointerLeave"
  >
    <!-- 首屏全屏 3D 液态丝绸光雕流形与微光星尘（仅限 100svh Banner，底部自然融回纯白） -->
    <div
      class="pointer-events-none absolute inset-0 transition-opacity duration-200"
      :style="{ opacity: centerOpacity }"
      aria-hidden="true"
    >
      <BannerSilkWave />
      <!-- 底部柔和融白过渡带，无缝衔接下方纯白工作台 -->
      <div class="absolute inset-x-0 bottom-0 h-32 bg-gradient-to-t from-white via-white/80 to-transparent" />
      <!-- 正中极轻柔白呼吸光晕，保证主标题与按钮永远清晰锐利 -->
      <div class="absolute inset-0 bg-[radial-gradient(ellipse_48%_40%_at_50%_50%,rgba(255,255,255,0.82)_0%,rgba(255,255,255,0.35)_52%,transparent_78%)]" />
    </div>

    <!-- ================= 全屏空间悬浮核心功能卡（左右严格对称排布） ================= -->
    <div class="pointer-events-none absolute inset-0 mx-auto max-w-[1540px]" aria-hidden="true">
      <!-- 1. 左上：流程一键重跑 -->
      <div class="bloom-el bloom-1 absolute left-[4%] top-[15%] hidden lg:block xl:left-[7%]">
        <div class="float-layer" :style="floatStyle(-1, 0.85, -4)">
          <div class="idle-drift-a w-[240px] rounded-[20px] border border-[#e7e5e0] bg-white p-4 shadow-[0_20px_48px_-24px_rgba(15,14,12,0.16),0_2px_8px_rgba(15,14,12,0.03)]">
            <div class="flex items-center justify-between gap-2">
              <span class="truncate text-[12px] font-semibold text-ink">顺达月结流程 · v3</span>
              <span class="inline-flex shrink-0 items-center gap-1 rounded-full bg-ink px-2.5 py-0.5 text-[10.5px] font-medium text-white">
                <Play class="size-2.5 fill-current" /> 重跑
              </span>
            </div>
            <div class="mt-3 flex items-center gap-1.5 text-[10.5px] font-medium">
              <span class="rounded-md bg-[#f5f4f0] px-2 py-1 text-ink">读表</span>
              <span class="flex-1 border-t border-dashed border-[#c5c8f5]" />
              <span class="rounded-md bg-[#f2f3fe] px-2 py-1 text-[#5255c8]">核算</span>
              <span class="flex-1 border-t border-dashed border-[#a5e0c2]" />
              <span class="rounded-md bg-[#edf9f2] px-2 py-1 text-[#158752]">交付</span>
            </div>
            <div class="mt-3 flex items-center justify-between text-[11px] text-mute">
              <span>新月账单直接复用</span>
              <span class="font-mono text-[#158752]">12s 完成</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 2. 左中：对话处理表格 + 绿芽墨墨 -->
      <div class="bloom-el bloom-2 absolute left-[5%] top-[47%] hidden md:block xl:left-[8.5%]">
        <div class="float-layer" :style="floatStyle(-1, 1.35, -2.5)">
          <div class="idle-drift-b relative flex items-center gap-3 rounded-full border border-[#eae8e2] bg-white/95 py-2 pl-2.5 pr-4 shadow-[0_20px_44px_-22px_rgba(15,14,12,0.18)]">
            <div class="flex size-14 items-center justify-center rounded-full bg-[#f5f4f0]">
              <Mascot variant="blob" :size="48" :shadow="false" />
            </div>
            <div class="leading-tight">
              <div class="text-[12.5px] font-semibold text-ink">对话即处理</div>
              <div class="mt-1 text-[11px] text-mute">“按上月口径出表”</div>
            </div>
          </div>
        </div>
      </div>

      <!-- 3. 左下：新旧表差异比对 + 靛蓝学霸小团子 -->
      <div class="bloom-el bloom-3 absolute bottom-[13%] left-[7%] hidden lg:block xl:left-[11.5%]">
        <div class="float-layer" :style="floatStyle(-1, 1.0, 2.5)">
          <div class="idle-drift-c flex items-center gap-3 rounded-[20px] border border-[#e7e5e0] bg-white px-4 py-3 shadow-[0_20px_48px_-24px_rgba(15,14,12,0.16)]">
            <Mascot variant="indigo-nerd" :size="42" :shadow="false" />
            <div class="leading-tight">
              <div class="flex items-center gap-1.5 text-[12px] font-semibold text-ink">
                <GitCompareArrows class="size-3.5 text-[#6265dc]" />
                <span>双表差异比对</span>
              </div>
              <div class="mt-1.5 flex items-center gap-1.5 font-mono text-[10.5px]">
                <span class="rounded bg-[#edf9f2] px-1.5 py-0.5 text-[#158752]">+2 新增</span>
                <span class="rounded bg-[#fff7e6] px-1.5 py-0.5 text-[#c26d09]">1 变动</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 4. 右上：公式库与 AI 试算 -->
      <div class="bloom-el bloom-4 absolute right-[4%] top-[15%] hidden lg:block xl:right-[7%]">
        <div class="float-layer" :style="floatStyle(1, 0.85, 4)">
          <div class="idle-drift-b w-[240px] rounded-[20px] border border-[#e7e5e0] bg-white p-4 shadow-[0_20px_48px_-24px_rgba(15,14,12,0.16),0_2px_8px_rgba(15,14,12,0.03)]">
            <div class="flex items-center justify-between text-[11.5px]">
              <span class="font-semibold text-ink">公式库 · 阶梯计费</span>
              <span class="inline-flex items-center gap-1 rounded-full bg-[#f2f3fe] px-2 py-0.5 text-[10px] font-medium text-[#5255c8]">
                <Sparkles class="size-2.5" /> AI 试算
              </span>
            </div>
            <div class="mt-2.5 rounded-lg bg-[#f7f6f3] px-2.5 py-1.5 font-mono text-[10.5px] text-ink">
              =IF(重&gt;3, 8+(重-3)*2.5, 8)
            </div>
            <div class="mt-2.5 flex items-center justify-between text-[11px] tabular-nums">
              <span class="text-mute">试算 6 kg</span>
              <span class="font-mono font-semibold text-[#158752]">¥ 15.50</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 5. 右中：客户规则与字段映射 + 蓝帽小团子 -->
      <div class="bloom-el bloom-5 absolute right-[5%] top-[47%] hidden md:block xl:right-[8.5%]">
        <div class="float-layer" :style="floatStyle(1, 1.35, 2.5)">
          <div class="idle-drift-a flex items-center gap-3 rounded-full border border-[#eae8e2] bg-white/95 py-2 pl-4 pr-2.5 shadow-[0_20px_44px_-22px_rgba(15,14,12,0.18)]">
            <div class="text-right leading-tight">
              <div class="text-[12.5px] font-semibold text-ink">记住客户规则</div>
              <div class="mt-1 text-[11px] text-mute">列名别名自动对齐</div>
            </div>
            <div class="flex size-14 items-center justify-center rounded-full bg-[#f5f4f0]">
              <Mascot variant="party-wink" :size="48" :shadow="false" />
            </div>
          </div>
        </div>
      </div>

      <!-- 6. 右下：本地脱敏保护 + 云朵奶白小团子 -->
      <div class="bloom-el bloom-6 absolute bottom-[13%] right-[7%] hidden lg:block xl:right-[11.5%]">
        <div class="float-layer" :style="floatStyle(1, 1.0, -2.5)">
          <div class="idle-drift-c flex items-center gap-3 rounded-[20px] border border-[#e7e5e0] bg-white px-4 py-3 shadow-[0_20px_48px_-24px_rgba(15,14,12,0.16)]">
            <div class="flex items-center gap-2">
              <span class="flex size-7 items-center justify-center rounded-full bg-[#edf9f2] text-[#158752]">
                <ShieldCheck class="size-3.5" />
              </span>
              <div class="leading-tight">
                <div class="font-mono text-[11.5px] font-medium text-[#5255c8]">[PHONE_01]</div>
                <div class="text-[10.5px] text-faint">本地脱敏保护</div>
              </div>
            </div>
            <Mascot variant="cream-shy" :size="42" :shadow="false" />
          </div>
        </div>
      </div>
    </div>

    <!-- ================= 正中主标题与行动区（大留白呼吸感） ================= -->
    <div
      class="relative z-10 mx-auto max-w-[760px] text-center transition-transform duration-150 ease-out"
      :style="{ opacity: centerOpacity, transform: centerShift }"
    >
      <div class="hero-center-1 inline-flex justify-center">
        <div class="flex size-[84px] items-center justify-center rounded-[28px] border border-[#ebe9e3] bg-white shadow-[0_14px_34px_-18px_rgba(15,14,12,0.16)]">
          <Mascot variant="blob" :size="64" :shadow="false" />
        </div>
      </div>

      <h1 class="hero-center-2 mt-7 text-[32px] font-semibold leading-[1.18] tracking-[-0.03em] text-ink sm:text-[44px] lg:text-[52px]">
        复杂账单，说清一次就够了
      </h1>

      <p class="hero-center-3 mx-auto mt-5 max-w-[520px] text-[15.5px] leading-relaxed text-mute sm:text-[17.5px]">
        丢进表格就能算，跑通自动存流程，下月新表一键复跑。
      </p>

      <div class="hero-center-4 mt-9 flex items-center justify-center gap-3">
        <button
          type="button"
          class="group inline-flex items-center gap-2 rounded-full bg-ink px-7 py-3.5 text-[15px] font-medium text-white shadow-[0_16px_34px_-14px_rgba(11,11,12,0.45)] transition hover:bg-black/85 active:scale-[0.98]"
          @click="emit('enter')"
        >
          <span>{{ ctaLabel }}</span>
          <ArrowRight class="size-4 transition-transform duration-200 group-hover:translate-x-0.5" />
        </button>
      </div>
    </div>

    <!-- 底部居中清晰向下按钮（无多余文字） -->
    <div class="absolute inset-x-0 bottom-8 z-10 flex justify-center">
      <button
        type="button"
        class="hero-scroll-cue group inline-flex size-12 items-center justify-center rounded-full border border-[#dfddd7] bg-white text-ink shadow-[0_8px_24px_-10px_rgba(15,14,12,0.16)] transition duration-250 hover:border-ink hover:bg-ink hover:text-white active:scale-95"
        :style="{ opacity: centerOpacity }"
        title="向下查看工作台"
        aria-label="向下查看工作台"
        @click="emit('scrollDown')"
      >
        <ChevronDown class="scroll-arrow size-5 stroke-[2.2]" />
      </button>
    </div>
  </section>
</template>

<style scoped>
/* ===== 开场瞬间：中心绽放 + 四角元素向外舒展落位 ===== */
.hero-center-1,
.hero-center-2,
.hero-center-3,
.hero-center-4,
.hero-scroll-cue {
  opacity: 0;
  will-change: transform, opacity, filter;
}

.hero-center-1 {
  transform: translate3d(0, 18px, 0) scale(0.88);
  transition: opacity 0.7s cubic-bezier(0.16, 1, 0.3, 1) 0.08s, transform 0.8s cubic-bezier(0.16, 1, 0.3, 1) 0.08s;
}
.hero-center-2 {
  transform: translate3d(0, 24px, 0);
  filter: blur(8px);
  transition: opacity 0.8s cubic-bezier(0.16, 1, 0.3, 1) 0.16s, transform 0.85s cubic-bezier(0.16, 1, 0.3, 1) 0.16s, filter 0.8s cubic-bezier(0.16, 1, 0.3, 1) 0.16s;
}
.hero-center-3 {
  transform: translate3d(0, 18px, 0);
  filter: blur(5px);
  transition: opacity 0.8s cubic-bezier(0.16, 1, 0.3, 1) 0.26s, transform 0.85s cubic-bezier(0.16, 1, 0.3, 1) 0.26s, filter 0.8s cubic-bezier(0.16, 1, 0.3, 1) 0.26s;
}
.hero-center-4 {
  transform: translate3d(0, 14px, 0);
  transition: opacity 0.75s cubic-bezier(0.16, 1, 0.3, 1) 0.36s, transform 0.8s cubic-bezier(0.16, 1, 0.3, 1) 0.36s;
}
.hero-scroll-cue {
  transform: translate3d(0, 10px, 0);
  transition: opacity 0.8s ease 0.75s, transform 0.8s ease 0.75s, color 0.2s ease;
}

.is-ready .hero-center-1,
.is-ready .hero-center-2,
.is-ready .hero-center-3,
.is-ready .hero-center-4,
.is-ready .hero-scroll-cue {
  opacity: 1;
  transform: none;
  filter: blur(0);
}

/* 六组全屏空间特效元素：开场从内向外顺滑绽放 */
.bloom-el {
  opacity: 0;
  will-change: transform, opacity, filter;
  filter: blur(6px);
}
.bloom-1 {
  transform: translate3d(96px, 56px, 0) scale(0.84);
  transition: opacity 0.9s cubic-bezier(0.16, 1, 0.3, 1) 0.22s, transform 1.05s cubic-bezier(0.16, 1, 0.3, 1) 0.22s, filter 0.9s ease 0.22s;
}
.bloom-2 {
  transform: translate3d(110px, 6px, 0) scale(0.82);
  transition: opacity 0.9s cubic-bezier(0.16, 1, 0.3, 1) 0.3s, transform 1.05s cubic-bezier(0.16, 1, 0.3, 1) 0.3s, filter 0.9s ease 0.3s;
}
.bloom-3 {
  transform: translate3d(88px, -54px, 0) scale(0.84);
  transition: opacity 0.9s cubic-bezier(0.16, 1, 0.3, 1) 0.38s, transform 1.05s cubic-bezier(0.16, 1, 0.3, 1) 0.38s, filter 0.9s ease 0.38s;
}
.bloom-4 {
  transform: translate3d(-96px, 56px, 0) scale(0.84);
  transition: opacity 0.9s cubic-bezier(0.16, 1, 0.3, 1) 0.26s, transform 1.05s cubic-bezier(0.16, 1, 0.3, 1) 0.26s, filter 0.9s ease 0.26s;
}
.bloom-5 {
  transform: translate3d(-110px, 6px, 0) scale(0.82);
  transition: opacity 0.9s cubic-bezier(0.16, 1, 0.3, 1) 0.34s, transform 1.05s cubic-bezier(0.16, 1, 0.3, 1) 0.34s, filter 0.9s ease 0.34s;
}
.bloom-6 {
  transform: translate3d(-88px, -54px, 0) scale(0.84);
  transition: opacity 0.9s cubic-bezier(0.16, 1, 0.3, 1) 0.42s, transform 1.05s cubic-bezier(0.16, 1, 0.3, 1) 0.42s, filter 0.9s ease 0.42s;
}

.is-ready .bloom-el {
  opacity: 1;
  transform: none;
  filter: blur(0);
}

/* 鼠标视差与滚离退场的平滑插值 */
.float-layer {
  transition: transform 0.28s cubic-bezier(0.22, 1, 0.36, 1), opacity 0.22s ease-out;
  will-change: transform, opacity;
}

/* 柔和漂浮呼吸 */
.idle-drift-a {
  animation: drift-y 6.4s ease-in-out infinite;
}
.idle-drift-b {
  animation: drift-y 7.2s ease-in-out 0.9s infinite;
}
.idle-drift-c {
  animation: drift-y 6.8s ease-in-out 1.7s infinite;
}
@keyframes drift-y {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-8px); }
}

.scroll-arrow {
  animation: scroll-nudge 2.1s ease-in-out infinite;
}

@keyframes scroll-nudge {
  0%, 100% { transform: translateY(-1.5px); }
  50% { transform: translateY(2.5px); }
}

@media (prefers-reduced-motion: reduce) {
  .hero-center-1,
  .hero-center-2,
  .hero-center-3,
  .hero-center-4,
  .hero-scroll-cue,
  .bloom-el {
    opacity: 1;
    transform: none;
    filter: none;
    transition: none;
  }
  .idle-drift-a,
  .idle-drift-b,
  .idle-drift-c,
  .scroll-arrow {
    animation: none;
  }
}
</style>
