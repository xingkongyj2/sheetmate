import { existsSync } from 'node:fs'
import { registerHooks } from 'node:module'
import { dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

// Application modules use Vite's extensionless imports; keep these tests on Node's native TS runtime.
registerHooks({
  resolve(specifier, context, nextResolve) {
    try {
      return nextResolve(specifier, context)
    } catch (error) {
      if (error.code !== 'ERR_MODULE_NOT_FOUND' || !specifier.startsWith('.') || !context.parentURL?.startsWith('file:')) throw error
      const path = resolve(dirname(fileURLToPath(context.parentURL)), `${specifier}.ts`)
      if (!existsSync(path)) throw error
      return nextResolve(pathToFileURL(path).href, context)
    }
  },
})
