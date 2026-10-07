export interface GenerationState {
  generations: Record<string, number>
}

export function createGenerationState(): GenerationState {
  return { generations: {} }
}

export function nextGeneration(state: GenerationState, jobId: string): number {
  if (!jobId) return 0
  const generation = (state.generations[jobId] ?? 0) + 1
  state.generations = { ...state.generations, [jobId]: generation }
  return generation
}

export function isCurrentGeneration(state: GenerationState, jobId: string, generation: number): boolean {
  return Boolean(jobId) && state.generations[jobId] === generation
}

export function invalidateGeneration(state: GenerationState, jobId: string): number {
  return nextGeneration(state, jobId)
}
