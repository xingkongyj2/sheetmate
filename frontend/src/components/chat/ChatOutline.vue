<script setup lang="ts">
/**
 * 对话目录：固定在聊天区左侧边缘的极简竖条刻度轨，
 * 悬停或聚焦时平滑展开浮层目录卡片，点击任意轮次平滑定位到对应消息。
 */
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'

import { formatOutlineMeta, type ChatOutlineItem } from '@/composables/chatOutline'

const props = withDefaults(defineProps<{
  title?: string
  items: ChatOutlineItem[]
  activeKey: string | null
  fallbackStart?: string | null
  fallbackEnd?: string | null
}>(), {
  title: '',
  fallbackStart: null,
  fallbackEnd: null,
})

const emit = defineEmits<{ select: [key: string] }>()

const open = ref(false)
const hoveredKey = ref<string | null>(null)
const hoverSource = ref<'rail' | 'list' | null>(null)
const shellEl = ref<HTMLElement | null>(null)
const railEl = ref<HTMLElement | null>(null)
const listEl = ref<HTMLElement | null>(null)
const railItemEls = new Map<string, HTMLElement>()
const listItemEls = new Map<string, HTMLElement>()
const railScroll = reactive({ top: false, bottom: false })
const listScroll = reactive({ top: false, bottom: false })

let openTimer = 0
let closeTimer = 0
let lastPointerType = 'mouse'

const displayTitle = computed(() => props.title?.trim() || '当前对话')
const metaText = computed(() => formatOutlineMeta(props.items, {
  fallbackStart: props.fallbackStart,
  fallbackEnd: props.fallbackEnd,
}))
const hoveredIndex = computed(() => (hoveredKey.value ? props.items.findIndex((item) => item.key === hoveredKey.value) : -1))

function asElement(el: unknown): HTMLElement | null {
  if (!el || typeof el !== 'object') return null
  if (typeof HTMLElement !== 'undefined' && !(el instanceof HTMLElement)) return null
  return el as HTMLElement
}

function setRailItemEl(key: string, el: unknown) {
  const element = asElement(el)
  if (element) railItemEls.set(key, element)
  else railItemEls.delete(key)
}

function setListItemEl(key: string, el: unknown) {
  const element = asElement(el)
  if (element) listItemEls.set(key, element)
  else listItemEls.delete(key)
}

function isNeighbor(index: number): boolean {
  return hoveredIndex.value >= 0 && Math.abs(index - hoveredIndex.value) === 1
}

function clearTimers() {
  window.clearTimeout(openTimer)
  window.clearTimeout(closeTimer)
  openTimer = 0
  closeTimer = 0
}

function scheduleOpen(delay = 65) {
  window.clearTimeout(closeTimer)
  closeTimer = 0
  if (open.value || openTimer) return
  if (delay <= 0) {
    open.value = true
    return
  }
  openTimer = window.setTimeout(() => {
    openTimer = 0
    open.value = true
  }, delay)
}

function scheduleClose(delay = 180) {
  window.clearTimeout(openTimer)
  openTimer = 0
  if (!open.value || closeTimer) return
  if (delay <= 0) {
    open.value = false
    hoveredKey.value = null
    hoverSource.value = null
    return
  }
  closeTimer = window.setTimeout(() => {
    closeTimer = 0
    open.value = false
    hoveredKey.value = null
    hoverSource.value = null
  }, delay)
}

function onShellEnter() {
  if (lastPointerType === 'touch') return
  scheduleOpen(65)
}

function onShellLeave() {
  scheduleClose(180)
}

function onShellPointerDown(event: PointerEvent) {
  lastPointerType = event.pointerType || 'mouse'
}

function onFocusIn() {
  scheduleOpen(0)
}

function onFocusOut(event: FocusEvent) {
  const next = event.relatedTarget as Node | null
  if (next && shellEl.value?.contains(next)) return
  scheduleClose(120)
}

