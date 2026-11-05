import type { UiMessage } from './chatModel'

const MISSING_KEY_PREFIX = '还没有配置模型的 API Key。'
const MISSING_KEY_HELP = '这次请求发送时尚未配置 API Key。保存模型设置后，可在本会话重新发送，无需新建会话。'

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
    [...errorTexts, content].some((text) => text.startsWith(MISSING_KEY_PREFIX))
  if (!missingKey) return null
  return {
    text: MISSING_KEY_HELP,
    duplicateContent: !!content && (content.startsWith(MISSING_KEY_PREFIX) || errorTexts.includes(content)),
  }
}
