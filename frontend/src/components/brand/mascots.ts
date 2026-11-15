/**
 * IP 形象「墨墨家族」：原创的圆团子角色，每个客户分到一个颜色 + 一个小配饰。
 * 黑色系 = 墨墨和它的兄弟们（同一个黑团子，换不同配饰）；彩色系 = 水果 / 甜品伙伴。
 * key 与后端 services/customers/service.py 的 MASCOTS 一致；老 key 不能删（数据库里存着）。
 */
export type Accessory =
  | 'sprout'
  | 'cat'
  | 'glasses'
  | 'headphones'
  | 'ninja'
  | 'party'
  | 'halo'
  | 'flower'
  | 'bowtie'
  | 'antenna'
  | 'calyx'
  | 'bow'
  | 'crown'
  | 'scarf'
  | 'beret'
  | 'beanie'

export interface MascotStyle {
  name: string
  /** 身体 */
  body: string
  /** 底部暗面 / 眼窝 */
  shade: string
  /** 嘴巴、笑眼、细线条（同色相的深色） */
  line: string
  accessory: Accessory
  /** 配饰主色 */
  accent: string
  /** 腮红；黑色系不画 */
  blush?: string
  dark?: boolean
}

const INK = { body: '#111113', shade: '#000000', line: '#ffffff', dark: true } as const

export const MASCOTS: Record<string, MascotStyle> = {
  // 黑色系
  blob: { ...INK, accessory: 'sprout', accent: '#22b573', name: '墨墨' },
  'ink-cat': { ...INK, accessory: 'cat', accent: '#f29bb6', name: '煤球' },
  'ink-glasses': { ...INK, accessory: 'glasses', accent: '#e9b949', name: '芝麻' },
  'ink-headphones': { ...INK, accessory: 'headphones', accent: '#f38230', name: '可乐' },
  'ink-ninja': { ...INK, accessory: 'ninja', accent: '#e5484d', name: '海苔' },
  'ink-party': { ...INK, accessory: 'party', accent: '#4c7cf0', name: '黑糖' },
  'ink-halo': { ...INK, accessory: 'halo', accent: '#f5c84c', name: '黑豆' },
  'ink-flower': { ...INK, accessory: 'flower', accent: '#ffc94a', name: '黑米' },
  'ink-bowtie': { ...INK, accessory: 'bowtie', accent: '#e5484d', name: '乌龙' },
  // 彩色系（按色相排）
  briefcase: { body: '#e85d53', shade: '#d1443d', line: '#6b1e1b', accessory: 'calyx', accent: '#3f9b5a', name: '番茄' },
  beret: { body: '#f38230', shade: '#dc6b00', line: '#672700', accessory: 'beret', accent: '#1d1d1f', blush: '#ffb08a', name: '橘子' },
  folder: { body: '#f9c647', shade: '#e3af20', line: '#563600', accessory: 'bow', accent: '#ef6f97', blush: '#ff9c7a', name: '布丁' },
  book: { body: '#48b06c', shade: '#2b9b56', line: '#004c1d', accessory: 'scarf', accent: '#f08a4b', name: '青豆' },
  mint: { body: '#31b5a1', shade: '#00a08c', line: '#004e3f', accessory: 'beanie', accent: '#2f3b66', name: '薄荷' },
  puzzle: { body: '#46b1e3', shade: '#259bcd', line: '#00446c', accessory: 'glasses', accent: '#1d1d1f', name: '汽水' },
  megaphone: { body: '#3a72e9', shade: '#255bd3', line: '#132457', accessory: 'headphones', accent: '#1d1d1f', name: '蓝莓' },
  crown: { body: '#8667db', shade: '#7351c5', line: '#412e71', accessory: 'crown', accent: '#f7c548', blush: '#ff9fc8', name: '葡萄' },
  rocket: { body: '#e573a3', shade: '#ce5c8e', line: '#661e40', accessory: 'antenna', accent: '#f7c548', blush: '#ffa3c4', name: '桃桃' },
}

export function mascotStyle(key?: string | null): MascotStyle {
  return MASCOTS[key ?? ''] ?? MASCOTS.blob
}

export const MASCOT_KEYS = Object.keys(MASCOTS)

/** 选择器分两排：黑色系 / 彩色系 */
export const MASCOT_GROUPS = [MASCOT_KEYS.filter((k) => MASCOTS[k].dark), MASCOT_KEYS.filter((k) => !MASCOTS[k].dark)]
