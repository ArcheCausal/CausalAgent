import type { App } from 'vue'
import CaBadge from './components/CaBadge.vue'
import CaButton from './components/CaButton.vue'
import CaCard from './components/CaCard.vue'
import CaEmptyState from './components/CaEmptyState.vue'
import CaErrorState from './components/CaErrorState.vue'
import CaInput from './components/CaInput.vue'
import CaLoadingState from './components/CaLoadingState.vue'
import CaPageHeader from './components/CaPageHeader.vue'
import CaTabs from './components/CaTabs.vue'

export {
  CaBadge,
  CaButton,
  CaCard,
  CaEmptyState,
  CaErrorState,
  CaInput,
  CaLoadingState,
  CaPageHeader,
  CaTabs,
}

export type {
  CaBadgeTone,
  CaButtonSize,
  CaButtonVariant,
  CaCardPadding,
  CaCardVariant,
  CaEmptyStateAlign,
  CaLoadingStateVariant,
  CaPageHeaderSize,
  CaTabItem,
} from './types'

/* 需要全局注册时使用；按需引入组件不需要这个插件。样式不在这里引入，
   页面必须在入口单独引入 @causalagent/design-system/styles.css。 */
export const CaDesignSystem = {
  install(app: App) {
    app.component('CaBadge', CaBadge)
    app.component('CaButton', CaButton)
    app.component('CaCard', CaCard)
    app.component('CaEmptyState', CaEmptyState)
    app.component('CaErrorState', CaErrorState)
    app.component('CaInput', CaInput)
    app.component('CaLoadingState', CaLoadingState)
    app.component('CaPageHeader', CaPageHeader)
    app.component('CaTabs', CaTabs)
  },
}
