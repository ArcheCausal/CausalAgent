import { computed, ref } from 'vue'
import type { Locale } from '../types/domain'
import { localeMessages } from './messages'

const locale = ref<Locale>(typeof localStorage !== 'undefined' && localStorage.getItem('language') === 'en' ? 'en' : 'zh')

export function useLocale() {
  const text = computed(() => localeMessages(locale.value))
  const toggle = () => {
    locale.value = locale.value === 'zh' ? 'en' : 'zh'
    localStorage.setItem('language', locale.value)
  }
  return { locale, text, toggle }
}