function onRailItemHover(key: string) {
  hoveredKey.value = key
  hoverSource.value = 'rail'
  if (lastPointerType !== 'touch') scheduleOpen(65)
}

function onRailItemFocus(key: string) {
  hoveredKey.value = key
  hoverSource.value = 'rail'
}

function onRailItemClick(key: string) {
  if (lastPointerType === 'touch' && !open.value) {
    hoveredKey.value = key
    hoverSource.value = 'rail'
    scheduleOpen(0)
    return
  }
  emit('select', key)
}

function onRowEnter(key: string) {
  hoveredKey.value = key
  hoverSource.value = 'list'
}

function onRowLeave(key: string) {
  if (hoveredKey.value === key) {
    hoveredKey.value = null
    hoverSource.value = null
  }
}

function onRowClick(key: string) {
  emit('select', key)
  scheduleClose(0)
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && open.value) {
    event.preventDefault()
    scheduleClose(0)
    ;(document.activeElement as HTMLElement | null)?.blur?.()
    return
  }
  if (!props.items.length || (event.key !== 'ArrowDown' && event.key !== 'ArrowUp')) return
  event.preventDefault()
  const current = hoveredKey.value ?? props.activeKey
  const index = current ? props.items.findIndex((item) => item.key === current) : -1
  const delta = event.key === 'ArrowDown' ? 1 : -1
  const nextIndex = index < 0
    ? (delta > 0 ? 0 : props.items.length - 1)
    : (index + delta + props.items.length) % props.items.length
  const target = props.items[nextIndex]
  if (!target) return
  hoveredKey.value = target.key
  hoverSource.value = 'list'
  if (!open.value) scheduleOpen(0)
  void nextTick(() => listItemEls.get(target.key)?.focus())
}

function updateScrollShadows(container: HTMLElement | null, target: { top: boolean; bottom: boolean }) {
  if (!container) {
    target.top = false
    target.bottom = false
    return
  }
  const overflow = container.scrollHeight - container.clientHeight
  target.top = overflow > 2 && container.scrollTop > 4
  target.bottom = overflow > 2 && container.scrollTop < overflow - 4
}

function updateRailScroll() {
  updateScrollShadows(railEl.value, railScroll)
}

function updateListScroll() {
  updateScrollShadows(listEl.value, listScroll)
}

/** 只滚动自身容器，避免干扰聊天正文滚动位置。用 offsetTop 避免入场 scale 动画影响测距。 */
function ensureVisibleInContainer(
  container: HTMLElement | null,
  itemEl: HTMLElement | undefined,
  mode: 'center' | 'nearest',
  smooth = false,
) {
  if (!container || !itemEl || container.scrollHeight <= container.clientHeight + 2) return
  const itemTop = itemEl.offsetTop
  const itemHeight = itemEl.offsetHeight
  const itemBottom = itemTop + itemHeight
  const pad = 16

  let nextScrollTop: number | null = null
  if (mode === 'center') {
    nextScrollTop = itemTop - (container.clientHeight - itemHeight) / 2
  } else if (itemTop < container.scrollTop + pad) {
    nextScrollTop = itemTop - pad
  } else if (itemBottom > container.scrollTop + container.clientHeight - pad) {
    nextScrollTop = itemBottom - container.clientHeight + pad
  }
  if (nextScrollTop === null) return

  const clamped = Math.max(0, Math.min(container.scrollHeight - container.clientHeight, nextScrollTop))
  if (smooth && !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
    container.scrollTo({ top: clamped, behavior: 'smooth' })
  } else {
    container.scrollTop = clamped
  }
}

function syncListPosition(mode: 'center' | 'nearest', smooth = false) {
  const focusKey = hoveredKey.value ?? props.activeKey
  if (focusKey) ensureVisibleInContainer(listEl.value, listItemEls.get(focusKey), mode, smooth)
  updateListScroll()
}

function syncListAfterOpen() {
  updateListScroll()
}

