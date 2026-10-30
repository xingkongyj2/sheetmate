/**
 * IP 形象「墨墨家族」：原创的圆团子角色，每个客户分到一个颜色 + 一个小配饰。
 * key 与后端 services/customers.py 的 MASCOTS 一致。
 */
export type Accessory = 'sprout' | 'antenna' | 'cap' | 'headphones' | 'bow' | 'glasses' | 'crown' | 'beret' | 'scarf'

export interface MascotStyle {
  body: string
  shade: string
  accessory: Accessory
  accent: string
  name: string
}

export const MASCOTS: Record<string, MascotStyle> = {
  blob: { body: '#111113', shade: '#000000', accessory: 'sprout', accent: '#22b573', name: '墨墨' },
  rocket: { body: '#ff6fae', shade: '#f0519a', accessory: 'antenna', accent: '#ffc531', name: '桃桃' },
  briefcase: { body: '#ff5a5f', shade: '#e94348', accessory: 'cap', accent: '#111113', name: '番茄' },
  megaphone: { body: '#2f7bff', shade: '#1f64e0', accessory: 'headphones', accent: '#111113', name: '蓝莓' },
  folder: { body: '#ffc531', shade: '#f2ad0f', accessory: 'bow', accent: '#ff6fae', name: '布丁' },
  puzzle: { body: '#3fc1f3', shade: '#22a8dd', accessory: 'glasses', accent: '#111113', name: '汽水' },
  crown: { body: '#8b6cf6', shade: '#7353ea', accessory: 'crown', accent: '#ffc531', name: '葡萄' },
  book: { body: '#22b573', shade: '#159a5d', accessory: 'scarf', accent: '#ff7a1a', name: '青豆' },
  beret: { body: '#ff7a1a', shade: '#ea620a', accessory: 'beret', accent: '#111113', name: '橘子' },
}

export function mascotStyle(key?: string | null): MascotStyle {
  return MASCOTS[key ?? ''] ?? MASCOTS.blob
}

export const MASCOT_KEYS = Object.keys(MASCOTS)
