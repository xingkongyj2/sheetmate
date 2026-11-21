<script setup lang="ts">
/**
 * 「墨墨家族」三排丝滑交错巡游：
 * - 展示 10 套原创色系与 42 款精选搞怪小团子卡片（头像 + 名称），悬停暂停，点击或点「换一只」可在上方聚光展示
 */
import { Shuffle } from '@lucide/vue'
import { computed, ref } from 'vue'

import Mascot from '@/components/brand/Mascot.vue'
import { MASCOT_KEYS, mascotStyle } from '@/components/brand/mascots'

const ROWS = [
  {
    dir: 'left',
    keys: [
      'blob',
      'sprout-star',
      'ink-party',
      'cream-shy',
      'indigo-eureka',
      'oat-cozy',
      'sage-leaf',
      'mist-shiny',
      'peach-cheer',
      'taro-dream',
      'rose-sweetie',
      'sprout-cool',
      'party-money',
      'cream-monocle',
    ],
  },
  {
    dir: 'right',
    keys: [
      'party-wink',
      'indigo-nerd',
      'cream-pixel',
      'peach-cheeky',
      'sage-scholar',
      'taro-coolcat',
      'mist-tune',
      'oat-sir',
      'rose-melody',
      'sprout-tongue',
      'party-love',
      'indigo-lollipop',
      'cream-kiss',
      'peach-dapper',
    ],
  },
  {
    dir: 'left-slow',
    keys: [
      'sprout-laugh',
      'party-cool',
      'cream-cross',
      'indigo-hum',
      'oat-coolpop',
      'sage-hype',
      'mist-boss',
      'peach-blush',
      'taro-flirt',
      'rose-silly',
      'sprout-bandaid',
      'party-xd',
      'mist-candy',
      'taro-noble',
    ],
  },
] as const

const spotlightKey = ref('blob')
const spotlight = computed(() => mascotStyle(spotlightKey.value))

function pickRandom() {
  const pool = MASCOT_KEYS.filter((k) => k !== spotlightKey.value)
  const next = pool[Math.floor(Math.random() * pool.length)]
  if (next) spotlightKey.value = next
}
</script>

<template>
  <div>
    <!-- 上方互动聚光胶囊 -->
    <div class="mx-auto mb-7 flex max-w-[420px] items-center justify-between gap-4 rounded-full border border-[#e7e5e0] bg-white px-3.5 py-2 shadow-[0_12px_30px_-20px_rgba(15,14,12,0.16)]">
      <div class="flex min-w-0 items-center gap-3">
        <div class="flex size-12 shrink-0 items-center justify-center rounded-full bg-[#f5f4f0]">
          <Transition name="spot-pop" mode="out-in">
            <Mascot :key="spotlightKey" :variant="spotlightKey" :size="44" :shadow="false" />
          </Transition>
        </div>
        <div class="min-w-0">
          <div class="truncate text-[14px] font-semibold text-ink">{{ spotlight.name }}</div>
          <div class="font-mono text-[11px] text-faint">90 款原创形象 · 专属客户头像</div>
        </div>
      </div>

      <button
        type="button"
        class="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-line bg-[#faf9f7] px-3 py-1.5 text-[12px] font-medium text-ink transition hover:border-ink/35 hover:bg-white active:scale-95"
        @click="pickRandom"
      >
        <Shuffle class="size-3.5" />
        <span>换一只</span>
      </button>
    </div>

    <!-- 三排交错丝滑巡游带 -->
    <div class="marquee-mask space-y-3 overflow-hidden py-1.5">
      <div
        v-for="(row, rowIdx) in ROWS"
        :key="rowIdx"
        class="marquee-track flex w-max gap-3"
        :class="`marquee-${row.dir}`"
      >
        <template v-for="dup in 2" :key="`r${rowIdx}-${dup}`">
          <button
            v-for="key in row.keys"
            :key="`${rowIdx}-${dup}-${key}`"
            type="button"
            class="group flex items-center gap-2.5 rounded-2xl border bg-white px-3.5 py-2.5 transition duration-300 hover:-translate-y-0.5 hover:shadow-[0_10px_24px_-16px_rgba(15,14,12,0.2)]"
            :class="spotlightKey === key ? 'border-ink shadow-xs' : 'border-[#eae8e2]'"
            @click="spotlightKey = key"
          >
            <Mascot :variant="key" :size="42" :shadow="false" class="transition-transform duration-300 group-hover:scale-110" />
            <span class="whitespace-nowrap text-[13px] font-medium text-ink/85">{{ mascotStyle(key).name }}</span>
          </button>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.marquee-mask {
  mask-image: linear-gradient(to right, transparent 0%, #000 8%, #000 92%, transparent 100%);
  -webkit-mask-image: linear-gradient(to right, transparent 0%, #000 8%, #000 92%, transparent 100%);
}

.marquee-left {
  animation: marquee-scroll-left 46s linear infinite;
}
.marquee-right {
  animation: marquee-scroll-right 50s linear infinite;
}
.marquee-left-slow {
  animation: marquee-scroll-left 56s linear infinite;
}
.marquee-track:hover {
  animation-play-state: paused;
}

@keyframes marquee-scroll-left {
  from { transform: translate3d(0, 0, 0); }
  to { transform: translate3d(-50%, 0, 0); }
}
@keyframes marquee-scroll-right {
  from { transform: translate3d(-50%, 0, 0); }
  to { transform: translate3d(0, 0, 0); }
}

.spot-pop-enter-active,
.spot-pop-leave-active {
  transition: opacity 0.2s ease, transform 0.26s cubic-bezier(0.2, 0.9, 0.3, 1.3);
}
.spot-pop-enter-from {
  opacity: 0;
  transform: scale(0.82) translateY(6px);
}
.spot-pop-leave-to {
  opacity: 0;
  transform: scale(0.9) translateY(-4px);
}

@media (prefers-reduced-motion: reduce) {
  .marquee-left,
  .marquee-right,
  .marquee-left-slow {
    animation: none;
  }
}
</style>
