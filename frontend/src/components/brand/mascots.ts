/**
 * IP 形象「墨墨家族」：10 套色系 × 9 款独立表情 = 90 款原创小团子。
 * - 默认平铺展示前 2 排（经典黑·绿豆芽 9 款 + 经典黑·蓝尖帽 9 款）
 * - 点击「查看更多」平铺展开 8 套低饱和潮玩色系（每色各自拥有 9 款不同搞怪表情，不重复套用）
 * - 老 key 保留在 LEGACY_MASCOTS 中兼容历史数据库记录
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
  | 'love'
  | 'xd'

export type EyeKind =
  | 'classic'
  | 'wide-up'
  | 'side-eye'
  | 'cross-eyed'
  | 'dot'
  | 'dot-wide'
  | 'arc-up'
  | 'arc-down'
  | 'wink-left'
  | 'wink-right'
  | 'flat'
  | 'squint'
  | 'star'
  | 'heart'
  | 'x-eyes'
  | 'sparkle-big'
  | 'monocle'
  | 'round-glasses'
  | 'pixel-shades'
  | 'sleep-half'
  | 'angry-eyes'

export type MouthKind =
  | 'smile'
  | 'big-grin'
  | 'cat'
  | 'smirk'
  | 'fang'
  | 'tongue-out'
  | 'o-small'
  | 'o-big'
  | 'flat'
  | 'wavy'
  | 'pout'
  | 'kiss'
  | 'whistle'
  | 'grit'
  | 'drool'
  | 'mustache'

export type ExtraKind =
  | 'sweat'
  | 'anger'
  | 'blush-lines'
  | 'zzz'
  | 'bubble'
  | 'music'
  | 'question'
  | 'exclaim'
  | 'heart-float'
  | 'sparkle-float'
  | 'steam'
  | 'tears'
  | 'lollipop'
  | 'leaf-bite'

export interface CustomFace {
  eyes: EyeKind
  mouth: MouthKind
  extra?: ExtraKind
}

export interface MascotStyle {
  name: string
  body: string
  shade: string
  line: string
  pupil?: string
  stroke?: string
  accessory: Accessory
  accent: string
  /** 前两排经典黑使用的预设表情 */
  face?: MascotFace
  /** 彩色系使用的组合独立表情 */
  customFace?: CustomFace
  blush?: string
  dark?: boolean
}

const INK = { body: '#111113', shade: '#000000', line: '#ffffff', pupil: '#111113', dark: true } as const
type SeriesTone = Omit<MascotStyle, 'name' | 'accessory' | 'accent' | 'face' | 'customFace'>
type CustomItem = readonly [suffix: string, label: string, accessory: Accessory, eyes: EyeKind, mouth: MouthKind, extra?: ExtraKind]

function buildCustomSeries(
  prefix: string,
  seriesName: string,
  tone: SeriesTone,
  accent: string,
  items: readonly CustomItem[],
): Record<string, MascotStyle> {
  return Object.fromEntries(
    items.map(([suffix, label, accessory, eyes, mouth, extra]) => [
      `${prefix}-${suffix}`,
      {
        ...tone,
        accessory,
        accent,
        customFace: { eyes, mouth, extra },
        name: `${seriesName} · ${label}`,
      },
    ]),
  )
}

/** 第 1 排（默认展示）：经典黑 · 绿豆芽（9 款） */
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

