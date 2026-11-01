import { defineComponent, h } from 'vue'

// Render reply text without DOMPurify's browser dependency; the message and timeline remain real.
export default defineComponent({
  props: ['source', 'streaming'],
  setup: (props) => () => h('div', { class: 'prose-sm' }, props.source),
})
