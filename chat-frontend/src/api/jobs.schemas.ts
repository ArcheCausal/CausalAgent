import { z } from 'zod'

export const backendJobStatusSchema = z.enum([
  'queued', 'running', 'waiting_input', 'succeeded', 'failed', 'canceled',
])

const jobResponseBaseSchema = z.object({
  success: z.boolean(),
  job_id: z.string().optional(),
  status: backendJobStatusSchema.optional(),
  existing: z.boolean().optional(),
  error: z.string().optional(),
}).passthrough()

export const jobActionResponseSchema = jobResponseBaseSchema

export const activeJobSchema = z.object({
  job_id: z.string(),
  user_id: z.number().int().optional(),
  session_id: z.string(),
  status: backendJobStatusSchema,
  attempt_count: z.number().int().nonnegative().optional(),
  recovery_count: z.number().int().nonnegative().optional(),
  resume_count: z.number().int().nonnegative().optional(),
  max_attempts: z.number().int().positive().optional(),
  current_question_id: z.string().nullable().optional(),
  current_waiting_prompt: z.string().nullable().optional(),
  input_user_file_id: z.number().int().positive().nullable().optional(),
  input_filename: z.string().nullable().optional(),
  last_event_id: z.number().int().nonnegative().optional(),
}).passthrough()

export const activeJobsResponseSchema = z.object({
  success: z.boolean(),
  jobs: z.array(activeJobSchema).optional(),
  error: z.string().optional(),
}).passthrough()

export const cancelConflictResponseSchema = z.object({
  success: z.literal(false).optional(),
  code: z.string().optional(),
  status: backendJobStatusSchema.optional(),
  error: z.string().optional(),
}).passthrough()

export type JobActionResponse = z.infer<typeof jobActionResponseSchema>
export type ActiveJobResponse = z.infer<typeof activeJobSchema>
export type ActiveJobsResponse = z.infer<typeof activeJobsResponseSchema>
