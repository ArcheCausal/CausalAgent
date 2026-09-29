<script setup lang="ts">
/* 本页结构与交互逐字取自官网原型 changelog.html 的内联内容。
   每个公开地址都是整页加载，同一个文档只会挂载一个页面，
   因此页面样式按自身作用范围生效，不再额外做作用域限定。 */
import { onMounted } from 'vue'
import { installGsap } from '../scripts/vendor-gsap.js'
import { initChangelog } from '../scripts/changelog.js'
import '../styles/pages/changelog.css'

onMounted(() => {
  installGsap()
  initChangelog()
})
</script>

<template>
  <a class="skip" href="#main">跳到主要内容</a>

  <div class="progress" id="progress" aria-hidden="true"></div>

  <div class="notice" id="notice">
    <p>0.2.0 已发布：登录与注册统一到同一个入口，账号改为角色与权限分离的模型。</p>
    <a class="pill" href="#v0-2-0">查看本次更新</a>
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
        <a href="/pricing">价格</a>
        <a href="/docs">文档</a>
        <a href="/changelog" aria-current="page">更新日志</a>
        <a href="/about">关于</a>
      </nav>
      <div class="nav-actions">
        <a class="ghost" href="/auth/sign-in">登录</a>
        <a class="pill" href="/auth/sign-up">免费注册</a>
      </div>
    </div>
  </header>

  <main id="main">

    <section class="hero" id="top" aria-labelledby="page-title">
      <div class="wrap" data-reveal="">
        <p class="eyebrow">CausalAgent · 版本记录</p>
        <h1 class="hero-title" id="page-title">更新日志</h1>
        <p class="hero-lede">持续更新，不断进步</p>

        <dl class="ver-facts">
          <div class="ver-fact">
            <dt>当前版本</dt>
            <dd class="od-nowrap">0.2.0</dd>
          </div>
          <div class="ver-fact">
            <dt>发布日期</dt>
            <dd class="od-nowrap">2026-09-19</dd>
          </div>
          <div class="ver-fact">
            <dt>记录范围</dt>
            <dd>CausalAgent项目更新</dd>
          </div>
        </dl>

        <nav class="ver-index" aria-label="版本索引">
          <p class="ver-index-label">版本索引</p>
          <ul class="ver-index-list">
            <li>
              <a class="ver-chip" href="#v0-2-0" aria-current="true">
                <span class="ver-chip-v od-nowrap">0.2.0</span>
                <span class="ver-chip-s">当前版本</span>
              </a>
            </li>
          </ul>
        </nav>
      </div>
    </section>

    <section class="section" id="releases" aria-labelledby="releases-title">
      <div class="wrap">
        <div class="section-head" data-reveal="">
          <h2 class="section-title" id="releases-title">发布记录</h2>
          <p class="section-lede">按版本从新到旧排列，每个版本先写影响使用方式的重点变化，再写具体细节。</p>
        </div>

        <ol class="timeline">
          <li class="timeline-item">
            <article class="release" id="v0-2-0" aria-labelledby="v0-2-0-title" data-reveal="">
              <header class="release-head">
                <h3 class="release-version" id="v0-2-0-title"><span class="od-nowrap">0.2.0</span></h3>
                <p class="release-status">当前版本</p>
                <time class="release-date" datetime="2026-09-19">2026-09-19</time>
              </header>
              <div class="release-body">
                <div class="release-group">
                  <h4 class="release-group-title">重点变化</h4>
                  <ul class="release-list release-list--key">
                    <li>官网、普通用户工作区、RAG 评测台与管理员系统按路径分开，各自使用独立构建产物</li>
                    <li>登录与注册统一到 /auth/sign-in 和 /auth/sign-up</li>
                    <li>账号授权改为角色与权限分离的模型</li>
                  </ul>
                </div>
                <div class="release-group">
                  <h4 class="release-group-title">具体细节</h4>
                  <ul class="release-list">
                    <li>官网提供产品介绍、公开文档与更新日志，未登录访客也可以浏览</li>
                    <li>普通用户工作区固定在 /dashboard，未登录访问跳转登录页面</li>
                    <li>RAG 评测台移动到 /rag-eval，仅管理员可访问，页面与接口使用同一权限边界</li>
                    <li>登录后的回跳地址只接受站内已登记路径，外部地址会被忽略</li>
                    <li>账号角色或状态变化后，旧的登录会话会在下一次请求时失效</li>
                  </ul>
                </div>
              </div>
            </article>
          </li>
        </ol>
      </div>
    </section>

    <section class="section" id="archive" aria-labelledby="archive-title">
      <div class="wrap">
        <div class="section-head">
          <h2 class="section-title" id="archive-title">更早的版本</h2>
          <p class="section-lede">如有更早的正式版本记录，后续将按发布时间顺序补充。</p>
        </div>
        <div class="empty-note" data-reveal="">
          <p>当前官网公开版本为 0.2.0，暂无更早版本记录。本页面仅发布影响用户使用方式的正式变更，内部开发日志不在官网展示。</p>
          <p class="empty-note-actions">
            <a class="ghost" href="/product">看产品能力与版本边界</a>
          </p>
        </div>
      </div>
    </section>

    <section class="closing">
      <div class="wrap" data-reveal="">
        <h2>先用一次真实的分析任务试试</h2>
        <p class="closing-lede">注册后即可上传数据、运行任务并导出报告；更新日志记录的是这些能力每次变化的公开部分。</p>
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
        <p class="foot-brand">面向因果分析场景的智能体工作台。如有错误，即刻提出</p>
        <div class="foot-col">
          <h3 class="foot-col-title">官网</h3>
          <a href="/">首页</a>
          <a href="/product">产品</a>
          <a href="/pricing">价格</a>
          <a href="/docs">文档</a>
          <a href="/changelog">更新日志</a>
          <a href="/about">关于</a>
        </div>
        <div class="foot-col">
          <h3 class="foot-col-title">产品</h3>
          <a href="/product">产品矩阵</a>
          <a href="/dashboard">进入工作区</a>
        </div>
        <div class="foot-col">
          <h3 class="foot-col-title">账号</h3>
          <a href="/auth/sign-in">登录</a>
          <a href="/auth/sign-up">注册</a>
        </div>
      </div>
      <div class="foot-base">
        <span>© 2026 CausalAgent</span>
        <span>CausalAgent团队具有一切解释权</span>
      </div>
    </div>
  </footer>

  <button class="to-top" id="to-top" type="button" aria-label="回到页面顶部">
    <svg viewBox="0 0 14 14" fill="none" stroke="#171717" stroke-width="1.25" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 12V2M3 6l4-4 4 4"></path></svg>
  </button>
</template>
