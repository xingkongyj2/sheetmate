import { computed, onScopeDispose, shallowRef, watch } from 'vue'

import type { AssistantDisplayGroup } from './assistantTimeline'

const TICK_MS = 24
const MIN_STEP = 2
const MAX_TICKS = 125
const segmenter = new Intl.Segmenter(undefined, { granularity: 'grapheme' })

interface TextState {
  text: string
  characters: string[]
  visible: number
}

interface ProgressiveReplyOptions {
  /** Existing text on mount was already received before this view opened. */
  initialVisible?: boolean
  /** Increment after replacing text with a restored snapshot; future appends still animate. */
  immediateRevision?: () => number | undefined
}

/** Reveal new replies in order while displaying restored text immediately. */
export function useProgressiveReply(source: () => AssistantDisplayGroup[], animate: boolean, options: ProgressiveReplyOptions = {}) {
  // Snapshot text fields: streaming mutates a block in place without changing its array.
  const target = computed(() => source().map((group) => group.kind === 'text' ? { ...group } : group))
  const states = shallowRef(new Map<string, TextState>())
  let timer: ReturnType<typeof setTimeout> | undefined
  let step = MIN_STEP
  let firstSync = true
  let previousRevision: number | undefined

  const pending = computed(() => animate && target.value.some((group) => {
    if (group.kind !== 'text' || !group.text) return false
    const state = states.value.get(group.key)
    return !state || state.text !== group.text || state.visible < state.characters.length
  }))

  const groups = computed(() => {
    if (!animate) return target.value
    const displayed: AssistantDisplayGroup[] = []
    for (const group of target.value) {
      if (group.kind !== 'text') {
        displayed.push(group)
        continue
      }
      const state = states.value.get(group.key)
      const text = state?.characters.slice(0, state.visible).join('') ?? ''
      const complete = text === group.text
      displayed.push({ ...group, text, closed: group.closed && complete })
      // Later processing sections and replies must not overtake unread text.
      if (!complete) break
    }
    return displayed
  })

  function schedule() {
    if (timer === undefined && pending.value) timer = setTimeout(tick, TICK_MS)
  }

  function tick() {
    timer = undefined
    const next = new Map(states.value)
    let budget = step
    for (const group of target.value) {
      if (group.kind !== 'text') continue
      const state = next.get(group.key)
      if (!state || state.visible === state.characters.length) continue
      const added = Math.min(budget, state.characters.length - state.visible)
      next.set(group.key, { ...state, visible: state.visible + added })
      budget -= added
      if (!budget) break
    }
    states.value = next
    if (pending.value) schedule()
    else step = MIN_STEP
  }

  if (animate) {
    watch([target, () => options.immediateRevision?.()], ([incoming, revision]) => {
      const revealAll = (firstSync && options.initialVisible === true) || revision !== previousRevision
      firstSync = false
      previousRevision = revision
      const next = new Map<string, TextState>()
      let remaining = 0
      for (const group of incoming) {
        if (group.kind !== 'text') continue
        const previous = states.value.get(group.key)
        let state = previous
        if (revealAll || !state || state.text !== group.text) {
          const characters = Array.from(segmenter.segment(group.text), ({ segment }) => segment)
          const shown = previous?.characters.slice(0, previous.visible).join('') ?? ''
          let visible = revealAll ? characters.length : 0
          let offset = 0
          // Final-text corrections retain only the already shown common prefix.
          if (!revealAll) {
            for (const character of characters) {
              if (!shown.startsWith(character, offset)) break
              offset += character.length
              visible++
            }
          }
          state = { text: group.text, characters, visible }
        }
        next.set(group.key, state)
        remaining += state.characters.length - state.visible
      }
      states.value = next
      // Large bursts still take several frames, without adding an unbounded wait.
      step = Math.max(step, Math.ceil(remaining / MAX_TICKS), MIN_STEP)
      if (pending.value) schedule()
      else {
        clearTimeout(timer)
        timer = undefined
        step = MIN_STEP
      }
    }, { immediate: true, flush: 'sync' })
  }

  onScopeDispose(() => { clearTimeout(timer) })
  return { groups, pending }
}
