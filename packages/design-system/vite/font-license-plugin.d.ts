export function fontLicenseFiles(): {
  name: string
  apply: 'build'
  configResolved(config: { root: string; build: { outDir: string } }): void
  closeBundle(): void
}
