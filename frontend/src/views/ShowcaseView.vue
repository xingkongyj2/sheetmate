<script setup lang="ts">
/**
 * SheetMate 产品展示页（/showcase）：
 * - 第一屏：100svh 全屏动效 Banner（实体账单卡片 + 墨墨家族空间绽放、鼠标视差、滚离退场）；
 * - 向下滚动：干净纯白背景，依次展示宽幅 1:1 真实软件工作台、6 张简约核心功能卡、小团子巡游与页尾全景。
 */
import { ArrowRight } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import LogoMark from '@/components/brand/LogoMark.vue'
import ShowcaseArtScenes from '@/components/showcase/ShowcaseArtScenes.vue'
import ShowcaseFeatureCards from '@/components/showcase/ShowcaseFeatureCards.vue'
import ShowcaseHeroBanner from '@/components/showcase/ShowcaseHeroBanner.vue'
import ShowcaseHeroStage from '@/components/showcase/ShowcaseHeroStage.vue'
import ShowcaseMascotParade from '@/components/showcase/ShowcaseMascotParade.vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()

const rootEl = ref<HTMLElement | null>(null)
const navReady = ref(false)
const scrolled = ref(false)
const activeSection = ref<'workspace' | 'features' | 'mascots'>('workspace')

let observer: IntersectionObserver | null = null

const isLoggedIn = computed(() => Boolean(auth.user))
const ctaLabel = computed(() => (isLoggedIn.value ? '进入工作台' : '开始使用'))

function enterApp() {
  router.push(isLoggedIn.value ? { name: 'home' } : { name: 'login' })
}

function scrollToSection(id: 'banner' | 'workspace' | 'features' | 'mascots') {
  if (id !== 'banner') activeSection.value = id
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function onScroll() {
  scrolled.value = window.scrollY > 32
  const mascotsEl = document.getElementById('mascots')
  const featuresEl = document.getElementById('features')
  const y = window.scrollY + 280
  if (mascotsEl && y >= mascotsEl.offsetTop) activeSection.value = 'mascots'
  else if (featuresEl && y >= featuresEl.offsetTop) activeSection.value = 'features'
  else activeSection.value = 'workspace'
}

onMounted(() => {
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      navReady.value = true
    })
  })
  window.addEventListener('scroll', onScroll, { passive: true })
  onScroll()

  const targets = rootEl.value?.querySelectorAll('.reveal-item') ?? []
  if ('IntersectionObserver' in window) {
    observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            entry.target.classList.add('is-visible')
            observer?.unobserve(entry.target)
          }
        }
      },
      { threshold: 0.1, rootMargin: '0px 0px -36px 0px' },
    )
    targets.forEach((el) => observer?.observe(el))
  } else {
    targets.forEach((el) => el.classList.add('is-visible'))
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('scroll', onScroll)
  observer?.disconnect()
})
</script>

