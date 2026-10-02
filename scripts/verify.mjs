import { readdir, readFile, lstat, access } from 'node:fs/promises'
import { resolve, dirname, extname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const config = JSON.parse(await readFile(resolve(root, 'release.json')))
const canonical = JSON.parse(await readFile(resolve(root, 'canonical.json')))
assert.deepEqual(config.packages.map(p => p.name), canonical[config.repository])
const host = JSON.parse(await readFile(resolve(root, 'HOST-CONTRACT.json')))
assert.equal(host.supportedHost, '2.0.3')
assert.deepEqual(host.staticImports, config.hostImports)
const closure = JSON.parse(await readFile(resolve(root, 'SOURCE-CLOSURE.json')))
for (const [path, expected] of Object.entries(closure.upstream_sha256)) {
  const local = resolve(root, 'upstream', path)
  assert(local.startsWith(root + '/upstream/'))
  assert.equal(createHash('sha256').update(await readFile(local)).digest('hex'), expected)
}
async function walk(path) {
  const stat = await lstat(path)
  assert(!stat.isSymbolicLink(), `symlink: ${path}`)
  if (!stat.isDirectory()) return [path]
  const files = []
  for (const name of await readdir(path)) files.push(...await walk(resolve(path, name)))
  return files
}
const importPattern = /(?:^|\n)\s*(?:import|export)\s+(?:[^"'`;]*?\sfrom\s+)?["']([^"']+)["']/g
const deployment = new Set()
const source = new Set()
for (const unit of config.packages) {
  assert.equal(unit.kind, 'view')
  const imports = new Set()
  assert(Object.values(unit.files).includes(unit.entry))
  for (const [path, target] of Object.entries(unit.files)) {
    assert(!source.has(path) && !deployment.has(target), 'source/deployment collision')
    source.add(path); deployment.add(target)
    const file = resolve(root, path)
    assert(file.startsWith(root + '/src/' + unit.name + '/'))
    const bytes = await readFile(file)
    if (target === unit.entry) {
      const tags = [...bytes.toString().matchAll(/customElements\.define\(["']([^"']+)["']/g)].map(m => m[1])
      assert.deepEqual(tags, [unit.tag], 'each entry must register exactly its own View')
    }
    assert.equal(createHash('sha256').update(bytes).digest('hex'), unit.sha256[path])
    for (const match of bytes.toString().matchAll(importPattern)) {
      const specifier = match[1]
      if (specifier.startsWith('.')) {
        const local = resolve(dirname(file), specifier)
        assert(local.startsWith(root + '/src/' + unit.name + '/'))
        await access(local)
        assert(source.has(local.slice(root.length + 1)) || Object.hasOwn(unit.files, local.slice(root.length + 1)))
      } else {
        assert(specifier.startsWith('/'), `undeclared dependency: ${specifier}`)
        assert(config.hostImports[unit.name].includes(specifier), `missing Host API: ${specifier}`)
        imports.add(specifier)
      }
    }
  }
  assert.deepEqual([...imports].sort(), config.hostImports[unit.name])
}
const actual = (await walk(resolve(root, 'src'))).map(p => p.slice(root.length + 1))
assert.deepEqual(actual.sort(), [...source].sort())
for (const folder of ['src', 'upstream', 'scripts']) {
  for (const file of await walk(resolve(root, folder))) {
    if (!['.js', '.mjs'].includes(extname(file))) continue
    const checked = spawnSync(process.execPath, ['--check', file], { encoding: 'utf8' })
    assert.equal(checked.status, 0, checked.stderr)
  }
}
console.log(`verified ${config.packages.length} View manifests, imports, hashes and JS syntax`)
