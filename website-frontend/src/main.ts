import { createApp } from 'vue'
import App from './App.vue'
/* 品牌字体与基础规则来自共享设计系统；原型各页自带的样式未放进层，
   优先级高于共享层，因此引入共享样式不会改变原型页面的排版。 */
import '@causalagent/design-system/styles/index.css'
import './styles/base.css'

createApp(App).mount('#app')
