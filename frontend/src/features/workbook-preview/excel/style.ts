import type { IBorderData, IBorderStyleData, IStyleData } from '@univerjs/core'
import type { BorderStyle, Color, Style } from 'exceljs'

type ExcelColor = Partial<Color> & { indexed?: number; tint?: number; auto?: boolean }

const DEFAULT_THEME = [
  'FFFFFF', '000000', 'EEECE1', '1F497D', '4F81BD', 'C0504D',
  '9BBB59', '8064A2', '4BACC6', 'F79646', '0000FF', '800080',
]
const INDEXED_COLORS = [
  '000000', 'FFFFFF', 'FF0000', '00FF00', '0000FF', 'FFFF00', 'FF00FF', '00FFFF',
  '000000', 'FFFFFF', 'FF0000', '00FF00', '0000FF', 'FFFF00', 'FF00FF', '00FFFF',
  '800000', '008000', '000080', '808000', '800080', '008080', 'C0C0C0', '808080',
  '9999FF', '993366', 'FFFFCC', 'CCFFFF', '660066', 'FF8080', '0066CC', 'CCCCFF',
  '000080', 'FF00FF', 'FFFF00', '00FFFF', '800080', '800000', '008080', '0000FF',
  '00CCFF', 'CCFFFF', 'CCFFCC', 'FFFF99', '99CCFF', 'FF99CC', 'CC99FF', 'FFCC99',
  '3366FF', '33CCCC', '99CC00', 'FFCC00', 'FF9900', 'FF6600', '666699', '969696',
  '003366', '339966', '003300', '333300', '993300', '993366', '333399', '333333',
]

// Numeric values mirror the public Univer 1.3 enum definitions. Keeping these
// local lets the adapter run in a worker or Node without loading the UI runtime.
const BORDER_STYLES: Record<BorderStyle, IBorderStyleData['s']> = {
  thin: 1, hair: 2, dotted: 3, dashed: 4, dashDot: 5, dashDotDot: 6,
  double: 7, medium: 8, mediumDashed: 9, mediumDashDot: 10,
  mediumDashDotDot: 11, slantDashDot: 12, thick: 13,
}

