export interface EventCursor {
  transportCursor: number
  resumeEventId: number
  renderedEventId: number
  observedLastEventId: number | null
}

export interface CursorAcceptance {
  accepted: boolean
  duplicate: boolean
  cursor: EventCursor
}

export function createEventCursor(initial = 0): EventCursor {
  const value = Number.isSafeInteger(initial) && initial >= 0 ? initial : 0
  return {
    transportCursor: value,
    resumeEventId: value,
    renderedEventId: value,
    observedLastEventId: null,
  }
}

export function acceptEventId(cursor: EventCursor, eventId: number | null): CursorAcceptance {
  if (eventId === null) {
    return { accepted: true, duplicate: false, cursor: { ...cursor } }
  }
  if (!Number.isSafeInteger(eventId) || eventId < 1) {
    return { accepted: false, duplicate: false, cursor: { ...cursor } }
  }
  if (eventId <= cursor.transportCursor) {
    return { accepted: false, duplicate: true, cursor: { ...cursor } }
  }
  return {
    accepted: true,
    duplicate: false,
    cursor: {
      ...cursor,
      transportCursor: eventId,
      resumeEventId: eventId,
    },
  }
}

export function observeActiveEventId(cursor: EventCursor, eventId: number | null | undefined): EventCursor {
  if (!Number.isSafeInteger(eventId) || (eventId as number) < 0) return { ...cursor }
  return { ...cursor, observedLastEventId: eventId as number }
}

export function markRendered(cursor: EventCursor, eventId: number | null): EventCursor {
  if (eventId === null || !Number.isSafeInteger(eventId) || eventId < 1) return { ...cursor }
  return { ...cursor, renderedEventId: Math.max(cursor.renderedEventId, eventId) }
}
