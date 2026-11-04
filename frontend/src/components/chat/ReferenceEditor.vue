<script setup lang="ts">
/** Native text editing with atomic file and worksheet labels. */
import { FileSpreadsheet, Table2 } from '@lucide/vue'
import { computed, h, nextTick, onBeforeUnmount, onMounted, ref, render as renderVNode, useId, watch } from 'vue'

import {
  formatFileReference, formatTableReference, isFileReference, parseTableReferences,
  projectTableReferences, updateTableReferenceText,
  type ComposerReference, type FileReference, type TableReference,
} from '@/composables/tableReferences'
import { findReferenceQuery, referenceSuggestions, type ReferenceSuggestion } from '@/composables/referenceSuggestions'

const props = withDefaults(defineProps<{
  modelValue: string
  tableReferences?: TableReference[]
  fileReferences?: FileReference[]
  referencesLoading?: boolean
  referencesError?: string
  placeholder?: string
  autofocus?: boolean
  large?: boolean
  busy?: boolean
}>(), { tableReferences: () => [], fileReferences: () => [], referencesLoading: false, referencesError: '', placeholder: '', autofocus: false, large: false, busy: false })
const emit = defineEmits<{ 'update:modelValue': [value: string]; send: [] }>()
const editor = ref<HTMLDivElement | null>(null)
const empty = ref(!props.modelValue)
let composing = false
const CARET_BUFFER = '\u200b'
let selection = { start: 0, end: 0 }
type Snapshot = { value: string; selection: typeof selection }
let history: Snapshot[] = []
let historyIndex = 0
const listId = useId()
const query = ref<ReturnType<typeof findReferenceQuery>>(null)
const activeSuggestion = ref(0)
const suggestions = computed(() => query.value ? referenceSuggestions(query.value.trigger, query.value.query, props.tableReferences, props.fileReferences) : [])
const loadingSuggestions = computed(() => query.value?.trigger === '#' && props.referencesLoading)
let dismissed: { start: number; trigger: string } | null = null
const referenceIcons = new Map<'file' | 'table', SVGSVGElement>()

function referenceIcon(kind: 'file' | 'table') {
  let template = referenceIcons.get(kind)
  if (!template) {
    const host = document.createElement('span')
    renderVNode(h(kind === 'file' ? FileSpreadsheet : Table2, {
      size: 14,
      strokeWidth: 1.8,
      class: `composer-reference__icon composer-reference__icon--${kind}`,
      'aria-hidden': 'true',
      focusable: 'false',
    }), host)
    template = host.firstElementChild!.cloneNode(true) as SVGSVGElement
    renderVNode(null, host)
    referenceIcons.set(kind, template)
  }
  // Decorative SVGs contain no text, so editor offsets and copied names stay unchanged.
  return template.cloneNode(true)
}

function chip(node: Node): node is HTMLSpanElement {
  return node instanceof HTMLElement && node.hasAttribute('data-reference-source')
}

function contents(node: Node, canonical = false): string {
  if (node.nodeType === Node.TEXT_NODE) return (node.textContent ?? '').replaceAll(CARET_BUFFER, '')
  if (chip(node)) return canonical ? node.dataset.referenceSource! : node.textContent ?? ''
  if (node instanceof HTMLBRElement) return '\n'
  let value = ''
  for (const child of node.childNodes) {
    const block = child instanceof HTMLElement && /^(DIV|P)$/.test(child.tagName)
    if (block && value && !value.endsWith('\n')) value += '\n'
    value += contents(child, canonical)
  }
  // Browsers keep one placeholder BR in an otherwise empty editable element.
  return node.childNodes.length === 1 && node.firstChild instanceof HTMLBRElement ? '' : value
}

function read() { return editor.value ? contents(editor.value, true) : props.modelValue }

function offsetAt(node: Node, offset: number) {
  const range = document.createRange()
  range.selectNodeContents(editor.value!)
  range.setEnd(node, offset)
  return contents(range.cloneContents()).length
}

