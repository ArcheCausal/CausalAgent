import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import '../../packages/design-system/src/styles/fonts.css'
import '../../packages/design-system/src/styles/tokens/typography.css'
import './styles/reset.css'
import './styles/tokens.css'
import './styles/motion.css'
import './styles/main.css'

createApp(App).use(createPinia()).mount('#app')
