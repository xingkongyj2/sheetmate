<script setup lang="ts">
/**
 * 首屏 Banner（上方 3D 液态丝绸光雕 + 4 张左右对称重点功能卡 + 居中主标题）：
 * - 紧凑舒展的首屏高度，让下方的宽幅真实工作台自然抬升进入首屏下半部；
 * - 3D 丝绸特效仅作用于顶部 Banner 区域，底部自然融回纯白背景。
 */
import { ArrowRight, Play, Sparkles } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import Mascot from '@/components/brand/Mascot.vue'
import BannerSilkWave from '@/components/showcase/BannerSilkWave.vue'

defineProps<{
  ctaLabel: string
}>()

const emit = defineEmits<{
  enter: []
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
  const threshold = Math.max(window.innerHeight * 0.55, 420)
  scrollRatio.value = Math.max(0, Math.min(1, window.scrollY / threshold))
}

function floatStyle(side: -1 | 1, depth: number, baseRot: number) {
  const px = pointer.value.x * 22 * depth
  const py = pointer.value.y * 14 * depth
  const sx = side * scrollRatio.value * (48 + depth * 22)
  const sy = -scrollRatio.value * (18 + depth * 14)
  const rot = baseRot + side * scrollRatio.value * 3
  const opacity = Math.max(0, 1 - scrollRatio.value * 1.1)
  return {
    transform: `translate3d(${(px + sx).toFixed(1)}px, ${(py + sy).toFixed(1)}px, 0) rotate(${rot.toFixed(1)}deg)`,
    opacity,
  }
}

const silkOpacity = computed(() => Math.max(0, 1 - scrollRatio.value * 1.15))

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
    class="banner-root relative w-full overflow-hidden bg-gradient-to-b from-[#faf9f6] via-[#fcfcfb] to-white px-4 pb-12 pt-28 sm:px-6 sm:pt-32"
    :class="{ 'is-ready': ready }"
    @pointermove="onPointerMove"
    @pointerleave="onPointerLeave"
  >
    <!-- 顶部专属 3D 液态丝绸光雕（仅覆盖首屏上区，底部自然融白，下滑后纯白背景） -->
    <div
      class="pointer-events-none absolute inset-x-0 top-0 h-[720px] transition-opacity duration-200"
      :style="{ opacity: silkOpacity }"
      aria-hidden="true"
    >
      <BannerSilkWave />
      <div class="absolute inset-x-0 bottom-0 h-44 bg-gradient-to-t from-white via-white/85 to-transparent" />
      <div class="absolute inset-0 bg-[radial-gradient(ellipse_48%_44%_at_50%_46%,rgba(255,255,255,0.82)_0%,rgba(255,255,255,0.36)_54%,transparent_78%)]" />
    </div>

    <!-- 4 张左右严格对称的核心功能悬浮卡 -->
    <div class="pointer-events-none absolute inset-x-0 top-0 mx-auto h-[580px] max-w-[1520px]" aria-hidden="true">
      <!-- 1. 左上：流程一键重跑 -->
      <div class="bloom-el bloom-1 absolute left-[3%] top-[116px] hidden lg:block xl:left-[6%]">
        <div class="float-layer" :style="floatStyle(-1, 0.85, -4)">
          <div class="idle-drift-a w-[238px] rounded-[20px] border border-[#e7e5e0] bg-white p-4 shadow-[0_20px_48px_-24px_rgba(15,14,12,0.16),0_2px_8px_rgba(15,14,12,0.03)]">
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

      <!-- 2. 左下：对话即处理 + 绿芽墨墨 -->
      <div class="bloom-el bloom-2 absolute left-[5%] top-[348px] hidden lg:block xl:left-[8.5%]">
        <div class="float-layer" :style="floatStyle(-1, 1.25, -2.5)">
          <div class="idle-drift-b relative flex items-center gap-3 rounded-full border border-[#eae8e2] bg-white/95 py-2 pl-2.5 pr-4 shadow-[0_20px_44px_-22px_rgba(15,14,12,0.18)]">
            <div class="flex size-13 items-center justify-center rounded-full bg-[#f5f4f0]">
              <Mascot variant="blob" :size="46" :shadow="false" />
            </div>
            <div class="leading-tight">
              <div class="text-[12.5px] font-semibold text-ink">对话即处理</div>
              <div class="mt-1 text-[11px] text-mute">“按上月口径出表”</div>
            </div>
          </div>
        </div>
      </div>

      <!-- 3. 右上：公式库与 AI 试算 -->
      <div class="bloom-el bloom-3 absolute right-[3%] top-[116px] hidden lg:block xl:right-[6%]">
        <div class="float-layer" :style="floatStyle(1, 0.85, 4)">
          <div class="idle-drift-b w-[238px] rounded-[20px] border border-[#e7e5e0] bg-white p-4 shadow-[0_20px_48px_-24px_rgba(15,14,12,0.16),0_2px_8px_rgba(15,14,12,0.03)]">
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

      <!-- 4. 右下：客户规则与字段映射 + 蓝帽小团子 -->
      <div class="bloom-el bloom-4 absolute right-[5%] top-[348px] hidden lg:block xl:right-[8.5%]">
        <div class="float-layer" :style="floatStyle(1, 1.25, 2.5)">
          <div class="idle-drift-a flex items-center gap-3 rounded-full border border-[#eae8e2] bg-white/95 py-2 pl-4 pr-2.5 shadow-[0_20px_44px_-22px_rgba(15,14,12,0.18)]">
            <div class="text-right leading-tight">
              <div class="text-[12.5px] font-semibold text-ink">记住客户规则</div>
              <div class="mt-1 text-[11px] text-mute">列名别名自动对齐</div>
            </div>
            <div class="flex size-13 items-center justify-center rounded-full bg-[#f5f4f0]">
              <Mascot variant="party-wink" :size="46" :shadow="false" />
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 正中主标题与主行动按钮 -->
    <div class="relative z-10 mx-auto max-w-[740px] pb-12 pt-5 text-center">
      <div class="hero-center-1 inline-flex justify-center">
        <div class="flex size-[74px] items-center justify-center rounded-[24px] border border-[#ebe9e3] bg-white shadow-[0_14px_34px_-18px_rgba(15,14,12,0.16)]">
          <Mascot variant="blob" :size="56" :shadow="false" />
        </div>
      </div>

      <h1 class="hero-center-2 mt-5 text-[32px] font-semibold leading-[1.18] tracking-[-0.03em] text-ink sm:text-[44px] lg:text-[50px]">
        复杂账单，说清一次就够了
      </h1>

      <p class="hero-center-3 mx-auto mt-4 max-w-[520px] text-[15px] leading-relaxed text-mute sm:text-[17px]">
        丢进表格就能算，跑通自动存流程，下月新表一键复跑。
      </p>

      <div class="hero-center-4 mt-7 flex items-center justify-center">
        <button
          type="button"
          class="group inline-flex items-center gap-2 rounded-full bg-ink px-7 py-3 text-[14.5px] font-medium text-white shadow-[0_16px_34px_-14px_rgba(11,11,12,0.45)] transition hover:bg-black/85 active:scale-[0.98]"
          @click="emit('enter')"
        >
          <span>{{ ctaLabel }}</span>
          <ArrowRight class="size-4 transition-transform duration-200 group-hover:translate-x-0.5" />
        </button>
      </div>
    </div>

    <!-- 插槽：温和露头于首屏下方的宽幅真实软件工作台 -->
    <div id="workspace" class="hero-workspace relative z-10 mx-auto mt-14 max-w-[1320px] scroll-mt-20 sm:mt-20">
      <slot />
    </div>
  </section>
