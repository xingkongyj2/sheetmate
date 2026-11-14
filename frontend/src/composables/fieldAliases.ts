/** 字段别名的输入：逗号不管敲的是中文还是英文，统一成中文逗号「，」。 */

/** 敲的过程中用：只把英文逗号和顿号换成中文逗号（一对一替换，长度不变，光标位置不受影响）。 */
export function normalizeAliasSeparators(text: string): string {
  return text.replace(/[,、]/g, '，')
}

/** 输入完成后用：统一逗号，并去掉逗号前后的空格、重复的逗号和首尾多余的逗号。 */
export function tidyAliases(text: string): string {
  return normalizeAliasSeparators(text)
    .replace(/\s*，\s*/g, '，')
    .replace(/，{2,}/g, '，')
    .replace(/^[，\s]+|[，\s]+$/g, '')
}

/** 拆成别名列表：中英文逗号、顿号、换行都认；去空、去重，保持输入顺序。 */
export function parseAliases(text: string): string[] {
  return [...new Set(text.split(/[,，、\n]+/).map((item) => item.trim()).filter(Boolean))]
}

/** 别名列表显示成输入框里的文字：用中文逗号连接。 */
export function formatAliases(aliases: readonly string[]): string {
  return aliases.join('，')
}
