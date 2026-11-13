<script setup lang="ts">
/**
 * 客户「处理逻辑」：AI 总结按文档排版，一段一行，左边是小标签，右边是内容；
 * 步骤、文件、要点各用最朴素的列表。不认识的结构按原 Markdown 展示，不丢内容。
 */
import DOMPurify from 'dompurify'
import { FileSpreadsheet } from '@lucide/vue'
import { computed } from 'vue'

import MarkdownView from '@/components/common/MarkdownView.vue'
import { markdownParser } from '@/composables/markdownParser'

type SectionKind = 'overview' | 'materials' | 'steps' | 'results' | 'notes' | 'other'
type Item = { title: string; html: string }
type Section = { kind: SectionKind; title: string; body: string; items: Item[] | null }

const props = defineProps<{ source: string }>()

const titles: Record<Exclude<SectionKind, 'other'>, [string, string]> = {
  overview: ['工作概述', 'Overview'],
  materials: ['所需文件', 'Files'],
  steps: ['处理步骤', 'Steps'],
  results: ['最终结果', 'Result'],
  notes: ['注意事项', 'Notes'],
}

function sectionKind(title: string): SectionKind | null {
  if (/^(这项工作做什么|整体处理逻辑|工作概览|处理概览)/.test(title)) return 'overview'
  if (/^(需要哪些文件|需要哪些资料|要处理的表|需要的资料|所需资料)/.test(title)) return 'materials'
  if (/^(具体怎么处理|执行步骤|处理步骤|处理流程)/.test(title)) return 'steps'
  if (/^(最后得到什么|交付结果|处理结果|输出结果)/.test(title)) return 'results'
  if (/^(需要留意什么|注意事项|需要注意|特别说明)/.test(title)) return 'notes'
  return null
}

const inline = (text: string) => DOMPurify.sanitize(markdownParser.parseInline(text) as string)

/** 列表项拆成「标题 + 说明」：步骤用开头的粗体，文件用第一个冒号前的文件名。 */
function splitItem(kind: SectionKind, text: string): Item {
  const bold = text.match(/^\s*\*\*(.+?)\*\*\s*[：:]\s*([\s\S]*)$/)
  if (bold) return { title: bold[1]!, html: inline(bold[2]!) }
  if (kind === 'materials') {
    const named = text.match(/^\s*([^：:\n]{1,80}?)\s*[：:]\s*([\s\S]+)$/)
    if (named) return { title: named[1]!, html: inline(named[2]!) }
  }
  return { title: '', html: inline(text) }
}

/** 整段只有列表时取出列表项；夹着别的内容就交给 Markdown 原样渲染。 */
function listItems(kind: SectionKind, body: string): Item[] | null {
  const tokens = markdownParser.lexer(body).filter((token) => token.type !== 'space')
  if (!tokens.length || tokens.some((token) => token.type !== 'list')) return null
  return tokens.flatMap((token) => (token.type === 'list' ? token.items.map((item: { text: string }) => splitItem(kind, item.text)) : []))
}