/** 第 2 排（默认展示）：经典黑 · 蓝波点尖帽（9 款） */
export const PARTY_MASCOTS: Record<string, MascotStyle> = {
  'ink-party': { ...INK, accessory: 'party', accent: '#4c7cf0', face: 'party-happy', name: '蓝帽嘻嘻' },
  'party-wink': { ...INK, accessory: 'party', accent: '#4c7cf0', face: 'wink-fang', name: '蓝帽小机灵' },
  'party-shock': { ...INK, accessory: 'party', accent: '#4c7cf0', face: 'shock', name: '蓝帽惊呆' },
  'party-whistle': { ...INK, accessory: 'party', accent: '#4c7cf0', face: 'whistle', name: '蓝帽吹口哨' },
  'party-money': { ...INK, accessory: 'party', accent: '#4c7cf0', face: 'money', name: '蓝帽发财眼' },
  'party-cool': { ...INK, accessory: 'party', accent: '#4c7cf0', face: 'party-cool', name: '蓝帽派对拽哥' },
  'party-teary': { ...INK, accessory: 'party-tilt', accent: '#4c7cf0', face: 'teary', name: '蓝帽委屈巴巴' },
  'party-love': { ...INK, accessory: 'party', accent: '#4c7cf0', face: 'love', name: '蓝帽心动眼' },
  'party-xd': { ...INK, accessory: 'party-tilt', accent: '#4c7cf0', face: 'xd', name: '蓝帽笑裂' },
}

/** 展开后 8 套低饱和潮玩配色 × 每套 9 款独立表情（72 款互不重复） */
const CREAM_MASCOTS = buildCustomSeries(
  'cream', '云朵奶白',
  { body: '#fffdf8', shade: '#ede9df', stroke: '#1d1d22', line: '#1d1d22', pupil: '#1d1d22', blush: '#ffb0a6' },
  '#22b573',
  [
    ['shy', '害羞脸红', 'sprout', 'dot', 'cat', 'blush-lines'],
    ['cross', '斗鸡搞怪', 'sprout-tilt', 'cross-eyed', 'tongue-out'],
    ['monocle', '单片镜绅士', 'sprout', 'monocle', 'mustache'],
    ['question', '满头问号', 'sprout-droop', 'wide-up', 'o-small', 'question'],
    ['sweat', '尬笑流汗', 'sprout', 'arc-up', 'grit', 'sweat'],
    ['pixel', '像素大佬', 'sprout', 'pixel-shades', 'smirk'],
    ['drool', '馋嘴流口水', 'sprout-tilt', 'sparkle-big', 'drool'],
    ['kiss', '飞吻卖萌', 'sprout', 'wink-left', 'kiss', 'heart-float'],
    ['faint', '电量耗尽', 'sprout-droop', 'x-eyes', 'wavy', 'sweat'],
  ],
)

const INDIGO_MASCOTS = buildCustomSeries(
  'indigo', '靛蓝团子',
  { body: '#6265dc', shade: '#4c4fc4', line: '#ffffff', pupil: '#18181f', blush: '#b5b8ff', dark: true },
  '#22b573',
  [
    ['eureka', '灵光一闪', 'sprout', 'sparkle-big', 'big-grin', 'exclaim'],
    ['nerd', '学霸圆眼镜', 'sprout', 'round-glasses', 'smirk'],
    ['steam', '气到冒烟', 'sprout-tilt', 'angry-eyes', 'grit', 'steam'],
    ['lollipop', '叼棒棒糖', 'sprout', 'wink-right', 'smile', 'lollipop'],
    ['side', '偷偷斜眼', 'sprout', 'side-eye', 'wavy'],
    ['overload', '忙到过载', 'sprout-droop', 'x-eyes', 'o-big', 'steam'],
    ['drowsy', '困到睁不开', 'sprout-droop', 'sleep-half', 'o-small', 'zzz'],
    ['chill', '悠闲叼叶', 'sprout-tilt', 'flat', 'smirk', 'leaf-bite'],
    ['hum', '开心哼歌', 'sprout', 'arc-up', 'cat', 'music'],
  ],
)

