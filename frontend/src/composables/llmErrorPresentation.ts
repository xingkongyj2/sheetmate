import type { UiMessage } from './chatModel'

const MISSING_MODEL_PREFIXES = ['还没有配置模型的 API Key。', '还没有启用可用的模型。']
const MISSING_MODEL_HELP = '这次请求发送时尚无已启用且配置完整的模型。请在「设置」里保存模型配置并开启「启用」，然后在本会话重新发送，无需新建会话。'

function isMissingModelText(text: string): boolean {
  return MISSING_MODEL_PREFIXES.some((prefix) => text.startsWith(prefix))
}

function withoutWarning(text: string | undefined): string {
  return (text ?? '').replace(/^\s*⚠\uFE0F?\s*/u, '').trim()
}

/** Explain a failed request without presenting its old configuration as the current state. */
export function missingLLMKeyPresentation(message: Pick<UiMessage, 'content' | 'error' | 'events'>): { text: string; duplicateContent: boolean } | null {
  const errors = (message.events ?? []).filter((event) => event.type === 'error')
  const errorTexts = [message.error, ...errors.map((event) => typeof event.content === 'string' ? event.content : '')]
    .map(withoutWarning).filter(Boolean)
  const content = withoutWarning(message.content)
  const missingKey = errors.some((event) => event.code === 'llm_not_configured') ||
    [...errorTexts, content].some(isMissingModelText)
  if (!missingKey) return null
  return {
    text: MISSING_MODEL_HELP,
    duplicateContent: !!content && (isMissingModelText(content) || errorTexts.includes(content)),
  }
}