function captureSelection() {
  const current = window.getSelection()
  if (!editor.value || !current?.rangeCount) return
  const range = current.getRangeAt(0)
  if (!editor.value.contains(range.startContainer) || !editor.value.contains(range.endContainer)) return
  selection = { start: offsetAt(range.startContainer, range.startOffset), end: offsetAt(range.endContainer, range.endOffset) }
}

function pointAt(offset: number): { node: Node; offset: number } {
  const root = editor.value!
  let position = 0
  for (const node of root.childNodes) {
    const length = contents(node).length
    if (chip(node)) {
      if (offset <= position) return { node: root, offset: [...root.childNodes].indexOf(node) }
      if (offset < position + length) return { node: root, offset: [...root.childNodes].indexOf(node) + 1 }
    } else if (node.nodeType === Node.TEXT_NODE && offset <= position + length) {
      const target = Math.max(0, offset - position)
      const text = node.textContent ?? ''
      let raw = 0
      let visible = 0
      while (raw < text.length && (visible < target || text[raw] === CARET_BUFFER)) {
        if (text[raw] !== CARET_BUFFER) visible++
        raw++
      }
      return { node, offset: raw }
    }
    position += length
  }
  return { node: root, offset: root.childNodes.length }
}

function restoreSelection(next = selection) {
  if (!editor.value) return
  const start = pointAt(next.start)
  const end = pointAt(next.end)
  const range = document.createRange()
  range.setStart(start.node, start.offset)
  range.setEnd(end.node, end.offset)
  const current = window.getSelection()
  current?.removeAllRanges()
  current?.addRange(range)
  selection = { ...next }
}

function render(value: string) {
  const root = editor.value
  if (!root) return
  const fragment = document.createDocumentFragment()
  for (const segment of parseTableReferences(value, props.tableReferences, props.fileReferences)) {
    if (segment.type === 'text') {
      fragment.append(document.createTextNode(segment.text))
      continue
    }
    const label = document.createElement('span')
    label.contentEditable = 'false'
    label.className = `composer-reference composer-reference--${segment.type}`
    label.dataset.referenceSource = segment.text
    label.dataset.referenceKind = segment.type
    const explicit = segment.text.startsWith('【表格：') || segment.text.startsWith('【文件：') || segment.text.startsWith('<table-ref>')
    label.textContent = explicit ? segment.type === 'file' ? segment.reference.filename : segment.reference.sheet : segment.text
    label.prepend(referenceIcon(segment.type))
    label.title = segment.type === 'file' ? segment.reference.filename : `${segment.reference.filename} / ${segment.reference.sheet}`
    // Text caret hosts on both sides keep the cursor visible beside a noneditable label.
    fragment.append(document.createTextNode(CARET_BUFFER), label, document.createTextNode(CARET_BUFFER))
  }
  root.replaceChildren(fragment)
  empty.value = !contents(root)
}

function remember(value: string) {
  if (history[historyIndex]?.value === value) {
    history[historyIndex]!.selection = { ...selection }
    return
  }
  history = history.slice(0, historyIndex + 1)
  history.push({ value, selection: { ...selection } })
  if (history.length > 150) history.shift()
  historyIndex = history.length - 1
}

function rememberSelection() {
  captureSelection()
  if (history[historyIndex]) history[historyIndex]!.selection = { ...selection }
}

function commit(value: string, next = selection) {
  render(value)
  restoreSelection(next)
  remember(value)
  emit('update:modelValue', value)
}

function undo(direction: -1 | 1) {
  const index = historyIndex + direction
  const snapshot = history[index]
  if (!snapshot) return
  historyIndex = index
  render(snapshot.value)
  restoreSelection(snapshot.selection)
  emit('update:modelValue', snapshot.value)
}

/** A selection touching any part of a label always includes the full label. */
function atomicSelection(current = selection) {
  const result = { ...current }
  const projection = projectTableReferences(read(), props.tableReferences, props.fileReferences)
  for (const range of projection.ranges) {
    if (result.start > range.start && result.start < range.end) result.start = range.start
    if (result.end > range.start && result.end < range.end) result.end = range.end
  }
  return result
}

