/**
 * 成员默认头像图标库（多层立体柔光玻璃球体风格）。
 * 根据用户名 / 账号等任意字符串 key 稳定生成具有明显空间层次感的渐变球体图标：
 * 底层球体基底 → 柔化中层光晕 → 紧致深色内核 → 内部反光亮区 → 外缘菲涅尔玻璃折射边。
 * 同一用户在不同页面、不同尺寸下始终呈现同一枚图标，并内置 Data URL 缓存确保数百行列表流畅渲染。
 */

export interface AvatarPalette {
  id: string
  name: string
  /** 球体受光面浅底色 */
  light: string
  /** 球体主体底色 */
  base: string
  /** 外缘菲涅尔玻璃折射边色（营造立体球壳轮廓） */
  rim: string
  /** 中层弥散光晕色 */
  mid: string
  /** 偏心饱和内核色 */
  core: string
  /** 内核深部锚点色（拉出纵深层次） */
  deep: string
}

export interface AvatarFocalLayout {
  /** 球体基底渐变方向 */
  angle: [number, number, number, number]
  /** 中层弥散光晕位置与旋转角 */
  mid: [number, number, number]
  /** 偏心主内核位置与旋转角 */
  core: [number, number, number]
  /** 内核深部锚点位置 */
  deep: [number, number]
  /** 内部亮面穹顶位置 */
  highlight: [number, number]
  /** 顶部玻璃微反光中心（百分比） */
  sheen: [number, number]
}

export interface AvatarIconSpec {
  key: string
  hash: number
  palette: AvatarPalette
  layoutIndex: number
  layout: AvatarFocalLayout
  dataUrl: string
}

export const AVATAR_PALETTES: readonly AvatarPalette[] = [
  { id: 'mint-ocean', name: '薄荷海蓝', light: '#e8fff1', base: '#c4f7d8', rim: '#9aeec0', mid: '#32dc92', core: '#1e82eb', deep: '#155cd0' },
  { id: 'apricot-coral', name: '暖杏珊瑚', light: '#fff6e8', base: '#fae7cc', rim: '#f2cfa2', mid: '#f8b24c', core: '#ff5d24', deep: '#e33f14' },
  { id: 'lavender-indigo', name: '薰衣草紫', light: '#f3f1ff', base: '#dfdafe', rim: '#c4bcf8', mid: '#c272f8', core: '#5f44ff', deep: '#4528e2' },
  { id: 'porcelain-blush', name: '暖瓷蜜桃', light: '#fff9f3', base: '#faede0', rim: '#f0d3be', mid: '#f7be9c', core: '#f07d67', deep: '#da5b4c' },
  { id: 'glacier-azure', name: '冰川蔚蓝', light: '#edf8ff', base: '#cdeeff', rim: '#9fd8fb', mid: '#36bef6', core: '#2062ec', deep: '#1745c6' },
  { id: 'rose-peony', name: '玫瑰粉霞', light: '#fff2f7', base: '#fde0ec', rim: '#f6bad2', mid: '#f578b2', core: '#e22e7e', deep: '#bc1a64' },
  { id: 'lagoon-teal', name: '青碧浅湾', light: '#eafcfa', base: '#c8f5ef', rim: '#98e6dc', mid: '#26d0bc', core: '#0c819a', deep: '#085e7b' },
  { id: 'honey-amber', name: '琥珀晨光', light: '#fffae8', base: '#fdf0ca', rim: '#f3d78e', mid: '#fab82e', core: '#e8710c', deep: '#c25106' },
  { id: 'orchid-plum', name: '兰花紫韵', light: '#fbf2ff', base: '#f0dafc', rim: '#ddb5f5', mid: '#e06ef6', core: '#882be8', deep: '#6718c4' },
  { id: 'spring-lime', name: '青柠初叶', light: '#f5fee9', base: '#e2f8c5', rim: '#bde993', mid: '#86d630', core: '#1e9a4a', deep: '#137438' },
  { id: 'sunset-berry', name: '落日莓果', light: '#fff3ef', base: '#fde3da', rim: '#f6c2b4', mid: '#fa8f66', core: '#e22d58', deep: '#b81a44' },
  { id: 'periwinkle-sky', name: '晴空鸢尾', light: '#f1f4ff', base: '#d9e2ff', rim: '#b4c5fb', mid: '#788efd', core: '#463ce8', deep: '#3126c4' },
  { id: 'aurora-jade', name: '极光翡翠', light: '#eafdf7', base: '#c8f7ea', rim: '#9ae9d4', mid: '#3adaa4', core: '#0d88c8', deep: '#0965a4' },
  { id: 'peach-violet', name: '暮色桃紫', light: '#fff3f8', base: '#fae3ed', rim: '#eebfd9', mid: '#f07eb6', core: '#7743f2', deep: '#5829d2' },
  { id: 'cobalt-cyan', name: '深海青蓝', light: '#eafafe', base: '#c9f1fa', rim: '#9be0f2', mid: '#22c0de', core: '#1a52d8', deep: '#123ab2' },
  { id: 'caramel-rose', name: '焦糖蔷薇', light: '#fef5ee', base: '#f7e4d6', rim: '#eac4b0', mid: '#ee9c72', core: '#d84b42', deep: '#b3322d' },
]

