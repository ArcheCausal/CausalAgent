import { z } from 'zod'

export const fileSchema = z.object({
  id: z.number().int().positive(),
  user_file_id: z.number().int().positive(),
  filename: z.string(),
  mime_type: z.string(),
  file_size: z.number().int().nonnegative(),
  uploaded_at: z.string().nullable(),
  last_accessed_at: z.string().nullable(),
  access_count: z.number().int().nonnegative(),
}).passthrough()

export const filesResponseSchema = z.array(fileSchema)

export const uploadFileResponseSchema = z.object({
  success: z.boolean(),
  message: z.string().optional(),
  file: fileSchema.optional(),
  user_file_id: z.number().int().positive().optional(),
  file_hash: z.string().optional(),
  error: z.string().optional(),
}).passthrough()

export const deleteFileResponseSchema = z.object({
  success: z.boolean(),
  message: z.string().optional(),
  user_file_id: z.number().int().positive().optional(),
  blob_deleted: z.boolean().optional(),
  error: z.string().optional(),
}).passthrough()

export type FileResponse = z.infer<typeof fileSchema>
export type UploadFileResponse = z.infer<typeof uploadFileResponseSchema>
export type DeleteFileResponse = z.infer<typeof deleteFileResponseSchema>
