import { z } from 'zod'

const unitSchema = z.string().trim().min(1).max(128)
const columnSchema = z.string().trim().min(1).max(255)

export const effectRequestSchema = z.object({
  effect: z.literal('average_total_effect'),
  intervention: z.discriminatedUnion('type', [
    z.object({
      type: z.literal('fixed_contrast'),
      from: z.number().finite(),
      to: z.number().finite(),
      unit: unitSchema,
    }).strict(),
    z.object({
      type: z.literal('additive_shift'),
      delta: z.number().finite(),
      unit: unitSchema,
    }).strict(),
  ]),
  target_population: z.string().trim().min(1).max(2000).nullable().optional(),
}).strict()

const treatmentTransformationSchema = z.object({
  type: z.enum(['identity', 'affine', 'nonlinear']),
  source_column: columnSchema,
  output_column: columnSchema,
  source_unit: unitSchema,
  output_unit: unitSchema,
  scale: z.number().finite().nullable().optional(),
  offset: z.number().finite().nullable().optional(),
  description: z.string().trim().min(1).max(2000).nullable().optional(),
}).strict().refine((value) => {
  if (value.type === 'identity') return value.source_unit === value.output_unit && value.scale == null && value.offset == null
  if (value.type === 'affine') return value.scale != null && value.scale !== 0 && value.offset != null
  return value.description != null && value.scale == null && value.offset == null
})

const outcomeTransformationSchema = z.object({
  type: z.enum(['identity', 'affine', 'nonlinear']),
  description: z.string().trim().min(1).max(2000).optional(),
}).strict().refine((value) => value.type === 'identity' ? value.description === undefined : value.description !== undefined)

export const effectExecutionContextSchema = z.object({
  treatment_column: columnSchema,
  outcome_column: columnSchema,
  treatment_unit: unitSchema,
  treatment_transform: treatmentTransformationSchema.nullable(),
  outcome_transform: outcomeTransformationSchema.nullable(),
}).strict().refine((value) => value.treatment_column !== value.outcome_column
  && (value.treatment_transform === null
    || value.treatment_transform.output_column === value.treatment_column
      && value.treatment_transform.source_unit === value.treatment_unit))

export type EffectRequest = z.infer<typeof effectRequestSchema>
export type EffectExecutionContext = z.infer<typeof effectExecutionContextSchema>
