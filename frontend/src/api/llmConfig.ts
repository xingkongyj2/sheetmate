import type { LLMAdvancedConfig, LLMConfig, LLMCapabilities } from './types'

const PROVIDER_IDS = ['deepseek', 'qwen', 'custom']
const VERSION_ERROR = '设置接口版本不兼容，请重启 SheetMate 后重新加载设置页。'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

function boundedNumber(value: unknown, fallback: number, min: number, max: number, integer = false): number {
  if (value === undefined) return fallback
  if (typeof value !== 'number' || !Number.isFinite(value) || value < min || value > max ||
    (integer && !Number.isInteger(value))) throw new Error(VERSION_ERROR)
  return value
}

function advancedConfig(value: Record<string, unknown>): LLMAdvancedConfig {
  const capabilities = value.capabilities === undefined ? {} : value.capabilities
  const policy = value.runtime_policy === undefined ? {} : value.runtime_policy
  if (!isRecord(capabilities) || !isRecord(policy)) throw new Error(VERSION_ERROR)
  if (policy.auxiliary_thinking_enabled !== undefined && typeof policy.auxiliary_thinking_enabled !== 'boolean') {
    throw new Error(VERSION_ERROR)
  }
  const protocol = capabilities.structured_output === undefined ? 'auto' : capabilities.structured_output
  if (typeof protocol !== 'string' || !['auto', 'json_schema', 'json_object', 'prompt'].includes(protocol)) {
    throw new Error(VERSION_ERROR)
  }
  return {
    timeout: boundedNumber(value.timeout, 120, 5, 900),
    max_tokens: boundedNumber(value.max_tokens, 8192, 1, 131072, true),
    capabilities: { structured_output: protocol as LLMCapabilities['structured_output'] },
    runtime_policy: {
      structured_retries: boundedNumber(policy.structured_retries, 1, 0, 3, true),
      tool_protocol_retries: boundedNumber(policy.tool_protocol_retries, 2, 0, 5, true),
      sdk_retries: boundedNumber(policy.sdk_retries, 1, 0, 3, true),
      structured_timeout: boundedNumber(policy.structured_timeout, 0, 0, 900),
      max_model_calls: boundedNumber(policy.max_model_calls, 120, 0, 1000, true),
      max_prompt_tokens: boundedNumber(policy.max_prompt_tokens, 300000, 0, 10000000, true),
      max_total_tokens: boundedNumber(policy.max_total_tokens, 500000, 0, 10000000, true),
      max_task_seconds: boundedNumber(policy.max_task_seconds, 600, 0, 7200),
      max_input_chars: boundedNumber(policy.max_input_chars, 240000, 0, 2000000, true),
      auxiliary_thinking_enabled: policy.auxiliary_thinking_enabled === true,
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
      (provider.thinking_models !== undefined && !isStringArray(provider.thinking_models))) {
      throw new Error(VERSION_ERROR)
    }
    ids.add(provider.id)
    return { ...provider, enabled: provider.enabled ?? true, ...advancedConfig(provider) }
  })
  return { ...value, ...advancedConfig(value), providers } as unknown as LLMConfig
}
