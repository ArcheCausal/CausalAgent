<script setup lang="ts">
import { computed, defineAsyncComponent } from 'vue'
import { SITE_PAGE_META, readSiteRoute } from './routes'
import type { SitePageName } from './routes'

/* 官网每个地址都是整页加载，同一个文档只会解析出一个页面。
   页面组件异步引入后，Vite 为每页单独产出样式与脚本，
   原型各页同名类（.wrap、.notice、body 等）不会互相覆盖。 */
const PAGE_COMPONENTS: Record<SitePageName, ReturnType<typeof defineAsyncComponent>> = {
  home: defineAsyncComponent(() => import('./pages/HomePage.vue')),
  product: defineAsyncComponent(() => import('./pages/ProductPage.vue')),
  pricing: defineAsyncComponent(() => import('./pages/PricingPage.vue')),
  about: defineAsyncComponent(() => import('./pages/AboutPage.vue')),
  docs: defineAsyncComponent(() => import('./pages/DocsPage.vue')),
  changelog: defineAsyncComponent(() => import('./pages/ChangelogPage.vue')),
  'sign-in': defineAsyncComponent(() => import('./pages/SignInPage.vue')),
  'sign-up': defineAsyncComponent(() => import('./pages/SignUpPage.vue')),
  'not-found': defineAsyncComponent(() => import('./pages/NotFoundPage.vue')),
}

const route = readSiteRoute(globalThis.location.pathname)
const page = computed(() => PAGE_COMPONENTS[route.name])

/* 标题与说明按当前地址取自各页原型 head，整页加载时写入文档。 */
const meta = SITE_PAGE_META[route.name]
globalThis.document.title = meta.title
globalThis.document
  .querySelector('meta[name="description"]')
  ?.setAttribute('content', meta.description)
</script>

<template>
  <component :is="page" />
</template>
