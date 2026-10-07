<script setup lang="ts">
/* 未登记的公开地址使用本站最简页面；结构与视觉沿用原型的纸面与墨色规则。 */
</script>

<template>
  <a class="skip-link" href="#main">跳到主要内容</a>
  <main class="not-found" id="main">
    <p class="not-found__kicker">404</p>
    <h1>页面不存在</h1>
    <p class="not-found__lede">该地址没有对应的公开页面，可能是链接过期或输入有误。</p>
    <div class="not-found__actions">
      <a class="not-found__pill" href="/">返回首页</a>
      <a class="not-found__ghost" href="/docs">查看文档</a>
    </div>
  </main>
</template>

<style scoped>
.skip-link {
  position: absolute;
  left: -9999px;
  top: 16px;
  z-index: 20;
  border-radius: var(--ca-radius-full);
  background: var(--ca-ink);
  color: var(--ca-paper);
  padding: 8px 16px;
  text-decoration: none;
}

.skip-link:focus {
  left: 16px;
}

.not-found {
  display: grid;
  gap: var(--ca-space-16);
  align-content: center;
  width: min(var(--ca-page-max-width), calc(100% - 48px));
  min-height: 100vh;
  margin: 0 auto;
  padding: var(--ca-space-80) 0;
}

.not-found__kicker {
  color: var(--ca-text-muted);
  font-size: var(--ca-text-caption);
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.not-found h1 {
  color: var(--ca-text-default);
  font-size: clamp(36px, 6vw, var(--ca-text-heading-lg));
  line-height: var(--ca-leading-heading-lg);
  margin: 0;
}

.not-found__lede {
  max-width: var(--ca-measure-body);
  color: var(--ca-text-muted);
  font-size: var(--ca-text-lede);
  line-height: var(--ca-leading-lede);
  margin: 0;
}

.not-found__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ca-space-16);
  margin-top: var(--ca-space-16);
}

.not-found__pill,
.not-found__ghost {
  display: inline-flex;
  align-items: center;
  min-height: 44px;
  padding: var(--ca-space-8) var(--ca-space-24);
  border-radius: var(--ca-radius-full);
  font-size: var(--ca-text-body-sm);
  line-height: var(--ca-leading-body-sm);
  text-decoration: none;
  transition: background var(--ca-motion-fast) var(--ca-ease-out), color var(--ca-motion-fast) var(--ca-ease-out);
}

.not-found__pill {
  background: var(--ca-ink);
  border: var(--ca-border-width) solid var(--ca-ink);
  color: var(--ca-paper);
}

.not-found__ghost {
  background: transparent;
  border: var(--ca-border-width) solid var(--ca-border-default);
  color: var(--ca-text-default);
}

@media (hover: hover) {
  .not-found__pill:hover {
    background: var(--ca-ink-hover);
    border-color: var(--ca-ink-hover);
  }

  .not-found__ghost:hover {
    background: var(--ca-surface-muted);
  }
}
</style>