function replaceSelection(text: string, visibleLength = text.length) {
  if (!editor.value) return
  rememberSelection()
  const current = atomicSelection()
  const value = read()
  const visible = projectTableReferences(value, props.tableReferences, props.fileReferences).text
  const next = updateTableReferenceText(value, visible.slice(0, current.start) + text + visible.slice(current.end), props.tableReferences, current, props.fileReferences)
  const caret = current.start + visibleLength
  editor.value.focus()
  commit(next, { start: caret, end: caret })
}

function insertReference(reference: ComposerReference) {
  query.value = null
  dismissed = null
  const file = isFileReference(reference)
  replaceSelection(file ? formatFileReference(reference) : formatTableReference(reference), file ? reference.filename.length : reference.sheet.length)
  const caret = { ...selection }
  // Closing a preview restores its previous focus during unmount, so focus after it closes.
  void nextTick(() => { editor.value?.focus(); restoreSelection(caret) })
}

function chooseSuggestion(suggestion: ReferenceSuggestion) {
  if (!query.value) return
  selection = { start: query.value.start, end: query.value.end }
  restoreSelection()
  insertReference(suggestion.reference)
}

function refreshSuggestions(automatic = false) {
  if (composing || !editor.value || document.activeElement !== editor.value) return
  captureSelection()
  if (selection.start !== selection.end) { query.value = null; return }
  const projection = projectTableReferences(read(), props.tableReferences, props.fileReferences)
  const next = findReferenceQuery(projection.text, selection.start, projection.ranges)
  if (!next) { query.value = null; dismissed = null; return }
  if (dismissed?.start === next.start && dismissed.trigger === next.trigger) return
  if (query.value?.start !== next.start || query.value.query !== next.query || query.value.trigger !== next.trigger) activeSuggestion.value = 0
  query.value = next
  const incomplete = next.trigger === '#' && (props.referencesLoading || !!props.referencesError)
  if (automatic && !incomplete && suggestions.value.length === 1) chooseSuggestion(suggestions.value[0]!)
}

function dismissSuggestions() {
  if (query.value) dismissed = { start: query.value.start, trigger: query.value.trigger }
  query.value = null
}

function deleteReference(backward: boolean) {
  captureSelection()
  const current = atomicSelection()
  const ranges = projectTableReferences(read(), props.tableReferences, props.fileReferences).ranges
  if (current.start === current.end) {
    const adjacent = ranges.find((range) => backward ? range.end === current.start : range.start === current.end)
    if (!adjacent) return false
    selection = { start: adjacent.start, end: adjacent.end }
  } else {
    if (!ranges.some((range) => range.start < current.end && range.end > current.start)) return false
    selection = current
  }
  // Keep the saved range while the preview/attachment buttons have focus.
  restoreSelection(selection)
  replaceSelection('')
  return true
}

function onInput() {
  const value = read()
  empty.value = !value
  if (composing) { emit('update:modelValue', value); return }
  captureSelection()
  commit(value)
  refreshSuggestions(true)
}

function onBeforeInput(event: InputEvent) {
  if (composing || event.isComposing) return
  rememberSelection()
  if (event.inputType === 'historyUndo' || event.inputType === 'historyRedo') {
    event.preventDefault()
    undo(event.inputType === 'historyUndo' ? -1 : 1)
  } else if (event.inputType === 'insertParagraph' || event.inputType === 'insertLineBreak') {
    event.preventDefault()
    replaceSelection('\n')
  } else if (event.inputType === 'deleteContentBackward' || event.inputType === 'deleteContentForward') {
    if (deleteReference(event.inputType === 'deleteContentBackward')) event.preventDefault()
  } else if (selection.start !== selection.end) {
    const current = atomicSelection()
    if (event.inputType === 'insertText' && (current.start !== selection.start || current.end !== selection.end)) {
      event.preventDefault()
      replaceSelection(event.data ?? '')
      refreshSuggestions(true)
    } else restoreSelection(current)
  }
}

