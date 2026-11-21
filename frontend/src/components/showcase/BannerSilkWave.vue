<script setup lang="ts">
/**
 * 首屏全屏 3D 液态丝绸光雕流形（仅作用于 100svh Banner 内部）：
 * - 双层交织 3D 透视丝绸曲面（主前景 38 根雕塑经线 + 远景 22 根反向轻纱 + 丝缎半透明光影褶皱）；
 * - 去掉零散星点，改为完全融于曲面的「3D 纬向织纹光肋（呼应表格经纬结构）」与「沿单根丝线顺滑游走的极细高光流丝」；
 * - 开场瞬间（0–1.55s）优雅舒展成横贯全屏的立体雕塑波浪，鼠标轻拂产生真实 3D 弹性波纹，下滑无缝融回纯白页面。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'

interface StrandGlint {
  veil: 1 | 2
  strandRatio: number // 所在纤维层比例 [0..1]
  uOffset: number     // 初始沿程位置
  speed: number       // 沿纤维滑行速度（正反向）
  span: number        // 高光流丝长度（归一化 u 跨度）
  phase: number       // 亮度呼吸相位
}

const canvasRef = ref<HTMLCanvasElement | null>(null)

const STRAND_COUNT = 38
const BACK_STRAND_COUNT = 22
const SEGMENTS = 96

let rafId = 0
let width = 0
let height = 0
let dpr = 1
let startTime = 0
let reducedMotion = false
let visible = true

const pointer = {
  x: 0.5,
  y: 0.5,
  targetX: 0.5,
  targetY: 0.5,
  strength: 0,
  targetStrength: 0,
}

// 18 道贴合单根 3D 丝绸纤维游走的精致高光流丝（随曲面扭转拉长/收细）
const glints: StrandGlint[] = Array.from({ length: 18 }, (_, i) => {
  const h1 = Math.sin(i * 19.13 + 3.7) * 43758.5453
  const r1 = h1 - Math.floor(h1)
  const h2 = Math.sin(i * 47.71 + 9.2) * 24634.6345
  const r2 = h2 - Math.floor(h2)
  return {
    veil: i % 3 === 0 ? 2 : 1,
    strandRatio: 0.06 + (i / 17) * 0.88,
    uOffset: r1,
    speed: (0.045 + r2 * 0.042) * (i % 2 === 0 ? 1 : -1),
    span: 0.065 + r1 * 0.065,
    phase: r2 * Math.PI * 2,
  }
})

function easeOutExpo(t: number): number {
  const k = Math.max(0, Math.min(1, t))
  return k === 1 ? 1 : 1 - 2 ** (-9 * k)
}

function easeOutCubic(t: number): number {
  const k = Math.max(0, Math.min(1, t))
  return 1 - (1 - k) ** 3
}

/** 计算丝绸曲面上任意归一化点 (u in [0..1], v in [0..1]) 的 3D 坐标并投影到屏幕 */
function projectPoint(u: number, v: number, t: number, openAmp: number, veil: 1 | 2 = 1) {
  const xNorm = (u - 0.5) * 2.48
  const vNorm = (v - 0.5) * 2
  const sign = veil === 1 ? 1 : -1

  const twist = xNorm * (veil === 1 ? 1.55 : -1.28) + Math.sin(t * 0.52 * sign) * 0.28 + (veil === 2 ? 1.15 : 0)
  const envelope = 0.58 + 0.42 * Math.cos(xNorm * 0.78)
  const ribbonRadius = (veil === 1 ? 156 + 78 * Math.sin(xNorm * 2.0 + t * 0.45) : 128 + 64 * Math.cos(xNorm * 1.75 - t * 0.38)) * openAmp

  const baseWaveY =
    sign * Math.cos(xNorm * (veil === 1 ? 2.05 : 1.75)) * (veil === 1 ? 116 : 138) * openAmp +
    Math.sin(xNorm * 1.55 - t * 0.62 * sign) * 44 * openAmp

  const foldY = Math.sin(twist + vNorm * 1.18) * ribbonRadius * envelope
  const foldZ = Math.cos(twist + vNorm * 1.18) * ribbonRadius * 1.22 * envelope + (veil === 2 ? 90 : 0)

  // 鼠标近场 3D 弹性波纹形变
  const pxNorm = (pointer.x - 0.5) * 2.48
  const pyWorld = (pointer.y - 0.5) * height
  const dx = xNorm - pxNorm
  const approxY = baseWaveY + foldY
  const dy = (approxY - pyWorld) / Math.max(height * 0.45, 1)
  const distSq = dx * dx + dy * dy
  const influence = pointer.strength * Math.exp(-distSq * 3.4)
  const ripple = Math.sin(Math.sqrt(distSq) * 9 - t * 4.5) * 38 * influence
  const pushZ = -110 * influence

  const worldX = xNorm * (width * 0.52)
  const worldY = baseWaveY + foldY + ripple
  const worldZ = foldZ + pushZ

  const fov = 860
  const perspective = fov / (fov + worldZ + 260)

  return {
    sx: width * 0.5 + worldX * perspective,
    sy: height * 0.5 + worldY * perspective,
    z: worldZ,
    perspective,
    sheen: Math.max(0, Math.min(1, 0.55 - worldZ / 360)),
  }
}

