<script setup lang="ts">
/* 本页结构与交互逐字取自官网原型 about.html 的内联内容。
   每个公开地址都是整页加载，同一个文档只会挂载一个页面，
   因此页面样式按自身作用范围生效，不再额外做作用域限定。 */
import { onMounted } from 'vue'
import { initAbout } from '../scripts/about.js'
import '../styles/pages/about.css'

onMounted(() => {
  initAbout()
})
</script>

<template>
  <a class="skip" href="#main">跳到主要内容</a>

  <div class="progress" id="progress" aria-hidden="true"></div>

  <div class="notice" id="notice">
    <p>CDFM 已公开：通用因果发现基础模型，论文编号 arXiv 2607.11508，权重放在 HuggingFace。</p>
    <a class="pill" href="https://github.com/DMIRLAB-Group/CDFM" target="_blank" rel="noopener">查看仓库</a>
    <button class="notice-close" id="notice-close" type="button" aria-label="关闭这条通告"></button>
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
        <a href="/dashboard">应用原型</a>
        <a href="/#experts">专家智囊</a>
        <a href="/about" aria-current="page">关于</a>
      </nav>
      <div class="nav-actions">
        <a class="pill" href="/dashboard">打开应用原型</a>
      </div>
    </div>
  </header>

  <main id="main">
    <section class="about-hero dark-surface" id="top" aria-labelledby="hero-title">
      <div class="starfield" aria-hidden="true">
        <canvas class="star-layer star-base" id="st-base"></canvas>
        <canvas class="star-layer star-glow star-glow--far" id="st-glow-1"></canvas>
        <canvas class="star-layer star-glow star-glow--mid" id="st-glow-2"></canvas>
        <canvas class="star-layer star-glow star-glow--near" id="st-glow-3"></canvas>
        <span class="meteor meteor--far"></span>
        <span class="meteor meteor--near"></span>
      </div>
      <div class="wrap about-hero-grid">
        <div class="hero-copy">
          <p class="hero-kicker">CausalAgent · 关于</p>
          <h1 class="hero-title" id="hero-title">让因果，成为理解世界的起点。</h1>
          <p class="hero-lede">我们相信，真正有价值的分析，不只回答发生了什么，还要继续追问为什么发生。CausalAgent 试着让这种追问更清晰，更可复核，也更容易继续推进。</p>
          <div class="hero-actions">
            <a class="ghost" href="#contact">联系 CausalAgent</a>
          </div>
        </div>

        <div class="diagram-wrap">
          <figure class="diagram-frame">
            <div class="diagram-meta">
              <span>一个简单的判断路径</span>
              <span aria-hidden="true">CausalAgent</span>
            </div>
            <svg class="causal-diagram" viewBox="0 0 600 360" role="img" aria-labelledby="diagram-title diagram-desc">
              <title id="diagram-title">从问题、材料和关系走向判断的因果关系示意图</title>
              <desc id="diagram-desc">问题和材料经过关系分析，形成可以继续复核的判断。</desc>
              <defs>
                <marker id="arrow" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                  <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor"></path>
                </marker>
              </defs>
              <path class="guide" d="M72 54H528M72 180H528M72 306H528"></path>
              <path class="link" d="M156 96C212 96 218 152 276 152"></path>
              <path class="link" d="M156 264C212 264 218 208 276 208"></path>
              <path class="link" d="M324 180H444"></path>
              <path class="link" d="M276 152C330 152 344 92 444 92"></path>
              <path class="link" d="M276 208C330 208 344 268 444 268"></path>
              <circle class="node" cx="120" cy="96" r="36"></circle>
              <circle class="node" cx="120" cy="264" r="36"></circle>
              <circle class="node node--core" cx="300" cy="180" r="40"></circle>
              <circle class="node" cx="480" cy="92" r="36"></circle>
              <circle class="node" cx="480" cy="268" r="36"></circle>
              <text x="120" y="96">问题</text>
              <text x="120" y="264">材料</text>
              <text x="300" y="180">关系</text>
              <text x="480" y="92">证据</text>
              <text x="480" y="268">判断</text>
            </svg>
            <figcaption class="diagram-caption">每一个结论，都应该能回到它所依赖的关系与证据。</figcaption>
          </figure>
        </div>
      </div>
    </section>

    <section class="section" id="idea" aria-labelledby="idea-title">
      <div class="wrap">
        <div class="section-heading">
          <p class="section-kicker">我们在做什么</p>
          <h2 class="section-title" id="idea-title">把复杂的问题，放回它真实发生的关系里。</h2>
          <p class="section-lede">从研究材料到分析结论，保留问题、证据、方法与判断之间的联系，让每一步都能被理解，也能被再次检验。</p>
        </div>

        <div class="principle-list" aria-label="CausalAgent 的理念">
          <article class="principle">
            <span class="principle-index">01</span>
            <h3 class="principle-title">追问原因</h3>
            <p class="principle-copy">不止描述结果，也寻找结果发生的条件与路径。</p>
          </article>
          <article class="principle">
            <span class="principle-index">02</span>
            <h3 class="principle-title">连接证据</h3>
            <p class="principle-copy">让材料、方法与判断互相指向，减少脱离依据的结论。</p>
          </article>
          <article class="principle">
            <span class="principle-index">03</span>
            <h3 class="principle-title">保持开放</h3>
            <p class="principle-copy">让新的问题与证据可以继续进入分析，推动理解向前。</p>
          </article>
        </div>
      </div>
    </section>

    <section class="contact dark-surface" id="contact" aria-labelledby="contact-title">
      <div class="wrap contact-grid">
        <div>
          <p class="contact-kicker">保持联系</p>
          <h2 class="contact-title" id="contact-title">如果你探索世界，欢迎写信给我们。</h2>
        </div>
        <div>
          <p class="contact-copy">产品反馈、研究合作，或任何关于 CausalAgent 的想法，都可以通过邮件联系。</p>
          <a class="contact-email" href="mailto:heyflyingpig@gmail.com">heyflyingpig@gmail.com</a>
        </div>
      </div>
    </section>
  </main>

  <footer class="foot">
    <div class="wrap">
      <h2 class="sr-only">页脚</h2>
      <div class="foot-grid">
        <p class="foot-brand">所有产品均以实际产品为准，一切解释权归CausalAgent所有</p>
        <div class="foot-col">
          <h3 class="foot-col-title">产品</h3>
          <a href="/dashboard">应用原型</a>
          <a href="/product#cdfm">CDFM 算法</a>
          <a href="/product#library">DMIR 算法库</a>
          <a href="/product">产品总览</a>
        </div>
        <div class="foot-col">
          <h3 class="foot-col-title">源码</h3>
          <a href="https://github.com/DMIRLAB-Group" target="_blank" rel="noopener">DMIRLAB-Group<span class="sr-only">（在新窗口打开）</span></a>
          <a href="https://github.com/DMIRLAB-Group/CDFM" target="_blank" rel="noopener">CDFM<span class="sr-only">（在新窗口打开）</span></a>
          <a href="https://github.com/DMIRLAB-Group/CausalAgent" target="_blank" rel="noopener">CausalAgent<span class="sr-only">（在新窗口打开）</span></a>
        </div>
        <div class="foot-col">
          <h3 class="foot-col-title">页面</h3>
          <a href="/">官网首页</a>
          <a href="/#experts">专家智囊</a>
          <a href="/about">关于我们</a>
          <a href="#top">回到页面顶部</a>
        </div>
      </div>
      <div class="foot-base">
        <span>© 2026 DMIRLAB-Group</span>
        <span>CausalAgent</span>
      </div>
    </div>
  </footer>

  <button class="to-top" id="to-top" type="button" aria-label="回到页面顶部">回到顶部</button>
</template>
