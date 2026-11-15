<script setup lang="ts" generic="T extends string">
/**
 * 下拉选择：代替浏览器原生的 select。
 * 和系统里其他弹出菜单同一套样式（白底圆角、边框、阴影，当前项打勾）；键盘可用：
 * ↑ ↓ 移动、回车选中、Esc 关闭、Home / End 跳到头尾。
 */
import { Check, ChevronDown } from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'

const props = defineProps<{
  modelValue: T
  options: { value: T; label: string }[]
  disabled?: boolean
  ariaLabel?: string
}>()
const emit = defineEmits<{ 'update:modelValue': [value: T]; change: [value: T] }>()

const id = useId()
const root = ref<HTMLElement | null>(null)
const list = ref<HTMLElement | null>(null)
const open = ref(false)
const active = ref(0)

const selectedIndex = computed(() => Math.max(0, props.options.findIndex((item) => item.value === props.modelValue)))
const current = computed(() => props.options.find((item) => item.value === props.modelValue))

/** 只在列表自己里面滚到当前项，不带着整个页面动 */
function revealActive() {
  const box = list.value
  const item = box?.querySelector<HTMLElement>('[data-active="true"]')
  if (!box || !item) return
  if (item.offsetTop < box.scrollTop) box.scrollTop = item.offsetTop - 6
  else if (item.offsetTop + item.offsetHeight > box.scrollTop + box.clientHeight) box.scrollTop = item.offsetTop + item.offsetHeight - box.clientHeight + 6
}

function show() {
  if (props.disabled || open.value) return
  active.value = selectedIndex.value
  open.value = true
  void nextTick(() => {
    revealActive()
    // 菜单靠近页面底部时往下展开：页面自己滚动，让整个菜单都看得到
    list.value?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  })
}

function hide() {
  open.value = false
}

function choose(index: number) {
  const item = props.options[index]
  if (!item) return
  hide()
  if (item.value === props.modelValue) return
  emit('update:modelValue', item.value)
  emit('change', item.value)
}

function move(step: number) {
  active.value = (active.value + step + props.options.length) % props.options.length
  void nextTick(revealActive)
}

function onKeydown(event: KeyboardEvent) {
  if (props.disabled) return
  if (!open.value) {
    if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) {
      event.preventDefault()
      show()
    }
    return
  }
  if (event.key === 'ArrowDown') move(1)
  else if (event.key === 'ArrowUp') move(-1)
  else if (event.key === 'Home') { active.value = 0; void nextTick(revealActive) }
  else if (event.key === 'End') { active.value = props.options.length - 1; void nextTick(revealActive) }
  else if (event.key === 'Enter' || event.key === ' ') choose(active.value)
  else if (event.key === 'Escape') hide()
  else if (event.key === 'Tab') hide()
  else return
  event.preventDefault()
  event.stopPropagation()
}

function onOutside(event: PointerEvent) {
  if (!root.value?.contains(event.target as Node)) hide()
}

watch(open, (value) => {
  if (value) document.addEventListener('pointerdown', onOutside)
  else document.removeEventListener('pointerdown', onOutside)
})
onBeforeUnmount(() => document.removeEventListener('pointerdown', onOutside))
</script>

<template>
  <div ref="root" class="relative">
    <button
      type="button"
      class="field flex items-center justify-between gap-2 text-left disabled:cursor-not-allowed disabled:opacity-50"
      :class="open ? '!border-ink/40 !ring-4 !ring-black/5' : ''"
      role="combobox"
      aria-haspopup="listbox"
      :aria-expanded="open"
      :aria-controls="`${id}-list`"
      :aria-activedescendant="open ? `${id}-${active}` : undefined"
      :aria-label="ariaLabel"
      :disabled="disabled"
      @click="open ? hide() : show()"
      @keydown="onKeydown"
    >
      <span class="min-w-0 flex-1 truncate">{{ current?.label }}</span>
      <ChevronDown class="size-4 shrink-0 text-mute transition-transform" :class="open ? 'rotate-180' : ''" />
    </button>
    <ul
      v-if="open"
      :id="`${id}-list`"
      ref="list"
      role="listbox"
      class="absolute left-0 right-0 z-30 mt-1.5 max-h-64 scroll-mb-4 animate-pop overflow-y-auto rounded-2xl border border-line bg-white p-1.5 shadow-xl"
    >
      <li
        v-for="(item, index) in options"
        :id="`${id}-${index}`"
        :key="item.value"
        role="option"
        :aria-selected="item.value === modelValue"
        :data-active="index === active"
        class="flex cursor-pointer items-center gap-2 rounded-xl px-3 py-2 text-sm"
        :class="index === active ? 'bg-paper' : ''"
        @pointerenter="active = index"
        @click="choose(index)"
      >
        <span class="min-w-0 flex-1 truncate">{{ item.label }}</span>
        <Check v-if="item.value === modelValue" class="size-4 shrink-0 text-success" />
      </li>
    </ul>
  </div>
</template>