function render(now: number) {
  const canvas = canvasRef.value
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return

  const elapsed = now - startTime
  const t = now * 0.00072
  const openAmp = reducedMotion ? 1 : easeOutExpo(elapsed / 1550)
  const openAlpha = reducedMotion ? 1 : easeOutCubic(elapsed / 900)

  pointer.x += (pointer.targetX - pointer.x) * 0.08
  pointer.y += (pointer.targetY - pointer.y) * 0.08
  pointer.strength += (pointer.targetStrength - pointer.strength) * 0.07

  ctx.clearRect(0, 0, width, height)

  const sweepPos = ((t * 0.34) % 1.6) - 0.3

  function buildGrid(veil: 1 | 2, strandCount: number) {
    const grid: ReturnType<typeof projectPoint>[][] = []
    for (let s = 0; s < strandCount; s++) {
      const v = s / (strandCount - 1)
      const row: ReturnType<typeof projectPoint>[] = []
      for (let i = 0; i <= SEGMENTS; i++) {
        row.push(projectPoint(i / SEGMENTS, v, t, openAmp, veil))
      }
      grid.push(row)
    }
    return grid
  }

  function drawVeil(c: CanvasRenderingContext2D, grid: ReturnType<typeof projectPoint>[][], opacityScale: number, drawRibs: boolean) {
    const strandCount = grid.length

    // 1. 丝缎半透明体积光泽带
    for (let s = 0; s < strandCount - 1; s += 2) {
      const rowA = grid[s]!
      const rowB = grid[Math.min(s + 2, strandCount - 1)]!
      const vNorm = Math.abs(s / (strandCount - 1) - 0.5) * 2

      c.beginPath()
      c.moveTo(rowA[0]!.sx, rowA[0]!.sy)
      for (let i = 1; i <= SEGMENTS; i++) c.lineTo(rowA[i]!.sx, rowA[i]!.sy)
      for (let i = SEGMENTS; i >= 0; i--) c.lineTo(rowB[i]!.sx, rowB[i]!.sy)
      c.closePath()

      const midA = rowA[Math.floor(SEGMENTS * 0.5)]!
      const shadeAlpha = (0.022 + (1 - vNorm) * 0.026) * openAlpha * opacityScale
      const grad = c.createLinearGradient(0, height * 0.1, width, height * 0.9)
      grad.addColorStop(0, `rgba(210, 205, 195, ${shadeAlpha})`)
      grad.addColorStop(Math.max(0.05, Math.min(0.95, sweepPos)), `rgba(255, 255, 255, ${shadeAlpha * 2.5})`)
      grad.addColorStop(1, `rgba(186, 189, 200, ${shadeAlpha * (midA.sheen + 0.5)})`)
      c.fillStyle = grad
      c.fill()
    }

    // 2. 3D 纬向织纹光肋（沿曲面横截面编织出极精致的经纬光感，呼应表格结构）
    if (drawRibs) {
      const ribStep = 6
      for (let i = ribStep; i < SEGMENTS; i += ribStep) {
        const u = i / SEGMENTS
        const edgeFade = Math.sin(u * Math.PI)
        // 日光扫过位置附近的纬线自然亮起
        const nearSweep = Math.exp(-(((u - sweepPos) * 4.2) ** 2))
        const ribAlpha = (0.045 + nearSweep * 0.14) * edgeFade * openAlpha * opacityScale
        if (ribAlpha <= 0.008) continue

        c.beginPath()
        c.moveTo(grid[0]![i]!.sx, grid[0]![i]!.sy)
        for (let s = 1; s < strandCount; s++) {
          c.lineTo(grid[s]![i]!.sx, grid[s]![i]!.sy)
        }
        c.strokeStyle = nearSweep > 0.45
          ? `rgba(255, 255, 255, ${Math.min(0.85, ribAlpha * 3.8)})`
          : `rgba(38, 37, 34, ${ribAlpha})`
        c.lineWidth = nearSweep > 0.45 ? 1.15 : 0.75
        c.stroke()
      }
    }

    // 3. 按 3D 平均深度由远及近绘制雕塑经线
    const orderedStrands = grid
      .map((row, s) => {
        let avgZ = 0
        for (let i = 0; i <= SEGMENTS; i += 8) avgZ += row[i]!.z
        return { row, s, avgZ: avgZ / (SEGMENTS / 8 + 1) }
      })
      .sort((a, b) => b.avgZ - a.avgZ)

    for (const { row, s } of orderedStrands) {
      const v = s / (strandCount - 1)
      const edgeFactor = 1 - Math.abs(v - 0.5) * 1.35
      const isHighlightRibbon = s === 0 || s === strandCount - 1 || s === Math.floor(strandCount * 0.5) || s % 9 === 0

      const strokeGrad = c.createLinearGradient(0, 0, width, 0)
      const baseOpacity = (isHighlightRibbon ? 0.3 : 0.115) * Math.max(0.35, edgeFactor) * openAlpha * opacityScale
      const peakCenter = Math.max(0.12, Math.min(0.88, sweepPos))

      strokeGrad.addColorStop(0, 'rgba(24, 24, 27, 0)')
      strokeGrad.addColorStop(0.12, `rgba(34, 33, 31, ${baseOpacity})`)
      strokeGrad.addColorStop(Math.max(0.14, peakCenter - 0.14), `rgba(24, 24, 27, ${baseOpacity * 1.18})`)
      strokeGrad.addColorStop(peakCenter, isHighlightRibbon ? `rgba(255, 255, 255, ${Math.min(0.96, openAlpha * 0.94)})` : `rgba(148, 144, 136, ${baseOpacity * 1.45})`)
      strokeGrad.addColorStop(Math.min(0.86, peakCenter + 0.14), `rgba(24, 24, 27, ${baseOpacity * 1.18})`)
      strokeGrad.addColorStop(0.88, `rgba(34, 33, 31, ${baseOpacity})`)
      strokeGrad.addColorStop(1, 'rgba(24, 24, 27, 0)')

      c.beginPath()
      c.moveTo(row[0]!.sx, row[0]!.sy)
      for (let i = 1; i < SEGMENTS; i++) {
        const xc = (row[i]!.sx + row[i + 1]!.sx) * 0.5
        const yc = (row[i]!.sy + row[i + 1]!.sy) * 0.5
        c.quadraticCurveTo(row[i]!.sx, row[i]!.sy, xc, yc)
      }
      c.lineTo(row[SEGMENTS]!.sx, row[SEGMENTS]!.sy)

      c.strokeStyle = strokeGrad
      c.lineWidth = isHighlightRibbon ? 1.45 : 0.9
      c.stroke()
    }
  }

  // 4. 沿单根 3D 丝绸纤维游走的精致高光流丝（取代散落星点，完全贴合曲面流形）
  function drawStrandGlints(c: CanvasRenderingContext2D, frontGrid: ReturnType<typeof projectPoint>[][], backGrid: ReturnType<typeof projectPoint>[][]) {
    const glintAlphaBase = reducedMotion ? 0.85 : easeOutCubic((elapsed - 280) / 850)
    if (glintAlphaBase <= 0.01) return

    c.save()
    c.lineCap = 'round'

    for (const g of glints) {
      const targetGrid = g.veil === 1 ? frontGrid : backGrid
      const strandIdx = Math.min(targetGrid.length - 1, Math.max(0, Math.round(g.strandRatio * (targetGrid.length - 1))))
      const row = targetGrid[strandIdx]!
      const headU = ((g.uOffset + t * g.speed) % 1 + 1) % 1
      const startU = Math.max(0.02, headU - g.span * 0.5)
      const endU = Math.min(0.98, headU + g.span * 0.5)
      if (endU - startU < 0.015) continue

      const startIdx = Math.floor(startU * SEGMENTS)
      const endIdx = Math.min(SEGMENTS, Math.ceil(endU * SEGMENTS))
      if (endIdx - startIdx < 2) continue

      const pStart = row[startIdx]!
      const pEnd = row[endIdx]!
      const pMid = row[Math.floor((startIdx + endIdx) * 0.5)]!
      const edgeFade = Math.sin(headU * Math.PI)
      const breathe = 0.55 + 0.45 * Math.sin(t * 2.1 + g.phase)
      const strength = edgeFade * breathe * glintAlphaBase * (g.veil === 1 ? 1 : 0.65)

      // 渐变高光丝：两头透明渐隐，中段晶润银白
      const grad = c.createLinearGradient(pStart.sx, pStart.sy, pEnd.sx, pEnd.sy)
      grad.addColorStop(0, 'rgba(255, 255, 255, 0)')
      grad.addColorStop(0.28, `rgba(42, 40, 38, ${0.26 * strength})`)
      grad.addColorStop(0.55, `rgba(255, 255, 255, ${0.98 * strength})`)
      grad.addColorStop(0.78, `rgba(42, 40, 38, ${0.22 * strength})`)
      grad.addColorStop(1, 'rgba(255, 255, 255, 0)')

      // 外层极轻银白柔光
      c.beginPath()
      c.moveTo(pStart.sx, pStart.sy)
      for (let i = startIdx + 1; i <= endIdx; i++) {
        c.lineTo(row[i]!.sx, row[i]!.sy)
      }
      c.strokeStyle = `rgba(255, 255, 255, ${0.62 * strength})`
      c.lineWidth = 4.2 * pMid.perspective
      c.stroke()

      // 内层锐利金属银白丝线
      c.strokeStyle = grad
      c.lineWidth = 1.85 * pMid.perspective
      c.stroke()
    }

    c.restore()
  }

  const backGrid = buildGrid(2, BACK_STRAND_COUNT)
  const frontGrid = buildGrid(1, STRAND_COUNT)

  drawVeil(ctx, backGrid, 0.58, false)
  drawVeil(ctx, frontGrid, 1, true)
  drawStrandGlints(ctx, frontGrid, backGrid)

  if (!reducedMotion && visible) {
    rafId = requestAnimationFrame(render)
  }
}

