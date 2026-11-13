import type { LLMAdvancedConfig, LLMConfig, LLMCapabilities } from './types'

const PROVIDER_IDS = ['deepseek', 'qwen', 'custom']
const VERSION_ERROR = '设置接口版本不兼容，请重启 SheetMate 后重新加载设置页。'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

function advancedConfig(value: Record<string, unknown>): LLMAdvancedConfig {
  const capabilities = value.capabilities === undefined ? {} : value.capabilities
  if (!isRecord(capabilities)) throw new Error(VERSION_ERROR)
  const protocol = capabilities.structured_output === undefined ? 'auto' : capabilities.structured_output
  const thinkingProtocol = capabilities.thinking_protocol === undefined ? 'auto' : capabilities.thinking_protocol
  if (typeof protocol !== 'string' || !['auto', 'json_schema', 'json_object', 'prompt'].includes(protocol)) {
    throw new Error(VERSION_ERROR)
  }
  if (typeof thinkingProtocol !== 'string' || !['auto', 'deepseek', 'qwen', 'none'].includes(thinkingProtocol)) {
    throw new Error(VERSION_ERROR)
  }
  return {
    capabilities: {
      structured_output: protocol as LLMCapabilities['structured_output'],
      thinking_protocol: thinkingProtocol as LLMCapabilities['thinking_protocol'],
    },
  }
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
  const providers = value.providers.map((provider) => {
    if (!isRecord(provider) || typeof provider.id !== 'string' || !PROVIDER_IDS.includes(provider.id) ||
      ids.has(provider.id) || typeof provider.name !== 'string' || typeof provider.description !== 'string' ||
      typeof provider.api_key !== 'string' || typeof provider.base_url !== 'string' ||
      typeof provider.model !== 'string' || typeof provider.configured !== 'boolean' ||
      (provider.enabled !== undefined && typeof provider.enabled !== 'boolean') ||
      typeof provider.thinking_supported !== 'boolean' || typeof provider.thinking_enabled !== 'boolean' ||
      !isStringArray(provider.models) ||
      (provider.thinking_models !== undefined && !isStringArray(provider.thinking_models)) ||
      (provider.thinking_model_aliases !== undefined && !isStringArray(provider.thinking_model_aliases))) {
      throw new Error(VERSION_ERROR)
    }
    ids.add(provider.id)
    return { ...provider, enabled: provider.enabled ?? true, ...advancedConfig(provider) }
  })
  return { ...value, ...advancedConfig(value), providers } as unknown as LLMConfig
}