function onKeydown(event: KeyboardEvent) {
  if (composing || event.isComposing) return
  if (query.value) {
    if (event.key === 'Escape') { event.preventDefault(); dismissSuggestions(); return }
    if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && suggestions.value.length) {
      event.preventDefault()
      activeSuggestion.value = (activeSuggestion.value + (event.key === 'ArrowDown' ? 1 : -1) + suggestions.value.length) % suggestions.value.length
      void nextTick(() => document.getElementById(`${listId}-${activeSuggestion.value}`)?.scrollIntoView({ block: 'nearest' }))
      return
    }
    if (event.key === 'Tab' && (event.shiftKey || !suggestions.value.length)) {
      dismissSuggestions()
      return
    }
    if (event.key === 'Enter' || event.key === 'Tab') {
      event.preventDefault()
      const suggestion = suggestions.value[activeSuggestion.value]
      if (suggestion) chooseSuggestion(suggestion)
      return
    }
  }
  if ((event.metaKey || event.ctrlKey) && !event.altKey) {
    if (event.key.toLowerCase() === 'z' || event.key.toLowerCase() === 'y') {
      event.preventDefault()
      undo(event.shiftKey || event.key.toLowerCase() === 'y' ? 1 : -1)
      return
    }
  }
  rememberSelection()
  if (event.key === 'Enter') {
    event.preventDefault()
    if (event.shiftKey) replaceSelection('\n')
    else if (!props.busy && read().trim()) emit('send')
  } else if (event.key === 'Backspace' || event.key === 'Delete') {
    if (deleteReference(event.key === 'Backspace')) event.preventDefault()
  }
}

function onPaste(event: ClipboardEvent) {
  event.preventDefault()
  replaceSelection(event.clipboardData?.getData('text/plain') ?? '')
  refreshSuggestions(true)
}

function onCopy(event: ClipboardEvent, cut = false) {
  captureSelection()
  if (selection.start === selection.end) return
  event.preventDefault()
  selection = atomicSelection()
  const text = projectTableReferences(read(), props.tableReferences, props.fileReferences).text.slice(selection.start, selection.end)
  event.clipboardData?.setData('text/plain', text)
  if (cut) { restoreSelection(selection); replaceSelection('') }
}

function onDrop(event: DragEvent) {
  if (event.dataTransfer?.files.length) return // File drops are handled by the composer.
  event.preventDefault()
  event.stopPropagation()
  replaceSelection(event.dataTransfer?.getData('text/plain') ?? '')
}

watch(() => props.modelValue, (value) => {
  if (!editor.value || composing || value === read()) return
  const focused = document.activeElement === editor.value
  captureSelection()
  render(value)
  const length = contents(editor.value).length
  selection = { start: Math.min(selection.start, length), end: Math.min(selection.end, length) }
  if (focused) restoreSelection()
  history = [{ value, selection: { ...selection } }]
  historyIndex = 0
})
watch([() => props.tableReferences, () => props.fileReferences], () => {
  if (!editor.value || composing) return
  const focused = document.activeElement === editor.value
  captureSelection()
  render(read())
  if (focused) restoreSelection()
  if (focused) refreshSuggestions(true)
}, { deep: true })
watch(() => props.referencesLoading, () => refreshSuggestions(true))

function onCompositionEnd() {
  composing = false
  void nextTick(onInput)
}

onMounted(() => {
  render(props.modelValue)
  const length = contents(editor.value!).length
  selection = { start: length, end: length }
  history = [{ value: props.modelValue, selection: { ...selection } }]
  if (props.autofocus) { editor.value?.focus(); restoreSelection() }
  document.addEventListener('selectionchange', captureSelection)
})
onBeforeUnmount(() => document.removeEventListener('selectionchange', captureSelection))
defineExpose({
  focus: () => { editor.value?.focus(); restoreSelection() },
  captureSelection,
  insertReference,
})
</script>