function resize() {
  const canvas = canvasRef.value
  if (!canvas) return
  const rect = canvas.parentElement?.getBoundingClientRect()
  width = rect?.width || window.innerWidth
  height = rect?.height || window.innerHeight
  dpr = Math.min(window.devicePixelRatio || 1, 2)
  canvas.width = Math.floor(width * dpr)
  canvas.height = Math.floor(height * dpr)
  canvas.style.width = `${width}px`
  canvas.style.height = `${height}px`
  const ctx = canvas.getContext('2d')
  if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  if (reducedMotion) render(performance.now())
}

function onPointerMove(e: PointerEvent) {
  const canvas = canvasRef.value
  if (!canvas) return
  const rect = canvas.getBoundingClientRect()
  if (e.clientY > rect.bottom || e.clientY < rect.top) {
    pointer.targetStrength = 0
    return
  }
  pointer.targetX = (e.clientX - rect.left) / Math.max(rect.width, 1)
  pointer.targetY = (e.clientY - rect.top) / Math.max(rect.height, 1)
  pointer.targetStrength = 1
}

function onPointerLeave() {
  pointer.targetStrength = 0
}

function onVisibilityChange() {
  if (reducedMotion) return
  if (document.hidden) {
    cancelAnimationFrame(rafId)
    rafId = 0
  } else if (visible && !rafId) {
    rafId = requestAnimationFrame(render)
  }
}

