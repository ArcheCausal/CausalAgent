import { z } from 'zod'

export const registerResponseSchema = z.object({
  success: z.boolean(),
  error: z.string().optional(),
}).passthrough()

export const loginResponseSchema = z.object({
  success: z.boolean(),
  username: z.string().optional(),
  role: z.string().optional(),
  permissions: z.array(z.string()).optional(),
  csrf_token: z.string().optional(),
  redirect_to: z.string().optional(),
  warning_code: z.string().optional(),
  error: z.string().optional(),
}).passthrough()

export const checkAuthResponseSchema = z.discriminatedUnion('isLoggedIn', [
  z.object({
    isLoggedIn: z.literal(true),
    username: z.string(),
    role: z.string().optional(),
    permissions: z.array(z.string()).optional(),
    csrf_token: z.string().optional(),
  }).passthrough(),
  z.object({
    isLoggedIn: z.literal(false),
  }).passthrough(),
])

export const logoutResponseSchema = z.object({
  success: z.boolean(),
  error: z.string().optional(),
}).passthrough()

export type RegisterResponse = z.infer<typeof registerResponseSchema>
export type LoginResponse = z.infer<typeof loginResponseSchema>
export type CheckAuthResponse = z.infer<typeof checkAuthResponseSchema>
export type LogoutResponse = z.infer<typeof logoutResponseSchema>
