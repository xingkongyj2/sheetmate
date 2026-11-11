import { existsSync } from 'node:fs'
import { dirname, resolve as resolvePath } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

// Node 22.11 has asynchronous loader hooks; newer runtimes use registerHooks instead.
export async function resolve(specifier, context, nextResolve) {
  try {
    return await nextResolve(specifier, context)
  } catch (error) {
    if (error.code !== 'ERR_MODULE_NOT_FOUND' || !specifier.startsWith('.') || !context.parentURL?.startsWith('file:')) throw error
    const path = resolvePath(dirname(fileURLToPath(context.parentURL)), `${specifier}.ts`)
    if (!existsSync(path)) throw error
    return nextResolve(pathToFileURL(path).href, context)
  }
}
