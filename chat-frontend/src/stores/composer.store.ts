import { defineStore } from 'pinia'

interface ComposerState {
  draft: string
  webSearchEnabled: boolean
  selectedFileId: number | null
  sending: boolean
}

export const useComposerStore = defineStore('composer', {
  state: (): ComposerState => ({
    draft: '',
    webSearchEnabled: false,
    selectedFileId: null,
    sending: false,
  }),
  actions: {
    setDraft(value: string): void {
      this.draft = value
    },
    setWebSearch(value: boolean): void {
      this.webSearchEnabled = value
    },
    setSelectedFile(value: number | null): void {
      this.selectedFileId = value
    },
    setSending(value: boolean): void {
      this.sending = value
    },
    clearAfterSend(): void {
      this.draft = ''
      this.selectedFileId = null
    },
    restoreDraft(original: string): void {
      if (!this.draft.trim()) this.draft = original
    },
    reset(): void {
      this.draft = ''
      this.webSearchEnabled = false
      this.selectedFileId = null
      this.sending = false
    },
  },
})
