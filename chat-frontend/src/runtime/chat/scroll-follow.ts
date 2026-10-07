import { inject, type InjectionKey } from 'vue'
import { CHAT_AUTO_SCROLL_THRESHOLD_PX, isNearBottom } from './presentation'

export interface ChatScrollFollow {
  attach(element: HTMLElement | null): void
  detach(): void
  handleScroll(): void
  keepLatest(): void
  resetToLatest(): void
}

/**
 * 聊天区滚动跟随控制器。它持有滚动容器这个 DOM 节点，属于 runtime controller，
 * 不进入 Pinia；用户主动向上滚动后停止自动跟随，回到底部后恢复。
 */
export function createChatScrollFollow(threshold: number = CHAT_AUTO_SCROLL_THRESHOLD_PX): ChatScrollFollow {
  let element: HTMLElement | null = null
  let following = true

  function keepLatest(): void {
    if (!element || !following) return
    element.scrollTop = element.scrollHeight
    following = true
  }

  function handleScroll(): void {
    if (!element) return
    following = isNearBottom(element.scrollTop, element.clientHeight, element.scrollHeight, threshold)
  }

  return {
    attach(next: HTMLElement | null): void {
      element = next
      following = true
    },
    detach(): void {
      element = null
    },
    handleScroll,
    keepLatest,
    resetToLatest(): void {
      following = true
      keepLatest()
    },
  }
}

export const chatScrollFollowKey: InjectionKey<ChatScrollFollow> = Symbol('chat-scroll-follow')

const detachedScrollFollow: ChatScrollFollow = {
  attach: () => undefined,
  detach: () => undefined,
  handleScroll: () => undefined,
  keepLatest: () => undefined,
  resetToLatest: () => undefined,
}

/** 在聊天区内取得滚动跟随控制器；脱离聊天区时退化为空实现。 */
export function useChatScrollFollow(): ChatScrollFollow {
  return inject(chatScrollFollowKey, detachedScrollFollow)
}
