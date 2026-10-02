import { readFile } from 'node:fs/promises'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'
import assert from 'node:assert/strict'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const config = JSON.parse(await readFile(resolve(root, 'release.json')))
const blend = config.packages.filter(p => p.name.includes('blend-space-'))
if (blend.length) {
  assert.equal(blend.length, 2)
  const original = await readFile(resolve(root, 'upstream/views/view-animation-blend-spaces.js'), 'utf8')
  const registry = new Map()
  for (const unit of blend) {
    const own = unit.name.endsWith('1d') ? '1d' : '2d'
    const other = own === '1d' ? '2d' : '1d'
    const block = `\nif (!customElements.get("view-animation-blend-space-${other}")) {\n  customElements.define("view-animation-blend-space-${other}", ViewAnimationBlendSpace${other.toUpperCase()})\n}\n`
    const path = Object.keys(unit.files).find(path => unit.files[path] === unit.entry)
    const source = await readFile(resolve(root, path), 'utf8')
    assert.equal(source, original.replace(block, '\n'), 'only opposite registration may change')
    const context = vm.createContext({
      HTMLElement: class { constructor() { this.dataset = {} } }, structuredClone,
      customElements: { get: tag => registry.get(tag), define: (tag, cls) => {
        assert(!registry.has(tag)); registry.set(tag, cls)
      } }, registerViewPlugin() {}, unregisterViewPlugin() {},
    })
    vm.runInContext(source.replace(/^import .*\n/, '').replaceAll('export class ', 'class '), context)
    const tag = `view-animation-blend-space-${own}`
    assert(registry.has(tag))
    const editor = new (registry.get(tag))()
    assert.equal(editor.expectedKind, 'blend-space-' + own)
    assert.equal(editor.fields.length, own === '1d' ? 3 : 6)
    assert.throws(() => { editor.animationNode = { kind: 'wrong' } })
    editor.animationNode = { kind: 'blend-space-' + own, name: 'Example', parameters: {}, points: [] }
    assert.equal(editor.animationNode.name, 'Example')
  }
  assert.deepEqual([...registry.keys()].sort(), ['view-animation-blend-space-1d', 'view-animation-blend-space-2d'])
  console.log('blend separation: original behavior source equivalence and combined registration verified (not DOM E2E)')
}
