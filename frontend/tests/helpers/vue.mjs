import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { compileScript, parse } from '@vue/compiler-sfc'
import ts from 'typescript'
import { createRenderer, defineComponent, h, reactive } from 'vue'

const sourceRoot = new URL('../../src/', import.meta.url)
const editorStub = new URL('ReferenceEditorStub.mjs', import.meta.url)

export async function loadVue(url, overrides = {}) {
  const filename = fileURLToPath(url)
  const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename })
  const result = compileScript(descriptor, { id: filename, inlineTemplate: true })
  let source = ts.transpileModule(result.content, {
    compilerOptions: { target: ts.ScriptTarget.ESNext, module: ts.ModuleKind.ESNext },
  }).outputText
  for (const match of [...source.matchAll(/\bfrom\s*(['"])([^'"]+)\1/g)]) {
    const specifier = match[2]
    let resolved
    if (overrides[specifier]) resolved = `data:text/javascript;base64,${Buffer.from(overrides[specifier]).toString('base64')}`
    else if (specifier.endsWith('/ReferenceEditor.vue') || specifier === './ReferenceEditor.vue') resolved = editorStub.href
    else if (specifier.startsWith('@/') || specifier.startsWith('.')) {
      let target = specifier.startsWith('@/') ? new URL(specifier.slice(2), sourceRoot) : new URL(specifier, url)
      if (!/\.[a-z]+$/.test(target.pathname)) target = new URL(existsSync(fileURLToPath(target) + '.ts') ? target.href + '.ts' : target.href + '/index.ts')
      resolved = target.href
    } else resolved = import.meta.resolve(specifier)
    source = source.replace(match[0], `from ${JSON.stringify(resolved)}`)
  }
  return (await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)).default
}

/** A minimal host renderer exercises real compiled Vue handlers without browser dependencies. */
export function mount(component, initialProps, components = {}) {
  const node = (tag, text = '') => ({ tag, text, props: {}, style: {}, children: [], parent: null,
    get nodeType() { return tag === '#text' ? 3 : 1 },
    get textContent() { return this.text },
    get childNodes() { return this.children },
    get firstChild() { return this.children[0] ?? null },
    get tagName() { return tag.toUpperCase() },
    get options() { return this.children.filter(child => child.tag === 'option') },
    addEventListener() {}, removeEventListener() {},
    focus() {}, replaceChildren(fragment) { this.children = fragment?.childNodes ?? [] } })
  const root = node('root')
  const body = node('body')
  const renderer = createRenderer({
    createElement: (tag) => node(tag),
    createText: (text) => node('#text', text),
    createComment: (text) => node('#comment', text),
    insert(child, parent, anchor) {
      if (child.parent) child.parent.children.splice(child.parent.children.indexOf(child), 1)
      const index = anchor ? parent.children.indexOf(anchor) : -1
      if (index < 0) parent.children.push(child)
      else parent.children.splice(index, 0, child)
      child.parent = parent
    },
    remove(child) {
      if (child.parent) child.parent.children.splice(child.parent.children.indexOf(child), 1)
    },
    patchProp(element, key, _previous, value) { element.props[key] = value },
    setText(element, text) { element.text = text },
    setElementText(element, text) { element.text = text; element.children = [] },
    parentNode: (child) => child.parent,
    nextSibling: (child) => child.parent?.children[child.parent.children.indexOf(child) + 1] ?? null,
    querySelector: (selector) => selector === 'body' ? body : null,
  })
  const props = reactive(initialProps)
  const app = renderer.createApp(defineComponent({ setup: () => () => h(component, props) }))
  for (const [name, component] of Object.entries(components)) app.component(name, component)
  app.mount(root)
  return { root, body, props, unmount: () => app.unmount() }
}

export function find(root, predicate) {
  if (predicate(root)) return root
  for (const child of root.children) {
    const result = find(child, predicate)
    if (result) return result
  }
  return null
}