<template>
  <div
    ref="rootEl"
    class="min-h-screen bg-white text-ink selection:bg-ink selection:text-white"
    :class="{ 'is-ready': navReady }"
  >
    <!-- 顶部极简悬浮胶囊导航（覆盖全屏 Banner，滚动时保持悬浮） -->
    <header class="fixed inset-x-0 top-4 z-50 px-4 sm:px-6">
      <nav
        aria-label="展示页导航"
        class="hero-nav mx-auto flex h-[52px] max-w-[740px] items-center justify-between rounded-full border px-2.5 pl-4 transition-all duration-300"
        :class="
          scrolled
            ? 'border-[#e4e2dc] bg-white/94 shadow-[0_12px_34px_-18px_rgba(15,14,12,0.16)] backdrop-blur-xl'
            : 'border-[#eceae4] bg-white/86 shadow-[0_4px_20px_-14px_rgba(15,14,12,0.1)] backdrop-blur-md'
        "
      >
        <button type="button" class="flex items-center" @click="scrollToSection('banner')">
          <LogoMark :size="23" with-text class="[&_span]:text-[16.5px]" />
        </button>

        <div class="hidden items-center gap-1 text-[13.5px] sm:flex">
          <button
            type="button"
            class="rounded-full px-3.5 py-1.5 transition"
            :class="activeSection === 'workspace' ? 'bg-[#f4f3ef] font-medium text-ink' : 'text-mute hover:text-ink'"
            @click="scrollToSection('workspace')"
          >
            工作台
          </button>
          <button
            type="button"
            class="rounded-full px-3.5 py-1.5 transition"
            :class="activeSection === 'features' ? 'bg-[#f4f3ef] font-medium text-ink' : 'text-mute hover:text-ink'"
            @click="scrollToSection('features')"
          >
            功能
          </button>
          <button
            type="button"
            class="rounded-full px-3.5 py-1.5 transition"
            :class="activeSection === 'mascots' ? 'bg-[#f4f3ef] font-medium text-ink' : 'text-mute hover:text-ink'"
            @click="scrollToSection('mascots')"
          >
            墨墨
          </button>
        </div>

        <button
          type="button"
          class="group inline-flex items-center gap-1.5 rounded-full bg-ink px-4 py-2 text-[13px] font-medium text-white transition hover:bg-black/85 active:scale-[0.98]"
          @click="enterApp"
        >
          <span>{{ ctaLabel }}</span>
          <ArrowRight class="size-3.5 transition-transform duration-200 group-hover:translate-x-0.5" />
        </button>
      </nav>
    </header>

    <!-- 第一屏：全屏空间特效 Banner（100svh） -->
    <ShowcaseHeroBanner
      :cta-label="ctaLabel"
      @enter="enterApp"
      @scroll-down="scrollToSection('workspace')"
    />

    <!-- 以下全部为干净纯白背景 -->
    <main class="bg-white">
      <!-- 宽幅 1:1 真实软件工作台演示 -->
      <section id="workspace" class="scroll-mt-20 px-4 pb-20 pt-8 sm:px-6 sm:pt-12">
        <div class="reveal-item mx-auto max-w-[1320px]">
          <ShowcaseHeroStage />
        </div>
      </section>

      <!-- 核心功能区（6 张简约白卡） -->
      <section id="features" class="scroll-mt-20 mx-auto max-w-[1200px] px-4 py-16 sm:px-6 sm:py-24">
        <h2 class="reveal-item mb-12 text-center text-[28px] font-semibold tracking-[-0.025em] text-ink sm:text-[36px]">
          核心功能
        </h2>
        <ShowcaseFeatureCards />
      </section>

      <!-- 原创小团子巡游区 -->
      <section id="mascots" class="scroll-mt-20 mx-auto max-w-[1200px] px-4 py-14 sm:px-6 sm:py-20">
        <h2 class="reveal-item mb-8 text-center text-[24px] font-semibold tracking-[-0.02em] text-ink sm:text-[30px]">
          90 款原创小团子，给每个客户一个性格
        </h2>
        <div class="reveal-item">
          <ShowcaseMascotParade />
        </div>
      </section>

      <!-- 页尾协同插画 + 收尾 CTA -->
      <section class="pt-12 sm:pt-16">
        <div class="mx-auto max-w-[1060px] px-4 text-center sm:px-6">
          <h2 class="reveal-item text-[26px] font-semibold tracking-[-0.025em] text-ink sm:text-[36px]">
            让墨墨接手下一份账单
          </h2>
          <div class="reveal-item mt-6">
            <button
              type="button"
              class="group inline-flex items-center gap-2 rounded-full bg-ink px-7 py-3.5 text-[14.5px] font-medium text-white shadow-[0_14px_30px_-14px_rgba(11,11,12,0.45)] transition hover:bg-black/85 active:scale-[0.98]"
              @click="enterApp"
            >
              <span>{{ isLoggedIn ? '进入 SheetMate' : '登录并开始使用' }}</span>
              <ArrowRight class="size-4 transition-transform duration-200 group-hover:translate-x-0.5" />
            </button>
          </div>

          <div class="reveal-item mt-8">
            <ShowcaseArtScenes variant="closing" />
          </div>
        </div>

        <footer class="border-t border-[#f0eee9] py-7">
          <div class="mx-auto flex max-w-[1200px] flex-wrap items-center justify-between gap-4 px-4 sm:px-6">
            <LogoMark :size="20" with-text class="[&_span]:text-[15px]" />
            <span class="text-xs text-faint">本产品归属「数智结算工坊」</span>
          </div>
        </footer>
      </section>
    </main>
  </div>
</template>

<style scoped>
.hero-nav {
  opacity: 0;
  transform: translate3d(0, -10px, 0);
  transition:
    opacity 0.6s cubic-bezier(0.22, 1, 0.36, 1) 0.04s,
    transform 0.65s cubic-bezier(0.22, 1, 0.36, 1) 0.04s,
    background-color 0.3s ease,
    border-color 0.3s ease,
    box-shadow 0.3s ease;
}

.is-ready .hero-nav {
  opacity: 1;
  transform: none;
}

:deep(.reveal-item),
.reveal-item {
  opacity: 0;
  transform: translate3d(0, 22px, 0);
  filter: blur(4px);
  transition:
    opacity 0.72s cubic-bezier(0.22, 1, 0.36, 1) var(--reveal-delay, 0ms),
    transform 0.78s cubic-bezier(0.22, 1, 0.36, 1) var(--reveal-delay, 0ms),
    filter 0.72s cubic-bezier(0.22, 1, 0.36, 1) var(--reveal-delay, 0ms);
}

:deep(.reveal-item.is-visible),
.reveal-item.is-visible {
  opacity: 1;
  transform: none;
  filter: blur(0);
}

@media (prefers-reduced-motion: reduce) {
  .hero-nav,
  :deep(.reveal-item),
  .reveal-item {
    opacity: 1;
    transform: none;
    filter: none;
    transition: none;
  }
}
</style>
