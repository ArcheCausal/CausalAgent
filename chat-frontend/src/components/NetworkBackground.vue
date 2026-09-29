<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

interface GraphNode {
  x: number
  y: number
  radius: number
}

interface GraphParticle {
  edge: readonly [number, number]
  progress: number
  speed: number
}

interface RgbColor {
  red: number
  green: number
  blue: number
}

const graphNodes: readonly GraphNode[] = [
  { x: 0.08, y: 0.18, radius: 3 },
  { x: 0.06, y: 0.52, radius: 2.4 },
  { x: 0.1, y: 0.84, radius: 2.6 },
  { x: 0.24, y: 0.33, radius: 2.6 },
  { x: 0.22, y: 0.68, radius: 3.2 },
  { x: 0.38, y: 0.5, radius: 4 },
  { x: 0.56, y: 0.22, radius: 2.8 },
  { x: 0.58, y: 0.78, radius: 3 },
  { x: 0.74, y: 0.42, radius: 3.4 },
  { x: 0.9, y: 0.26, radius: 2.4 },
  { x: 0.92, y: 0.7, radius: 2.6 },
]

const graphEdges: readonly (readonly [number, number])[] = [
  [0, 3], [1, 3], [1, 4], [2, 4], [0, 4], [3, 5], [4, 5],
  [5, 6], [5, 7], [6, 8], [7, 8], [8, 9], [8, 10], [6, 9],
]

const particles: GraphParticle[] = graphEdges.map((edge, index) => ({
  edge,
  progress: index / graphEdges.length,
  speed: 0.000035 + 0.000015 * (index % 4),
}))

const canvas = ref<HTMLCanvasElement | null>(null)
let context: CanvasRenderingContext2D | null = null
let color: RgbColor | null = null
let width = 0
let height = 0
let scale = 1
let lineOpacity = 0.05
let nodeOpacity = 0.05
let particleOpacity = 0.1
let lastPaintAt = 0
let animationFrame: number | null = null
let resizeObserver: ResizeObserver | null = null
let motionPreference: MediaQueryList | null = null
let usesWindowResize = false

function readColor(value: string): RgbColor | null {
  let hex = value.trim().replace(/^#/, '')
  if (hex.length === 3) hex = hex.split('').map((digit) => digit + digit).join('')
  if (!/^[\da-f]{6}$/i.test(hex)) return null
  const packed = Number.parseInt(hex, 16)
  return {
    red: (packed >> 16) & 255,
    green: (packed >> 8) & 255,
    blue: packed & 255,
  }
}

function readOpacity(token: string, fallback: number): number {
  const value = Number.parseFloat(window.getComputedStyle(document.documentElement).getPropertyValue(token))
  return Number.isFinite(value) ? value : fallback
}

function ink(opacity: number): string {
  if (!color) return 'transparent'
  return 'rgba(' + color.red + ', ' + color.green + ', ' + color.blue + ', ' + opacity + ')'
}

function drawGraph(now: number): void {
  const ctx = context
  if (!ctx || !width || !height) return
  const driftX = Math.sin(now / 26000) * 4
  const driftY = Math.cos(now / 31000) * 3

  ctx.clearRect(0, 0, width, height)
  ctx.globalAlpha = 1
  ctx.lineWidth = 1
  ctx.strokeStyle = ink(lineOpacity)
  for (const [fromIndex, toIndex] of graphEdges) {
    const from = graphNodes[fromIndex]
    const to = graphNodes[toIndex]
    if (!from || !to) continue
    ctx.beginPath()
    ctx.moveTo(from.x * width + driftX, from.y * height + driftY)
    ctx.lineTo(to.x * width + driftX, to.y * height + driftY)
    ctx.stroke()
  }

  ctx.strokeStyle = ink(nodeOpacity)
  graphNodes.forEach((node, index) => {
    ctx.globalAlpha = 0.7 + 0.3 * Math.sin(now / 5200 + index * 1.7)
    ctx.beginPath()
    ctx.arc(node.x * width + driftX, node.y * height + driftY, node.radius * scale, 0, Math.PI * 2)
    ctx.stroke()
  })

  ctx.fillStyle = ink(particleOpacity)
  for (const particle of particles) {
    const from = graphNodes[particle.edge[0]]
    const to = graphNodes[particle.edge[1]]
    if (!from || !to) continue
    ctx.globalAlpha = 0.15 + 0.85 * Math.sin(Math.PI * particle.progress)
    ctx.beginPath()
    ctx.arc(
      (from.x + (to.x - from.x) * particle.progress) * width + driftX,
      (from.y + (to.y - from.y) * particle.progress) * height + driftY,
      1.7 * scale,
      0,
      Math.PI * 2,
    )
    ctx.fill()
  }
  ctx.globalAlpha = 1
}

function resizeGraph(): void {
  const element = canvas.value
  const parent = element?.parentElement
  if (!element || !parent || !context) return

  width = parent.clientWidth
  height = parent.clientHeight
  if (!width || !height) return

  const pixelRatio = window.devicePixelRatio || 1
  scale = Math.max(0.85, Math.min(width / 1400, 1.5))
  element.width = Math.round(width * pixelRatio)
  element.height = Math.round(height * pixelRatio)
  context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0)
  drawGraph(window.performance.now())
}

function animate(now: number): void {
  const delta = lastPaintAt ? Math.min(now - lastPaintAt, 120) : 0
  if (!lastPaintAt || now - lastPaintAt >= 33) {
    lastPaintAt = now
    particles.forEach((particle) => {
      particle.progress = (particle.progress + particle.speed * delta) % 1
    })
    drawGraph(now)
  }
  animationFrame = window.requestAnimationFrame(animate)
}

function stopAnimation(): void {
  if (animationFrame !== null) window.cancelAnimationFrame(animationFrame)
  animationFrame = null
  lastPaintAt = 0
}

function onMotionPreferenceChange(): void {
  stopAnimation()
  if (motionPreference?.matches) drawGraph(window.performance.now())
  else animationFrame = window.requestAnimationFrame(animate)
}

onMounted(() => {
  if (!canvas.value) return
  context = canvas.value.getContext('2d')
  if (!context) return

  const styles = window.getComputedStyle(document.documentElement)
  color = readColor(styles.getPropertyValue('--color-text'))
  if (!color) return
  lineOpacity = readOpacity('--app-graph-line', 0.05)
  nodeOpacity = readOpacity('--app-graph-node', 0.05)
  particleOpacity = readOpacity('--app-graph-dot', 0.1)

  const parent = canvas.value.parentElement
  if (parent && typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(resizeGraph)
    resizeObserver.observe(parent)
  } else {
    window.addEventListener('resize', resizeGraph)
    usesWindowResize = true
  }
  resizeGraph()

  motionPreference = window.matchMedia('(prefers-reduced-motion: reduce)')
  motionPreference.addEventListener('change', onMotionPreferenceChange)
  if (!motionPreference.matches) animationFrame = window.requestAnimationFrame(animate)
})

onBeforeUnmount(() => {
  stopAnimation()
  resizeObserver?.disconnect()
  resizeObserver = null
  if (usesWindowResize) window.removeEventListener('resize', resizeGraph)
  usesWindowResize = false
  motionPreference?.removeEventListener('change', onMotionPreferenceChange)
  motionPreference = null
  context = null
})
</script>

<template>
  <canvas ref="canvas" aria-hidden="true"></canvas>
</template>