let io: IntersectionObserver | null = null

onMounted(() => {
  reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  resize()
  startTime = performance.now()

  window.addEventListener('resize', resize, { passive: true })
  window.addEventListener('pointermove', onPointerMove, { passive: true })
  window.addEventListener('pointerleave', onPointerLeave, { passive: true })
  document.addEventListener('visibilitychange', onVisibilityChange)

  if ('IntersectionObserver' in window && canvasRef.value) {
    io = new IntersectionObserver(
      (entries) => {
        const isIn = entries[0]?.isIntersecting ?? true
        visible = isIn
        if (isIn && !reducedMotion && !rafId) {
          rafId = requestAnimationFrame(render)
        } else if (!isIn && rafId) {
          cancelAnimationFrame(rafId)
          rafId = 0
        }
      },
      { threshold: 0.01 },
    )
    io.observe(canvasRef.value)
  }

  if (reducedMotion) {
    render(startTime + 2000)
  } else {
    rafId = requestAnimationFrame(render)
  }
})

onBeforeUnmount(() => {
  cancelAnimationFrame(rafId)
  io?.disconnect()
  window.removeEventListener('resize', resize)
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerleave', onPointerLeave)
  document.removeEventListener('visibilitychange', onVisibilityChange)
})
</script>

<template>
  <canvas
    ref="canvasRef"
    class="pointer-events-none absolute inset-0 block h-full w-full"
    aria-hidden="true"
  />
</template>
