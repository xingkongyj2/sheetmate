import { defineComponent, h } from 'vue'

export default defineComponent({
  inheritAttrs: false,
  setup(_props, { attrs, expose }) {
    expose({ focus() {}, captureSelection() {}, insertReference() {} })
    return () => h('reference-editor', attrs)
  },
})
