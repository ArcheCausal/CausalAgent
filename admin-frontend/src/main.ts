import { createApp } from 'vue'
import {
  ElAlert,
  ElButton,
  ElCollapse,
  ElCollapseItem,
  ElDescriptions,
  ElDescriptionsItem,
  ElDialog,
  ElDrawer,
  ElForm,
  ElFormItem,
  ElInput,
  ElInputNumber,
  ElLoading,
  ElOption,
  ElSelect,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTimeline,
  ElTimelineItem,
  ElTooltip,
} from 'element-plus'
import '@causalagent/design-system/styles.css'
import 'element-plus/dist/index.css'
import App from './App.vue'
import { router } from './router'
import './element-plus.css'
import './styles.css'

const app = createApp(App)
for (const plugin of [
  ElAlert,
  ElButton,
  ElCollapse,
  ElCollapseItem,
  ElDescriptions,
  ElDescriptionsItem,
  ElDialog,
  ElDrawer,
  ElForm,
  ElFormItem,
  ElInput,
  ElInputNumber,
  ElLoading,
  ElOption,
  ElSelect,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTimeline,
  ElTimelineItem,
  ElTooltip,
]) {
  app.use(plugin)
}
app.use(router).mount('#app')