</template>

<style scoped>
.hero-center-1,
.hero-center-2,
.hero-center-3,
.hero-center-4,
.hero-workspace {
  opacity: 0;
  will-change: transform, opacity, filter;
}

.hero-center-1 {
  transform: translate3d(0, 16px, 0) scale(0.88);
  transition: opacity 0.65s cubic-bezier(0.16, 1, 0.3, 1) 0.08s, transform 0.75s cubic-bezier(0.16, 1, 0.3, 1) 0.08s;
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
.hero-workspace {
  transform: translate3d(0, 34px, 0) scale(0.985);
  filter: blur(4px);
  transition: opacity 0.9s cubic-bezier(0.16, 1, 0.3, 1) 0.42s, transform 0.95s cubic-bezier(0.16, 1, 0.3, 1) 0.42s, filter 0.85s ease 0.42s;
}

.is-ready .hero-center-1,
.is-ready .hero-center-2,
.is-ready .hero-center-3,
.is-ready .hero-center-4,
.is-ready .hero-workspace {
  opacity: 1;
  transform: none;
  filter: blur(0);
}

/* 4 张悬浮功能卡：开场从中心向外绽放 */
.bloom-el {
  opacity: 0;
  will-change: transform, opacity, filter;
  filter: blur(6px);
}
.bloom-1 {
  transform: translate3d(88px, 42px, 0) scale(0.84);
  transition: opacity 0.85s cubic-bezier(0.16, 1, 0.3, 1) 0.2s, transform 1s cubic-bezier(0.16, 1, 0.3, 1) 0.2s, filter 0.85s ease 0.2s;
}
.bloom-2 {
  transform: translate3d(96px, -16px, 0) scale(0.84);
  transition: opacity 0.85s cubic-bezier(0.16, 1, 0.3, 1) 0.28s, transform 1s cubic-bezier(0.16, 1, 0.3, 1) 0.28s, filter 0.85s ease 0.28s;
}
.bloom-3 {
  transform: translate3d(-88px, 42px, 0) scale(0.84);
  transition: opacity 0.85s cubic-bezier(0.16, 1, 0.3, 1) 0.24s, transform 1s cubic-bezier(0.16, 1, 0.3, 1) 0.24s, filter 0.85s ease 0.24s;
}
.bloom-4 {
  transform: translate3d(-96px, -16px, 0) scale(0.84);
  transition: opacity 0.85s cubic-bezier(0.16, 1, 0.3, 1) 0.32s, transform 1s cubic-bezier(0.16, 1, 0.3, 1) 0.32s, filter 0.85s ease 0.32s;
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
  animation: drift-y 7.2s ease-in-out 0.9s infinite;
}
@keyframes drift-y {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-7px); }
}

@media (prefers-reduced-motion: reduce) {
  .hero-center-1,
  .hero-center-2,
  .hero-center-3,
  .hero-center-4,
  .hero-workspace,
  .bloom-el {
    opacity: 1;
    transform: none;
    filter: none;
    transition: none;
  }
  .idle-drift-a,
  .idle-drift-b {
    animation: none;
  }
}
</style>