const OAT_MASCOTS = buildCustomSeries(
  'oat', '燕麦拿铁',
  { body: '#d8c2a6', shade: '#c2a784', line: '#4d3622', pupil: '#382515', blush: '#f29e8b' },
  '#4c7cf0',
  [
    ['cozy', '憨厚豆豆眼', 'party', 'dot', 'smile', 'sparkle-float'],
    ['sir', '拿铁绅士', 'party', 'round-glasses', 'mustache'],
    ['yawn', '打哈欠', 'party-tilt', 'arc-down', 'o-big', 'zzz'],
    ['crave', '馋到放光', 'party', 'star', 'drool'],
    ['awkward', '尴尬出汗', 'party', 'dot-wide', 'flat', 'sweat'],
    ['grumpy', '气鼓鼓', 'party-tilt', 'angry-eyes', 'pout', 'anger'],
    ['coolpop', '叼糖小酷', 'party', 'pixel-shades', 'smile', 'lollipop'],
    ['derp', '斗鸡傻笑', 'party', 'cross-eyed', 'big-grin'],
    ['smooch', '害羞么么', 'party', 'arc-up', 'kiss', 'blush-lines'],
  ],
)

const SAGE_MASCOTS = buildCustomSeries(
  'sage', '鼠尾草绿',
  { body: '#6cb89c', shade: '#52a184', line: '#184435', pupil: '#14382c', blush: '#f7b29c' },
  '#4c7cf0',
  [
    ['leaf', '悠哉叼草', 'party', 'arc-up', 'smile', 'leaf-bite'],
    ['huh', '懵逼问号', 'party-tilt', 'dot', 'o-small', 'question'],
    ['grit', '憋笑咬牙', 'party', 'squint', 'grit'],
    ['scholar', '单镜学者', 'party', 'monocle', 'smirk', 'sparkle-float'],
    ['judge', '嫌弃斜眼', 'party', 'side-eye', 'pout'],
    ['touched', '感动泪花', 'party-tilt', 'sparkle-big', 'cat', 'tears'],
    ['blep', '吃瓜吐舌', 'party', 'wide-up', 'tongue-out'],
    ['nap', '困到冒泡', 'party-tilt', 'sleep-half', 'wavy', 'bubble'],
    ['hype', '热血大笑', 'party', 'angry-eyes', 'big-grin', 'exclaim'],
  ],
)

const MIST_MASCOTS = buildCustomSeries(
  'mist', '雾霾冰蓝',
  { body: '#6f96e8', shade: '#547bd4', line: '#182c5c', pupil: '#15254d', blush: '#ffaec4' },
  '#22b573',
  [
    ['shiny', '水灵闪闪', 'sprout', 'sparkle-big', 'smile', 'sparkle-float'],
    ['blank', '冷漠出汗', 'sprout', 'flat', 'flat', 'sweat'],
    ['confused', '斗鸡装傻', 'sprout-tilt', 'cross-eyed', 'o-small', 'question'],
    ['boss', '像素酷哥', 'sprout', 'pixel-shades', 'fang'],
    ['sob', '委屈挤泪', 'sprout-droop', 'squint', 'pout', 'tears'],
    ['tune', '吹哨听歌', 'sprout', 'wink-left', 'whistle', 'music'],
    ['ko', '晕厥吐舌', 'sprout-droop', 'x-eyes', 'tongue-out'],
    ['candy', '吃糖笑眯', 'sprout', 'arc-up', 'cat', 'lollipop'],
    ['focus', '认真记账', 'sprout', 'round-glasses', 'flat', 'exclaim'],
  ],
)

const PEACH_MASCOTS = buildCustomSeries(
  'peach', '蜜桃奶橘',
  { body: '#f29b72', shade: '#de8055', line: '#5e2711', pupil: '#471d0c', blush: '#ff7d8d' },
  '#22b573',
  [
    ['cheer', '元气大笑', 'sprout', 'arc-up', 'big-grin', 'sparkle-float'],
    ['blush', '害羞猫嘴', 'sprout', 'sparkle-big', 'cat', 'blush-lines'],
    ['rage', '气炸喷火', 'sprout-tilt', 'angry-eyes', 'o-big', 'anger'],
    ['yummy', '嘴馋流口水', 'sprout', 'arc-up', 'drool', 'heart-float'],
    ['cheeky', '俏皮虎牙', 'sprout-tilt', 'wink-left', 'fang'],
    ['doze', '犯困撅嘴', 'sprout-droop', 'sleep-half', 'pout', 'zzz'],
    ['sassy', '傲娇叼叶', 'sprout', 'side-eye', 'smirk', 'leaf-bite'],
    ['spook', '吓出冷汗', 'sprout-droop', 'dot-wide', 'wavy', 'sweat'],
    ['dapper', '小胡子橘', 'sprout', 'classic', 'mustache', 'sparkle-float'],
  ],
)

