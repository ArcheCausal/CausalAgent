import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const licenseSourceDirectory = fileURLToPath(new URL('../src/fonts/licenses/', import.meta.url))
const licenseFiles = ['GEIST-OFL-1.1.txt', 'NOTO-SANS-SC-OFL-1.1.txt']

export function fontLicenseFiles() {
  let outputDirectory

  return {
    name: 'causalagent-font-license-files',
    apply: 'build',
    configResolved(config) {
      outputDirectory = resolve(config.root, config.build.outDir)
    },
    closeBundle() {
      if (!outputDirectory) throw new Error('Vite did not resolve the frontend output directory')

      const destinationDirectory = resolve(outputDirectory, 'assets', 'font-licenses')
      mkdirSync(destinationDirectory, { recursive: true })
      for (const filename of licenseFiles) {
        const contents = readFileSync(resolve(licenseSourceDirectory, filename))
        const digest = createHash('sha256').update(contents).digest('hex').slice(0, 12)
        const outputName = filename.replace(/[.]txt$/u, '-' + digest + '.txt')
        writeFileSync(resolve(destinationDirectory, outputName), contents)
      }
    },
  }
}
