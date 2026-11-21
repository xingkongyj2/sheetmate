/**
 * IP 形象「墨墨家族」：原创圆黑团子主 IP（绿豆芽搞怪系列 + 蓝波点尖帽搞怪系列）。
 * 老 key 保留在 LEGACY_MASCOTS 中兼容历史数据库记录，新建/编辑选择器展示全新 16 款纯黑搞怪小团子。
 */
export type Accessory =
  | 'sprout'
  | 'sprout-tilt'
  | 'sprout-droop'
  | 'sprout-bandaid'
  | 'party'
  | 'party-tilt'
  // 历史兼容配饰
  | 'cat'
  | 'glasses'
  | 'headphones'
  | 'ninja'
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

export type MascotFace =
  | 'normal'
  | 'laugh'
  | 'cool'
  | 'star'
  | 'tongue'
  | 'dizzy'
  | 'deadpan'
  | 'sleepy'
  | 'goofy'
  | 'party-happy'
  | 'wink-fang'
  | 'shock'
  | 'whistle'
  | 'money'
  | 'party-cool'
  | 'teary'

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
  /** 专属表情（缺省为经典大眼） */
  face?: MascotFace
  /** 腮红；黑色系不画 */
  blush?: string
  dark?: boolean
}

const INK = { body: '#111113', shade: '#000000', line: '#ffffff', dark: true } as const

/** 第一排：绿豆芽搞怪表情系列（9 款） */
export const SPROUT_MASCOTS: Record<string, MascotStyle> = {
  blob: { ...INK, accessory: 'sprout', accent: '#22b573', face: 'normal', name: '经典呆萌' },
  'sprout-laugh': { ...INK, accessory: 'sprout', accent: '#22b573', face: 'laugh', name: '眯眼大笑' },
  'sprout-cool': { ...INK, accessory: 'sprout', accent: '#22b573', face: 'cool', name: '拽酷墨镜' },
  'sprout-star': { ...INK, accessory: 'sprout', accent: '#22b573', face: 'star', name: '两眼放光' },
  'sprout-tongue': { ...INK, accessory: 'sprout-tilt', accent: '#22b573', face: 'tongue', name: '眨眼吐舌' },
  'sprout-dizzy': { ...INK, accessory: 'sprout-droop', accent: '#22b573', face: 'dizzy', name: '对账晕晕' },
  'sprout-deadpan': { ...INK, accessory: 'sprout', accent: '#22b573', face: 'deadpan', name: '无语盯人' },
  'sprout-sleepy': { ...INK, accessory: 'sprout-droop', accent: '#22b573', face: 'sleepy', name: '摸鱼打盹' },
  'sprout-bandaid': { ...INK, accessory: 'sprout-bandaid', accent: '#22b573', face: 'goofy', name: '大小眼憨憨' },
}

/** 第二排：蓝波点尖帽搞怪系列（7 款） */
export const PARTY_MASCOTS: Record<string, MascotStyle> = {
  'ink-party': { ...INK, accessory: 'party', accent: '#4c7cf0', face: 'party-happy', name: '蓝帽嘻嘻' },
  'party-wink': { ...INK, accessory: 'party', accent: '#4c7cf0', face: 'wink-fang', name: '蓝帽小机灵' },
  'party-shock': { ...INK, accessory: 'party', accent: '#4c7cf0', face: 'shock', name: '蓝帽惊呆' },
  'party-whistle': { ...INK, accessory: 'party', accent: '#4c7cf0', face: 'whistle', name: '蓝帽吹口哨' },
  'party-money': { ...INK, accessory: 'party', accent: '#4c7cf0', face: 'money', name: '蓝帽发财眼' },
  'party-cool': { ...INK, accessory: 'party', accent: '#4c7cf0', face: 'party-cool', name: '蓝帽派对拽哥' },
  'party-teary': { ...INK, accessory: 'party-tilt', accent: '#4c7cf0', face: 'teary', name: '蓝帽委屈巴巴' },
}

/** 历史已存客户兼容定义（不出现在新建/编辑选择器中） */
const LEGACY_MASCOTS: Record<string, MascotStyle> = {
  'ink-cat': { ...INK, accessory: 'cat', accent: '#f29bb6', name: '煤球' },
  'ink-glasses': { ...INK, accessory: 'glasses', accent: '#e9b949', name: '芝麻' },
  'ink-headphones': { ...INK, accessory: 'headphones', accent: '#f38230', name: '可乐' },
  'ink-ninja': { ...INK, accessory: 'ninja', accent: '#e5484d', name: '海苔' },
  'ink-halo': { ...INK, accessory: 'halo', accent: '#f5c84c', name: '黑豆' },
  'ink-flower': { ...INK, accessory: 'flower', accent: '#ffc94a', name: '黑米' },
  'ink-bowtie': { ...INK, accessory: 'bowtie', accent: '#e5484d', name: '乌龙' },
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

export const MASCOTS: Record<string, MascotStyle> = {
  ...SPROUT_MASCOTS,
  ...PARTY_MASCOTS,
  ...LEGACY_MASCOTS,
}

export function mascotStyle(key?: string | null): MascotStyle {
  return MASCOTS[key ?? ''] ?? MASCOTS.blob
}

/** 选择器与随机默认使用的全新 16 款纯黑搞怪小团子 */
export const MASCOT_GROUPS = [Object.keys(SPROUT_MASCOTS), Object.keys(PARTY_MASCOTS)]
export const MASCOT_KEYS = [...MASCOT_GROUPS[0], ...MASCOT_GROUPS[1]]