const TARO_MASCOTS = buildCustomSeries(
  'taro', '香芋慕斯',
  { body: '#9d8ce8', shade: '#8370d6', line: '#2d2063', pupil: '#241952', blush: '#ffb0d0' },
  '#6265dc',
  [
    ['dream', '梦幻星星', 'party', 'sparkle-big', 'o-small', 'sparkle-float'],
    ['coolcat', '甜酷墨镜', 'party', 'pixel-shades', 'cat'],
    ['flirt', '飞吻比心', 'party', 'wink-right', 'kiss', 'heart-float'],
    ['bubblecat', '困倦冒泡', 'party-tilt', 'arc-down', 'cat', 'bubble'],
    ['curious', '疑惑歪眼', 'party', 'side-eye', 'o-small', 'question'],
    ['sweet', '吃糖乖巧', 'party', 'arc-up', 'smile', 'lollipop'],
    ['effort', '咬牙坚持', 'party-tilt', 'angry-eyes', 'grit', 'sweat'],
    ['dizzyx', '眩晕叉叉', 'party-tilt', 'x-eyes', 'wavy'],
    ['noble', '贵气单片镜', 'party', 'monocle', 'cat'],
  ],
)

const ROSE_MASCOTS = buildCustomSeries(
  'rose', '草莓奶昔',
  { body: '#eb8ca8', shade: '#d67190', line: '#5c1f35', pupil: '#471528', blush: '#ff6f91' },
  '#4c7cf0',
  [
    ['sweetie', '甜心比心', 'party', 'wink-left', 'big-grin', 'heart-float'],
    ['bashful', '娇羞脸红', 'party', 'dot', 'wavy', 'blush-lines'],
    ['adore', '馋嘴爱心', 'party', 'heart', 'drool'],
    ['melody', '哼歌猫笑', 'party', 'wink-right', 'cat', 'music'],
    ['feisty', '奶凶虎牙', 'party-tilt', 'angry-eyes', 'fang', 'anger'],
    ['wail', '感动爆哭', 'party-tilt', 'squint', 'o-big', 'tears'],
    ['geek', '眼镜吐舌', 'party', 'round-glasses', 'tongue-out'],
    ['gasp', '惊呆豆眼', 'party', 'dot-wide', 'o-small', 'exclaim'],
    ['silly', '斗鸡卖萌', 'party', 'cross-eyed', 'cat', 'sparkle-float'],
  ],
)

const EXTRA_SERIES: Record<string, MascotStyle>[] = [
  CREAM_MASCOTS,
  INDIGO_MASCOTS,
  OAT_MASCOTS,
  SAGE_MASCOTS,
  MIST_MASCOTS,
  PEACH_MASCOTS,
  TARO_MASCOTS,
  ROSE_MASCOTS,
]

const COLOR_MASCOTS: Record<string, MascotStyle> = Object.assign({}, ...EXTRA_SERIES)

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
  ...COLOR_MASCOTS,
  ...LEGACY_MASCOTS,
}

export function mascotStyle(key?: string | null): MascotStyle {
  return MASCOTS[key ?? ''] ?? MASCOTS.blob
}

export const PRIMARY_MASCOT_KEYS: string[] = [...Object.keys(SPROUT_MASCOTS), ...Object.keys(PARTY_MASCOTS)]
export const MASCOT_GROUPS: string[][] = [
  Object.keys(SPROUT_MASCOTS),
  Object.keys(PARTY_MASCOTS),
  ...EXTRA_SERIES.map((group) => Object.keys(group)),
]
export const MASCOT_KEYS: string[] = MASCOT_GROUPS.flat()