/** Extract the workbook's theme palette without depending on DOMParser. */
export function readThemeColors(themes: unknown): string[] {
  const colors = [...DEFAULT_THEME]
  const xml = typeof themes === 'string' ? themes : themes && typeof themes === 'object'
    ? Object.values(themes).find((value): value is string => typeof value === 'string' && value.includes('clrScheme'))
    : undefined
  if (!xml) return colors
  const scheme = xml.match(/<(?:\w+:)?clrScheme\b[^>]*>([\s\S]*?)<\/(?:\w+:)?clrScheme>/)?.[1]
  if (!scheme) return colors
  const names = ['lt1', 'dk1', 'lt2', 'dk2', 'accent1', 'accent2', 'accent3', 'accent4', 'accent5', 'accent6', 'hlink', 'folHlink']
  names.forEach((name, index) => {
    const block = scheme.match(new RegExp(`<(?:\\w+:)?${name}\\b[^>]*>([\\s\\S]*?)<\\/(?:\\w+:)?${name}>`))?.[1]
    const color = block?.match(/\b(?:lastClr|val)=["']([0-9a-fA-F]{6})["']/)?.[1]
    if (color) colors[index] = color.toUpperCase()
  })
  return colors
}

function tintColor(hex: string, tint: number): string {
  // Excel adjusts HSL luminance for theme tints, rather than tinting RGB bytes.
  const rgb = [0, 2, 4].map((offset) => parseInt(hex.slice(offset, offset + 2), 16) / 255)
  const max = Math.max(...rgb), min = Math.min(...rgb), delta = max - min
  let light = (max + min) / 2
  const saturation = delta === 0 ? 0 : delta / (1 - Math.abs(2 * light - 1))
  let hue = 0
  if (delta !== 0) {
    if (max === rgb[0]) hue = ((rgb[1]! - rgb[2]!) / delta) % 6
    else if (max === rgb[1]) hue = (rgb[2]! - rgb[0]!) / delta + 2
    else hue = (rgb[0]! - rgb[1]!) / delta + 4
    hue = ((hue * 60) + 360) % 360
  }
  light = tint < 0 ? light * (1 + tint) : light * (1 - tint) + tint
  const chroma = (1 - Math.abs(2 * light - 1)) * saturation
  const secondary = chroma * (1 - Math.abs((hue / 60) % 2 - 1))
  const channels = hue < 60 ? [chroma, secondary, 0] : hue < 120 ? [secondary, chroma, 0]
    : hue < 180 ? [0, chroma, secondary] : hue < 240 ? [0, secondary, chroma]
      : hue < 300 ? [secondary, 0, chroma] : [chroma, 0, secondary]
  const offset = light - chroma / 2
  return channels.map((value) => Math.round(Math.max(0, Math.min(1, value + offset)) * 255).toString(16).padStart(2, '0')).join('').toUpperCase()
}

export function colorToHex(color: ExcelColor | undefined, theme: string[]): string | undefined {
  if (!color) return undefined
  let hex: string | undefined
  if (color.argb && /^(?:[0-9a-f]{6}|[0-9a-f]{8})$/i.test(color.argb)) hex = color.argb.slice(-6).toUpperCase()
  else if (typeof color.theme === 'number') hex = theme[color.theme]
  else if (typeof color.indexed === 'number') hex = INDEXED_COLORS[color.indexed] ?? (color.indexed === 64 ? '000000' : undefined)
  else if (color.auto) hex = '000000'
  if (!hex) return undefined
  if (typeof color.tint === 'number' && color.tint !== 0) hex = tintColor(hex, Math.max(-1, Math.min(1, color.tint)))
  return `#${hex}`
}

export function convertStyle(source: Partial<Style>, theme: string[], warnings: Set<string>): IStyleData {
  const style: IStyleData = {}
  const font = source.font
  if (font) {
    if (font.name) style.ff = font.name
    if (font.size) style.fs = font.size
    if (font.bold !== undefined) style.bl = font.bold ? 1 : 0
    if (font.italic !== undefined) style.it = font.italic ? 1 : 0
    if (font.strike) style.st = { s: 1 }
    if (font.underline && font.underline !== 'none') {
      const decoration = font.underline === 'double' ? 10 : font.underline === 'singleAccounting' ? 18 : font.underline === 'doubleAccounting' ? 19 : 12
      style.ul = { s: 1, t: decoration }
    }
    if (font.vertAlign) style.va = font.vertAlign === 'superscript' ? 3 : 2
    const foreground = colorToHex(font.color, theme)
    if (foreground) style.cl = { rgb: foreground }
  }
  const fill = source.fill
  // ExcelJS reads the ordinary empty <patternFill/> as { type: 'pattern' }.
  // A missing pattern is the same as 'none', not an unsupported pattern fill.
  if (fill?.type === 'pattern' && fill.pattern && fill.pattern !== 'none') {
    const background = colorToHex(fill.fgColor ?? fill.bgColor, theme)
    if (background) style.bg = { rgb: background }
    if (fill.pattern !== 'solid') warnings.add('部分单元格使用图案填充，预览以基础底色显示。')
  } else if (fill?.type === 'gradient') {
    const background = colorToHex(fill.stops[0]?.color, theme)
    if (background) style.bg = { rgb: background }
    warnings.add('部分单元格使用渐变填充，预览以基础底色显示。')
  }
  if (source.border) {
    const borders: IBorderData = {}
    for (const [excel, univer] of [['top', 't'], ['right', 'r'], ['bottom', 'b'], ['left', 'l']] as const) {
      const border = source.border[excel]
      if (border?.style) borders[univer] = { s: BORDER_STYLES[border.style], cl: { rgb: colorToHex(border.color, theme) ?? '#000000' } }
    }
    const diagonal = source.border.diagonal
    if (diagonal?.style) {
      const border = { s: BORDER_STYLES[diagonal.style], cl: { rgb: colorToHex(diagonal.color, theme) ?? '#000000' } }
      if (diagonal.down) borders.tl_br = border
      if (diagonal.up) borders.bl_tr = border
    }
    if (Object.keys(borders).length) style.bd = borders
  }
  const alignment = source.alignment
  if (alignment) {
    const horizontal = { left: 1, center: 2, right: 3, fill: 4, justify: 4, centerContinuous: 2, distributed: 6 } as const
    const vertical = { top: 1, middle: 2, bottom: 3, distributed: 2, justify: 2 } as const
    if (alignment.horizontal) style.ht = horizontal[alignment.horizontal]
    if (alignment.vertical) style.vt = vertical[alignment.vertical]
    if (alignment.wrapText !== undefined) style.tb = alignment.wrapText ? 3 : 1
    if (alignment.shrinkToFit) style.stf = 1
    if (alignment.indent) style.pd = { l: alignment.indent * 9 }
    if (alignment.readingOrder) style.td = alignment.readingOrder === 'rtl' ? 2 : 1
    if (alignment.textRotation === 'vertical') style.tr = { a: 0, v: 1 }
    else if (typeof alignment.textRotation === 'number') style.tr = { a: alignment.textRotation }
  }
  if (source.numFmt && source.numFmt !== 'General') style.n = { pattern: source.numFmt }
  return style
}

/** Excel widths use font-character units; Univer lays columns out in CSS pixels. */
export function columnWidthToPixels(width: number): number {
  return Math.max(1, Math.round(width < 1 ? width * 12 : width * 7 + 5))
}
