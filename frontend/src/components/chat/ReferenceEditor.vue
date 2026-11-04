<script setup lang="ts">
/** Native text editing with atomic file and worksheet labels. */
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import {
  formatFileReference, formatTableReference, isFileReference, parseTableReferences,
  projectTableReferences, updateTableReferenceText,
  type ComposerReference, type FileReference, type TableReference,
} from '@/composables/tableReferences'

const props = withDefaults(defineProps<{
  modelValue: string
  tableReferences?: TableReference[]
  fileReferences?: FileReference[]
  placeholder?: string
  autofocus?: boolean
  large?: boolean
  busy?: boolean
}>(), { tableReferences: () => [], fileReferences: () => [], placeholder: '', autofocus: false, large: false, busy: false })
const emit = defineEmits<{ 'update:modelValue': [value: string]; send: [] }>()
const editor = ref<HTMLDivElement | null>(null)
const empty = ref(!props.modelValue)
let composing = false
let selection = { start: 0, end: 0 }
type Snapshot = { value: string; selection: typeof selection }
let history: Snapshot[] = []
let historyIndex = 0

function chip(node: Node): node is HTMLSpanElement {
  return node instanceof HTMLElement && node.hasAttribute('data-reference-source')
}

function contents(node: Node, canonical = false): string {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent ?? ''
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
      return { node, offset: Math.max(0, offset - position) }
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
    label.textContent = segment.type === 'file' ? segment.reference.filename : segment.reference.sheet
    label.title = segment.type === 'file' ? segment.reference.filename : `${segment.reference.filename} / ${segment.reference.sheet}`
    fragment.append(label)
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
  const file = isFileReference(reference)
  replaceSelection(file ? formatFileReference(reference) : formatTableReference(reference), file ? reference.filename.length : reference.sheet.length)
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
    restoreSelection(atomicSelection())
  }
}

function onKeydown(event: KeyboardEvent) {
  if (composing || event.isComposing) return
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
}, { deep: true })

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
  <div
    ref="editor"
    contenteditable="true"
    role="textbox"
    aria-multiline="true"
    :aria-label="placeholder"
    :data-placeholder="placeholder"
    :data-empty="empty"
    spellcheck="false"
    autocapitalize="off"
    class="reference-editor relative max-h-[240px] w-full overflow-y-auto px-5 text-[15px] leading-[30px] outline-none"
    :class="large ? 'min-h-[84px] pt-3.5' : 'min-h-[56px] pt-3'"
    @input="onInput"
    @beforeinput="onBeforeInput($event as InputEvent)"
    @keydown="onKeydown"
    @blur="captureSelection"
    @pointerup="captureSelection"
    @keyup="captureSelection"
    @paste="onPaste"
    @copy="onCopy($event)"
    @cut="onCopy($event, true)"
    @drop="onDrop"
    @compositionstart="composing = true"
    @compositionend="onCompositionEnd"
  />
</template>

<style scoped>
.reference-editor {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-variant-ligatures: none;
  cursor: text;
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
  margin: 1px 3px;
  padding: 1px 7px;
  border: 1px solid;
  border-radius: 7px;
  font-size: 14px;
  font-weight: 500;
  line-height: 23px;
  vertical-align: baseline;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  cursor: default;
}
.reference-editor :deep(.composer-reference--file) {
  background: #e4eaf2;
  border-color: #cbd5e2;
  color: #42566f;
}
.reference-editor :deep(.composer-reference--table) {
  background: #e0ece5;
  border-color: #c6d9ce;
  color: #3f6152;
}
</style>
