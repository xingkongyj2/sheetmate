<script setup lang="ts">
/**
 * 登录 / 引导页的舞台：表单区与插画区组成左右分栏
 * （浅灰大写标语 + 漂浮的彩色物件 + 从底部探出头的墨墨）。
 */
import FloatingObjects from './FloatingObjects.vue'
import Mascot from './Mascot.vue'

withDefaults(defineProps<{ headline?: string[]; mood?: 'idle' | 'thinking' | 'happy' | 'working'; fullScreen?: boolean }>(), {
  headline: () => ['SHAPE YOUR', 'SHEETS WITH', 'SHEETMATE'],
  mood: 'idle',
  fullScreen: false,
})
</script>

<template>
  <div
    class="relative flex min-h-full items-center justify-center overflow-hidden bg-[radial-gradient(1200px_600px_at_15%_0%,#ecebff_0%,transparent_60%),radial-gradient(900px_500px_at_100%_100%,#f1efff_0%,transparent_55%),#f6f6f8]"
    :class="fullScreen ? 'min-h-screen p-0' : 'p-4 sm:p-8'"
  >
    <div
      class="grid w-full animate-rise bg-white lg:grid-cols-2"
      :class="fullScreen ? 'min-h-screen max-w-none rounded-none shadow-none' : 'max-w-[1120px] overflow-hidden rounded-[28px] shadow-[0_40px_80px_-40px_rgba(40,30,120,0.35)]'"
    >
      <section class="relative flex min-h-[560px] flex-col px-8 py-10 sm:px-14" :class="fullScreen ? 'lg:order-2 min-h-screen lg:justify-center' : ''">
        <slot />
      </section>
      <section class="relative hidden min-h-[600px] overflow-hidden bg-paper lg:order-1 lg:block" :class="fullScreen ? 'lg:min-h-screen' : ''">
        <div class="headline-ghost absolute inset-x-0 top-12 text-center text-[34px]">
          <div v-for="line in headline" :key="line">{{ line }}</div>
        </div>
        <FloatingObjects preset="auth" />
        <div class="absolute bottom-0 left-1/2 h-[200px] w-[330px] -translate-x-1/2 overflow-hidden">
          <div class="absolute left-0 top-0">
            <Mascot variant="blob" :size="330" :mood="mood" :shadow="false" />
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
