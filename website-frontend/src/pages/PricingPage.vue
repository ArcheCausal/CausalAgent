<script setup lang="ts">
/* 本页结构与交互逐字取自官网原型 pricing.html 的内联内容。
   每个公开地址都是整页加载，同一个文档只会挂载一个页面，
   因此页面样式按自身作用范围生效，不再额外做作用域限定。 */
import { onMounted } from 'vue'
import { initPricing } from '../scripts/pricing.js'
import '../styles/pages/pricing.css'

onMounted(() => {
  initPricing()
})
</script>

<template>
  <a class="skip" href="#content">跳到主要内容</a>
    <div class="progress" id="progress" aria-hidden="true"></div>

    <div class="notice" id="notice">
      <p>0.2.0 已发布：登录与注册统一到同一个入口。</p>
      <a class="pill" href="/changelog">查看更新日志</a>
      <button class="notice-close" id="notice-close" type="button" aria-label="关闭这条通告">
        <svg viewBox="0 0 12 12" fill="none" stroke="#171717" stroke-width="1.25" stroke-linecap="round" aria-hidden="true"><path d="M1 1l10 10M11 1L1 11"></path></svg>
      </button>
    </div>

    <header class="topnav" id="topnav">
      <div class="wrap topnav-inner">
        <a class="brand" href="/">
          <img class="brand-mark" :src="'/site-assets/brand/causalagent-mark.svg'" alt="" width="28" height="28" />
          <span class="wordmark">CausalAgent</span>
        </a>
        <nav class="nav-links" aria-label="主导航">
          <a href="/">首页</a>
          <a href="/product">产品</a>
          <a href="/pricing" aria-current="page">价格</a>
          <a href="/docs">文档</a>
          <a href="/changelog">更新日志</a>
          <a href="/about">关于</a>
        </nav>
        <div class="nav-actions">
          <a class="ghost" href="/auth/sign-in">登录</a>
          <a class="pill" href="/auth/sign-up">免费注册</a>
        </div>
      </div>
    </header>

    <main id="content">
      <section class="price-hero wrap" id="top" aria-labelledby="price-title">
        <p class="eyebrow">价格 / PRICING</p>
        <h1 class="price-title" id="price-title">按现在的需求开始，按未来的规模继续</h1>
        <p class="price-lede">从FREE开始探索整个世界</p>

      </section>

      <section class="wrap" aria-labelledby="plans-title">
        <h2 class="sr-only" id="plans-title">方案</h2>
        <div class="pricing-grid">
          <article class="price-card" aria-labelledby="free-title">
            <div class="price-card-head">
              <div>
                <p class="plan-index">PLAN 01</p>
                <h2 class="plan-name" id="free-title">FREE</h2>
              </div>
              <span class="tag">现在可用</span>
            </div>
            <div class="price-amount od-nowrap"><span class="price-value">¥0</span><span class="price-unit">起</span></div>
            <p class="price-description">先用一次真实的分析任务试试。注册后即可上传数据、运行任务并导出报告。</p>
            <ul class="feature-list">
              <li><svg viewBox="0 0 14 14" aria-hidden="true"><use href="#ic-check"></use></svg><span>注册后进入工作区</span></li>
              <li><svg viewBox="0 0 14 14" aria-hidden="true"><use href="#ic-check"></use></svg><span>上传数据并运行分析任务</span></li>
              <li><svg viewBox="0 0 14 14" aria-hidden="true"><use href="#ic-check"></use></svg><span>导出分析报告</span></li>
            </ul>
            <div class="price-actions">
              <a class="pill pill--lg" href="/auth/sign-up">免费注册</a>
            </div>
          </article>

          <article class="price-card price-card--future" aria-labelledby="future-title">
            <div class="price-card-head">
              <div>
                <p class="plan-index">PLAN 02</p>
                <h2 class="plan-name" id="future-title">FUTURE</h2>
              </div>
              <span class="tag">即将开放</span>
            </div>
            <div class="price-amount od-nowrap"><span class="price-value">探索未来</span></div>
            <p class="price-description">后续方案的金额与具体权益尚未公布，开放前会在更新日志中发布正式信息。</p>
            <ul class="feature-list">
              <li class="feature-muted"><svg viewBox="0 0 14 14" aria-hidden="true"><use href="#ic-dash"></use></svg><span>价格将在开放前公布</span></li>
              <li class="feature-muted"><svg viewBox="0 0 14 14" aria-hidden="true"><use href="#ic-dash"></use></svg><span>权益将在开放前公布</span></li>
              <li class="feature-muted"><svg viewBox="0 0 14 14" aria-hidden="true"><use href="#ic-dash"></use></svg><span>当前不提供购买入口</span></li>
            </ul>
            <div class="price-actions">
              <a class="ghost ghost--lg" href="/changelog">查看更新日志</a>
            </div>
          </article>
        </div>

        <div class="price-note" id="price-note">
          <h2>价格说明</h2>
          <p>当前页面只展示 FREE 与 FUTURE 两个方案。
  FUTURE 的金额与权益尚未公布。正式信息会在更新日志中同步。</p>
        </div>
      </section>

      <section class="faq wrap" aria-labelledby="faq-title">
        <p class="eyebrow">常见问题</p>
        <h2 class="section-title" id="faq-title">如果您有疑问？</h2>
        <div class="faq-list">
          <details>
            <summary>FREE 现在可以做什么？</summary>
            <p>FREE计划包含我们产品的所有功能</p>
          </details>
          <details>
            <summary>FUTURE 什么时候开放？</summary>
            <p>目前还没有公布开放时间。价格、权益与开放时间会在更新日志中发布。</p>
          </details>
          <details>
            <summary>两个方案会一起显示吗？</summary>
            <p>会。这个页面当前只保留 FREE 与 FUTURE 两个方案，便于直接比较当前可用范围与后续计划。</p>
          </details>
        </div>
      </section>

      <section class="closing" aria-labelledby="closing-title">
        <div class="wrap">
          <h2 id="closing-title">先用一次真实的分析任务试试</h2>
          <p class="closing-lede">注册后即可上传数据、运行任务并导出报告。</p>
          <div class="closing-actions">
            <a class="pill pill--lg" href="/auth/sign-up">免费注册</a>
            <a class="ghost ghost--lg" href="/auth/sign-in">已有账号，直接登录</a>
          </div>
        </div>
      </section>
    </main>

    <footer class="foot">
      <div class="wrap">
        <h2 class="sr-only">页脚</h2>
        <div class="foot-grid">
          <p class="foot-brand">面向因果分析场景的智能体工作台。数据文件、内部知识库与公开资料在同一条链路上走完，过程与结论一起留下。</p>
          <div class="foot-col">
            <h3 class="foot-col-title">产品</h3>
            <a href="/product">产品能力</a>
            <a href="/dashboard">进入工作区</a>
            <a href="/changelog">更新日志</a>
          </div>
          <div class="foot-col">
            <h3 class="foot-col-title">资源</h3>
            <a href="/docs">使用文档</a>
            <a href="/about">关于我们</a>
            <a href="/#experts">专家智囊</a>
          </div>
          <div class="foot-col">
            <h3 class="foot-col-title">账号</h3>
            <a href="/auth/sign-in">登录</a>
            <a href="/auth/sign-up">注册</a>
          </div>
        </div>
        <div class="foot-base">
          <span>© 2026 CausalAgent</span>

        </div>
      </div>
    </footer>

    <svg width="0" height="0" aria-hidden="true" focusable="false" class="icon-defs"><defs><symbol id="ic-check" viewBox="0 0 14 14"><path d="M2.5 7.5 L5.5 10.5 L11.5 3.5" fill="none" stroke="#171717" stroke-width="1.25" stroke-linecap="round" stroke-linejoin="round"></path></symbol><symbol id="ic-dash" viewBox="0 0 14 14"><path d="M2.5 7 h9" stroke="#c7c7c7" stroke-width="1.25" stroke-linecap="round"></path></symbol></defs></svg>
    <button class="to-top" id="to-top" type="button" aria-label="回到页面顶部"><svg viewBox="0 0 14 14" fill="none" stroke="#171717" stroke-width="1.25" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 12V2M3 6l4-4 4 4"></path></svg></button>
</template>
