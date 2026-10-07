/// <reference types="vite/client" />

declare global {
  interface Window {
    marked?: {
      parse: (source: string) => string
    }
  }
}

export {}