watch(open, async (isOpen) => {
  if (!isOpen) return
  await nextTick()
  syncListPosition('center', false)
})

watch([hoveredKey, hoverSource], async ([key, source]) => {
  if (!key) return
  await nextTick()
  if (source === 'rail' && open.value) {
    ensureVisibleInContainer(listEl.value, listItemEls.get(key), 'nearest', true)
    updateListScroll()
  } else if (source === 'list') {
    ensureVisibleInContainer(railEl.value, railItemEls.get(key), 'nearest', true)
    updateRailScroll()
  }
})

watch(() => props.activeKey, async (key) => {
  if (!key) return
  await nextTick()
  ensureVisibleInContainer(railEl.value, railItemEls.get(key), 'nearest', true)
  updateRailScroll()
})

watch(() => props.items.length, async () => {
  await nextTick()
  updateRailScroll()
  if (open.value) updateListScroll()
})

onBeforeUnmount(() => {
  clearTimers()
})
</script>

<template>
  <div class="chat-outline-layer" data-testid="chat-outline">
    <div
      ref="shellEl"
      class="chat-outline-shell"
      :class="{ 'is-open': open }"
      @pointerdown="onShellPointerDown"
      @mouseenter="onShellEnter"
      @mouseleave="onShellLeave"
      @focusin="onFocusIn"
      @focusout="onFocusOut"
      @keydown="onKeydown"
    >
      <nav
        ref="railEl"
        class="chat-outline-rail"
        :class="{
          'is-open': open,
          'is-compact': items.length > 18 && items.length <= 30,
          'is-dense': items.length > 30,
          'has-top-fade': railScroll.top,
          'has-bottom-fade': railScroll.bottom,
        }"
        aria-label="对话目录"
        @scroll.passive="updateRailScroll"
      >
        <TransitionGroup name="outline-tick" tag="div" class="chat-outline-track">
          <button
            v-for="(item, idx) in items"
            :key="item.key"
            :ref="(el) => setRailItemEl(item.key, el)"
            type="button"
            class="outline-tick-btn"
            :class="{
              'is-active': item.key === activeKey,
              'is-hovered': item.key === hoveredKey,
              'is-neighbor': isNeighbor(idx),
              'is-running': item.running,
            }"
            :aria-label="`跳转到第 ${item.index} 轮：${item.fullLabel}`"
            :aria-current="item.key === activeKey ? 'true' : undefined"
            :data-outline-key="item.key"
            @mouseenter="onRailItemHover(item.key)"
            @focus="onRailItemFocus(item.key)"
            @click="onRailItemClick(item.key)"
          >
            <span
              class="outline-tick"
              :class="item.weight === 'bar' ? 'is-bar' : 'is-dot'"
              aria-hidden="true"
            />
          </button>
        </TransitionGroup>
      </nav>

      <Transition name="outline-popover" @after-enter="syncListAfterOpen">
        <div v-if="open" class="chat-outline-popover-bridge">
          <section
            class="chat-outline-popover"
            aria-label="对话目录列表"
            data-testid="chat-outline-popover"
          >
            <header class="chat-outline-header" :class="{ 'is-scrolled': listScroll.top }">
              <h3 class="chat-outline-title">{{ displayTitle }}</h3>
              <p v-if="metaText" class="chat-outline-meta">{{ metaText }}</p>
            </header>

            <div
              ref="listEl"
              class="chat-outline-list"
              :class="{
                'has-top-fade': listScroll.top,
                'has-bottom-fade': listScroll.bottom,
              }"
              role="list"
              @scroll.passive="updateListScroll"
            >
              <button
                v-for="item in items"
                :key="item.key"
                :ref="(el) => setListItemEl(item.key, el)"
                type="button"
                role="listitem"
                class="chat-outline-row"
                :class="{
                  'is-active': item.key === activeKey,
                  'is-hovered': item.key === hoveredKey,
                }"
                :title="item.fullLabel"
                :aria-current="item.key === activeKey ? 'true' : undefined"
                :data-outline-row="item.key"
                @mouseenter="onRowEnter(item.key)"
                @mouseleave="onRowLeave(item.key)"
                @click="onRowClick(item.key)"
              >
                <span class="chat-outline-indicator" aria-hidden="true" />
                <span class="chat-outline-time">{{ item.timeLabel || `#${item.index}` }}</span>
                <span class="chat-outline-summary">
                  <span class="chat-outline-text">{{ item.text }}</span>
                  <span v-if="item.attachmentNote" class="chat-outline-attachment">（{{ item.attachmentNote }}）</span>
                </span>
                <span v-if="item.running" class="chat-outline-live" aria-label="回复中" title="回复中" />
              </button>
            </div>
          </section>
        </div>
      </Transition>
    </div>
  </div>
