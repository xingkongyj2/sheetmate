<script setup lang="ts">
/**
 * 登录 / 引导页的舞台：居中的白色大卡片，左边放表单，右边是灰色插画区
 * （浅灰大写标语 + 漂浮的彩色物件 + 从底部探出头的墨墨）。
 */
import FloatingObjects from './FloatingObjects.vue'
import Mascot from './Mascot.vue'

withDefaults(defineProps<{ headline?: string[]; mood?: 'idle' | 'thinking' | 'happy' | 'working' }>(), {
  headline: () => ['SHAPE YOUR', 'SHEETS WITH', 'SHEETMATE'],
  mood: 'idle',
})
</script>

<template>
  <div class="relative flex min-h-full items-center justify-center overflow-hidden bg-[radial-gradient(1200px_600px_at_15%_0%,#ecebff_0%,transparent_60%),radial-gradient(900px_500px_at_100%_100%,#f1efff_0%,transparent_55%),#f6f6f8] p-4 sm:p-8">
    <div class="grid w-full max-w-[1120px] animate-rise overflow-hidden rounded-[28px] bg-white shadow-[0_40px_80px_-40px_rgba(40,30,120,0.35)] lg:grid-cols-2">
      <section class="relative flex min-h-[560px] flex-col px-8 py-10 sm:px-14">
        <slot />
      </section>
      <section class="relative hidden min-h-[600px] overflow-hidden bg-paper lg:block">
        <div class="headline-ghost absolute inset-x-0 top-12 text-center text-[34px]">
          <div v-for="line in headline" :key="line">{{ line }}</div>
        </div>
        <FloatingObjects preset="auth" />
        <div class="absolute bottom-0 left-1/2 h-[200px] w-[330px] -translate-x-[38%] overflow-hidden">
          <div class="absolute left-0 top-0">
            <Mascot variant="blob" :size="330" :mood="mood" :shadow="false" />
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