<template>
  <div class="relative">
    <div
      v-if="query"
      class="absolute bottom-full left-4 right-4 z-50 mb-1 overflow-hidden rounded-xl border border-[#dfe4e8] bg-white shadow-[0_8px_32px_rgba(30,41,59,0.12)]"
      @pointerdown.prevent
    >
      <div class="flex items-center justify-between border-b border-line px-3 py-2 text-xs text-mute">
        <span>{{ query.trigger === '@' ? '选择文件' : '选择工作表' }}</span>
        <span class="text-[10px] text-faint">↑↓ 选择 · Enter 确认 · Esc 关闭</span>
      </div>
      <div :id="listId" role="listbox" :aria-label="query.trigger === '@' ? '已上传文件' : '已上传工作表'" class="max-h-60 overflow-y-auto p-1">
        <button
          v-for="(suggestion, index) in suggestions"
          :id="`${listId}-${index}`"
          :key="suggestion.key"
          type="button"
          role="option"
          tabindex="-1"
          :aria-selected="index === activeSuggestion"
          :title="suggestion.label"
          class="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] text-ink transition"
          :class="index === activeSuggestion ? 'bg-[#edf1f4]' : 'hover:bg-paper'"
          @pointerenter="activeSuggestion = index"
          @click="chooseSuggestion(suggestion)"
        >
          <span class="flex size-7 shrink-0 items-center justify-center rounded-md" :class="suggestion.type === 'file' ? 'bg-[#e4eaf2] text-[#42566f]' : 'bg-[#e0ece5] text-[#3f6152]'">
            <FileSpreadsheet v-if="suggestion.type === 'file'" class="size-4" :stroke-width="1.8" /><Table2 v-else class="size-4" :stroke-width="1.8" />
          </span>
          <span class="min-w-0 truncate">{{ suggestion.label }}</span>
        </button>
        <p v-if="loadingSuggestions" class="px-3 py-3 text-xs text-mute" role="status">正在读取工作表…</p>
        <p v-else-if="!suggestions.length" class="px-3 py-3 text-xs text-mute" role="status">{{ query.trigger === '@' ? fileReferences.length ? '没有匹配的文件' : '请先上传文件' : referencesError || (fileReferences.length ? '没有匹配的工作表' : '请先上传文件') }}</p>
      </div>
    </div>
  <div
    ref="editor"
    contenteditable="true"
    role="textbox"
    aria-multiline="true"
    :aria-label="placeholder"
    aria-haspopup="listbox"
    :aria-expanded="!!query"
    :aria-controls="query ? listId : undefined"
    :aria-activedescendant="query && suggestions.length ? `${listId}-${activeSuggestion}` : undefined"
    :data-placeholder="placeholder"
    :data-empty="empty"
    spellcheck="false"
    autocapitalize="off"
    class="reference-editor relative max-h-[240px] w-full overflow-y-auto px-5 text-[15px] leading-[30px] outline-none"
    :class="large ? 'min-h-[84px] pt-3.5' : 'min-h-[56px] pt-3'"
    @input="onInput"
    @beforeinput="onBeforeInput($event as InputEvent)"
    @keydown="onKeydown"
    @blur="captureSelection(); query = null"
    @pointerup="captureSelection(); refreshSuggestions()"
    @keyup="captureSelection(); refreshSuggestions()"
    @paste="onPaste"
    @copy="onCopy($event)"
    @cut="onCopy($event, true)"
    @drop="onDrop"
    @compositionstart="composing = true"
    @compositionend="onCompositionEnd"
  />
  </div>
</template>

<style scoped>
.reference-editor {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-variant-ligatures: none;
  cursor: text;
  caret-color: var(--color-ink);
}
.reference-editor[data-empty='true']::before {
  content: attr(data-placeholder);
  position: absolute;
  color: var(--color-faint);
  pointer-events: none;
}
.reference-editor :deep(.composer-reference) {
  display: inline-block;
  max-width: 100%;
  margin: 0 4px;
  padding: 0 3px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: rgb(0 0 0 / 8%);
  color: #242424;
  font-size: 14px;
  font-weight: 500;
  line-height: 23px;
  vertical-align: baseline;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  cursor: default;
}
.reference-editor :deep(.composer-reference__icon) {
  display: inline-block;
  width: 14px;
  height: 14px;
  margin-right: 4px;
  vertical-align: -2px;
  pointer-events: none;
  user-select: none;
}
.reference-editor :deep(.composer-reference__icon--file) {
  color: #52719b;
}
.reference-editor :deep(.composer-reference__icon--table) {
  color: #4c7e67;
}
</style>
