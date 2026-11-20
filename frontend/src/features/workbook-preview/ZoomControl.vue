<script setup lang="ts">
/** 底部右侧固定宽度缩放控件：− 滑块 + 百分比下拉（与左侧 Sheet 栏共用同一行）。 */
import { Check, ChevronDown, Minus, Plus } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const PRESETS = [50, 75, 90, 100, 125, 150, 200]
const MIN = 50
const MAX = 200

const props = defineProps<{
  modelValue: number
  disabled?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [ratio: number]
}>()

const open = ref(false)
const root = ref<HTMLElement>()

const percent = computed(() => Math.min(MAX, Math.max(MIN, Math.round(props.modelValue * 100))))

function setPercent(next: number) {
  const clamped = Math.min(MAX, Math.max(MIN, Math.round(next)))
  emit('update:modelValue', clamped / 100)
}

function step(delta: number) {
  if (props.disabled) return
  setPercent(Math.round((percent.value + delta) / 10) * 10)
}

function onRangeInput(event: Event) {
  if (props.disabled) return
  setPercent(Number((event.target as HTMLInputElement).value))
}

function choosePreset(value: number) {
  open.value = false
  setPercent(value)
}

function onOutside(event: Event) {
  if (!open.value) return
  if (event instanceof KeyboardEvent) {
    if (event.key === 'Escape') open.value = false
    return
  }
  if (!root.value?.contains(event.target as Node)) open.value = false
}

onMounted(() => {
  window.addEventListener('pointerdown', onOutside)
  window.addEventListener('keydown', onOutside)
})
onBeforeUnmount(() => {
  window.removeEventListener('pointerdown', onOutside)
  window.removeEventListener('keydown', onOutside)
})
</script>

<template>
  <div ref="root" class="relative flex w-[204px] shrink-0 select-none items-center justify-end gap-1.5" :class="{ 'opacity-45': disabled }">
    <button
      type="button"
      class="zoom-step"
      :disabled="disabled || percent <= MIN"
      title="缩小"
      aria-label="缩小"
      @click="step(-10)"
    >
      <Minus class="size-3.5" />
    </button>

    <input
      type="range"
      :min="MIN"
      :max="MAX"
      step="5"
      :value="percent"
      :disabled="disabled"
      aria-label="缩放比例"
      class="zoom-slider"
      :style="{ '--pct': `${((percent - MIN) / (MAX - MIN)) * 100}%` }"
      @input="onRangeInput"
    >

    <button
      type="button"
      class="zoom-step"
      :disabled="disabled || percent >= MAX"
      title="放大"
      aria-label="放大"
      @click="step(10)"
    >
      <Plus class="size-3.5" />
    </button>

    <button
      type="button"
      class="zoom-pct"
      :disabled="disabled"
      aria-haspopup="menu"
      :aria-expanded="open"
      title="选择缩放比例"
      @click="open = !open"
    >
      <span class="tabular-nums">{{ percent }}%</span>
      <ChevronDown class="size-3 text-faint" />
    </button>

    <div
      v-if="open && !disabled"
      class="absolute bottom-9 right-0 z-40 w-24 rounded-xl border border-line bg-white p-1 shadow-[0_8px_24px_rgba(0,0,0,0.08)]"
      role="menu"
    >
      <button
        v-for="item in PRESETS"
        :key="item"
        type="button"
        role="menuitem"
        class="flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-xs tabular-nums text-ink transition hover:bg-paper"
        :class="{ 'bg-paper/80 font-medium': percent === item }"
        @click="choosePreset(item)"
      >
        <span>{{ item }}%</span>
        <Check v-if="percent === item" class="size-3 text-ink" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.zoom-step {
  display: inline-flex;
  width: 24px;
  height: 24px;
  flex: none;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  color: #6b6863;
  transition: background-color .15s, color .15s;
}
.zoom-step:hover:not(:disabled) {
  background: rgb(11 11 12 / 0.055);
  color: #111;
}
.zoom-step:disabled {
  opacity: 0.35;
  cursor: default;
}

.zoom-slider {
  width: 84px;
  height: 16px;
  flex: none;
  cursor: pointer;
  appearance: none;
  background: transparent;
}
.zoom-slider:disabled {
  cursor: default;
}
.zoom-slider::-webkit-slider-runnable-track {
  height: 3px;
  border-radius: 9999px;
  background: linear-gradient(to right, #18181b var(--pct, 33%), #dcdad6 var(--pct, 33%));
}
.zoom-slider::-webkit-slider-thumb {
  width: 11px;
  height: 11px;
  margin-top: -4px;
  border-radius: 9999px;
  border: 1.5px solid #18181b;
  background: #fff;
  box-shadow: 0 1px 2px rgb(0 0 0 / 0.14);
  appearance: none;
  transition: transform .12s;
}
.zoom-slider:hover:not(:disabled)::-webkit-slider-thumb {
  transform: scale(1.12);
}
.zoom-slider::-moz-range-track {
  height: 3px;
  border-radius: 9999px;
  background: #dcdad6;
}
.zoom-slider::-moz-range-progress {
  height: 3px;
  border-radius: 9999px;
  background: #18181b;
}
.zoom-slider::-moz-range-thumb {
  width: 11px;
  height: 11px;
  border-radius: 9999px;
  border: 1.5px solid #18181b;
  background: #fff;
  box-shadow: 0 1px 2px rgb(0 0 0 / 0.14);
}

.zoom-pct {
  display: inline-flex;
  width: 58px;
  height: 26px;
  flex: none;
  align-items: center;
  justify-content: space-between;
  border-radius: 7px;
  border: 1px solid var(--color-line);
  background: #fff;
  padding: 0 6px 0 8px;
  font-size: 11.5px;
  color: #111;
  transition: border-color .15s, background-color .15s;
}
.zoom-pct:hover:not(:disabled) {
  border-color: rgb(11 11 12 / 0.25);
}
.zoom-pct:disabled {
  cursor: default;
}
</style>
