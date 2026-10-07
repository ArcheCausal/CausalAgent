let loadPromise: Promise<void> | null = null

function markedApi(): NonNullable<Window['marked']> | null {
  return typeof window !== 'undefined' && window.marked ? window.marked : null
}

export function ensureMarkedLoaded(): Promise<void> {
  if (markedApi()) return Promise.resolve()
  if (loadPromise) return loadPromise
  loadPromise = new Promise<void>((resolve) => {
    if (typeof document === 'undefined') {
      resolve()
      return
    }
    const script = document.createElement('script')
    script.src = `${import.meta.env.BASE_URL}vendor/marked.min.js`
    script.async = true
    script.addEventListener('load', () => resolve())
    script.addEventListener('error', () => resolve())
    document.head.appendChild(script)
  })
  return loadPromise
}

function fallbackMarkdown(source: string): string {
  return source.split('\n').map((line) => `${line}<br>`).join('')
}

/**
 * 普通端的 marked 兼容边界。当前版本允许报告中的原始 HTML，故这里不增加清洗。
 * 业务组件只能依赖这个适配器，不能直接读取 window.marked。
 */
export function renderMarkdown(source: string): string {
  const parser = markedApi()
  if (!parser) return fallbackMarkdown(source)
  try {
    return parser.parse(source)
  } catch {
    return fallbackMarkdown(source)
  }
}