const sections = computed(() => {
  const tokens = markdownParser.lexer(props.source)
  const definitions = Object.entries(tokens.links).map(([label, link]) => {
    const title = link.title ? ` ${JSON.stringify(link.title)}` : ''
    return `[${label.replace(/\]/g, '\\]')}]: <${link.href.replace(/>/g, '%3E')}>${title}`
  }).join('\n')
  const result: Section[] = []
  let current = { kind: 'other' as SectionKind, title: '', body: '' }
  let hasKnownHeading = false

  function appendCurrent() {
    const body = current.body.trim()
    if (!current.title && !body) return
    const items = definitions || current.kind === 'overview' || current.kind === 'other' ? null : listItems(current.kind, body)
    result.push({ ...current, body: definitions ? `${body}\n\n${definitions}` : body, items })
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

const headingOf = (section: Section) => (section.kind === 'other' ? section.title : titles[section.kind][0])
const subOf = (section: Section) => (section.kind === 'other' ? '' : titles[section.kind][1])
</script>

<template>
  <div v-if="sections.length" class="logic">
    <section v-for="(section, index) in sections" :key="index" class="row" :class="`row--${section.kind}`">
      <h3 class="row-label">{{ headingOf(section) }}<small v-if="subOf(section)">{{ subOf(section) }}</small></h3>
      <div class="row-content">
        <ol v-if="section.kind === 'steps' && section.items" class="logic-steps">
          <li v-for="(item, i) in section.items" :key="i">
            <span class="logic-steps-no">{{ i + 1 }}</span>
            <div class="min-w-0">
              <div v-if="item.title" class="logic-steps-title">{{ item.title }}</div>
              <!-- eslint-disable-next-line vue/no-v-html -->
              <div class="desc" v-html="item.html" />
            </div>
          </li>
        </ol>
        <ul v-else-if="section.kind === 'materials' && section.items" class="files">
          <li v-for="(item, i) in section.items" :key="i">
            <span class="files-icon"><FileSpreadsheet class="size-4 text-fun-green" /></span>
            <div class="min-w-0">
              <div v-if="item.title" class="files-name">{{ item.title }}</div>
              <!-- eslint-disable-next-line vue/no-v-html -->
              <div class="desc" v-html="item.html" />
            </div>
          </li>
        </ul>
        <ul v-else-if="section.items" class="points">
          <!-- eslint-disable-next-line vue/no-v-html -->
          <li v-for="(item, i) in section.items" :key="i" v-html="item.html" />
        </ul>
        <MarkdownView v-else-if="section.body" :source="section.body" class="md" />
      </div>
    </section>
  </div>
  <div v-else class="md"><MarkdownView :source="source" /></div>
</template>

<style scoped>
/* 单列文档排版：每段一个加粗小标题，下面是内容，段与段之间一条细线 */
.row { padding: 26px 0 28px; border-top: 1px solid #ececf1; }
.row:first-child { padding-top: 2px; border-top: 0; }
.row:last-child { padding-bottom: 2px; }
.row-label { display: flex; align-items: baseline; gap: 10px; margin: 0 0 14px; color: #14141c; font-size: 16px; font-weight: 700; line-height: 26px; }
.row-label small { color: #a4a4b2; font-size: 11px; font-weight: 500; letter-spacing: 0.06em; }
.row-content { min-width: 0; }

/* 正文 */
.md, .md :deep(.prose-sm) { color: #4a4a58; font-size: 14.5px; line-height: 1.85; }
.md :deep(p) { margin: 0; }
.md :deep(p + p) { margin-top: 10px; }
.md :deep(strong) { color: #14141c; font-weight: 650; }
.md :deep(ul), .md :deep(ol) { margin: 0; padding-left: 20px; }
.md :deep(li::marker) { color: #9a9cdf; }
.row--overview .md, .row--overview .md :deep(.prose-sm) { color: #2a2a38; font-size: 15.5px; line-height: 1.85; }

.desc { color: #666675; font-size: 13.5px; line-height: 1.75; }
.desc :deep(strong) { color: #14141c; font-weight: 650; }
.desc :deep(code) { padding: 1px 5px; border-radius: 5px; background: #f1f1f5; font-size: 0.92em; }

/* 步骤：强调色序号 + 加粗步骤名 + 说明 */
.logic-steps { margin: 0; padding: 0; list-style: none; }
.logic-steps li { display: grid; grid-template-columns: 1.75rem minmax(0, 1fr); gap: 0 10px; }
.logic-steps li + li { margin-top: 18px; }
.logic-steps-no { color: #6265dc; font-size: 14px; font-weight: 700; font-variant-numeric: tabular-nums; line-height: 26px; }
.logic-steps-title { color: #14141c; font-size: 14.5px; font-weight: 650; line-height: 26px; }

/* 文件：图标 + 加粗文件名 + 用途 */
.files { margin: 0; padding: 0; list-style: none; }
.files li { display: grid; grid-template-columns: 2rem minmax(0, 1fr); gap: 0 12px; }
.files li + li { margin-top: 14px; }
.files-icon { display: grid; width: 32px; height: 32px; place-items: center; border-radius: 10px; background: #f6f6f8; }
.files-name { color: #14141c; font-size: 14px; font-weight: 650; line-height: 20px; overflow-wrap: anywhere; }
.files li > div { padding-top: 0; }

/* 要点：小圆点 */
.points { margin: 0; padding: 0; list-style: none; }
.points li { position: relative; padding-left: 18px; color: #4a4a58; font-size: 14px; line-height: 1.8; }
.points li + li { margin-top: 9px; }
.points li::before { position: absolute; top: 11px; left: 3px; width: 5px; height: 5px; border-radius: 50%; background: #b7b8ec; content: ''; }
.points :deep(strong) { color: #14141c; font-weight: 650; }
</style>
