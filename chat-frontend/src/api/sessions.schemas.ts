import { z } from 'zod'

const sessionInfoSchema = z.object({
  preview: z.string(),
  last_time: z.string(),
}).passthrough()

export const sessionsResponseSchema = z.array(
  z.tuple([z.string(), sessionInfoSchema]),
)

export const newChatResponseSchema = z.object({
  success: z.boolean(),
  new_session_id: z.string().optional(),
  error: z.string().optional(),
}).passthrough()

export const structuredMessageSchema = z.object({
  type: z.string(),
  summary: z.string().optional(),
  layout: z.string().optional(),
  data: z.unknown().optional(),
  references: z.array(z.object({
    title: z.string(),
    url: z.string(),
  }).passthrough()).optional(),
}).passthrough()

export const historyEventSchema = z.object({
  type: z.string(),
  event_id: z.number().int().nonnegative().optional(),
  step_id: z.string().optional(),
  node_name: z.string().optional(),
  title: z.string().optional(),
}).passthrough()

export const executionPhaseSchema = z.object({
  phase_sequence: z.number().int().nonnegative(),
  status: z.string(),
  elapsed_seconds: z.number().nonnegative(),
  last_event_id: z.number().int().nonnegative(),
  events: z.array(historyEventSchema),
  analysis_job_id: z.string().nullable().optional(),
  analysis_job_input_id: z.number().int().positive().nullable().optional(),
}).passthrough()

export const chatMessageSchema = z.object({
  sender: z.enum(['user', 'ai']),
  text: z.union([z.string(), structuredMessageSchema]),
  analysis_job_id: z.string().nullable().optional(),
  analysis_job_input_id: z.number().int().positive().nullable().optional(),
  file_attachment: z.object({
    filename: z.string().min(1),
  }).optional(),
  references: z.array(z.object({
    title: z.string(),
    url: z.string(),
  }).passthrough()).optional(),
  thinking_after: executionPhaseSchema.optional(),
}).passthrough()

export const loadSessionResponseSchema = z.object({
  success: z.boolean(),
  messages: z.array(chatMessageSchema).optional(),
  error: z.string().optional(),
}).passthrough()

export const changeSessionResponseSchema = z.object({
  success: z.boolean(),
  message: z.string().optional(),
  error: z.string().optional(),
}).passthrough()

export const deleteSessionResponseSchema = z.object({
  success: z.boolean(),
  message: z.string().optional(),
  checkpoint_cleanup: z.enum(['pending', 'succeeded']).optional(),
  error: z.string().optional(),
}).passthrough()

export type SessionsResponse = z.infer<typeof sessionsResponseSchema>
export type NewChatResponse = z.infer<typeof newChatResponseSchema>
export type LoadSessionResponse = z.infer<typeof loadSessionResponseSchema>
export type ChatMessageResponse = z.infer<typeof chatMessageSchema>
export type ExecutionPhaseResponse = z.infer<typeof executionPhaseSchema>
export type ChangeSessionResponse = z.infer<typeof changeSessionResponseSchema>
export type DeleteSessionResponse = z.infer<typeof deleteSessionResponseSchema>