</template>

<style scoped>
.chat-outline-layer {
  position: absolute;
  top: 12px;
  bottom: 112px;
  left: 10px;
  z-index: 20;
  display: flex;
  align-items: center;
  max-width: calc(100% - 20px);
  pointer-events: none;
}

.chat-outline-shell {
  display: flex;
  align-items: center;
  max-height: 100%;
  pointer-events: auto;
}

/* 左侧竖条刻度轨 */
.chat-outline-rail {
  position: relative;
  display: flex;
  flex: none;
  flex-direction: column;
  align-items: center;
  max-height: min(336px, calc(100vh - 210px));
  max-height: min(336px, calc(100dvh - 210px));
  padding: 9px 2px;
  border: 1px solid transparent;
  border-radius: 999px;
  background: transparent;
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-width: none;
  transition:
    background-color 220ms cubic-bezier(0.22, 1, 0.36, 1),
    border-color 220ms cubic-bezier(0.22, 1, 0.36, 1),
    box-shadow 240ms cubic-bezier(0.22, 1, 0.36, 1);
}

.chat-outline-rail::-webkit-scrollbar {
  display: none;
}

.chat-outline-rail.is-open,
.chat-outline-rail:hover {
  border-color: rgba(229, 227, 222, 0.92);
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(8px);
  box-shadow:
    0 4px 16px -4px rgba(15, 14, 12, 0.08),
    0 1px 3px rgba(15, 14, 12, 0.04);
}