/** 4 组偏心立体光斑构图，保持统一的上缘柔光玻璃球质感，又各有自然呼吸感 */
const FOCAL_LAYOUTS: readonly AvatarFocalLayout[] = [
  { angle: [14, 10, 86, 90], mid: [36, 41, -18], core: [43, 47, -15], deep: [46, 51], highlight: [23, 21], sheen: [30, 23] },
  { angle: [16, 12, 84, 88], mid: [40, 39, 12], core: [46, 44, 15], deep: [49, 47], highlight: [21, 23], sheen: [27, 25] },
  { angle: [22, 8, 78, 92], mid: [33, 43, -8], core: [39, 49, -10], deep: [41, 53], highlight: [25, 20], sheen: [33, 21] },
  { angle: [86, 10, 14, 90], mid: [28, 41, 18], core: [21, 47, 15], deep: [18, 51], highlight: [41, 21], sheen: [70, 23] },
]

const dataUrlCache = new Map<string, string>()

/** FNV-1a 32 位哈希 + avalanche 混合：中英文、相似前缀账号（如 m_001 / m_002）都能均匀分散。 */
export function hashAvatarKey(key: string): number {
  const normalized = (key || 'sheetmate').trim().toLowerCase()
  let h = 0x811c9dc5
  for (let i = 0; i < normalized.length; i++) {
    h ^= normalized.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  h ^= h >>> 16
  h = Math.imul(h, 0x85ebca6b)
  h ^= h >>> 13
  h = Math.imul(h, 0xc2b2ae35)
  h ^= h >>> 16
  return h >>> 0
}

function buildOrbSvg(palette: AvatarPalette, layout: AvatarFocalLayout, uid: string, size: number): string {
  const [x1, y1, x2, y2] = layout.angle
  const [mx, my, mRot] = layout.mid
  const [cx, cy, cRot] = layout.core
  const [dx, dy] = layout.deep
  const [hx, hy] = layout.highlight
  const [sx, sy] = layout.sheen

  return [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 64 64" fill="none">`,
    `<defs>`,
    `<clipPath id="cp-${uid}"><circle cx="32" cy="32" r="32"/></clipPath>`,
    `<filter id="bs-${uid}" x="-32" y="-32" width="128" height="128" filterUnits="userSpaceOnUse" color-interpolation-filters="sRGB"><feGaussianBlur stdDeviation="7.5"/></filter>`,
    `<filter id="bc-${uid}" x="-32" y="-32" width="128" height="128" filterUnits="userSpaceOnUse" color-interpolation-filters="sRGB"><feGaussianBlur stdDeviation="5.8"/></filter>`,
    `<linearGradient id="sh-${uid}" x1="${x1}%" y1="${y1}%" x2="${x2}%" y2="${y2}%">`,
    `<stop offset="0%" stop-color="${palette.base}"/>`,
    `<stop offset="48%" stop-color="${palette.light}"/>`,
    `<stop offset="100%" stop-color="${palette.base}"/>`,
    `</linearGradient>`,
    // 极轻的球壳外缘折射边：柔和托出圆形轮廓，不形成暗圈阴影
    `<radialGradient id="fr-${uid}" cx="50%" cy="50%" r="50%">`,
    `<stop offset="0%" stop-color="${palette.rim}" stop-opacity="0"/>`,
    `<stop offset="76%" stop-color="${palette.rim}" stop-opacity="0"/>`,
    `<stop offset="92%" stop-color="${palette.rim}" stop-opacity="0.14"/>`,
    `<stop offset="100%" stop-color="${palette.rim}" stop-opacity="0.32"/>`,
    `</radialGradient>`,
    // 顶部柔和玻璃镜面反光（偏离边缘一点，勾出球面弧度而不把外缘漂白）
    `<radialGradient id="sp-${uid}" cx="${sx}%" cy="${sy}%" r="42%">`,
    `<stop offset="0%" stop-color="#ffffff" stop-opacity="0.26"/>`,
    `<stop offset="48%" stop-color="#ffffff" stop-opacity="0.08"/>`,
    `<stop offset="100%" stop-color="#ffffff" stop-opacity="0"/>`,
    `</radialGradient>`,
    `</defs>`,
    `<g clip-path="url(#cp-${uid})">`,
    // 1. 球体柔彩基底 + 极轻的通透边缘层
    `<rect width="64" height="64" fill="url(#sh-${uid})"/>`,
    `<circle cx="32" cy="32" r="32" fill="url(#fr-${uid})"/>`,
    // 2. 内部柔光亮区穹顶（位于外缘内侧，与外圈拉开第一重立体层次）
    `<g filter="url(#bs-${uid})">`,
    `<ellipse cx="${hx}" cy="${hy}" rx="17" ry="14" transform="rotate(-30 ${hx} ${hy})" fill="${palette.light}" fill-opacity="0.9"/>`,
    `<ellipse cx="${hx}" cy="${hy}" rx="10" ry="8.5" transform="rotate(-30 ${hx} ${hy})" fill="#ffffff" fill-opacity="0.34"/>`,
    // 3. 中层弥散光晕（包裹内核，与亮区形成第二重色彩过渡）
    `<ellipse cx="${mx}" cy="${my}" rx="20.5" ry="18.5" transform="rotate(${mRot} ${mx} ${my})" fill="${palette.mid}" fill-opacity="0.92"/>`,
    `</g>`,
    // 4. 更紧致的偏心深色内核（自然融入右下边缘，形成第三重纵深焦点）
    `<g filter="url(#bc-${uid})">`,
    `<ellipse cx="${cx}" cy="${cy}" rx="16.5" ry="15" transform="rotate(${cRot} ${cx} ${cy})" fill="${palette.core}" fill-opacity="0.92"/>`,
    `<ellipse cx="${dx}" cy="${dy}" rx="9.5" ry="9" fill="${palette.deep}" fill-opacity="0.48"/>`,
    `</g>`,
    // 5. 顶部柔光弧面反光
    `<circle cx="32" cy="32" r="32" fill="url(#sp-${uid})"/>`,
    `</g>`,
    `</svg>`,
  ].join('')
}

/** 生成独立可用的 SVG 字符串（适合导出、通知图标或自定义嵌入）。 */
export function avatarSvg(key: string, size = 128): string {
  const hash = hashAvatarKey(key)
  const palette = AVATAR_PALETTES[hash % AVATAR_PALETTES.length]!
  const layoutIndex = (hash >>> 8) % FOCAL_LAYOUTS.length
  const layout = FOCAL_LAYOUTS[layoutIndex]!
  return buildOrbSvg(palette, layout, `${palette.id}-${layoutIndex}`, size)
}

/** 将生成的立体玻璃球体 SVG 编码为缓存的 Data URL，供 <img :src> 复用。 */
export function avatarDataUrl(key: string, size = 128): string {
  const hash = hashAvatarKey(key)
  const palette = AVATAR_PALETTES[hash % AVATAR_PALETTES.length]!
  const layoutIndex = (hash >>> 8) % FOCAL_LAYOUTS.length
  const cacheKey = `${palette.id}:${layoutIndex}:${size}`
  const cached = dataUrlCache.get(cacheKey)
  if (cached) return cached
  const svg = buildOrbSvg(palette, FOCAL_LAYOUTS[layoutIndex]!, `${palette.id}-${layoutIndex}`, size)
  const url = `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`
  dataUrlCache.set(cacheKey, url)
  return url
}

/** 根据用户名 / 账号 key 解析完整的头像配色与渲染 Data URL。 */
export function resolveAvatarIcon(key: string): AvatarIconSpec {
  const hash = hashAvatarKey(key)
  const palette = AVATAR_PALETTES[hash % AVATAR_PALETTES.length]!
  const layoutIndex = (hash >>> 8) % FOCAL_LAYOUTS.length
  const layout = FOCAL_LAYOUTS[layoutIndex]!
  return {
    key,
    hash,
    palette,
    layoutIndex,
    layout,
    dataUrl: avatarDataUrl(key),
  }
}
