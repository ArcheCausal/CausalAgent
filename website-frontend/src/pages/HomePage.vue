<script setup lang="ts">
/* 本页结构与交互逐字取自官网原型 index.html 的内联内容。
   每个公开地址都是整页加载，同一个文档只会挂载一个页面，
   因此页面样式按自身作用范围生效，不再额外做作用域限定。 */
import { onMounted } from 'vue'
import { installGsap } from '../scripts/vendor-gsap.js'
import { initHome } from '../scripts/home.js'
import '../styles/pages/home.css'

onMounted(() => {
  installGsap()
  initHome()
})
</script>

<template>
  <a class="skip" href="#main">跳到主要内容</a>
    <svg width="0" height="0" aria-hidden="true" focusable="false" class="icon-defs"><defs><symbol id="ic-check" viewBox="0 0 14 14"><path d="M2.5 7.5 L5.5 10.5 L11.5 3.5" fill="none" stroke="#171717" stroke-width="1.25" stroke-linecap="round" stroke-linejoin="round"></path></symbol><symbol id="ic-dash" viewBox="0 0 14 14"><path d="M2.5 7 h9" stroke="#c7c7c7" stroke-width="1.25" stroke-linecap="round"></path></symbol><symbol id="ic-slash" viewBox="0 0 14 14"><path d="M3 11 L11 3" stroke="#c7c7c7" stroke-width="1.25" stroke-linecap="round"></path></symbol></defs></svg>
  <div class="cursor-dot" id="cursor-dot" aria-hidden="true"><i></i></div>
  <div class="progress" id="progress" aria-hidden="true"></div>

  <div class="notice" id="notice">
    <p>0.2.0 已发布：登录与注册统一到同一个入口，账号改为角色与权限分离的模型。</p>
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
        <a href="/" aria-current="page">首页</a>
        <a href="/product">产品</a>
        <a href="/pricing">价格</a>
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

  <nav class="chapter-rail" id="chapter-rail" aria-label="章节索引">
    <a href="#top" aria-current="true"><span class="name">首屏</span><i class="tick"></i></a>
    <a href="#workbench" aria-current="false"><span class="name">分析台</span><i class="tick"></i></a>
    <a href="#capabilities" aria-current="false"><span class="name">能力</span><i class="tick"></i></a>
    <a href="#advantages" aria-current="false"><span class="name">优势</span><i class="tick"></i></a>
    <a href="#pipeline" aria-current="false"><span class="name">链路</span><i class="tick"></i></a>
    <a href="#materials" aria-current="false"><span class="name">场景</span><i class="tick"></i></a>
    <a href="#improvement" aria-current="false"><span class="name">进步</span><i class="tick"></i></a>
    <a href="#research" aria-current="false"><span class="name">研究</span><i class="tick"></i></a>
    <a href="#privacy" aria-current="false"><span class="name">隐私</span><i class="tick"></i></a>
    <a href="#library" aria-current="false"><span class="name">算法库</span><i class="tick"></i></a>
    <a href="#figures" aria-current="false"><span class="name">图形</span><i class="tick"></i></a>
    <a href="#measure" aria-current="false"><span class="name">衡量</span><i class="tick"></i></a>
    <a href="#experts" aria-current="false"><span class="name">专家</span><i class="tick"></i></a>
    <a href="#access" aria-current="false"><span class="name">开始</span><i class="tick"></i></a>
  </nav>

  <main id="main">
    <section class="hero-showcase" id="top">
      <div class="hero-sticky-track" id="hero-track">
        <div class="hero-backdrop-wrap" id="hero-backdrop" role="region" aria-labelledby="workbench-title">
          <div class="wrap workbench-stage-head">
            <h2 class="workbench-stage-title" id="workbench-title" style="font-size: 36px;">CausalAgent会帮你解决最重要的问题</h2>
            <a class="pill" href="/dashboard">进入工作区</a>
          </div>
          <div class="workbench-fit" id="workbench-fit">
            <div class="workbench-frame" id="workbench-frame">
              <div class="preview app-preview" id="app-demo-preview">
                <div class="app-demo" id="app-demo">
                  <button class="app-demo-control workbench-play" id="demo-play" type="button" hidden>暂停演示</button>
                  <button class="app-demo-menu-toggle" id="demo-menu-toggle" type="button" aria-label="打开菜单">☰</button>
                  <aside class="app-demo-sidebar" aria-label="普通用户工作区侧边栏">
                    <div class="app-demo-sidebar-header">
                      <div class="app-demo-brand">
                        <span class="app-demo-brand-glyph" aria-hidden="true">C</span>
                        <span class="app-demo-brand-name">CausalAgent</span>
                      </div>
                      <button class="app-demo-icon-button app-demo-collapse" id="demo-collapse" type="button" aria-label="收起侧边栏" aria-expanded="true">
                        <svg class="app-demo-collapse-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m14.5 5-7 7 7 7"></path></svg>
                      </button>
                    </div>
                    <nav class="app-demo-rail" aria-label="侧边栏快捷导航">
                      <button class="app-demo-rail-button" type="button" aria-label="新建对话" title="新建对话" data-demo-action="new">
                        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"></path></svg>
                      </button>
                      <button class="app-demo-rail-button" type="button" aria-label="会话" title="会话" data-demo-action="sessions">
                        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 6h12M8 12h12M8 18h12M3.5 6h.01M3.5 12h.01M3.5 18h.01"></path></svg>
                      </button>
                      <button class="app-demo-rail-button" type="button" aria-label="文件列表" title="文件列表" data-demo-action="files">
                        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 3h9l5 5v13H5zM14 3v6h6M8 14h8M8 17h8"></path></svg>
                      </button>
                    </nav>
                    <div class="app-demo-sidebar-content">
                      <button class="app-demo-new-chat" id="demo-new-chat" type="button">新建对话</button>
                      <section class="app-demo-side-section app-demo-side-section--sessions" id="demo-session-section" aria-labelledby="demo-sessions-title">
                        <h3 class="app-demo-side-title" id="demo-sessions-title">会话</h3>
                        <button class="app-demo-session" type="button" data-demo-session="new" aria-pressed="true">
                          <span class="app-demo-session-date">2026-09-24</span>
                          <span class="app-demo-session-name">新建分析</span>
                        </button>
                        <button class="app-demo-session" type="button" data-demo-session="medical" aria-pressed="false">
                          <span class="app-demo-session-date">2026-09-23</span>
                          <span class="app-demo-session-name">医学场景案例</span>
                        </button>
                        <button class="app-demo-session" type="button" data-demo-session="industrial" aria-pressed="false">
                          <span class="app-demo-session-date">2026-09-22</span>
                          <span class="app-demo-session-name">工业场景案例</span>
                        </button>
                      </section>
                      <section class="app-demo-side-section app-demo-side-section--files" id="demo-file-section" aria-labelledby="demo-files-title">
                        <h3 class="app-demo-side-title" id="demo-files-title">文件列表</h3>
                        <button class="app-demo-file" type="button" data-demo-file="clinical-outcomes.csv" aria-pressed="false">
                          <span class="app-demo-file-date">2026-09-23</span>
                          <span class="app-demo-file-name">clinical-outcomes.csv</span>
                        </button>
                        <button class="app-demo-file" type="button" data-demo-file="causal-methods.pdf" aria-pressed="false">
                          <span class="app-demo-file-date">2026-09-21</span>
                          <span class="app-demo-file-name">causal-methods.pdf</span>
                        </button>
                      </section>
                    </div>
                    <div class="app-demo-sidebar-footer">
                      <button class="app-demo-avatar" id="demo-avatar" type="button" aria-label="用户信息" aria-expanded="false">A</button>
                    </div>
                    <div class="app-demo-user-menu" id="demo-user-menu" role="dialog" aria-label="用户信息" hidden>
                      <h4>用户信息</h4>
                      <p>账号：alice</p>
                      <button type="button">管理后台</button>
                      <button type="button">RAG 评测台</button>
                      <button type="button" id="demo-user-close">关闭</button>
                    </div>
                  </aside>
                  <button class="app-demo-scrim" id="demo-scrim" type="button" aria-label="关闭菜单"></button>
                  <main class="app-demo-main">
                    <canvas class="app-demo-network" id="demo-network" aria-hidden="true"></canvas>
                    <section class="app-demo-conversation" id="demo-conversation" aria-live="polite" aria-label="演示会话消息">
                      <div class="app-demo-message-row" id="demo-message-row" hidden>
                        <article class="app-demo-user-message"><p id="demo-user-text">请分析强化康复方案对患者 90 天功能恢复的影响。</p></article>
                        <div class="app-demo-thinking" id="demo-thinking">
                          <button class="app-demo-thinking-header" id="demo-thinking-toggle" type="button" aria-expanded="true">
                            <span id="demo-thinking-status">正在处理</span>
                            <span class="app-demo-thinking-duration" id="demo-thinking-duration">0.0s</span>
                            <span class="app-demo-thinking-dots" id="demo-thinking-dots" aria-hidden="true">...</span>
                            <span class="app-demo-thinking-disclosure" aria-hidden="true">▾</span>
                          </button>
                          <div class="app-demo-thinking-line" id="demo-thinking-line" aria-hidden="true"><i></i></div>
                          <div class="app-demo-thinking-detail" id="demo-thinking-detail">
                            <div class="app-demo-step" data-demo-step="0" data-state="done">
                              <button class="app-demo-step-header" type="button"><span class="app-demo-step-status" aria-hidden="true"></span><span class="app-demo-step-name">读取材料</span><span class="app-demo-step-time">0.4s</span><span class="app-demo-disclosure" aria-hidden="true">▾</span></button>
                              <div class="app-demo-step-detail">读取已选数据文件，并确认分析目标与字段范围。</div>
                            </div>
                            <div class="app-demo-step" data-demo-step="1" data-state="active">
                              <button class="app-demo-step-header" type="button"><span class="app-demo-step-status" aria-hidden="true"></span><span class="app-demo-step-name">执行 Deep Agent 分析</span><span class="app-demo-step-time">进行中</span><span class="app-demo-disclosure" aria-hidden="true">▾</span></button>
                              <div class="app-demo-step-detail">算法决策：先用因果图识别共同原因，再估计干预对结果的影响。</div>
                            </div>
                            <div class="app-demo-step" data-demo-step="2" data-state="todo">
                              <button class="app-demo-step-header" type="button"><span class="app-demo-step-status" aria-hidden="true"></span><span class="app-demo-step-name">整理结构化报告</span><span class="app-demo-step-time">待开始</span><span class="app-demo-disclosure" aria-hidden="true">▸</span></button>
                              <div class="app-demo-step-detail">报告会保留结论、因果图、指标区间和来源。</div>
                            </div>
                          </div>
                        </div>
                      </div>
                      <article class="app-demo-report-message" id="demo-report-row" hidden>
                        <div class="app-demo-report-document">
                          <h2 class="app-demo-report-title" id="demo-report-title">医学场景 · 干预效果报告</h2>
                          <section class="app-demo-report-section">
                            <h3 class="app-demo-report-section-title">结论摘要</h3>
                            <div class="app-demo-report-markdown">
                              <p id="demo-report-summary">在控制基线病情、年龄与既往治疗后，强化康复方案对患者 90 天功能恢复评分呈正向影响。</p>
                              <ul id="demo-report-summary-list">
                                <li>平均处理效应：+6.8 分</li>
                                <li>95% 置信区间：2.1 ～ 11.4 分，不跨 0</li>
                                <li>证据强度：中等，结论适用于本次演示数据范围</li>
                              </ul>
                            </div>
                          </section>
                          <section class="app-demo-report-section">
                            <h3 class="app-demo-report-section-title">变量定义</h3>
                            <dl class="app-demo-report-facts">
                              <div><dt>干预变量</dt><dd id="demo-report-treatment">强化康复方案 vs 常规方案</dd></div>
                              <div><dt>结果变量</dt><dd id="demo-report-outcome">90 天功能恢复评分</dd></div>
                              <div><dt>调整变量</dt><dd id="demo-report-adjustment">基线病情、年龄、既往治疗史</dd></div>
                              <div><dt>分析样本</dt><dd id="demo-report-sample">486 名患者 · 12 周随访</dd></div>
                            </dl>
                          </section>
                          <section class="app-demo-report-section">
                            <h3 class="app-demo-report-section-title">识别与分析过程</h3>
                            <ol class="app-demo-report-list" id="demo-report-steps">
                              <li>按患者编号合并基线、干预和随访记录。</li>
                              <li>用因果图识别需要调整的共同原因。</li>
                              <li>估计平均处理效应，并计算 95% 置信区间。</li>
                              <li>进行分层分析和安慰剂时间窗检查。</li>
                            </ol>
                            <p id="demo-report-adjustment-note">调整集合保留干预发生前可观测的共同原因，不把治疗后的复诊信息放入模型。</p>
                          </section>
                          <section class="app-demo-report-section">
                            <h3 class="app-demo-report-section-title">效应估计</h3>
                            <div class="app-demo-report-result-grid">
                              <div><span>平均处理效应</span><strong id="demo-report-effect">+6.8 分</strong></div>
                              <div><span>95% 置信区间</span><strong id="demo-report-ci">2.1 ～ 11.4 分</strong></div>
                              <div><span>观察期</span><strong id="demo-report-period">入组后 90 天</strong></div>
                            </div>
                          </section>
                          <section class="app-demo-report-section">
                            <h3 class="app-demo-report-section-title">主要因果关系</h3>
                            <div class="app-demo-report-causal-graph app-demo-report-graph">
                              <svg id="demo-report-graph" viewBox="0 0 620 300" role="img" aria-label="基线病情影响康复方案和功能恢复，康复方案影响功能恢复，功能恢复影响复诊率">
                                <defs>
                                  <marker id="demo-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="8" markerHeight="8" markerUnits="userSpaceOnUse" orient="auto">
                                    <path class="demo-arrow" d="M0 0 L8 4 L0 8 Z"></path>
                                  </marker>
                                </defs>
                                <path class="demo-edge" data-demo-edge="size-treatment" marker-end="url(#demo-arrow)" d="M96 152 H282"></path>
                                <path class="demo-edge" data-demo-edge="size-visit" marker-end="url(#demo-arrow)" d="M92 140 C174 74 388 54 504 84"></path>
                                <path class="demo-edge" data-demo-edge="treatment-visit" marker-end="url(#demo-arrow)" d="M318 144 C382 120 444 104 504 96"></path>
                                <path class="demo-edge" data-demo-edge="visit-repeat" marker-end="url(#demo-arrow)" d="M520 108 V202"></path>
                                <g data-demo-node="size" tabindex="0" role="button" aria-pressed="false" aria-label="基线病情，混杂变量"><circle class="node-halo" cx="78" cy="152" r="27"></circle><circle class="node-ring" cx="78" cy="152" r="16"></circle><text id="demo-graph-size-name" x="78" y="185" text-anchor="middle">基线病情</text><text class="graph-sub" id="demo-graph-size-sub" x="78" y="202" text-anchor="middle">混杂变量</text></g>
                                <g data-demo-node="treatment" tabindex="0" role="button" aria-pressed="false" aria-label="强化康复方案，处理变量"><circle class="node-halo" cx="300" cy="152" r="29"></circle><circle class="node-ring" cx="300" cy="152" r="18"></circle><circle class="node-dot" cx="300" cy="152" r="8"></circle><text id="demo-graph-treatment-name" x="300" y="185" text-anchor="middle">强化康复方案</text><text class="graph-sub" id="demo-graph-treatment-sub" x="300" y="202" text-anchor="middle">处理变量</text></g>
                                <g data-demo-node="visit" tabindex="0" role="button" aria-pressed="false" aria-label="功能恢复评分，结果变量"><circle class="node-halo" cx="520" cy="90" r="27"></circle><circle class="node-ring" cx="520" cy="90" r="16"></circle><text id="demo-graph-visit-name" x="520" y="123" text-anchor="middle">功能恢复评分</text><text class="graph-sub" id="demo-graph-visit-sub" x="520" y="140" text-anchor="middle">结果变量</text></g>
                                <g data-demo-node="repeat" tabindex="0" role="button" aria-pressed="false" aria-label="复诊率，结果变量"><circle class="node-halo" cx="520" cy="220" r="27"></circle><circle class="node-ring" cx="520" cy="220" r="16"></circle><text id="demo-graph-repeat-name" x="520" y="253" text-anchor="middle">复诊率</text><text class="graph-sub" id="demo-graph-repeat-sub" x="520" y="270" text-anchor="middle">结果变量</text></g>
                              </svg>
                              <p class="app-demo-graph-readout" id="demo-graph-readout" role="status">点击一个节点，查看它在报告中的直接关系。</p>
                            </div>
                          </section>
                          <section class="app-demo-report-section">
                            <h3 class="app-demo-report-section-title">稳健性与边界</h3>
                            <div class="app-demo-report-markdown">
                              <p id="demo-report-robustness">分层到轻、中、重度患者后，方向保持一致；仍可能受到未观测康复依从性的影响。</p>
                              <ul id="demo-report-robustness-list"><li>未将结果解释为个体患者的确定性收益。</li><li>建议回到原始记录复核缺失值和入组标准。</li></ul>
                            </div>
                          </section>
                          <section class="app-demo-report-section">
                            <h3 class="app-demo-report-section-title">来源与复核</h3>
                            <div class="app-demo-report-markdown"><p id="demo-report-source-note">方法选择：因果图识别共同原因，结合分层分析复核结论。</p></div>
                            <ul class="app-demo-report-sources" id="demo-report-sources"><li>clinical-outcomes.csv</li><li>causal-methods.pdf</li></ul>
                          </section>
                          <p class="app-demo-report-future-note">展示部分未来内容</p>
                        </div>
                      </article>
                    </section>
                    <div class="app-demo-new-chat-stage">
                      <div class="app-demo-welcome" id="demo-welcome">
                        <h3>上传因果数据文件，开始因果分析</h3>
                        <p>我会基于你上传的数据进行分析并生成因果关系结果。</p>
                      </div>
                      <section class="app-demo-composer" aria-label="消息输入区">
                        <div class="app-demo-selected-file" id="demo-selected-file" hidden>
                          <span class="app-demo-file-kind">CSV</span>
                          <span><strong>clinical-outcomes.csv</strong><small>CSV · 128 KB</small></span>
                          <button class="app-demo-file-remove" id="demo-file-remove" type="button" aria-label="清除文件选择">×</button>
                        </div>
                        <textarea class="app-demo-textarea" id="demo-input" rows="3" aria-label="输入消息">请分析促销力度对到店人数的影响。</textarea>
                        <div class="app-demo-actions">
                          <button class="app-demo-action app-demo-action--search" id="demo-search" type="button" aria-pressed="false">
                            <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.4"></circle><path d="M3.6 12h16.8"></path><ellipse cx="12" cy="12" rx="3.6" ry="8.4"></ellipse></svg>
                            <span>联网搜索</span>
                          </button>
                          <button class="app-demo-action app-demo-action--upload" id="demo-upload" type="button" aria-label="上传" title="上传">
                            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 16.5V4.5"></path><path d="M7.5 9 12 4.5 16.5 9"></path><path d="M4.5 16.5v3h15v-3"></path></svg>
                          </button>
                          <button class="app-demo-action app-demo-action--send" id="demo-send" type="button" aria-label="发送">
                            <svg class="app-demo-send-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M2 21 23 12 2 3v7l15 2-15 2v7Z"></path></svg>
                            <span class="app-demo-send-label">发送</span>
                            <span class="app-demo-send-stop" aria-hidden="true"></span>
                          </button>
                        </div>
                      </section>
                    </div>
                  </main>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="hero-foreground-wrap" id="hero-foreground">
          <canvas class="hero-ambient-canvas" id="hero-network-ambient" aria-hidden="true"></canvas>
          <canvas class="hero-canvas" id="hero-network" aria-hidden="true"></canvas>
          <div class="wrap hero-inner">
            <div class="hero-copy">
              <h1 class="hero-title" id="hero-title"><span class="ch">CausalAgent</span><span class="ch">：</span><br><span class="ch">用</span><span class="ch-mark"><span class="ch">因</span><span class="ch">果</span><span class="ch-mark-anchor" aria-hidden="true"><span class="ch-mark-rule"></span></span></span><span class="ch">连</span><span class="ch">接</span><span class="ch">世</span><span class="ch">界</span></h1>
              <p class="hero-lede" data-hero-fade="">上传你想分析的内容, CausalAgent会理解材料, 并结合专业知识库，选择正确的因果方法, 给出严谨的报告。</p>
              <div class="hero-actions" data-hero-fade="">
                <a class="pill pill--lg" href="/dashboard">进入工作区</a>
                <a class="ghost ghost--lg" href="/product">了解产品</a>
              </div>
              <p class="hero-note" data-hero-fade="">随时随地    与我交流</p>
            </div>
          </div>
        </div>
        <div class="marquee hero-marquee" aria-label="合作单位与技术栈">
          <div class="wrap marquee-inner">
            <div class="marquee-viewport">
              <div class="marquee-track">
                <div class="marquee-half">
                  <span class="logo-chip"><img :src="'/site-assets/assets/logo/gdut-wide.png'" alt="广东工业大学" width="137" height="32"></span>
                  <span class="logo-chip"><img :src="'/site-assets/assets/logo/dmir.png'" alt="DMIR" width="32" height="32"></span>
                  <span class="logo-chip" aria-hidden="true"><img :src="'/site-assets/assets/logo/gdut-wide.png'" alt="" width="137" height="32"></span>
                  <span class="logo-chip" aria-hidden="true"><img :src="'/site-assets/assets/logo/dmir.png'" alt="" width="32" height="32"></span>
                  <span class="logo-chip" aria-hidden="true"><img :src="'/site-assets/assets/logo/gdut-wide.png'" alt="" width="137" height="32"></span>
                  <span class="logo-chip" aria-hidden="true"><img :src="'/site-assets/assets/logo/dmir.png'" alt="" width="32" height="32"></span>
                </div>
                <div class="marquee-half" aria-hidden="true">
                  <span class="logo-chip" aria-hidden="true"><img :src="'/site-assets/assets/logo/gdut-wide.png'" alt="" width="137" height="32"></span>
                  <span class="logo-chip" aria-hidden="true"><img :src="'/site-assets/assets/logo/dmir.png'" alt="" width="32" height="32"></span>
                  <span class="logo-chip" aria-hidden="true"><img :src="'/site-assets/assets/logo/gdut-wide.png'" alt="" width="137" height="32"></span>
                  <span class="logo-chip" aria-hidden="true"><img :src="'/site-assets/assets/logo/dmir.png'" alt="" width="32" height="32"></span>
                  <span class="logo-chip" aria-hidden="true"><img :src="'/site-assets/assets/logo/gdut-wide.png'" alt="" width="137" height="32"></span>
                  <span class="logo-chip" aria-hidden="true"><img :src="'/site-assets/assets/logo/dmir.png'" alt="" width="32" height="32"></span>
                </div>
              </div>
            </div>
          </div>
          <div class="wrap marquee-inner marquee-inner--stack">
            <div class="marquee-viewport">
              <div class="marquee-track">
                <div class="marquee-half">
                  <span class="logo-chip"><img :src="'/site-assets/assets/logo/langchain.png'" alt="LangChain" width="149" height="32"></span>
                  <span class="logo-chip"><img :src="'/site-assets/assets/logo/langgraph.png'" alt="LangGraph" width="149" height="32"></span>
                  <span class="logo-chip"><img :src="'/site-assets/assets/logo/deepagents.png'" alt="deepagents" width="162" height="32"></span>
                  <span class="logo-chip"><img :src="'/site-assets/assets/logo/langsmith.png'" alt="LangSmith" width="206" height="32"></span>
                  <span class="logo-chip" aria-hidden="true"><img :src="'/site-assets/assets/logo/langchain.png'" alt="" width="149" height="32"></span>
                  <span class="logo-chip" aria-hidden="true"><img :src="'/site-assets/assets/logo/langgraph.png'" alt="" width="149" height="32"></span>
                  <span class="logo-chip" aria-hidden="true"><img :src="'/site-assets/assets/logo/deepagents.png'" alt="" width="162" height="32"></span>
                  <span class="logo-chip" aria-hidden="true"><img :src="'/site-assets/assets/logo/langsmith.png'" alt="" width="206" height="32"></span>
                </div>
                <div class="marquee-half" aria-hidden="true">
                  <span class="logo-chip" aria-hidden="true"><img :src="'/site-assets/assets/logo/langchain.png'" alt="" width="149" height="32"></span>
                  <span class="logo-chip" aria-hidden="true"><img :src="'/site-assets/assets/logo/langgraph.png'" alt="" width="149" height="32"></span>
                  <span class="logo-chip" aria-hidden="true"><img :src="'/site-assets/assets/logo/deepagents.png'" alt="" width="162" height="32"></span>
                  <span class="logo-chip" aria-hidden="true"><img :src="'/site-assets/assets/logo/langsmith.png'" alt="" width="206" height="32"></span>
                  <span class="logo-chip" aria-hidden="true"><img :src="'/site-assets/assets/logo/langchain.png'" alt="" width="149" height="32"></span>
                  <span class="logo-chip" aria-hidden="true"><img :src="'/site-assets/assets/logo/langgraph.png'" alt="" width="149" height="32"></span>
                  <span class="logo-chip" aria-hidden="true"><img :src="'/site-assets/assets/logo/deepagents.png'" alt="" width="162" height="32"></span>
                  <span class="logo-chip" aria-hidden="true"><img :src="'/site-assets/assets/logo/langsmith.png'" alt="" width="206" height="32"></span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
      <span class="hero-stage-anchor" id="workbench"></span>
    </section>



    <div class="wrap"><div class="divider"></div></div>

    <section class="section" id="capabilities" aria-labelledby="capabilities-title">
      <div class="wrap">
        <h2 class="section-title" id="capabilities-title">核心能力</h2>
        <p class="section-lede">谁在决策、知识从哪来、方法怎么选、结论怎么写</p>
        <div class="bento" data-reveal-stagger="">
          <article class="bcard bcard--wide">
            <span class="icon-circle" aria-hidden="true"><svg viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linecap="round"><circle cx="9" cy="4.4" r="2.1"></circle><circle cx="4.4" cy="13.4" r="2.1"></circle><circle cx="13.6" cy="13.4" r="2.1"></circle><path d="M9 6.6v2.6M9 9.2L5.6 11.8M9 9.2l3.4 2.6"></path></svg></span>
            <h3>Agent 的强大决策能力</h3>
            <p> Agent 智能判断分析方向：在一个上下文内动态选择算法与工具</p>
            <div class="bcard-art">
              <svg viewBox="0 0 520 140" role="img" aria-label="决策示意图：分析目标进入后由 Agent 规划，分别交给 RAG 知识库、联网搜索与动态算法，最后汇总生成报告。">
                <path class="flow-line" d="M96 70 L130 70"></path>
                <path class="flow-ink" pathLength="100" style="animation-duration:2.5s;animation-delay:0s" d="M96 70 L130 70"></path>
                <path class="flow-ink-2" pathLength="100" style="animation-duration:2.5s;animation-delay:0s" d="M96 70 L130 70"></path>
                <path class="flow-ink-3" pathLength="100" style="animation-duration:2.5s;animation-delay:0s" d="M96 70 L130 70"></path>
                <path class="flow-line" d="M242 66 C272 62 258 22 286 22"></path>
                <path class="flow-ink" pathLength="100" style="animation-duration:3.45s;animation-delay:-0.2s" d="M242 66 C272 62 258 22 286 22"></path>
                <path class="flow-ink-2" pathLength="100" style="animation-duration:3.45s;animation-delay:-0.2s" d="M242 66 C272 62 258 22 286 22"></path>
                <path class="flow-ink-3" pathLength="100" style="animation-duration:3.45s;animation-delay:-0.2s" d="M242 66 C272 62 258 22 286 22"></path>
                <path class="flow-line" d="M242 70 L286 70"></path>
                <path class="flow-ink" pathLength="100" style="animation-duration:2.8s;animation-delay:-0.5s" d="M242 70 L286 70"></path>
                <path class="flow-ink-2" pathLength="100" style="animation-duration:2.8s;animation-delay:-0.5s" d="M242 70 L286 70"></path>
                <path class="flow-ink-3" pathLength="100" style="animation-duration:2.8s;animation-delay:-0.5s" d="M242 70 L286 70"></path>
                <path class="flow-line" d="M242 74 C272 78 258 118 286 118"></path>
                <path class="flow-ink" pathLength="100" style="animation-duration:3.8s;animation-delay:-0.3s" d="M242 74 C272 78 258 118 286 118"></path>
                <path class="flow-ink-2" pathLength="100" style="animation-duration:3.8s;animation-delay:-0.3s" d="M242 74 C272 78 258 118 286 118"></path>
                <path class="flow-ink-3" pathLength="100" style="animation-duration:3.8s;animation-delay:-0.3s" d="M242 74 C272 78 258 118 286 118"></path>
                <path class="flow-line" d="M406 22 C432 24 428 60 450 64"></path>
                <path class="flow-ink" pathLength="100" style="animation-duration:3.3s;animation-delay:-0.1s" d="M406 22 C432 24 428 60 450 64"></path>
                <path class="flow-ink-2" pathLength="100" style="animation-duration:3.3s;animation-delay:-0.1s" d="M406 22 C432 24 428 60 450 64"></path>
                <path class="flow-ink-3" pathLength="100" style="animation-duration:3.3s;animation-delay:-0.1s" d="M406 22 C432 24 428 60 450 64"></path>
                <path class="flow-line" d="M406 70 L450 70"></path>
                <path class="flow-ink" pathLength="100" style="animation-duration:3s;animation-delay:-0.4s" d="M406 70 L450 70"></path>
                <path class="flow-ink-2" pathLength="100" style="animation-duration:3s;animation-delay:-0.4s" d="M406 70 L450 70"></path>
                <path class="flow-ink-3" pathLength="100" style="animation-duration:3s;animation-delay:-0.4s" d="M406 70 L450 70"></path>
                <path class="flow-line" d="M406 118 C432 116 428 80 450 76"></path>
                <path class="flow-ink" pathLength="100" style="animation-duration:3.6s;animation-delay:-0.6s" d="M406 118 C432 116 428 80 450 76"></path>
                <path class="flow-ink-2" pathLength="100" style="animation-duration:3.6s;animation-delay:-0.6s" d="M406 118 C432 116 428 80 450 76"></path>
                <path class="flow-ink-3" pathLength="100" style="animation-duration:3.6s;animation-delay:-0.6s" d="M406 118 C432 116 428 80 450 76"></path>
                <rect class="flow-chip" x="0" y="56" width="96" height="28" rx="14"></rect>
                <text class="flow-chip-label" x="48" y="74" text-anchor="middle">分析目标</text>
                <rect class="flow-node" x="130" y="56" width="112" height="28" rx="14"></rect>
                <text x="186" y="74" text-anchor="middle" font-size="12" fill="#ffffff">Agent 规划</text>
                <rect class="flow-chip" x="286" y="8" width="120" height="28" rx="14"></rect>
                <text class="flow-chip-label" x="346" y="26" text-anchor="middle">RAG 知识库</text>
                <rect class="flow-chip" x="286" y="56" width="120" height="28" rx="14"></rect>
                <text class="flow-chip-label" x="346" y="74" text-anchor="middle">联网搜索</text>
                <rect class="flow-chip" x="286" y="104" width="120" height="28" rx="14"></rect>
                <text class="flow-chip-label" x="346" y="122" text-anchor="middle">动态算法</text>
                <rect class="flow-chip" x="450" y="56" width="70" height="28" rx="14"></rect>
                <text class="flow-chip-label" x="485" y="74" text-anchor="middle">生成报告</text>
              </svg>
            </div>
          </article>
          <article class="bcard">
            <span class="icon-circle" aria-hidden="true"><svg viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linecap="round"><ellipse cx="9" cy="4.6" rx="5.5" ry="2.1"></ellipse><path d="M3.5 4.6v8.8c0 1.2 2.5 2.1 5.5 2.1s5.5-.9 5.5-2.1V4.6"></path><path d="M3.5 9c0 1.2 2.5 2.1 5.5 2.1s5.5-.9 5.5-2.1"></path></svg></span>
            <h3>定制知识库的专业知识</h3>
            <p>行业里的论文、报告和内部资料整理成专业知识库。</p>
            <ul class="bcard-list">
              <li>按行业定制的专业知识</li>
              <li>学术论文</li>
              <li>行业报告</li>
              <li>内部行业资料</li>
            </ul>
          </article>
          <article class="bcard bcard--half">
            <span class="icon-circle" aria-hidden="true"><svg viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linecap="round"><circle cx="3.6" cy="9" r="1.6"></circle><circle cx="14.4" cy="4.4" r="1.6"></circle><circle cx="14.4" cy="13.6" r="1.6"></circle><path d="M5.1 8.4l7.6-3.2M5.1 9.6l7.6 3.2"></path></svg></span>
            <h3>多样化算法调用</h3>
            <p>动态调用最合适的算法，竞争并抉择出最优路劲</p>
            <ul class="bcard-list">
              <li>动态选择不同算法</li>
              <li>依据不同算法构建 DAG</li>
              <li>严格的算法校验</li>
            </ul>
          </article>
          <article class="bcard bcard--half">
            <span class="icon-circle" aria-hidden="true"><svg viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linecap="round"><path d="M5 2.5h5.5l3 3V15.5H5z"></path><path d="M10.5 2.5v3h3"></path><path d="M7.2 8.6h4M7.2 11.6h4"></path></svg></span>
            <h3>专业化报告</h3>
            <p>生成的不是一段结论，而是一份专业严谨的报告：每个判断都有来源。</p>
            <ul class="bcard-list">
              <li>按专业结构组织：方法、过程与结论分别写</li>
              <li>每个结论标注事实来源，可以回到原始材料</li>
              <li>报告内的因果图是动态的，可以查看具体的变量关系</li>
            </ul>
          </article>
        </div>
      </div>
    </section>

    <div class="wrap"><div class="divider"><i aria-hidden="true"></i><i aria-hidden="true"></i></div></div>

    <section class="section advantages" id="advantages" aria-labelledby="advantages-title">
      <div class="wrap">
        <h2 class="section-title" id="advantages-title">更强，更快，更高效</h2>
        <p class="section-lede">CausalAgent 帮助您更快更好的完成所有任务</p>
        <div class="advantages-grid" data-reveal-stagger="">
          <article class="advantage" aria-labelledby="advantage-runtime-title">
            <h3 class="advantage-title" id="advantage-runtime-title">平均运行数据</h3>
            <p class="advantage-value od-nowrap" data-count="48655" data-count-decimals="0" data-count-grouped="true" data-count-suffix=" tokens">48,655 tokens</p>
            <p class="advantage-detail">只需近一分钟</p>
          </article>
          <article class="advantage" aria-labelledby="advantage-test-title">
            <h3 class="advantage-title" id="advantage-test-title">因果测试表现</h3>
            <div class="advantage-value advantage-pair" aria-label="Precision 0.843，Recall 0.849">
              <span class="advantage-number" data-count="0.843" data-count-decimals="3">0.843</span>
              <span class="advantage-separator" aria-hidden="true">/</span>
              <span class="advantage-number" data-count="0.849" data-count-decimals="3">0.849</span>
            </div>
            <p class="advantage-detail">Precision / Recall</p>
            <p class="advantage-note">在 15 种因果测试集上的平均测试效果</p>
          </article>
          <article class="advantage" aria-labelledby="advantage-report-title">
            <h3 class="advantage-title" id="advantage-report-title">综合报告可信度</h3>
            <p class="advantage-value od-nowrap" data-count="78" data-count-decimals="0" data-count-suffix="%">78%</p>
          </article>
        </div>
        <div class="advantages-compare" aria-labelledby="compare-kicker" style="transform: translate(2px, -19px);">
          <p class="compare-kicker" id="compare-kicker">横向对比</p>


          <div class="compare-wrap" tabindex="0" role="group" aria-label="对比表，窄屏下可以横向滚动">
            <table class="compare-table" id="compare-table">
              <caption class="sr-only">CausalAgent、通用算法、通用LLM、通用 Agents 与 codex 在资源开销、流程能力和算法能力上的定性差别</caption>
              <thead>
                <tr>
                  <th scope="col">对比变量</th>
                  <th scope="col" class="col-focus">CausalAgent</th>
                  <th scope="col">通用算法</th>
                  <th scope="col">通用LLM</th>
                  <th scope="col">通用 Agents</th>
                  <th scope="col">codex</th>
                </tr>
              </thead>
              <tbody>
                <tr><th scope="row">Token量<span class="compare-mark" aria-hidden="true">※</span></th><td class="col-focus">≈5w Token</td><td><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-slash"></use></svg><span class="sr-only">不具备</span></td><td>≈10w token</td><td>≈30w token</td><td>≈20w Token</td></tr>
                <tr><th scope="row">耗时<span class="compare-mark" aria-hidden="true">※</span></th><td class="col-focus">≈60s</td><td>≈80s</td><td>≈30s</td><td>≈80s</td><td>≈120s</td></tr>
                <tr><th scope="row">可解释的结构化报告</th><td class="col-focus"><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-check"></use></svg><span class="sr-only">具备</span></td><td><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-slash"></use></svg><span class="sr-only">不具备</span></td><td><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-check"></use></svg><span class="sr-only">具备</span></td><td><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-check"></use></svg><span class="sr-only">具备</span></td><td><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-check"></use></svg><span class="sr-only">具备</span></td></tr>
                <tr><th scope="row">专业完整的定制化链路</th><td class="col-focus"><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-check"></use></svg><span class="sr-only">具备</span></td><td><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-slash"></use></svg><span class="sr-only">不具备</span></td><td><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-slash"></use></svg><span class="sr-only">不具备</span></td><td><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-slash"></use></svg><span class="sr-only">不具备</span></td><td><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-slash"></use></svg><span class="sr-only">不具备</span></td></tr>
                <tr><th scope="row">可复用的链路与结论</th><td class="col-focus"><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-check"></use></svg><span class="sr-only">具备</span></td><td><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-slash"></use></svg><span class="sr-only">不具备</span></td><td><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-check"></use></svg><span class="sr-only">具备</span></td><td><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-check"></use></svg><span class="sr-only">具备</span></td><td><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-check"></use></svg><span class="sr-only">具备</span></td></tr>
                <tr><th scope="row">多样的分析流程</th><td class="col-focus"><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-check"></use></svg><span class="sr-only">具备</span></td><td><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-slash"></use></svg><span class="sr-only">不具备</span></td><td><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-slash"></use></svg><span class="sr-only">不具备</span></td><td><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-check"></use></svg><span class="sr-only">具备</span></td><td><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-check"></use></svg><span class="sr-only">具备</span></td></tr>
                <tr><th scope="row">定制化算法</th><td class="col-focus"><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-check"></use></svg><span class="sr-only">具备</span></td><td><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-check"></use></svg><span class="sr-only">具备</span></td><td><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-slash"></use></svg><span class="sr-only">不具备</span></td><td><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-slash"></use></svg><span class="sr-only">不具备</span></td><td><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-slash"></use></svg><span class="sr-only">不具备</span></td></tr>
                <tr><th scope="row">算法的交叉比对</th><td class="col-focus"><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-check"></use></svg><span class="sr-only">具备</span></td><td><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-slash"></use></svg><span class="sr-only">不具备</span></td><td><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-slash"></use></svg><span class="sr-only">不具备</span></td><td><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-slash"></use></svg><span class="sr-only">不具备</span></td><td><svg class="mark" width="14" height="14" aria-hidden="true"><use href="#ic-slash"></use></svg><span class="sr-only">不具备</span></td></tr>
              </tbody>
              <tfoot>
                <tr><td colspan="6"><span class="compare-note-mark" aria-hidden="true">※</span>单次因果分析任务</td></tr>
              </tfoot>
            </table>
          </div>
          <p class="caption-note">本表为实验室测试版本，用来说明五类方式在资源、流程与算法能力上的差别；不代表固定性能指标或具体产品测评。</p>
        </div>
      </div>
    </section>

    <div class="wrap"><div class="divider"><i aria-hidden="true"></i><i aria-hidden="true"></i></div></div>

    <section class="section" id="pipeline" aria-labelledby="pipeline-title">
      <div class="wrap">
        <div class="pipeline">
          <div class="pipe-left">
            <h2 id="pipeline-title">一条链路完成分析</h2>
            <p>Simple, Fast, Professional</p>
            <div class="pipe-rail">
              <div class="pipe-track" aria-hidden="true"><div class="pipe-fill" id="pipe-fill"></div></div>
              <ol class="pipe-index" id="pipe-index">
                <li><button type="button" data-pipe="0" aria-current="true"><i aria-hidden="true"></i>第一阶段 · 描述问题</button></li>
                <li><button type="button" data-pipe="1" aria-current="false"><i aria-hidden="true"></i>第二阶段 · 让智能体执行</button></li>
                <li><button type="button" data-pipe="2" aria-current="false"><i aria-hidden="true"></i>第三阶段 · 复核并取用结论</button></li>
              </ol>
            </div>
          </div>
          <ol class="ledger" id="ledger">
            <li class="ledger-row" data-ledger="0" data-reveal="">
              <p class="ledger-num">第一阶段</p>
              <h3>描述问题</h3>
              <p style="border-radius: 0px; font-weight: 400;">用自然语言说明分析目标，需要时上传统计数据文件。 自动识别处理变量与结果变量，进行文件预处理</p>
              <ul class="ledger-list">
                <li>上传文件, 全程保护隐私</li>
                <li>给出或者自动识别处理变量、结果变量</li>
                <li>文件清洗与预处理</li>
              </ul>
              <div class="ledger-art">
                <svg viewBox="0 0 520 120" role="img" aria-label="描述问题阶段：确定处理变量与结果变量。">
                  <rect class="flow-chip" x="0" y="40" width="180" height="40" rx="20"></rect>
                  <text class="flow-chip-label" x="90" y="65" text-anchor="middle">处理变量 · 血小板浓度</text>
                  <path class="flow-line" d="M186 60 L334 60"></path>
                  <rect class="flow-chip" x="340" y="40" width="180" height="40" rx="20"></rect>
                  <text class="flow-chip-label" x="430" y="65" text-anchor="middle">结果变量 · 是否服用药物</text>
                  <text class="flow-chip-label" x="260" y="30" text-anchor="middle" fill="#6f6f6f">因果效应</text>
                </svg>
              </div>
            </li>
            <li class="ledger-row" data-ledger="1" data-reveal="">
              <p class="ledger-num">第二阶段</p>
              <h3>智能体执行</h3>
              <p>智能体检查数据、选择方法、运行算法，并把阶段性结论与依据持续展示出来。</p>
              <ul class="ledger-list">
                <li>执行过程以阶段与事件的形式展示，长任务可以取消</li>
                <li>每一步决策及其依据随任务事件公开</li>
                <li>选择算法和工具, 给出依据</li>
              </ul>
              <div class="ledger-art">
                <svg viewBox="0 0 520 120" role="img" aria-label="执行阶段：因果发现、效应估计与反证检验依次推进。">
                  <path class="flow-line" d="M138 60 L214 60 M306 60 L382 60"></path>
                  <rect class="flow-chip" x="36" y="40" width="96" height="40" rx="20"></rect>
                  <text class="flow-chip-label" x="84" y="65" text-anchor="middle">预处理</text>
                  <rect class="flow-node" x="220" y="40" width="80" height="40" rx="20"></rect>
                  <text x="260" y="65" text-anchor="middle" font-size="12" fill="#ffffff">智能体规划</text>
                  <rect class="flow-chip" x="388" y="40" width="96" height="40" rx="20"></rect>
                  <text class="flow-chip-label" x="436" y="65" text-anchor="middle">执行算法</text>
                  <text class="flow-chip-label" x="260" y="24" text-anchor="middle" fill="#6f6f6f">执行算法或者工具</text>
                </svg>
              </div>
            </li>
            <li class="ledger-row" data-ledger="2" data-reveal="">
              <p class="ledger-num">第三阶段</p>
              <h3>复核并取用报告</h3>
              <p>查看因果图、指标与结构化报告<br>对报告继续追问，直到完全清楚为止</p>
              <ul class="ledger-list">
                <li>获取严谨清晰的报告</li>
                <li>查看对应的来源，事实与分析</li>
                <li>不断追问，修改报告，直到满意为止</li>
              </ul>
              <div class="ledger-art">
                <svg viewBox="0 0 520 120" role="img" aria-label="复核阶段：结论可以逐条回到产生它的步骤。">
                  <path class="flow-line" d="M0 34 L300 34 M0 60 L260 60 M0 86 L220 86"></path>
                  <circle cx="316" cy="34" r="8" fill="#171717"></circle>
                  <circle cx="276" cy="60" r="8" fill="#ffffff" stroke="#5e5e5e" stroke-width="1.25"></circle>
                  <circle cx="236" cy="86" r="8" fill="#ffffff" stroke="#5e5e5e" stroke-width="1.25"></circle>
                  <text class="flow-chip-label" x="336" y="38">展开来源 · 定位到引用它的段落</text>
                  <text class="flow-chip-label" x="296" y="64">继续追问 · 回答疑问</text>
                  <text class="flow-chip-label" x="256" y="90">要求重写 </text>
                </svg>
              </div>
            </li>
          </ol>
        </div>
      </div>
    </section>

  <div class="wrap"><div class="divider"><i aria-hidden="true"></i><i aria-hidden="true"></i></div></div>

    <section class="section" id="materials" aria-labelledby="materials-title">
      <div class="wrap">
        <div class="materials-head">
          <h2 class="section-title" id="materials-title">多类使用场景</h2>
          <p class="section-lede">从临床指标到生产数据，再到学习表现，用因果分析从数据关系走向可复核的决策建议。</p>
        </div>
        <div class="stack">
          <div class="stack-slot">
            <article class="stack-card">
              <div class="stack-card-head"><h3>医疗场景</h3><span class="tag">辅助诊疗</span></div>
              <p>结合多项临床指标进行严谨的数学分析，形成诊疗建议参考，并指出当前数据可能缺失、值得补充的检查项目。</p>
              <ul class="stack-tags"><li>指标关联分析</li><li>诊疗建议参考</li><li>补充检查提示</li></ul>
              <svg class="stack-card-art scene-art--medical" viewBox="0 0 360 160" aria-hidden="true" focusable="false">
                <path class="scene-art__draw" pathLength="100" d="M72 84h38l16-18 18 37 24-52 24 36 14-15h54"></path>
              </svg>
            </article>
          </div>
          <div class="stack-slot">
            <article class="stack-card">
              <div class="stack-card-head"><h3>工业场景</h3><span class="tag">流程诊断</span></div>
              <p>分析工业指标之间的潜在关联，定位影响生产结果的关键路径，并指出流程中可能缺失的中间环节或监测点。</p>
              <ul class="stack-tags"><li>内部关联分析</li><li>关键路径定位</li><li>流程缺口提示</li></ul>
              <svg class="stack-card-art scene-art--industry" viewBox="0 0 360 160" aria-hidden="true" focusable="false">
                <path class="scene-art__solid" d="M48 134V94l57 24V89l53 27V82l59 23V73h99v61H48Z"></path>
                <path class="scene-art__solid" d="M224 73V31h50v42Z"></path>
                <path class="scene-art__solid scene-art__smoke scene-art__smoke--one" d="M224 24c-6-4-3-13 4-14 2-8 14-10 18-3 8-2 12 8 7 14H224Z"></path>
                <path class="scene-art__solid scene-art__smoke scene-art__smoke--two" d="M268 29c-5-3-3-10 3-11 1-6 10-8 13-2 6-1 9 7 4 13h-20Z"></path>
                <path class="scene-art__cutout" d="M72 112h17v13H72zM120 112h17v13h-17zM170 112h17v13h-17zM242 92h17v13h-17zM278 92h17v13h-17z"></path>
              </svg>
            </article>
          </div>
          <div class="stack-slot">
            <article class="stack-card">
              <div class="stack-card-head"><h3>教育场景</h3><span class="tag">成因追溯</span></div>
              <p>探查成绩数据背后可能的影响因素，识别学习过程中的异常信号，并定位教学环节可能存在的偏差或断点。</p>
              <ul class="stack-tags"><li>成绩成因探查</li><li>影响因素识别</li><li>教育环节诊断</li></ul>
              <svg class="stack-card-art scene-art--education" viewBox="0 0 360 160" aria-hidden="true" focusable="false">
                <path class="scene-art__solid" d="M74 112h212v14c0 5-4 9-9 9H83c-5 0-9-4-9-9v-14Z"></path>
                <path class="scene-art__pages" d="M87 116h185v5H87z"></path>
                <path class="scene-art__solid" d="M88 85h184v25H88z"></path>
                <path class="scene-art__pages" d="M101 90h157v5H101z"></path>
                <path class="scene-art__solid" d="M102 61h156v21H102z"></path>
                <path class="scene-art__pages" d="M113 66h134v4H113z"></path>
                <path class="scene-art__solid scene-art__cap" d="m131 49 49-21 49 21-49 21-49-21Zm19 7 60 0v7c0 9-60 9-60 0v-7Zm76-7h5v19h-5z"></path>
              </svg>
            </article>
          </div>
        </div>
      </div>
    </section>

  <div class="wrap"><div class="divider"><i aria-hidden="true"></i><span>算法库</span><i aria-hidden="true"></i></div></div>

    <section class="section" id="library" aria-labelledby="library-title">
      <div class="wrap">
        <h2 class="section-title" id="library-title">庞大的算法库</h2>
        <p class="section-lede">CausalAgent 判断该用哪种方法，靠的是团队多年积累的因果算法。这些工作大部分已经开源。</p>
        <dl class="stats" data-reveal-stagger="">
          <div class="stat od-stat"><dt>公开仓库</dt><dd><span data-count="59">59</span> <span class="od-nowrap">个</span></dd></div>
          <div class="stat od-stat"><dt>首次公开</dt><dd>2016 <span class="od-nowrap">年</span></dd></div>
          <div class="stat od-stat"><dt>覆盖方向</dt><dd><span data-count="6">6</span> <span class="od-nowrap">类</span></dd></div>
        </dl>
        <div class="bento algo-bento" data-reveal-stagger="">
          <article class="bcard algo-card">
            <h3>CDMIR 因果分析工具箱</h3>
            <p>一个按用途拆分的 Python 包，从寻找结构一路做到估计效应。</p>
            <ul class="algo-list">
              <li><a class="algo-item" href="https://github.com/DMIRLAB-Group/CDMIR/tree/development/cdmir/discovery" target="_blank" rel="noopener"><span class="algo-name">cdmir.discovery</span><span class="algo-note">约束方法与函数式方法</span><span class="sr-only">（在新窗口打开）</span></a></li>
              <li><a class="algo-item" href="https://github.com/DMIRLAB-Group/CDMIR/tree/development/cdmir/effect" target="_blank" rel="noopener"><span class="algo-name">cdmir.effect</span><span class="algo-note">平均处理效应与反事实估计</span><span class="sr-only">（在新窗口打开）</span></a></li>
              <li><a class="algo-item" href="https://github.com/DMIRLAB-Group/CDMIR/tree/development/cdmir/graph" target="_blank" rel="noopener"><span class="algo-name">cdmir.graph</span><span class="algo-note">因果图与部分有向图操作</span><span class="sr-only">（在新窗口打开）</span></a></li>
              <li><a class="algo-item" href="https://github.com/DMIRLAB-Group/CDMIR/tree/development/cdmir/utils" target="_blank" rel="noopener"><span class="algo-name">cdmir.utils</span><span class="algo-note">独立性检验与局部评分</span><span class="sr-only">（在新窗口打开）</span></a></li>
              <li><a class="algo-item" href="https://github.com/DMIRLAB-Group/CDMIR/tree/development/cdmir/datasets" target="_blank" rel="noopener"><span class="algo-name">cdmir.datasets</span><span class="algo-note">仿真数据与基准数据生成</span><span class="sr-only">（在新窗口打开）</span></a></li>
            </ul>
          </article>
          <article class="bcard algo-card">
            <h3>因果发现的基础方法</h3>
            <p>从结构方程到加性噪声，一批被反复引用的基础算法。</p>
            <ul class="algo-list">
              <li><a class="algo-item" href="https://github.com/DMIRLAB-Group/SELF" target="_blank" rel="noopener"><span class="algo-name">SELF</span><span class="algo-note">AAAI 2018</span><span class="sr-only">（在新窗口打开）</span></a></li>
              <li><a class="algo-item" href="https://github.com/DMIRLAB-Group/CANM" target="_blank" rel="noopener"><span class="algo-name">CANM</span><span class="algo-note">IJCAI 2019</span><span class="sr-only">（在新窗口打开）</span></a></li>
              <li><a class="algo-item" href="https://github.com/DMIRLAB-Group/PBSCM" target="_blank" rel="noopener"><span class="algo-name">PBSCM</span><span class="algo-note">AAAI 2024</span><span class="sr-only">（在新窗口打开）</span></a></li>
              <li><a class="algo-item" href="https://github.com/DMIRLAB-Group/PBSCM-PGF" target="_blank" rel="noopener"><span class="algo-name">PBSCM-PGF</span><span class="algo-note">NeurIPS 2024</span><span class="sr-only">（在新窗口打开）</span></a></li>
            </ul>
          </article>
          <article class="bcard algo-card">
            <h3>结构模型与基础模型</h3>
            <p>把可识别性的结论推进到更复杂的结构，也推进到预训练范式。</p>
            <ul class="algo-list">
              <li><a class="algo-item" href="https://github.com/DMIRLAB-Group/LC-PB-SCM" target="_blank" rel="noopener"><span class="algo-name">LC-PB-SCM</span><span class="algo-note">ICML 2026</span><span class="sr-only">（在新窗口打开）</span></a></li>
              <li><a class="algo-item" href="https://github.com/DMIRLAB-Group/SHP" target="_blank" rel="noopener"><span class="algo-name">SHP</span><span class="algo-note">IJCAI 2023</span><span class="sr-only">（在新窗口打开）</span></a></li>
              <li><a class="algo-item" href="https://github.com/DMIRLAB-Group/CDFM" target="_blank" rel="noopener"><span class="algo-name">CDFM</span><span class="algo-note">arXiv 2607.11508</span><span class="sr-only">（在新窗口打开）</span></a></li>
            </ul>
          </article>
          <article class="bcard algo-card">
            <h3>因果表征与识别</h3>
            <p>从观测数据里恢复潜变量与时间结构。</p>
            <ul class="algo-list">
              <li><a class="algo-item" href="https://github.com/DMIRLAB-Group/IDOL" target="_blank" rel="noopener"><span class="algo-name">IDOL</span><span class="algo-note">ICLR 2025</span><span class="sr-only">（在新窗口打开）</span></a></li>
              <li><a class="algo-item" href="https://github.com/DMIRLAB-Group/LSTD" target="_blank" rel="noopener"><span class="algo-name">LSTD</span><span class="algo-note">AAAI 2025</span><span class="sr-only">（在新窗口打开）</span></a></li>
              <li><a class="algo-item" href="https://github.com/DMIRLAB-Group/LCA" target="_blank" rel="noopener"><span class="algo-name">LCA</span><span class="algo-note">隐因果对齐</span><span class="sr-only">（在新窗口打开）</span></a></li>
            </ul>
          </article>
          <article class="bcard algo-card">
            <h3>时序预测与领域适应</h3>
            <p>把因果结构带进时间序列与跨域预测。</p>
            <ul class="algo-list">
              <li><a class="algo-item" href="https://github.com/DMIRLAB-Group/GCA" target="_blank" rel="noopener"><span class="algo-name">GCA</span><span class="algo-note">因果条件漂移下的时序预测</span><span class="sr-only">（在新窗口打开）</span></a></li>
              <li><a class="algo-item" href="https://github.com/DMIRLAB-Group/SASA" target="_blank" rel="noopener"><span class="algo-name">SASA</span><span class="algo-note">稀疏关联结构对齐</span><span class="sr-only">（在新窗口打开）</span></a></li>
              <li><a class="algo-item" href="https://github.com/DMIRLAB-Group/DSR" target="_blank" rel="noopener"><span class="algo-name">DSR</span><span class="algo-note">IJCAI 2019</span><span class="sr-only">（在新窗口打开）</span></a></li>
            </ul>
          </article>
          <article class="bcard algo-card">
            <h3>大模型与因果推理</h3>
            <p>让大模型在决策与识别任务里用上因果结构。</p>
            <ul class="algo-list">
              <li><a class="algo-item" href="https://github.com/DMIRLAB-Group/CausalAgent" target="_blank" rel="noopener"><span class="algo-name">CausalAgent</span><span class="algo-note">IUI 2026</span><span class="sr-only">（在新窗口打开）</span></a></li>
              <li><a class="algo-item" href="https://github.com/DMIRLAB-Group/Dr.ECI" target="_blank" rel="noopener"><span class="algo-name">Dr.ECI</span><span class="algo-note">COLING 2025</span><span class="sr-only">（在新窗口打开）</span></a></li>
              <li><a class="algo-item" href="https://github.com/DMIRLAB-Group/Causal-aware_LLMs" target="_blank" rel="noopener"><span class="algo-name">Causal-aware LLMs</span><span class="algo-note">IJCAI 2025</span><span class="sr-only">（在新窗口打开）</span></a></li>
              <li><a class="algo-item" href="https://github.com/DMIRLAB-Group/HCKT" target="_blank" rel="noopener"><span class="algo-name">HCKT</span><span class="algo-note">ICDM 2026</span><span class="sr-only">（在新窗口打开）</span></a></li>
              <li><a class="algo-item" href="https://github.com/DMIRLAB-Group/TSDR" target="_blank" rel="noopener"><span class="algo-name">TSDR</span><span class="algo-note">IJCAI 2026</span><span class="sr-only">（在新窗口打开）</span></a></li>
            </ul>
          </article>
        </div>
        <div class="algo-foot">
          <p class="algo-foot-note">会议与期刊信息取自各仓库自己的说明；其余仓库按方向归类，不在这里列出具体会议。</p>
          <ul class="algo-links">
            <li><a href="https://github.com/DMIRLAB-Group/" target="_blank" rel="noopener">GitHub 组织页<span class="sr-only">（在新窗口打开）</span></a></li>
            <li><a href="https://github.com/DMIRLAB-Group/CDMIR" target="_blank" rel="noopener">CDMIR 仓库<span class="sr-only">（在新窗口打开）</span></a></li>
            <li><a href="https://dmir.gdut.edu.cn/" target="_blank" rel="noopener">DMIR 实验室主页<span class="sr-only">（在新窗口打开）</span></a></li>
          </ul>
        </div>
      </div>
    </section>

  <div class="wrap"><div class="divider"><i aria-hidden="true"></i><span>隐私与管理</span><i aria-hidden="true"></i></div></div>

    <section class="section" id="privacy" aria-labelledby="privacy-title">
      <div class="wrap">
        <div class="privacy-layout">
          <div class="privacy-copy" data-reveal="">
            <p class="privacy-eyebrow">数据使用 · 自主管理</p>
            <h2 class="section-title" id="privacy-title">隐私、保护与RAG管理</h2>
            <p class="section-lede">专业，安全的流程管理，智能化的RAG测评更新策略</p>
          </div>

          <div class="privacy-carousel" id="privacy-carousel" role="region" aria-roledescription="轮播" aria-label="管理端、RAG 端和日志端产品截图" tabindex="0" data-reveal="">
            <p class="privacy-current-page" id="privacy-current-page" role="status" aria-live="polite" aria-atomic="true">1 / 3</p>
            <div class="privacy-viewport" id="privacy-viewport">
              <div class="privacy-track" id="privacy-track">
                <article class="privacy-slide" role="group" aria-roledescription="幻灯片" aria-label="1 / 3：管理端" aria-hidden="false">
                  <img class="privacy-screen" :src="'/site-assets/media/admin-console.png'" width="1600" height="1000" alt="CausalAgent 管理端业务概览界面，展示用户、会话、消息、文件与分析任务的汇总，以及数据库共享监控快照。" />
                  <div class="privacy-slide-copy">
                    <p class="privacy-slide-kicker">管理端</p>
                    <h3 class="privacy-slide-title">按团队职责自定义管理</h3>
                    <p class="privacy-slide-description">通过管理端梳理成员与访问范围，让管理方式贴合团队的协作流程。</p>
                  </div>
                </article>
                <article class="privacy-slide" role="group" aria-roledescription="幻灯片" aria-label="2 / 3：RAG 端" aria-hidden="true">
                  <img class="privacy-screen" :src="'/site-assets/media/rag-eval-console.png'" width="1600" height="1000" alt="CausalAgent RAG 测评台评测配置界面，展示检索参数、当前评测配置与正式配置。" />
                  <div class="privacy-slide-copy">
                    <p class="privacy-slide-kicker">RAG 端</p>
                    <h3 class="privacy-slide-title">让知识来源清晰可管</h3>
                    <p class="privacy-slide-description">集中呈现知识内容与来源，便于团队维护专业资料和检索范围。</p>
                  </div>
                </article>
                <article class="privacy-slide" role="group" aria-roledescription="幻灯片" aria-label="3 / 3：日志端" aria-hidden="true">
                  <img class="privacy-screen" :src="'/site-assets/media/observability-logs.png'" width="1600" height="1000" alt="CausalAgent 日志仪表盘，展示异常总量、级别趋势、按服务与分类的分布以及最近的异常日志。" />
                  <div class="privacy-slide-copy">
                    <p class="privacy-slide-kicker">日志端</p>
                    <h3 class="privacy-slide-title">保留过程，方便回看</h3>
                    <p class="privacy-slide-description">通过操作记录回看管理过程，为追溯配置变化与处理问题提供线索。</p>
                  </div>
                </article>
              </div>
            </div>
            <div class="privacy-carousel-controls">
              <button class="privacy-arrow" type="button" data-privacy-prev="" aria-label="查看上一页">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 18 9 12l6-6"></path></svg>
              </button>
              <div class="privacy-pagination" id="privacy-pagination" role="group" aria-label="选择产品截图页面">
                <button class="privacy-dot" type="button" aria-label="查看管理端截图" aria-pressed="true"><span aria-hidden="true"></span></button>
                <button class="privacy-dot" type="button" aria-label="查看 RAG 端截图" aria-pressed="false"><span aria-hidden="true"></span></button>
                <button class="privacy-dot" type="button" aria-label="查看日志端截图" aria-pressed="false"><span aria-hidden="true"></span></button>
              </div>
              <button class="privacy-arrow" type="button" data-privacy-next="" aria-label="查看下一页">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 18 6-6-6-6"></path></svg>
              </button>
            </div>
          </div>
        </div>
      </div>
    </section>

  <div class="wrap"><div class="divider"><i aria-hidden="true"></i><span>无限进步</span><i aria-hidden="true"></i></div></div>

    <section class="section improvement" id="improvement" aria-labelledby="improvement-title">
      <div class="wrap">
        <h2 class="section-title" id="improvement-title">无限进步</h2>
        <p class="section-lede">两周内从因果分析架构到检索配置持续迭代。</p>

        <article class="improvement-block" aria-labelledby="improvement-causal-title">
          <div class="improvement-heading">
            <svg class="improvement-icon" viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path class="improvement-icon-flow" d="M17 32h11m8 0h11M32 17v11m0 8v11M21 21l8 8m6 6 8 8m0-22-8 8m-6 6-8 8"></path>
              <circle class="improvement-icon-pulse" cx="32" cy="32" r="7"></circle>
              <circle cx="17" cy="17" r="3.5"></circle><circle class="improvement-icon-pulse improvement-icon-pulse--late" cx="47" cy="17" r="3.5"></circle>
              <circle class="improvement-icon-pulse improvement-icon-pulse--late" cx="17" cy="47" r="3.5"></circle><circle cx="47" cy="47" r="3.5"></circle>
            </svg>
            <div>
              <p class="improvement-kicker">两周内 · 架构重构</p>
              <h3 id="improvement-causal-title">CausalAgent</h3>
              <p class="improvement-summary">完成新架构重构后，Precision 与 Recall 同步提升。</p>
            </div>
          </div>

          <div class="improvement-metrics">
            <div class="improvement-metric" aria-labelledby="precision-title">
              <h4 id="precision-title">Precision</h4>
              <div class="improvement-compare" aria-label="旧框架 0.776，新框架 0.843">
                <div class="od-stat"><span class="improvement-stat-label">旧框架</span><span class="improvement-stat-value">0.776</span></div>
                <svg class="improvement-compare-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12h15m-6-6 6 6-6 6"></path></svg>
                <div class="od-stat"><span class="improvement-stat-label">新框架</span><span class="improvement-stat-value">0.843</span></div>
              </div>
              <p class="improvement-gain">提升 <span class="od-nowrap">6.68 个百分点</span><br>相对约 8.6%</p>
            </div>

            <div class="improvement-metric" aria-labelledby="recall-title">
              <h4 id="recall-title">Recall</h4>
              <div class="improvement-compare" aria-label="旧框架 0.749，新框架 0.849">
                <div class="od-stat"><span class="improvement-stat-label">旧框架</span><span class="improvement-stat-value">0.749</span></div>
                <svg class="improvement-compare-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12h15m-6-6 6 6-6 6"></path></svg>
                <div class="od-stat"><span class="improvement-stat-label">新框架</span><span class="improvement-stat-value">0.849</span></div>
              </div>
              <p class="improvement-gain">提升 <span class="od-nowrap">10.00 个百分点</span><br>相对约 13.3%</p>
            </div>
          </div>
        </article>

        <div class="improvement-rag">
          <figure class="improvement-rag-chart" aria-labelledby="improvement-rag-chart-title">
            <figcaption class="improvement-rag-head">
              <div>
                <p class="improvement-kicker">两周内 · RAG 检索优化</p>
                <h3 id="improvement-rag-chart-title" style="font-size: 27px;">让证据更充分，也让回答更可靠</h3>
              </div>
              <p class="improvement-rag-note">每轮实验同时观察召回、上下文噪声与回答可靠性。</p>
            </figcaption>

            <div class="improvement-chart-row" aria-labelledby="context-recall-title">
              <div class="improvement-chart-metric">
                <div class="improvement-rag-label">
                  <svg class="improvement-icon" viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M20 13h24v34H20zM25 22h14M25 29h14M25 36h8"></path>
                    <path class="improvement-icon-flow" d="M13 20v31h24"></path>
                    <circle class="improvement-icon-pulse" cx="44" cy="43" r="4"></circle>
                  </svg>
                  <div><h4 id="context-recall-title">关键证据覆盖</h4><p>Context Recall</p></div>
                </div>
                <div class="od-stat improvement-rag-delta"><strong data-count="26.6" data-count-decimals="1" data-count-prefix="+" data-count-suffix="%">+26.6%</strong><span>相对提升</span></div>
              </div>
              <div class="improvement-chart-visual">
                <svg class="improvement-chart-line" viewBox="0 0 600 80" preserveAspectRatio="none" aria-hidden="true">
                  <path class="improvement-chart-guide" d="M0 12H600M0 40H600M0 68H600"></path>
                  <path class="improvement-chart-path" pathLength="1" d="M100 66L300 46L500 16"></path>
                  <circle class="improvement-chart-node" cx="100" cy="66" r="4" data-path-progress="0"></circle><circle class="improvement-chart-node" cx="300" cy="46" r="4" data-path-progress="0.49846247"></circle><circle class="improvement-chart-node" cx="500" cy="16" r="4" data-path-progress="1"></circle>
                </svg>
                <ol class="improvement-chart-points" aria-label="Context Recall 阶段数据与配置">
                  <li class="improvement-chart-point"><span class="improvement-chart-value" data-count="0.4287" data-count-decimals="4">0.4287</span><span class="improvement-chart-stage">Baseline</span><div class="improvement-chart-config od-stack" style="--od-gap: var(--spacing-4);"><span>TopK = 4</span></div></li>
                  <li class="improvement-chart-point"><span class="improvement-chart-value" data-count="0.4777" data-count-decimals="4">0.4777</span><span class="improvement-chart-stage">Context 数量控制实验</span><div class="improvement-chart-config od-stack" style="--od-gap: var(--spacing-4);"><span>Answer Context = 4</span><span>Ragas Context = 4</span></div></li>
                  <li class="improvement-chart-point"><span class="improvement-chart-value" data-count="0.5427" data-count-decimals="4">0.5427</span><span class="improvement-chart-stage">最终 Top6</span><div class="improvement-chart-config od-stack" style="--od-gap: var(--spacing-4);"><span>Answer Context = 6</span><span>无额外压缩</span></div></li>
                </ol>
              </div>
            </div>

            <div class="improvement-chart-row" aria-labelledby="hit-rate-title">
              <div class="improvement-chart-metric">
                <div class="improvement-rag-label">
                  <svg class="improvement-icon" viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <circle cx="32" cy="32" r="22"></circle><circle cx="32" cy="32" r="14"></circle><circle cx="32" cy="32" r="6"></circle>
                    <g class="improvement-icon-orbit"><circle cx="32" cy="10" r="3.5" fill="currentColor" stroke="none"></circle></g>
                    <path d="M32 32 45 22"></path>
                  </svg>
                  <div><h4 id="hit-rate-title">检索命中率</h4><p>Hit Rate</p></div>
                </div>
                <div class="od-stat improvement-rag-delta"><strong data-count="10.3" data-count-decimals="1" data-count-prefix="+" data-count-suffix="%">+10.3%</strong><span>相对提升</span></div>
              </div>
              <div class="improvement-chart-visual">
                <svg class="improvement-chart-line" viewBox="0 0 600 80" preserveAspectRatio="none" aria-hidden="true">
                  <path class="improvement-chart-guide" d="M0 12H600M0 40H600M0 68H600"></path>
                  <path class="improvement-chart-path" pathLength="1" d="M100 66L300 57L500 16"></path>
                  <circle class="improvement-chart-node" cx="100" cy="66" r="4" data-path-progress="0"></circle><circle class="improvement-chart-node" cx="300" cy="57" r="4" data-path-progress="0.49510728"></circle><circle class="improvement-chart-node" cx="500" cy="16" r="4" data-path-progress="1"></circle>
                </svg>
                <ol class="improvement-chart-points" aria-label="Hit Rate 阶段数据与配置">
                  <li class="improvement-chart-point"><span class="improvement-chart-value" data-count="67.50" data-count-decimals="2" data-count-suffix="%">67.50%</span><span class="improvement-chart-stage">Baseline</span><div class="improvement-chart-config od-stack" style="--od-gap: var(--spacing-4);"><span>Sparse Fetch = 8</span><span>TopK = 4</span></div></li>
                  <li class="improvement-chart-point"><span class="improvement-chart-value" data-count="68.89" data-count-decimals="2" data-count-suffix="%">68.89%</span><span class="improvement-chart-stage">Top5 实验</span><div class="improvement-chart-config od-stack" style="--od-gap: var(--spacing-4);"><span>Sparse Fetch = 16</span><span>MMR = 0.6</span><span>TopK = 5</span></div></li>
                  <li class="improvement-chart-point"><span class="improvement-chart-value" data-count="74.44" data-count-decimals="2" data-count-suffix="%">74.44%</span><span class="improvement-chart-stage">最终 Top6</span><div class="improvement-chart-config od-stack" style="--od-gap: var(--spacing-4);"><span>Sparse Fetch = 16</span><span>MMR = 0.6</span><span>TopK = 6</span></div></li>
                </ol>
              </div>
            </div>
          </figure>
        </div>

        <aside class="improvement-principle" aria-label="优化原则">
          <p>整体优化并非单纯扩大检索数量，而是在<strong>召回能力、上下文噪声与回答可靠性</strong>之间反复验证并寻找平衡，使检索质量的提升能够真正传递到最终回答效果。</p>
        </aside>
      </div>
    </section>

  <div class="wrap"><div class="divider"><i aria-hidden="true"></i><span>研究成果</span><i aria-hidden="true"></i></div></div>

    <section class="section research" id="research" aria-labelledby="research-title">
      <div class="wrap">
        <h2 class="section-title" id="research-title">研究成果</h2>
        <p class="section-lede">从对话式因果推断、通用因果发现，到隐藏混杂因素的迭代验证。</p>
        <div class="research-carousel" id="research-carousel" role="region" aria-roledescription="轮播" aria-label="研究成果论文" tabindex="0" data-reveal="">
          <p class="research-current-page" id="research-current-page" role="status" aria-live="polite" aria-atomic="true">第 1 篇 / 共 3 篇 · CausalAgent</p>
          <div class="research-viewport" id="research-viewport">
            <div class="research-track" id="research-track">
              <article class="research-entry" role="group" aria-roledescription="幻灯片" aria-label="1 / 3：CausalAgent" aria-hidden="false">
                <header class="research-entry-head od-stack" style="--od-gap: var(--spacing-8);">
                  <p class="research-kicker">IUI Companion ’26</p>
                  <h3 class="research-title">CausalAgent: A Conversational Multi-Agent System for End-to-End Causal Inference</h3>
                  <p class="research-copy">以对话式多智能体串联数据处理、因果结构学习与报告生成，呈现端到端因果推断流程。</p>
                </header>
                <ol class="research-pages od-grid" aria-label="CausalAgent 论文原文页">
                  <li><a class="research-page-link" href="/site-assets/PixPin_2026-09-23_15-45-18.png" target="_blank" rel="noopener" aria-label="查看 CausalAgent 论文第 1 页原图"><img class="od-media" :src="'/site-assets/PixPin_2026-09-23_15-45-18.png'" width="816" height="1062" alt="CausalAgent 论文标题页" loading="lazy" decoding="async" draggable="false"><span class="research-page-number" aria-hidden="true">第 1 页</span></a></li>
                  <li><a class="research-page-link" href="/site-assets/PixPin_2026-09-23_15-45-32.png" target="_blank" rel="noopener" aria-label="查看 CausalAgent 论文第 2 页原图"><img class="od-media" :src="'/site-assets/PixPin_2026-09-23_15-45-32.png'" width="816" height="1063" alt="CausalAgent 论文原文第 2 页" loading="lazy" decoding="async" draggable="false"><span class="research-page-number" aria-hidden="true">第 2 页</span></a></li>
                  <li><a class="research-page-link" href="/site-assets/PixPin_2026-09-23_15-45-44.png" target="_blank" rel="noopener" aria-label="查看 CausalAgent 论文第 3 页原图"><img class="od-media" :src="'/site-assets/PixPin_2026-09-23_15-45-44.png'" width="814" height="1057" alt="CausalAgent 论文原文第 3 页" loading="lazy" decoding="async" draggable="false"><span class="research-page-number" aria-hidden="true">第 3 页</span></a></li>
                </ol>
                <a class="research-source" href="https://doi.org/10.1145/3742420.3749777" target="_blank" rel="noopener noreferrer">查看 IUI 论文全文</a>
              </article>

              <article class="research-entry" role="group" aria-roledescription="幻灯片" aria-label="2 / 3：CDFM" aria-hidden="true">
                <header class="research-entry-head od-stack" style="--od-gap: var(--spacing-8);">
                  <p class="research-kicker">arXiv:2607.11508</p>
                  <h3 class="research-title">CDFM: Towards a General-Purpose Causal Discovery Foundation Model</h3>
                  <p class="research-copy">以多样化合成结构因果模型开展预训练，探索在未知因果机制下进行零样本结构推断。</p>
                </header>
                <ol class="research-pages od-grid" aria-label="CDFM 论文原文页">
                  <li><a class="research-page-link" href="/site-assets/assets/research-cdfm-2607.11508/cdfm-01.png" target="_blank" rel="noopener" aria-label="查看 CDFM 论文第 1 页原图"><img class="od-media" :src="'/site-assets/assets/research-cdfm-2607.11508/cdfm-01.png'" width="1020" height="1320" alt="CDFM 论文标题与摘要页" loading="lazy" decoding="async" draggable="false"><span class="research-page-number" aria-hidden="true">第 1 页</span></a></li>
                  <li><a class="research-page-link" href="/site-assets/assets/research-cdfm-2607.11508/cdfm-02.png" target="_blank" rel="noopener" aria-label="查看 CDFM 论文第 2 页原图"><img class="od-media" :src="'/site-assets/assets/research-cdfm-2607.11508/cdfm-02.png'" width="1020" height="1320" alt="CDFM 论文原文第 2 页" loading="lazy" decoding="async" draggable="false"><span class="research-page-number" aria-hidden="true">第 2 页</span></a></li>
                  <li><a class="research-page-link" href="/site-assets/assets/research-cdfm-2607.11508/cdfm-03.png" target="_blank" rel="noopener" aria-label="查看 CDFM 论文第 3 页原图"><img class="od-media" :src="'/site-assets/assets/research-cdfm-2607.11508/cdfm-03.png'" width="1020" height="1320" alt="CDFM 论文原文第 3 页" loading="lazy" decoding="async" draggable="false"><span class="research-page-number" aria-hidden="true">第 3 页</span></a></li>
                </ol>
                <a class="research-source" href="https://arxiv.org/abs/2607.11508" target="_blank" rel="noopener noreferrer">查看 arXiv 原文</a>
              </article>

              <article class="research-entry" role="group" aria-roledescription="幻灯片" aria-label="3 / 3：VIGOR+" aria-hidden="true">
                <header class="research-entry-head od-stack" style="--od-gap: var(--spacing-8);">
                  <p class="research-kicker">arXiv:2512.19349 · 2025.12.22</p>
                  <h3 class="research-title">VIGOR+: Iterative Confounder Generation and Validation via LLM-CEVAE Feedback Loop</h3>
                  <p class="research-copy">将大语言模型生成的候选混杂因素与 CEVAE 统计验证连接成迭代反馈流程，并据此逐轮改进候选因素。</p>
                </header>
                <ol class="research-pages od-grid" aria-label="VIGOR+ 论文原文页">
                  <li><a class="research-page-link" href="/site-assets/assets/research-vigor-2512.19349/vigor-01-title.png" target="_blank" rel="noopener" aria-label="查看 VIGOR+ 论文标题页原图"><img class="od-media" :src="'/site-assets/assets/research-vigor-2512.19349/vigor-01-title.png'" width="791" height="1010" alt="VIGOR+ 论文标题与摘要页" loading="lazy" decoding="async" draggable="false"><span class="research-page-number" aria-hidden="true">标题页</span></a></li>
                  <li><a class="research-page-link" href="/site-assets/assets/research-vigor-2512.19349/vigor-02.png" target="_blank" rel="noopener" aria-label="查看 VIGOR+ 论文原文页"><img class="od-media" :src="'/site-assets/assets/research-vigor-2512.19349/vigor-02.png'" width="791" height="1022" alt="VIGOR+ 论文方法与模型结构页" loading="lazy" decoding="async" draggable="false"><span class="research-page-number" aria-hidden="true">原文页</span></a></li>
                  <li><a class="research-page-link" href="/site-assets/assets/research-vigor-2512.19349/vigor-03.png" target="_blank" rel="noopener" aria-label="查看 VIGOR+ 论文原文页"><img class="od-media" :src="'/site-assets/assets/research-vigor-2512.19349/vigor-03.png'" width="789" height="1019" alt="VIGOR+ 论文迭代反馈与实验页" loading="lazy" decoding="async" draggable="false"><span class="research-page-number" aria-hidden="true">原文页</span></a></li>
                </ol>
                <a class="research-source" href="https://arxiv.org/abs/2512.19349" target="_blank" rel="noopener noreferrer">查看 arXiv 原文</a>
              </article>
            </div>
          </div>
          <div class="research-carousel-controls">
            <button class="research-arrow" type="button" data-research-prev="" aria-label="查看上一篇论文" disabled>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 18 9 12l6-6"></path></svg>
            </button>
            <div class="research-pagination" id="research-pagination" role="group" aria-label="选择研究论文">
              <button class="research-dot" type="button" aria-label="查看 CausalAgent 论文" aria-pressed="true"><span aria-hidden="true"></span></button>
              <button class="research-dot" type="button" aria-label="查看 CDFM 论文" aria-pressed="false"><span aria-hidden="true"></span></button>
              <button class="research-dot" type="button" aria-label="查看 VIGOR+ 论文" aria-pressed="false"><span aria-hidden="true"></span></button>
            </div>
            <button class="research-arrow" type="button" data-research-next="" aria-label="查看下一篇论文">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 18 6-6-6-6"></path></svg>
            </button>
          </div>
        </div>
      </div>
    </section>

  <div class="wrap"><div class="divider"><i aria-hidden="true"></i><span>为什么选择因果推断</span><i aria-hidden="true"></i></div></div>

    <section class="section" id="figures" aria-labelledby="figures-title">
      <div class="wrap">
        <h2 class="section-title" id="figures-title">为什么选择因果推断？</h2>
        <p class="section-lede">相关性只能说明变量一起变化，因果推断进一步回答：真正改变一个变量之后，结果会怎样。</p>
        <div class="figures">

          <figure class="figure" id="fig-dag" data-causal-stage="path">
            <div class="fig-head">
              <h3>01 / 因果图：先看作用路径</h3>
              <span class="tag">解释机制</span>
            </div>
            <div class="dag-layout">
            <div class="plate" data-plate="">
              <div class="plate-inner" data-plate-inner="">
                <div class="fig-scroll fig-scroll--wide" tabindex="0" role="group" aria-label="因果图，窄屏下可以横向滚动">
                <svg class="plot dag" id="dag" viewBox="0 0 720 380" role="group" aria-labelledby="dag-title">
                  <title id="dag-title">变量关系示意图</title>
                  <path class="dag-edge" data-dag-edge="" id="e-schedule-promo" d="M130 80 L380 190"></path>
                  <path class="dag-edge" data-dag-edge="" id="e-size-promo" d="M130 300 L380 190"></path>
                  <path class="dag-edge" data-dag-edge="" id="e-size-visit" d="M130 300 Q 380 356 620 132"></path>
                  <path class="dag-edge dag-edge--main" data-dag-edge="" id="e-promo-visit" d="M380 190 L620 120"></path>
                  <path class="dag-edge dag-edge--main" data-dag-edge="" id="e-promo-repeat" d="M380 190 L620 300"></path>
                  <path class="dag-edge" data-dag-edge="" id="e-visit-repeat" d="M620 140 L620 280"></path>
                  <g class="dag-node" data-node="schedule" data-links="schedule,e-schedule-promo" tabindex="0" role="img" aria-label="门店档期，外生变量，指向促销力度"><circle class="dag-ring" cx="130" cy="80" r="18"></circle><circle class="dag-dot" cx="130" cy="80" r="8"></circle><text class="dag-label" x="152" y="76">门店档期</text><text class="dag-sub" x="152" y="94">外生</text></g>
                  <g class="dag-node" data-node="size" data-links="size,e-size-promo,e-size-visit" tabindex="0" role="img" aria-label="门店规模，混杂变量，同时影响促销力度与到店人数"><circle class="dag-ring" cx="130" cy="300" r="18"></circle><circle class="dag-dot" cx="130" cy="300" r="8"></circle><text class="dag-label" x="152" y="296">门店规模</text><text class="dag-sub" x="152" y="314">混杂</text></g>
                  <g class="dag-node" data-role="treatment" data-node="promo" data-links="promo,e-schedule-promo,e-size-promo,e-promo-visit,e-promo-repeat" tabindex="0" role="img" aria-label="促销力度，处理变量，影响到店人数与复购率"><circle class="dag-ring" cx="380" cy="190" r="20"></circle><circle class="dag-dot" cx="380" cy="190" r="10"></circle><text class="dag-label" x="380" y="228" text-anchor="middle">促销力度</text><text class="dag-sub" x="380" y="246" text-anchor="middle">处理变量</text></g>
                  <g class="dag-node" data-node="visit" data-links="visit,e-size-visit,e-promo-visit,e-visit-repeat" tabindex="0" role="img" aria-label="到店人数，结果变量，进一步影响复购率"><circle class="dag-ring" cx="620" cy="120" r="18"></circle><circle class="dag-dot" cx="620" cy="120" r="8"></circle><text class="dag-label" x="600" y="88" text-anchor="middle">到店人数</text><text class="dag-sub" x="600" y="106" text-anchor="middle">结果</text></g>
                  <g class="dag-node" data-node="repeat" data-links="repeat,e-promo-repeat,e-visit-repeat" tabindex="0" role="img" aria-label="复购率，结果变量"><circle class="dag-ring" cx="620" cy="300" r="18"></circle><circle class="dag-dot" cx="620" cy="300" r="8"></circle><text class="dag-label" x="600" y="340" text-anchor="middle">复购率</text><text class="dag-sub" x="600" y="358" text-anchor="middle">结果</text></g>
                </svg>
                </div>
              </div>
            </div>

            <aside class="dag-side" aria-label="因果图节点说明">
              <p class="dag-side-label">节点说明</p>
              <p class="chart-readout" id="dag-readout" role="status">选择或聚焦任意节点，查看它的变量角色与直接关系。</p>
            </aside>
            </div>
            <figcaption class="fig-cap">实心点表示处理变量，空心点表示其他变量。图形只用于说明结构。</figcaption>
          </figure>

          <ol class="causal-reasons" aria-label="选择因果推断的三点理由">
            <li class="causal-reason">
              <span class="causal-reason-num">02</span>
              <div>
                <h3>回答反事实</h3>
                <p>比较同一个对象在“做了”和“没做”之间的差异，避免把同时发生误认为真正影响。</p>
              </div>
            </li>
            <li class="causal-reason">
              <span class="causal-reason-num">03</span>
              <div>
                <h3>保留复核路径</h3>
                <p>每个结论都能回到变量、方法、区间和来源，读者可以继续检查它为什么成立。</p>
              </div>
            </li>
            <li class="causal-reason">
              <span class="causal-reason-num">04</span>
              <div>
                <h3>检验干预结果</h3>
                <p>用干预前后的趋势和对照关系验证结论，让分析支持下一步行动。</p>
              </div>
            </li>
          </ol>

        </div>
      </div>
    </section>

    <section class="measure" id="measure" aria-labelledby="measure-text">
      <div class="wrap measure-inner">
        <h2 class="measure-text" id="measure-text" data-split="" aria-label="只需要一个简单的描述，帮你从材料、方法推导到详细的结论，每一步都留下可复核、可追溯的依据。" style="transform: translate(-2px, -8px);">只需要一个简单的描述，帮你从材料、方法推导到详细的结论，每一步都留下可复核、可追溯的依据。</h2>
        <div class="measure-rule" aria-hidden="true"></div>
        <div class="measure-foot">
          <p class="measure-sub">CausalAgent——探索世界的尽头</p>
          <div>

            <p style="transform: translate(-3px, -23px);"><a class="ghost" href="/docs">联系我们</a></p>
          </div>
        </div>
      </div>
    </section>

    <div class="wrap"><div class="divider"><i aria-hidden="true"></i><span>专家智囊</span><i aria-hidden="true"></i></div></div>

    <section class="section" id="experts" aria-labelledby="experts-title">
      <div class="wrap">
        <div class="ex-head">
          <h2 class="section-title" id="experts-title">专家智囊</h2>
          <p class="section-lede">多为专家老师为 CausalAgent 的因果方法与大模型应用提供研究支持。</p>
        </div>

        <ol class="ex-list">
          <li class="ex-item">
            <img class="od-media ex-photo" :src="'/site-assets/assets/experts/cai-ruichu.jpg'" width="1544" height="2023" alt="蔡瑞初的公开照片">
            <div class="od-stack ex-body">
              <div class="od-stack ex-ident">
                <h3 class="ex-name">蔡瑞初</h3>
                <p class="ex-role">广东工业大学 国家优青、二级教授</p>
              </div>
              <p class="ex-bio">长期从事因果发现、因果学习与深度学习研究，现任 DMIR 数据挖掘与信息检索实验室主任。</p>
              <p><a class="ex-link" href="https://ruichucai.github.io/" target="_blank" rel="noopener">公开主页<span class="sr-only">（在新窗口打开）</span></a></p>
            </div>
          </li>

          <li class="ex-item">
            <img class="od-media ex-photo" :src="'/site-assets/assets/experts/chen-wei.jpg'" width="354" height="472" alt="陈薇的公开照片">
            <div class="od-stack ex-body">
              <div class="od-stack ex-ident">
                <h3 class="ex-name">陈薇</h3>
                <p class="ex-role">广东工业大学 副教授、硕士生导师</p>
              </div>
              <p class="ex-bio">从事机器学习、因果推断与因果表征学习研究，并将相关方法用于智能运维、神经科学和心理健康。</p>
              <p><a class="ex-link" href="https://chenweidelight.github.io/zh-cn/" target="_blank" rel="noopener">公开主页<span class="sr-only">（在新窗口打开）</span></a></p>
            </div>
          </li>

          <li class="ex-item">
            <img class="od-media ex-photo" :src="'/site-assets/assets/experts/xu-boyan.jpg'" width="1024" height="1340" alt="许柏炎的公开照片">
            <div class="od-stack ex-body">
              <div class="od-stack ex-ident">
                <h3 class="ex-name">许柏炎</h3>
                <p class="ex-role">广东工业大学 高级实验师</p>
              </div>
              <p class="ex-bio">从事大语言模型、语义解析与因果性学习研究，关注大模型可信性和 Text-to-SQL 等应用问题。</p>
              <p><a class="ex-link" href="https://boyanxu1.github.io/" target="_blank" rel="noopener">个人主页<span class="sr-only">（在新窗口打开）</span></a></p>
            </div>
          </li>
        </ol>

        <section class="ex-team" aria-labelledby="team-title">
          <div class="ex-team-head">
            <h3 class="ex-team-title" id="team-title">团队成员</h3>
            <p class="ex-team-lede">精益求精，矢志不渝</p>
          </div>
          <ol class="ex-member-list" aria-label="团队成员">
            <li class="ex-member">
              <img class="od-media ex-member-photo" :src="'/site-assets/assets/team/member-01.png'" width="1536" height="1024" alt="团队成员 01">
              <div class="od-stack ex-member-copy">
                <h4 class="ex-member-name">朱家纬</h4>
                <p class="ex-member-bio">团队负责人，负责整体架构设计与演进，重点研究大语言模型、智能体系统与 Harness 工程。</p>
              </div>
            </li>
            <li class="ex-member">
              <img class="od-media ex-member-photo" :src="'/site-assets/assets/team/member-02.png'" width="1536" height="1024" alt="团队成员 02">
              <div class="od-stack ex-member-copy">
                <h4 class="ex-member-name">蔡乔夕</h4>
                <p class="ex-member-bio">负责 RAG 系统迭代与知识库自进化，重点研究智能体架构与 Harness 工程。</p>
              </div>
            </li>
            <li class="ex-member">
              <img class="od-media ex-member-photo" :src="'/site-assets/assets/team/member-03.png'" width="1536" height="1024" alt="团队成员 03">
              <div class="od-stack ex-member-copy">
                <h4 class="ex-member-name">钟江铭</h4>
                <p class="ex-member-bio">负责后端架构与日志系统优化，重点研究智能体系统与因果推断。</p>
              </div>
            </li>
            <li class="ex-member">
              <img class="od-media ex-member-photo" :src="'/site-assets/assets/team/member-04.png'" width="1536" height="1024" alt="团队成员 04">
              <div class="od-stack ex-member-copy">
                <h4 class="ex-member-name">陈婉怡</h4>
                <p class="ex-member-bio">负责智能体架构迭代，重点研究Agent，因果推断与因果架构设计。</p>
              </div>
            </li>
            <li class="ex-member">
              <img class="od-media ex-member-photo" :src="'/site-assets/assets/team/member-05.png'" width="1536" height="1024" alt="团队成员 05">
              <div class="od-stack ex-member-copy">
                <h4 class="ex-member-name">李家乐</h4>
                <p class="ex-member-bio">负责因果算法库维护与更新，重点研究智能体系统、因果推断与量化智能体。</p>
              </div>
            </li>
          </ol>
        </section>
      </div>
    </section>

    <div class="wrap"><div class="divider"><i aria-hidden="true"></i><span>开始使用</span><i aria-hidden="true"></i></div></div>

    <section class="section" id="access" aria-labelledby="access-title">
      <div class="wrap">
        <div class="access-head">
          <h2 id="access-title">开始使用</h2>
          <div class="seg" id="access-seg" role="group" aria-label="选择进入方式">
            <button type="button" data-access="browser" aria-pressed="true">浏览器访问</button>
            <button type="button" data-access="desktop" aria-pressed="false">Windows 桌面客户端</button>
          </div>
        </div>
        <div class="access-pane" id="access-browser">
          <div>
            <h3>浏览器访问</h3>
            <p>无需安装，使用任意现代浏览器打开本站并登录即可；换一台机器时，历史会话与文件随账号一起出现。</p>
            <p><a class="pill" href="/dashboard">进入工作区</a></p>
          </div>
          <ul class="access-list">
            <li>不用安装，登录后直接进入工作区</li>
            <li>历史会话与上传的文件随账号保留</li>
            <li>与桌面端共用同一份执行记录</li>
          </ul>
        </div>
        <div class="access-pane" id="access-desktop" hidden>
          <div>
            <h3>Windows 桌面客户端</h3>
            <p>适合日常使用的独立窗口，登录后直接进入工作区。</p>
            <p class="muted-note">在发布页获取安装包</p>
          </div>
          <ul class="access-list">



          </ul>
        </div>
      </div>
    </section>

    <section class="closing">
      <div class="wrap">
        <h2>先用一次真实的分析任务试试</h2>
        <p class="closing-lede">注册后即可上传数据、运行任务并得到报告。</p>
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
        <p class="foot-brand">面向因果分析场景的智能体工作台，提供可复核的分析流程。</p>
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