.chat-outline-rail.has-top-fade {
  mask-image: linear-gradient(to bottom, transparent 0, #000 18px, #000 100%);
}
.chat-outline-rail.has-bottom-fade {
  mask-image: linear-gradient(to bottom, #000 0, #000 calc(100% - 18px), transparent 100%);
}
.chat-outline-rail.has-top-fade.has-bottom-fade {
  mask-image: linear-gradient(to bottom, transparent 0, #000 18px, #000 calc(100% - 18px), transparent 100%);
}

.chat-outline-track {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.outline-tick-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  padding: 3.5px 0;
  border: 0;
  background: transparent;
  cursor: pointer;
  outline: none;
}

.chat-outline-rail.is-compact .outline-tick-btn {
  padding: 2.5px 0;
}

.chat-outline-rail.is-dense .outline-tick-btn {
  padding: 2px 0;
}

.outline-tick-btn:focus-visible .outline-tick {
  box-shadow: 0 0 0 3px rgba(11, 11, 12, 0.16);
}

.outline-tick {
  display: block;
  border-radius: 999px;
  background: rgba(11, 11, 12, 0.22);
  transition:
    width 220ms cubic-bezier(0.22, 1, 0.36, 1),
    height 220ms cubic-bezier(0.22, 1, 0.36, 1),
    background-color 180ms ease,
    box-shadow 220ms ease,
    opacity 180ms ease;
}

.outline-tick.is-dot {
  width: 4px;
  height: 4px;
}

.outline-tick.is-bar {
  width: 3px;
  height: 9px;
}

.outline-tick-btn.is-neighbor .outline-tick {
  background: rgba(11, 11, 12, 0.36);
}
.outline-tick-btn.is-neighbor .outline-tick.is-dot {
  width: 4.5px;
  height: 4.5px;
}
.outline-tick-btn.is-neighbor .outline-tick.is-bar {
  width: 3.5px;
  height: 10px;
}

.outline-tick-btn.is-hovered .outline-tick {
  width: 5px;
  height: 13px;
  background: rgba(11, 11, 12, 0.56);
}

.outline-tick-btn.is-active .outline-tick {
  background: var(--color-ink);
  box-shadow: 0 0 0 2.5px rgba(11, 11, 12, 0.09);
}
.outline-tick-btn.is-active .outline-tick.is-dot {
  width: 5px;
  height: 5px;
}
.outline-tick-btn.is-active .outline-tick.is-bar {
  width: 4.5px;
  height: 13px;
}

.outline-tick-btn.is-active.is-hovered .outline-tick {
  width: 5.5px;
  height: 15px;
  background: var(--color-ink);
  box-shadow: 0 0 0 3px rgba(11, 11, 12, 0.13);
}

.outline-tick-btn.is-running .outline-tick {
  animation: outline-tick-pulse 1.8s ease-in-out infinite;
}

@keyframes outline-tick-pulse {
  0%, 100% {
    box-shadow: 0 0 0 2px rgba(11, 11, 12, 0.12);
  }
  50% {
    box-shadow: 0 0 0 4.5px rgba(11, 11, 12, 0.04);
  }
}

/* 刻度新增动画 */
.outline-tick-enter-active,
.outline-tick-leave-active {
  transition: opacity 220ms ease, transform 240ms cubic-bezier(0.22, 1, 0.36, 1);
}
.outline-tick-enter-from,
.outline-tick-leave-to {
  opacity: 0;
  transform: scale(0.4);
}
.outline-tick-move {
  transition: transform 240ms cubic-bezier(0.22, 1, 0.36, 1);
}

/* 悬浮目录卡片（左侧含 10px 悬停桥接区，鼠标斜向移入卡片不会误关闭） */
.chat-outline-popover-bridge {
  display: flex;
  max-height: 100%;
  padding-left: 10px;
}

.chat-outline-popover {
  display: flex;
  flex-direction: column;
  width: min(318px, calc(100cqi - 56px));
  max-height: min(410px, calc(100vh - 200px));
  max-height: min(410px, calc(100dvh - 200px));
  overflow: hidden;
  border: 1px solid #e5e3de;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.96);
  backdrop-filter: blur(14px);
  box-shadow:
    0 14px 38px -10px rgba(15, 14, 12, 0.14),
    0 2px 8px rgba(15, 14, 12, 0.05);
}

.chat-outline-header {
  flex: none;
  padding: 14px 16px 10px;
  border-bottom: 1px solid transparent;
  transition: border-color 180ms ease;
}

.chat-outline-header.is-scrolled {
  border-bottom-color: rgba(232, 230, 227, 0.85);
}

.chat-outline-title {
  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  color: var(--color-ink);
  font-size: 13.5px;
  font-weight: 600;
  line-height: 1.38;
  overflow-wrap: anywhere;
}

.chat-outline-meta {
  margin-top: 4px;
  color: var(--color-faint);
  font-size: 11.5px;
  line-height: 1.4;
  font-variant-numeric: tabular-nums;
}

.chat-outline-list {
  position: relative;
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 2px 8px 10px;
}

.chat-outline-list.has-top-fade {
  mask-image: linear-gradient(to bottom, transparent 0, #000 18px, #000 100%);
}
.chat-outline-list.has-bottom-fade {
  mask-image: linear-gradient(to bottom, #000 0, #000 calc(100% - 24px), transparent 100%);
}
.chat-outline-list.has-top-fade.has-bottom-fade {
  mask-image: linear-gradient(to bottom, transparent 0, #000 18px, #000 calc(100% - 24px), transparent 100%);
}

.chat-outline-row {
  position: relative;
  display: flex;
  width: 100%;
  align-items: center;
  gap: 8px;
  padding: 6px 10px 6px 12px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: var(--color-mute);
  text-align: left;
  cursor: pointer;
  outline: none;
  transition:
    background-color 160ms ease,
    color 160ms ease,
    transform 160ms cubic-bezier(0.22, 1, 0.36, 1);
}

.chat-outline-row + .chat-outline-row {
  margin-top: 1px;
}

.chat-outline-row:hover,
.chat-outline-row.is-hovered {
  background: rgba(11, 11, 12, 0.045);
  color: var(--color-ink);
}

.chat-outline-row.is-active {
  background: var(--color-paper);
  color: var(--color-ink);
  font-weight: 500;
}

.chat-outline-row.is-active:hover,
.chat-outline-row.is-active.is-hovered {
  background: #eceae5;
}

.chat-outline-row:active {
  transform: scale(0.99);
}

.chat-outline-row:focus-visible {
  box-shadow: inset 0 0 0 1.5px rgba(11, 11, 12, 0.35);
}

.chat-outline-indicator {
  position: absolute;
  left: 4px;
  top: 50%;
  width: 2.5px;
  height: 0;
  border-radius: 999px;
  background: var(--color-ink);
  opacity: 0;
  transform: translateY(-50%);
  transition:
    height 200ms cubic-bezier(0.22, 1, 0.36, 1),
    opacity 160ms ease;
}

.chat-outline-row.is-active .chat-outline-indicator {
  height: 12px;
  opacity: 1;
}

.chat-outline-time {
  flex: none;
  width: 36px;
  color: var(--color-faint);
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1.45;
  font-variant-numeric: tabular-nums;
  transition: color 160ms ease;
}

.chat-outline-row.is-active .chat-outline-time,
.chat-outline-row:hover .chat-outline-time,
.chat-outline-row.is-hovered .chat-outline-time {
  color: var(--color-mute);
}

.chat-outline-summary {
  min-width: 0;
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12.5px;
  line-height: 1.45;
}

.chat-outline-attachment {
  color: var(--color-faint);
  font-size: 11.5px;
  font-weight: 400;
}

.chat-outline-live {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: var(--color-ink);
  animation: outline-live-blink 1.4s ease-in-out infinite;
}

@keyframes outline-live-blink {
  0%, 100% {
    opacity: 0.9;
    transform: scale(1);
  }
  50% {
    opacity: 0.25;
    transform: scale(0.78);
  }
}

/* 浮层展开 / 收起过渡 */
.outline-popover-enter-active {
  transition:
    opacity 210ms cubic-bezier(0.22, 1, 0.36, 1),
    transform 250ms cubic-bezier(0.22, 1, 0.36, 1);
  transform-origin: left center;
}

.outline-popover-leave-active {
  pointer-events: none;
  transition:
    opacity 150ms cubic-bezier(0.4, 0, 1, 1),
    transform 160ms cubic-bezier(0.4, 0, 1, 1);
  transform-origin: left center;
}

.outline-popover-enter-from {
  opacity: 0;
  transform: translate3d(-10px, 0, 0) scale(0.965);
}

.outline-popover-leave-to {
  opacity: 0;
  transform: translate3d(-6px, 0, 0) scale(0.98);
}

@media (prefers-reduced-motion: reduce) {
  .chat-outline-rail,
  .outline-tick,
  .outline-tick-enter-active,
  .outline-tick-leave-active,
  .outline-tick-move,
  .chat-outline-header,
  .chat-outline-row,
  .chat-outline-indicator,
  .chat-outline-time,
  .outline-popover-enter-active,
  .outline-popover-leave-active {
    transition: none;
  }
  .outline-tick-btn.is-running .outline-tick,
  .chat-outline-live {
    animation: none;
  }
}
</style>
