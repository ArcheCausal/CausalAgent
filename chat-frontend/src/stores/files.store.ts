import { defineStore } from 'pinia'
import { api } from '../api/client'
import type { FileResponse } from '../api/files.schemas'
import type { UserFile } from '../types/domain'

interface FilesState {
  items: UserFile[]
  selectedId: number | null
  status: 'idle' | 'loading' | 'uploading' | 'deleting' | 'ready' | 'error'
  error: string | null
}

function toFile(value: FileResponse): UserFile {
  return {
    id: value.id,
    userFileId: value.user_file_id,
    filename: value.filename,
    mimeType: value.mime_type,
    fileSize: value.file_size,
    uploadedAt: value.uploaded_at,
    lastAccessedAt: value.last_accessed_at,
    accessCount: value.access_count,
  }
}

export const useFilesStore = defineStore('files', {
  state: (): FilesState => ({
    items: [],
    selectedId: null,
    status: 'idle',
    error: null,
  }),
  getters: {
    selected: (state): UserFile | null => state.items.find((file) => file.userFileId === state.selectedId) ?? null,
  },
  actions: {
    async load(): Promise<void> {
      this.status = 'loading'
      try {
        this.items = (await api.listFiles()).map(toFile)
        this.status = 'ready'
        this.error = null
      } catch (error) {
        this.status = 'error'
        this.error = error instanceof Error ? error.message : '加载文件列表失败。'
        throw error
      }
    },
    select(fileId: number): void {
      this.selectedId = this.items.some((file) => file.userFileId === fileId) ? fileId : null
    },
    clearSelection(): void {
      this.selectedId = null
    },
    async upload(file: File): Promise<UserFile> {
      this.status = 'uploading'
      try {
        const response = await api.uploadFile(file)
        if (!response.success || !response.file) throw new Error(response.error || '文件上传失败。')
        const converted = toFile(response.file)
        const index = this.items.findIndex((candidate) => candidate.userFileId === converted.userFileId)
        if (index >= 0) this.items[index] = converted
        else this.items.unshift(converted)
        this.selectedId = converted.userFileId
        this.status = 'ready'
        this.error = null
        return converted
      } catch (error) {
        this.status = 'error'
        this.error = error instanceof Error ? error.message : '文件上传失败。'
        throw error
      }
    },
    async remove(fileId: number): Promise<void> {
      this.status = 'deleting'
      try {
        const response = await api.deleteFile(fileId)
        if (!response.success) throw new Error(response.error || '删除文件失败。')
        this.items = this.items.filter((file) => file.userFileId !== fileId)
        if (this.selectedId === fileId) this.selectedId = null
        this.status = 'ready'
        this.error = null
      } catch (error) {
        this.status = 'error'
        this.error = error instanceof Error ? error.message : '删除文件失败。'
        throw error
      }
    },
    reset(): void {
      this.items = []
      this.selectedId = null
      this.status = 'idle'
      this.error = null
    },
  },
})
