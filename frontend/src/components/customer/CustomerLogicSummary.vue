<script setup lang="ts">
import { CircleCheck, Files, Lightbulb, ListChecks, Target } from '@lucide/vue'
import { marked } from 'marked'
import { computed } from 'vue'

import MarkdownView from '@/components/common/MarkdownView.vue'

type SectionKind = 'overview' | 'materials' | 'steps' | 'results' | 'notes' | 'other'
type Section = { kind: SectionKind; title: string; body: string }

const props = defineProps<{ source: string }>()

const sectionPresentation = {
  overview: { title: '这项工作做什么', icon: Target },
  materials: { title: '需要哪些文件', icon: Files },
  steps: { title: '具体怎么处理', icon: ListChecks },
  results: { title: '最后得到什么', icon: CircleCheck },
  notes: { title: '需要留意什么', icon: Lightbulb },
}

function sectionKind(title: string): SectionKind | null {
  if (/^(这项工作做什么|整体处理逻辑|工作概览|处理概览)/.test(title)) return 'overview'
  if (/^(需要哪些文件|需要哪些资料|要处理的表|需要的资料|所需资料)/.test(title)) return 'materials'
  if (/^(具体怎么处理|执行步骤|处理步骤|处理流程)/.test(title)) return 'steps'
  if (/^(最后得到什么|交付结果|处理结果|输出结果)/.test(title)) return 'results'
  if (/^(需要留意什么|注意事项|需要注意|特别说明)/.test(title)) return 'notes'
  return null
}

const sections = computed(() => {
  const tokens = marked.lexer(props.source, { gfm: true })
  const definitions = Object.entries(tokens.links).map(([label, link]) => {
    const title = link.title ? ` ${JSON.stringify(link.title)}` : ''
    return `[${label.replace(/\]/g, '\\]')}]: <${link.href.replace(/>/g, '%3E')}>${title}`
  }).join('\n')
  const result: Section[] = []
  let current: Section = { kind: 'other', title: '', body: '' }
  let hasKnownHeading = false

  function appendCurrent() {
    const body = current.body.trim()
    if (current.title || body) result.push({ ...current, body: definitions ? `${body}\n\n${definitions}` : body })
  }

  for (const token of tokens) {
    const title = token.type === 'heading' && token.depth <= 3 && typeof token.text === 'string' ? token.text : ''
    const kind = sectionKind(title)
    if (kind) {
      appendCurrent()
      current = { kind, title, body: '' }
      hasKnownHeading = true
    } else {
      current.body += token.raw
    }
  }
  appendCurrent()
  return hasKnownHeading ? result : []
})
</script>

<template>
  <div v-if="sections.length" class="logic-summary">
    <section
      v-for="(section, index) in sections"
      :key="index"
      class="logic-section"
      :class="`logic-section--${section.kind}`"
    >
      <header v-if="section.title" class="logic-section-heading">
        <component
          :is="sectionPresentation[section.kind].icon"
          v-if="section.kind !== 'other'"
          class="logic-heading-icon"
          :stroke-width="1.7"
          aria-hidden="true"
        />
        <h3>{{ section.kind === 'other' ? section.title : sectionPresentation[section.kind].title }}</h3>
      </header>
      <div v-if="section.body" class="logic-section-body"><MarkdownView :source="section.body" /></div>
    </section>
  </div>
  <div v-else class="logic-unstructured"><MarkdownView :source="source" /></div>
</template>

<style scoped>
.logic-summary { display: grid; gap: 12px; }
.logic-section { min-width: 0; padding: 21px 22px; border-radius: 20px; background: #fafafc; }
.logic-section--overview { position: relative; overflow: hidden; background: linear-gradient(120deg, #f1f1ff, #f8f8ff 70%, #f5f5fb); }
.logic-section--overview::after { position: absolute; top: -42px; right: -28px; width: 124px; height: 124px; border: 24px solid #fff6; border-radius: 50%; content: ''; pointer-events: none; }
.logic-section--steps { padding: 19px 3px 12px; background: transparent; }
.logic-section--results { background: #f4f7f8; }
.logic-section--notes { background: #fcf9f2; }
.logic-section--other { background: #fafafc; }
.logic-section-heading { position: relative; z-index: 1; display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.logic-section-heading h3 { min-width: 0; margin: 0; color: #252537; font-size: 14px; font-weight: 650; line-height: 1.5; }
.logic-heading-icon { width: 18px; height: 18px; flex: 0 0 18px; color: #8581ad; }
.logic-section-body { position: relative; z-index: 1; }
.logic-section-body :deep(.prose-sm), .logic-unstructured :deep(.prose-sm) { color: #555563; font-size: 14px; line-height: 1.9; }
.logic-section--overview .logic-section-body :deep(.prose-sm) { color: #38384c; }
.logic-section-body :deep(.prose-sm > ul) { margin: 0; padding-left: 20px; }
.logic-section-body :deep(.prose-sm li::marker) { color: #9a9cdf; }
.logic-section--steps .logic-section-body :deep(.prose-sm > ol:not([start])) { padding: 0; margin: 0; list-style: none; counter-reset: logic-step; }
.logic-section--steps .logic-section-body :deep(.prose-sm > ol:not([start]) > li) { position: relative; min-height: 47px; padding: 12px 15px 12px 58px; margin: 0 0 10px; border-radius: 15px; background: #fff; box-shadow: 0 3px 16px #28283d0a, 0 0 0 1px #eeeeF6; counter-increment: logic-step; }
.logic-section--steps .logic-section-body :deep(.prose-sm > ol:not([start]) > li::before) { position: absolute; top: 11px; left: 12px; display: grid; width: 32px; height: 32px; place-items: center; border-radius: 11px; background: #eeeefc; color: #6265dc; content: counter(logic-step, decimal-leading-zero); font-size: 12px; font-weight: 650; font-variant-numeric: tabular-nums; }
.logic-section--steps .logic-section-body :deep(.prose-sm > ol:not([start]) > li > p) { margin: 0; }
.logic-section--steps .logic-section-body :deep(.prose-sm strong) { color: #272735; }
@media (max-width: 540px) {
  .logic-section { padding: 17px; }
  .logic-section--steps { padding: 16px 2px 8px; }
}
</style>
