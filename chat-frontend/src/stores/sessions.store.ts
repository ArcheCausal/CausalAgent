import { defineStore } from 'pinia'
import { api } from '../api/client'
import type { ChatMessageResponse, ExecutionPhaseResponse } from '../api/sessions.schemas'
import type { ChatMessage, ExecutionPhase, MessageText, SessionSummary, StructuredMessage, ThinkingProjection } from '../types/domain'

interface SessionsState {
  items: SessionSummary[]
  currentId: string | null
  messages: ChatMessage[]
  conversationVersion: number | null
  listStatus: 'idle' | 'loading' | 'ready' | 'error'
  loadStatus: 'idle' | 'loading' | 'ready' | 'error'
  error: string | null
  listGeneration: number
  loadGeneration: number
  localMessageSequence: number
}

function toStructuredMessage(value: Exclude<MessageText, string>): StructuredMessage {
  return { ...value }
}

function toPhase(value: ExecutionPhaseResponse): ExecutionPhase {
  return {
    phaseSequence: value.phase_sequence,
    status: value.status,
    elapsedSeconds: value.elapsed_seconds,
    lastEventId: value.last_event_id,
    analysisJobId: value.analysis_job_id ?? undefined,
    analysisJobInputId: value.analysis_job_input_id ?? undefined,
    events: value.events.map((event) => ({ ...event })),
  }
}

function toMessage(value: ChatMessageResponse, localId: string): ChatMessage {
  const text: MessageText = typeof value.text === 'string' ? value.text : toStructuredMessage(value.text)
  return {
    localId,
    messageId: value.message_id,
    checkpointId: value.checkpoint_id,
    sender: value.sender,
    text,
    analysisJobId: value.analysis_job_id ?? undefined,
    analysisJobInputId: value.analysis_job_input_id ?? undefined,
    fileAttachment: value.file_attachment ? { filename: value.file_attachment.filename } : undefined,
    references: value.references?.map((reference) => ({ title: reference.title, url: reference.url })),
    thinkingAfter: value.thinking_after ? toPhase(value.thinking_after) : undefined,
  }
}

export const useSessionsStore = defineStore('sessions', {
  state: (): SessionsState => ({
    items: [],
    currentId: null,
    messages: [],
    conversationVersion: null,
    listStatus: 'idle',
    loadStatus: 'idle',
    error: null,
    listGeneration: 0,
    loadGeneration: 0,
    localMessageSequence: 0,
  }),
  getters: {
    current: (state): SessionSummary | null => state.items.find((item) => item.id === state.currentId) ?? null,
    hasMessages: (state): boolean => state.messages.length > 0,
  },
  actions: {
    async loadList(): Promise<void> {
      const generation = this.listGeneration + 1
      this.listGeneration = generation
      this.listStatus = 'loading'
      try {
        const response = await api.listSessions()
        if (this.listGeneration !== generation) return
        this.items = response.map(([id, info]) => ({ id, preview: info.preview, lastTime: info.last_time }))
        this.listStatus = 'ready'
        this.error = null
      } catch (error) {
        if (this.listGeneration !== generation) return
        this.listStatus = 'error'
        this.error = error instanceof Error ? error.message : '加载历史记录失败。'
        throw error
      }
    },
    async create(): Promise<string> {
      const response = await api.newChat()
      if (!response.success || !response.new_session_id) throw new Error(response.error || '创建新对话失败。')
      this.loadGeneration += 1
      this.currentId = response.new_session_id
      this.messages = []
      this.conversationVersion = 0
      this.loadStatus = 'ready'
      return response.new_session_id
    },
    async select(sessionId: string, preserveMessageId?: number): Promise<boolean> {
      const generation = this.loadGeneration + 1
      this.loadGeneration = generation
      this.loadStatus = 'loading'
      try {
        const response = await api.loadSession(sessionId)
        if (this.loadGeneration !== generation) return false
        if (!response.success) throw new Error(response.error || '无法加载会话。')
        if (preserveMessageId !== undefined && !response.messages?.some((message) => message.message_id === preserveMessageId)) {
          this.loadStatus = 'ready'
          return false
        }
        this.currentId = sessionId
        this.conversationVersion = response.conversation_version ?? null
        this.localMessageSequence = 0
        this.messages = (response.messages ?? []).map((message) => {
          this.localMessageSequence += 1
          return toMessage(message, message.message_id === undefined ? `message-${this.localMessageSequence}` : `saved-${message.message_id}`)
        })
        this.loadStatus = 'ready'
        this.error = null
        return true
      } catch (error) {
        if (this.loadGeneration !== generation) return false
        this.loadStatus = 'error'
        this.error = error instanceof Error ? error.message : '加载会话失败。'
        throw error
      }
    },
    appendUserMessage(text: string, jobId: string, fileAttachment?: ChatMessage['fileAttachment'], messageId?: number): void {
      this.localMessageSequence += 1
      this.messages.push({
        localId: messageId === undefined ? `message-${this.localMessageSequence}` : `saved-${messageId}`,
        messageId,
        sender: 'user',
        text,
        analysisJobId: jobId,
        fileAttachment,
      })
    },
    /** 追问恢复后，上一阶段的执行记录固定在它所属的用户消息上，不再随运行态记录继续变化。 */
    freezeThinking(jobId: string, thinking: ThinkingProjection): void {
      for (let index = this.messages.length - 1; index >= 0; index -= 1) {
        const message: ChatMessage | undefined = this.messages[index]
        if (!message || message.sender !== 'user' || message.analysisJobId !== jobId) continue
        if (message.frozenThinking) continue
        this.messages[index] = { ...message, frozenThinking: thinking }
        return
      }
    },
    async rename(sessionId: string, title: string): Promise<void> {
      const response = await api.changeSession(sessionId, title)
      if (!response.success) throw new Error(response.error || '更新标题失败。')
      const item = this.items.find((candidate) => candidate.id === sessionId)
      if (item) item.preview = title
    },
    async remove(sessionId: string): Promise<void> {
      const response = await api.deleteSession(sessionId)
      if (!response.success) throw new Error(response.error || '删除会话失败。')
      this.items = this.items.filter((item) => item.id !== sessionId)
      if (this.currentId === sessionId) {
        this.currentId = null
        this.messages = []
        this.conversationVersion = null
        this.loadStatus = 'idle'
      }
    },
    reset(): void {
      this.items = []
      this.currentId = null
      this.messages = []
      this.conversationVersion = null
      this.listStatus = 'idle'
      this.loadStatus = 'idle'
      this.error = null
      this.listGeneration += 1
      this.loadGeneration += 1
    },
  },
})
