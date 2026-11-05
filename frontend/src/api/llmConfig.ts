import type { LLMConfig } from './types'

const PROVIDER_IDS = ['deepseek', 'qwen', 'custom']
const VERSION_ERROR = '设置接口版本不兼容，请重启 SheetMate 后重新加载设置页。'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

/** Reject the old single-provider API before its metadata reaches the forms. */
export function parseLLMConfig(value: unknown): LLMConfig {
  if (!isRecord(value) || typeof value.configured !== 'boolean' ||
    typeof value.base_url !== 'string' || typeof value.model !== 'string' ||
    typeof value.thinking_enabled !== 'boolean' || typeof value.thinking_supported !== 'boolean' ||
    typeof value.key_hint !== 'string' || typeof value.default_model !== 'string' ||
    !(value.active_provider_id === null || PROVIDER_IDS.includes(String(value.active_provider_id))) ||
    !Array.isArray(value.providers) || value.providers.length !== PROVIDER_IDS.length) {
    throw new Error(VERSION_ERROR)
  }
  const ids = new Set<string>()
  for (const provider of value.providers) {
    if (!isRecord(provider) || typeof provider.id !== 'string' || !PROVIDER_IDS.includes(provider.id) ||
      ids.has(provider.id) || typeof provider.name !== 'string' || typeof provider.description !== 'string' ||
      typeof provider.api_key !== 'string' || typeof provider.base_url !== 'string' ||
      typeof provider.model !== 'string' || typeof provider.configured !== 'boolean' ||
      typeof provider.thinking_supported !== 'boolean' || typeof provider.thinking_enabled !== 'boolean' ||
      !isStringArray(provider.models) ||
      (provider.thinking_models !== undefined && !isStringArray(provider.thinking_models))) {
      throw new Error(VERSION_ERROR)
    }
    ids.add(provider.id)
  }
  return value as unknown as LLMConfig
}
