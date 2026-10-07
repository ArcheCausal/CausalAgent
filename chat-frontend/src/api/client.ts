import { z } from 'zod'
import { ApiError, errorMessageForStatus, fallbackErrorCode } from './errors'
import type { CheckAuthResponse, LoginResponse, LogoutResponse, RegisterResponse } from './auth.schemas'
import {
  checkAuthResponseSchema,
  loginResponseSchema,
  logoutResponseSchema,
  registerResponseSchema,
} from './auth.schemas'
import type { FileResponse, UploadFileResponse, DeleteFileResponse } from './files.schemas'
import { deleteFileResponseSchema, filesResponseSchema, uploadFileResponseSchema } from './files.schemas'
import type { ActiveJobsResponse, JobActionResponse } from './jobs.schemas'
import { activeJobsResponseSchema, jobActionResponseSchema } from './jobs.schemas'
import type {
  ChangeSessionResponse,
  DeleteSessionResponse,
  LoadSessionResponse,
  NewChatResponse,
  SessionResponse,
} from './client-types'
import {
  changeSessionResponseSchema,
  deleteSessionResponseSchema,
  loadSessionResponseSchema,
  newChatResponseSchema,
  sessionsResponseSchema,
} from './client-types'

export interface RequestOptions extends RequestInit {
  schema?: z.ZodType<unknown>
}

async function readBody(response: Response): Promise<unknown> {
  const text = await response.text()
  if (!text) return null
  try {
    const parsed: unknown = JSON.parse(text)
    return parsed
  } catch {
    return text
  }
}

function payloadMessage(payload: unknown, status: number | null): string {
  if (typeof payload === 'object' && payload !== null && 'error' in payload) {
    const message = payload.error
    if (typeof message === 'string' && message.trim()) return message
  }
  return errorMessageForStatus(status)
}

function payloadCode(payload: unknown, status: number | null): string {
  if (typeof payload === 'object' && payload !== null && 'code' in payload) {
    const code = payload.code
    if (typeof code === 'string' && code.trim()) return code
  }
  return fallbackErrorCode(status)
}

export async function requestJson<T>(
  input: string,
  init: RequestInit,
  schema: z.ZodType<T>,
): Promise<T> {
  let response: Response
  try {
    response = await fetch(input, {
      credentials: 'same-origin',
      ...init,
    })
  } catch (cause) {
    throw new ApiError({
      status: null,
      code: 'network_error',
      message: errorMessageForStatus(null),
      requestId: null,
      cause,
    })
  }

  const requestId = response.headers.get('X-Request-ID')
  let payload: unknown
  try {
    payload = await readBody(response)
  } catch (cause) {
    throw new ApiError({
      status: response.status,
      code: 'response_body_error',
      message: '无法读取服务器响应。',
      requestId,
      cause,
    })
  }
  if (!response.ok) {
    throw new ApiError({
      status: response.status,
      code: payloadCode(payload, response.status),
      message: payloadMessage(payload, response.status),
      requestId,
      details: payload,
    })
  }

  const parsed = schema.safeParse(payload)
  if (!parsed.success) {
    throw new ApiError({
      status: response.status,
      code: 'invalid_response',
      message: '服务器响应不符合当前前端协议。',
      requestId,
      details: parsed.error.issues,
    })
  }
  return parsed.data
}

export const api = {
  register(username: string, password: string): Promise<RegisterResponse> {
    return requestJson('/api/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    }, registerResponseSchema)
  },
  login(username: string, password: string, next: string | null): Promise<LoginResponse> {
    const body: Record<string, string> = { username, password }
    if (next) body.next = next
    return requestJson('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }, loginResponseSchema)
  },
  checkAuth(): Promise<CheckAuthResponse> {
    return requestJson('/api/check_auth', {}, checkAuthResponseSchema)
  },
  logout(): Promise<LogoutResponse> {
    return requestJson('/api/logout', { method: 'POST' }, logoutResponseSchema)
  },
  setting(topic: 'userAgreement' | 'userManual'): Promise<{ success: boolean; messages?: string; error?: string }> {
    return requestJson(`/api/setting?topic=${encodeURIComponent(topic)}`, {}, z.object({
      success: z.boolean(),
      messages: z.string().optional(),
      error: z.string().optional(),
    }).passthrough())
  },
  listSessions(): Promise<SessionResponse> {
    return requestJson('/api/sessions', {}, sessionsResponseSchema)
  },
  newChat(): Promise<NewChatResponse> {
    return requestJson('/api/new_chat', { method: 'POST' }, newChatResponseSchema)
  },
  loadSession(sessionId: string): Promise<LoadSessionResponse> {
    return requestJson(`/api/load_session?session=${encodeURIComponent(sessionId)}`, {}, loadSessionResponseSchema)
  },
  changeSession(sessionId: string, title: string): Promise<ChangeSessionResponse> {
    return requestJson('/api/change_session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, title }),
    }, changeSessionResponseSchema)
  },
  deleteSession(sessionId: string): Promise<DeleteSessionResponse> {
    return requestJson('/api/delete_session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId }),
    }, deleteSessionResponseSchema)
  },
  listFiles(): Promise<FileResponse[]> {
    return requestJson('/api/files', {}, filesResponseSchema)
  },
  uploadFile(file: File): Promise<UploadFileResponse> {
    const formData = new FormData()
    formData.append('file', file)
    return requestJson('/api/upload_file', { method: 'POST', body: formData }, uploadFileResponseSchema)
  },
  deleteFile(fileId: number): Promise<DeleteFileResponse> {
    return requestJson('/api/delete_file', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_id: fileId }),
    }, deleteFileResponseSchema)
  },
  createJob(message: string, sessionId: string, idempotencyKey: string, fileId: number | null, webSearchEnabled: boolean): Promise<JobActionResponse> {
    return requestJson('/api/agent/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify({
        message,
        session_id: sessionId,
        input_user_file_id: fileId,
        web_search_enabled: webSearchEnabled,
      }),
    }, jobActionResponseSchema)
  },
  activeJobs(sessionId: string | null = null): Promise<ActiveJobsResponse> {
    const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
    return requestJson(`/api/agent/jobs/active${query}`, {}, activeJobsResponseSchema)
  },
  resumeJob(jobId: string, questionId: string, answer: string, idempotencyKey: string): Promise<JobActionResponse> {
    return requestJson(`/api/agent/jobs/${encodeURIComponent(jobId)}/resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify({ question_id: questionId, answer }),
    }, jobActionResponseSchema)
  },
  cancelJob(jobId: string, idempotencyKey: string): Promise<JobActionResponse> {
    return requestJson(`/api/agent/jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey },
      body: '{}',
    }, jobActionResponseSchema)
  },
}
