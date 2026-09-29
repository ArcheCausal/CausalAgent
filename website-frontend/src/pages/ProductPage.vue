<script setup lang="ts">
/* 本页结构与交互逐字取自官网原型 product.html 的内联内容。
   每个公开地址都是整页加载，同一个文档只会挂载一个页面，
   因此页面样式按自身作用范围生效，不再额外做作用域限定。 */
import { onMounted } from 'vue'
import { initProduct } from '../scripts/product.js'
import '../styles/pages/product.css'

onMounted(() => {
  initProduct()
})
</script>

<template>
  <a class="skip" href="#main">跳到主要内容</a>

  <svg width="0" height="0" aria-hidden="true" focusable="false" class="icon-defs"><defs>
    <symbol id="ic-close" viewBox="0 0 12 12"><path d="M1 1l10 10M11 1L1 11" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linecap="round"></path></symbol>
    <symbol id="ic-up" viewBox="0 0 14 14"><path d="M7 12V2M3 6l4-4 4 4" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linecap="round" stroke-linejoin="round"></path></symbol>
    <symbol id="ic-out" viewBox="0 0 14 14"><path d="M5 3H3.5v7.5H11V9" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linecap="round" stroke-linejoin="round"></path><path d="M7.5 2.5H11.5V6.5M11.2 2.8L6.6 7.4" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linecap="round" stroke-linejoin="round"></path></symbol>
    <symbol id="ic-check" viewBox="0 0 14 14"><path d="M2.5 7.5 5.5 10.5 11.5 3.5" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linecap="round" stroke-linejoin="round"></path></symbol>
    <symbol id="ic-cube" viewBox="0 0 18 18"><path d="M9 2.2l6 3.2v7.2l-6 3.2-6-3.2V5.4z" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linejoin="round"></path><path d="M3 5.4l6 3.2 6-3.2M9 8.6V16" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linejoin="round"></path></symbol>
    <symbol id="ic-nodes" viewBox="0 0 18 18"><circle cx="9" cy="4.4" r="2.1" fill="none" stroke="currentColor" stroke-width="1.25"></circle><circle cx="4.4" cy="13.4" r="2.1" fill="none" stroke="currentColor" stroke-width="1.25"></circle><circle cx="13.6" cy="13.4" r="2.1" fill="none" stroke="currentColor" stroke-width="1.25"></circle><path d="M9 6.5v2.7M9 9.2l-3.4 2.6M9 9.2l3.4 2.6" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linecap="round"></path></symbol>
    <symbol id="ic-window" viewBox="0 0 18 18"><rect x="2.5" y="3.5" width="13" height="11" rx="2" fill="none" stroke="currentColor" stroke-width="1.25"></rect><path d="M2.5 7h13" fill="none" stroke="currentColor" stroke-width="1.25"></path><circle cx="5" cy="5.2" r="0.7" fill="currentColor"></circle></symbol>
    <symbol id="ic-down" viewBox="0 0 14 14"><path d="M7 2v10M3 8l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linecap="round" stroke-linejoin="round"></path></symbol>
  </defs></svg>

  <div class="progress" id="progress" aria-hidden="true"></div>

  <div class="notice" id="notice">
    <p>CDFM 已公开：通用因果发现基础模型，论文编号 arXiv 2607.11508，权重放在 HuggingFace。</p>
    <a class="pill" href="https://github.com/DMIRLAB-Group/CDFM" target="_blank" rel="noopener">查看仓库</a>
    <button class="notice-close" id="notice-close" type="button" aria-label="关闭这条通告">
      <svg aria-hidden="true"><use href="#ic-close"></use></svg>
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
        <a href="/product" aria-current="page">产品</a>
        <a href="/pricing">价格</a>
        <a href="/docs">文档</a>
        <a href="/changelog">更新日志</a>
        <a href="/about">关于</a>
      </nav>
      <div class="nav-actions">
        <a class="pill" href="/dashboard">打开工作区</a>
      </div>
    </div>
  </header>

  <nav class="subnav" id="subnav" aria-label="本页章节">
    <div class="wrap subnav-inner">
      <a href="#top">产品总览</a>
      <a href="#app">应用</a>
      <a href="#cdfm">CDFM 算法</a>
      <a href="#library">DMIR 算法库</a>
      <a href="#rag-eval">RAG 测评端</a>
      <a href="#admin-console">管理员端</a>
    </div>
  </nav>

  <main id="main">

    <section class="stage" id="top" role="region" aria-labelledby="hero-title">
      <div class="wrap stage-head">
        <div class="stage-head-main">
          <p class="stage-eyebrow">CausalAgent · 产品矩阵</p>
          <h1 class="hero-title" id="hero-title">五个入口，一条因果分析的链路</h1>
        </div>
        <div class="stage-aside">


        </div>
      </div>

      <div class="wrap">
        <div class="stage-viewport" id="stage-viewport">
          <div class="stage-track" id="stage-track">

            <article class="slide is-live" id="slide-app" role="group" aria-label="第 1 件，共 5 件：CausalAgent 应用" tabindex="0">
              <div class="slide-grid">
                <div class="slide-copy">
                  <p class="slide-step">01 / 应用</p>
                  <h2 class="slide-title">CausalAgent</h2>
                  <p class="slide-text">上传数据集，智能体自动选用合适的因果分析算法，生成专业报告。</p>
                  <div class="slide-stats">
                    <div class="stage-stat"><span class="stage-stat-num">4</span><span class="stage-stat-cap">步分析流程</span></div>
                    <div class="stage-stat"><span class="stage-stat-num">42</span><span class="stage-stat-cap">GitHub 星标</span></div>
                  </div>
                  <div class="slide-actions" aria-label="CausalAgent 入口">
                    <a class="slide-link slide-link--primary" href="/">前往官网<svg aria-hidden="true"><use href="#ic-up"></use></svg></a>
                    <a class="slide-link" href="/docs">查看文档<svg aria-hidden="true"><use href="#ic-up"></use></svg></a>
                  </div>
                </div>
                <div class="slide-visual">
                  <figure class="shot">
                    <img class="shot-image" :src="'/site-assets/media/workspace-console.png'" width="1600" height="1000" alt="CausalAgent 工作台：左侧是会话与文件列表，中间是上传数据文件的分析输入框。" loading="lazy" decoding="async" />
                    <figcaption class="slide-cap">CausalAgent 工作台：会话与文件按账号保存，输入框接收数据文件与问题。</figcaption>
                  </figure>
                </div>
              </div>
            </article>

            <article class="slide" id="slide-paper" role="group" aria-label="第 2 件，共 5 件：CDFM 模型" tabindex="-1" aria-hidden="true">
              <div class="slide-grid">
                <div class="slide-copy">
                  <p class="slide-step">02 / 模型</p>
                  <h2 class="slide-title">CDFM</h2>
                  <p class="slide-text">通用因果发现基础模型：输入纯观测数据，一次前向输出整张因果图，同一份权重适用于不同的样本量与变量数。</p>
                  <div class="slide-stats">
                    <div class="stage-stat"><span class="stage-stat-num">+0.074</span><span class="stage-stat-cap">平均 AUROC 领先，15 个机制族</span></div>
                    <div class="stage-stat"><span class="stage-stat-num">63</span><span class="stage-stat-cap">GitHub 星标</span></div>
                  </div>
                  <a class="slide-link" href="#cdfm" tabindex="-1">看 CDFM 的细节与数据<svg aria-hidden="true"><use href="#ic-down"></use></svg></a>
                </div>
                <div class="slide-visual">
                  <figure class="shot">
                    <div class="paper-gallery" role="group" aria-label="CDFM 论文前五页完整原页组成的错层纸堆">
                      <img class="paper paper--last" :src="'/site-assets/assets/product/cdfm-paper-p5.png'" width="1105" height="1430" alt="论文第五页完整原页，包含定理 2、定理 3 与 CDFM 的变分推断框架。">
                      <img class="paper paper--4" :src="'/site-assets/assets/product/cdfm-paper-p4.png'" width="1105" height="1430" alt="论文第四页完整原页，包含背景与预备知识、从可识别性理论到基础模型的范式转变，以及定理 1。">
                      <img class="paper paper--3" :src="'/site-assets/assets/product/cdfm-paper-p3.png'" width="1105" height="1430" alt="论文第三页完整原页。">
                      <img class="paper paper--2" :src="'/site-assets/assets/product/cdfm-paper-p2.png'" width="1105" height="1430" alt="论文第二页完整原页，展示 CDFM 与既有因果发现范式的对比。">
                      <img class="paper paper--front" :src="'/site-assets/assets/product/cdfm-paper-p1.png'" width="1105" height="1430" alt="论文第一页完整原页：CDFM: Towards a General-Purpose Causal Discovery Foundation Model，包含标题、作者、摘要与引言，左侧竖排标注 arXiv:2607.11508v1。">
                    </div>
                    <figcaption class="slide-cap">CDFM: Towards a General-Purpose Causal Discovery Foundation Model，arXiv 2607.11508v1 前 5 页完整原页（共 27 页），2026-07-13。</figcaption>
                  </figure>
                </div>
              </div>
            </article>

            <article class="slide" id="slide-algo" role="group" aria-label="第 3 件，共 5 件：DMIR 算法库" tabindex="-1" aria-hidden="true">
              <div class="slide-grid">
                <div class="slide-copy">
                  <p class="slide-step">03 / 方法</p>
                  <h2 class="slide-title">DMIR 算法库</h2>
                  <p class="slide-text">会议或期刊论文公开仓库：覆盖因果发现、事件序列、文本与推荐。</p>
                  <div class="slide-stats">
                    <div class="stage-stat"><span class="stage-stat-num">59</span><span class="stage-stat-cap">个公开仓库</span></div>
                    <div class="stage-stat"><span class="stage-stat-num">2016</span><span class="stage-stat-cap">年起公开代码</span></div>
                  </div>
                  <a class="slide-link" href="#library" tabindex="-1">看 59 个算法仓库<svg aria-hidden="true"><use href="#ic-down"></use></svg></a>
                </div>
                <div class="slide-visual">
                  <figure class="shot">
                    <div class="frame frame--algo">
                      <svg class="algo" viewBox="0 0 520 320" aria-hidden="true" focusable="false">
                        <defs>
                          <marker id="algo-arrow" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse">
                            <path d="M1.2 1.2 8 5 1.2 8.8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"></path>
                          </marker>
                        </defs>
                        <g class="algo-points">
                          <circle class="pt" style="--n:0" cx="26" cy="96" r="3.2"></circle>
                          <circle class="pt" style="--n:1" cx="44" cy="132" r="3.2"></circle>
                          <circle class="pt" style="--n:2" cx="30" cy="182" r="3.2"></circle>
                          <circle class="pt" style="--n:3" cx="58" cy="72" r="3.2"></circle>
                          <circle class="pt" style="--n:4" cx="62" cy="158" r="3.2"></circle>
                          <circle class="pt" style="--n:5" cx="76" cy="206" r="3.2"></circle>
                          <circle class="pt" style="--n:6" cx="88" cy="120" r="3.2"></circle>
                          <circle class="pt" style="--n:7" cx="96" cy="52" r="3.2"></circle>
                          <circle class="pt" style="--n:8" cx="108" cy="178" r="3.2"></circle>
                          <circle class="pt" style="--n:9" cx="120" cy="96" r="3.2"></circle>
                          <circle class="pt" style="--n:10" cx="126" cy="232" r="3.2"></circle>
                          <circle class="pt" style="--n:11" cx="140" cy="150" r="3.2"></circle>
                          <circle class="pt" style="--n:12" cx="154" cy="84" r="3.2"></circle>
                          <circle class="pt" style="--n:13" cx="166" cy="196" r="3.2"></circle>
                          <circle class="pt" style="--n:14" cx="178" cy="124" r="3.2"></circle>
                          <circle class="pt" style="--n:15" cx="186" cy="58" r="3.2"></circle>
                        </g>
                        <g class="algo-flows" fill="none" stroke-width="1.2" stroke-linecap="round">
                          <path class="flow" style="--n:0" d="M204 112 C240 112 250 100 282 98"></path>
                          <path class="flow" style="--n:1" d="M204 152 C244 152 252 152 282 156"></path>
                          <path class="flow" style="--n:2" d="M204 192 C240 192 250 206 282 214"></path>
                        </g>
                        <g class="algo-edges" fill="none" stroke-width="1.4" marker-end="url(#algo-arrow)">
                          <path class="edge" style="--n:0" d="M345 81 C370 76 394 71 419 67"></path>
                          <path class="edge" style="--n:1" d="M342 93 C366 112 390 131 414 148"></path>
                          <path class="edge" style="--n:2" d="M435 79 C433 100 431 120 429 141"></path>
                          <path class="edge" style="--n:3" d="M333 209 C359 195 386 180 413 166"></path>
                          <path class="edge" style="--n:4" d="M434 172 C443 194 454 216 463 238"></path>
                        </g>
                        <path class="edge-alt" style="--n:5" fill="none" stroke-width="1.2" stroke-linecap="round" d="M334 99 C342 140 332 170 322 201"></path>
                        <g class="algo-nodes">
                          <circle class="node" style="--n:0" cx="330" cy="84" r="14"></circle>
                          <circle class="node" style="--n:1" cx="436" cy="64" r="14"></circle>
                          <circle class="node" style="--n:2" cx="320" cy="216" r="14"></circle>
                          <circle class="node" style="--n:3" cx="428" cy="158" r="14"></circle>
                          <circle class="node" style="--n:4" cx="470" cy="254" r="14"></circle>
                        </g>
                        <g class="algo-labels">
                          <text class="node-label" style="--n:0" x="330" y="84">X1</text>
                          <text class="node-label" style="--n:1" x="436" y="64">X2</text>
                          <text class="node-label" style="--n:2" x="320" y="216">X3</text>
                          <text class="node-label" style="--n:3" x="428" y="158">X4</text>
                          <text class="node-label" style="--n:4" x="470" y="254">X5</text>
                        </g>
                      </svg>
                    </div>
                    <figcaption class="slide-cap">示意图：从观测数据推断变量之间的方向，算法库里的因果发现方法多在解这一步，虚线表示方向还没定下来的边。</figcaption>
                  </figure>
                </div>
              </div>
            </article>

            <article class="slide" id="slide-rag" role="group" aria-label="第 4 件，共 5 件：RAG 测评端" tabindex="-1" aria-hidden="true">
              <div class="slide-grid">
                <div class="slide-copy">
                  <p class="slide-step">04 / 工作台</p>
                  <h2 class="slide-title">RAG 测评端</h2>
                  <p class="slide-text">自定义专业知识库，个性化知识内容，定制化RAG流程管理</p>
                  <a class="slide-link" href="#rag-eval" tabindex="-1">看 RAG 测评端<svg aria-hidden="true"><use href="#ic-down"></use></svg></a>
                </div>
                <div class="slide-visual">
                  <figure class="shot">
                    <img class="shot-image" :src="'/site-assets/media/rag-eval-console.png'" width="1600" height="1000" alt="RAG 测评台评测配置：策略 profile、检索参数与正式配置。" loading="lazy" decoding="async" />
                    <figcaption class="slide-cap">RAG 测评台评测中心：按索引与题集配置检索参数，产出可对比的评测报告。</figcaption>
                  </figure>
                </div>
              </div>
            </article>

            <article class="slide" id="slide-admin" role="group" aria-label="第 5 件，共 5 件：管理员端" tabindex="-1" aria-hidden="true">
              <div class="slide-grid">
                <div class="slide-copy">
                  <p class="slide-step">05 / 管理</p>
                  <h2 class="slide-title">管理员端</h2>
                  <p class="slide-text">集中管理业务对象、数据库状态与审计入口</p>
                  <a class="slide-link" href="#admin-console" tabindex="-1">看管理员端<svg aria-hidden="true"><use href="#ic-down"></use></svg></a>
                </div>
                <div class="slide-visual">
                  <figure class="shot">
                    <img class="shot-image" :src="'/site-assets/media/admin-console.png'" width="1600" height="1000" alt="管理员端业务概览：业务对象汇总与共享监控快照。" loading="lazy" decoding="async" />
                    <figcaption class="slide-cap">管理员端业务概览：业务对象汇总与数据库共享监控快照。</figcaption>
                  </figure>
                </div>
              </div>
            </article>

          </div>
        </div>
      </div>

    </section>

    <section class="section module" id="app" aria-labelledby="app-title">
      <div class="wrap">
        <div class="section-head">
          <span class="tag">应用 · CausalAgent</span>
          <h2 class="section-title" id="app-title">CausalAgent：基于Harness的因果分析智能体</h2>
          <p class="section-lede">上传数据集，智能体自动选用因果分析算法，生成可以追问的专业因果图与结构化报告。</p>
        </div>
        <div class="module-body">
          <div class="module-main">
            <div data-reveal="">
              <p class="module-p">CausalAgent判断当前该做数据预处理、该跑因果分析、该查知识库，还是该写报告，然后把任务分给对应的智能体。</p>

            </div>
            <ul class="keylist" data-reveal="">
              <li>
                <svg aria-hidden="true"><use href="#ic-nodes"></use></svg>
                <div>
                  <span class="key-title">多智能体协作</span>
                  <span class="key-desc">基于 LangGraph，在预处理、因果分析、知识库问答与报告生成之间自动路由。</span>
                </div>
              </li>
              <li>
                <svg aria-hidden="true"><use href="#ic-check"></use></svg>
                <div>
                  <span class="key-title">可交互的专业报告</span>
                  <span class="key-desc">生成的内容包括详专业的知识库与简洁的因果图。</span>
                </div>
              </li>
              <li>
                <svg aria-hidden="true"><use href="#ic-cube"></use></svg>
                <div>
                  <span class="key-title">MCP 算法框架</span>
                  <span class="key-desc">算法与智能体的解耦链接。</span>
                </div>
              </li>
              <li>
                <svg aria-hidden="true"><use href="#ic-window"></use></svg>
                <div>
                  <span class="key-title">RAG 知识库</span>
                  <span class="key-desc">用因果推断领域的书籍与论文做检索增强，给报告的结论补上文献背景。</span>
                </div>
              </li>
            </ul>
            <div data-reveal="">
              <h3 class="card-title">涉及领域</h3>
              <ol class="flow">
                <li>Agent</li>
                <li>因果推断</li>
                <li>Harness</li>
                <li>LLMs</li>
                <li>世界模型</li>
              </ol>
            </div>
            <div class="state-note" data-reveal="">
              <b>当前状态</b>
              <span>项目正在开发中，目前仍然是测试版本。</span>

            </div>
          </div>
          <aside class="aside">
            <div class="card" data-reveal="">
              <h3 class="card-title">项目概况</h3>
              <dl class="meta-list">
                <div><dt>定位</dt><dd>对话式多智能体因果分析系统</dd></div>
                <div><dt>语言</dt><dd>Python 3.11+</dd></div>
                <div><dt>许可</dt><dd>MIT</dd></div>
                <div><dt>星标</dt><dd>42</dd></div>
                <div><dt>最近更新</dt><dd>2026-09-18</dd></div>
                <div><dt>技术栈</dt><dd>LangGraph、LangChain、DeepAgent、Langsmith</dd></div>
                <div><dt>论文</dt><dd>IUI 2026</dd></div>
              </dl>
            </div>
            <div class="card" data-reveal="">
              <h3 class="card-title">入口</h3>
              <ul class="linklist">
                <li><a href="/dashboard">进入工作区<svg aria-hidden="true"><use href="#ic-up"></use></svg></a></li>
                <li><a href="https://github.com/DMIRLAB-Group/CausalAgent" target="_blank" rel="noopener">GitHub 仓库<span class="sr-only">（在新窗口打开）</span><svg aria-hidden="true"><use href="#ic-out"></use></svg></a></li>
                <li><a href="https://doi.org/10.1145/3742414.3794777" target="_blank" rel="noopener">IUI 2026 论文<span class="sr-only">（在新窗口打开）</span><svg aria-hidden="true"><use href="#ic-out"></use></svg></a></li>
              </ul>
            </div>
          </aside>
        </div>
      </div>
    </section>

    <section class="section module" id="cdfm" aria-labelledby="cdfm-title">
      <div class="wrap">
        <div class="section-head">
          <span class="tag">基础模型 · 开源</span>
          <h2 class="section-title" id="cdfm-title">CDFM：通用因果发现基础模型</h2>
          <p class="section-lede">Towards a General-Purpose Causal Discovery Foundation Model。给定纯观测数据 X (N, D)，一次前向输出因果图 G (D, D)。</p>
        </div>
        <div class="module-body">
          <div class="module-main">
            <div data-reveal="">
              <p class="module-p">预训练的数据来自大量合成结构因果模型，模型把不同机制里的统计不对称性收进同一份权重，在 15 个机制族与若干真实基准上超过全部对比方法，一份预训练权重适用于任意样本量 N 与任意变量数 D</p>

            </div>
            <ul class="keylist" data-reveal="">
              <li>
                <svg aria-hidden="true"><use href="#ic-cube"></use></svg>
                <div>
                  <span class="key-title">零样本</span>
                  <span class="key-desc">一份预训练权重应对任意 N 与任意 D，换数据集不需要重新训练。</span>
                </div>
              </li>
              <li>
                <svg aria-hidden="true"><use href="#ic-check"></use></svg>
                <div>
                  <span class="key-title">覆盖 15 个机制族</span>
                  <span class="key-desc">仓库给出的基准图逐族对比 CDFM 与该族最佳基线的 AUROC，覆盖线性、非线性、离散、时间滞后等机制。</span>
                </div>
              </li>
              <li>
                <svg aria-hidden="true"><use href="#ic-nodes"></use></svg>
                <div>
                  <span class="key-title">一行调用</span>
                  <span class="key-desc">pip 安装之后，predict 返回邻接矩阵、边概率与原始分数，阈值可以自动校准，也可以手动指定。</span>
                </div>
              </li>
              <li>
                <svg aria-hidden="true"><use href="#ic-window"></use></svg>
                <div>
                  <span class="key-title">附带缺失值填补</span>
                  <span class="key-desc">模型另有一个用分位数损失训练的填补头，会自动识别 NaN 并补上数值。</span>
                </div>
              </li>
            </ul>
            <div data-reveal="">
              <h3 class="card-title">调用示例</h3>
              <div class="codeblock" id="codeblock">
                <div class="codeblock-bar">
                  <div class="tabs" role="tablist" aria-label="CDFM 调用示例">
                    <button role="tab" id="tab-install" aria-controls="panel-install" aria-selected="true" tabindex="0" type="button">安装</button>
                    <button role="tab" id="tab-discover" aria-controls="panel-discover" aria-selected="false" tabindex="-1" type="button">因果发现</button>
                    <button role="tab" id="tab-impute" aria-controls="panel-impute" aria-selected="false" tabindex="-1" type="button">缺失值填补</button>
                  </div>
                  <button class="copy-btn" id="copy-code" type="button">复制</button>
                </div>
                <div class="codepanel" role="tabpanel" id="panel-install" aria-labelledby="tab-install" tabindex="0">
                  <pre><code>pip install cdfm-base</code></pre>
                </div>
                <div class="codepanel" role="tabpanel" id="panel-discover" aria-labelledby="tab-discover" tabindex="0" hidden>
                  <pre><code>from cdfm import CDFM
  import numpy as np

  # 从 HuggingFace Hub 加载预训练权重
  model = CDFM.from_pretrained("DMIRLAB/CDFM")

  data = np.loadtxt("tests/data/simple/data.csv", delimiter=",")

  # 默认自动校准阈值，也可以写成 model.predict(data, threshold=0.5)
  result = model.predict(data)

  print(result.adjacency)        # (D, D) 的 0/1 因果图
  print(result.probabilities)    # 边概率
  print(result.runtime_sec)      # 用时</code></pre>
                </div>
                <div class="codepanel" role="tabpanel" id="panel-impute" aria-labelledby="tab-impute" tabindex="0" hidden>
                  <pre><code>from cdfm import CDFM
  import numpy as np

  model = CDFM.from_pretrained("DMIRLAB/CDFM")

  data = np.loadtxt("tests/data/simple/data.csv", delimiter=",")
  rng = np.random.default_rng(42)
  data_with_nan = data.copy()
  data_with_nan[rng.random(data.shape) &lt; 0.2] = np.nan

  # 自动识别 NaN 并填补
  imputed = model.imputation(data_with_nan)</code></pre>
                </div>
              </div>
              <p class="copy-status" id="copy-status" role="status" aria-live="polite"></p>
              <p class="figcap">依赖 torch 2.0 以上、numpy 1.20 以上、safetensors、networkx 与 huggingface_hub，Python 3.10 起。详细内容请看仓库 README </p>
            </div>
            <figure class="figure" data-reveal="">
              <img :src="'/site-assets/assets/product/cdfm-benchmark-overview.png'" width="3546" height="1207" alt="柱状图：15 个机制族上的 AUROC 对比，蓝色柱为 CDFM，灰色柱为该族的最佳基线，每一族上 CDFM 都更高。">
              <figcaption class="figcap">15 个机制族上 CDFM 与该族最佳基线的 AUROC 对比</figcaption>
            </figure>
            <div class="bench" data-reveal="">
              <div class="bench-head">

                <div class="sortgroup" role="group" aria-label="表格排序方式">
                  <button type="button" data-sort="family" aria-pressed="true">按机制族</button>
                  <button type="button" data-sort="cdfm" aria-pressed="false">按 CDFM 得分</button>
                  <button type="button" data-sort="lead" aria-pressed="false">按领先幅度</button>
                </div>
              </div>
              <div class="tablescroll" tabindex="0" role="region" aria-label="AUROC 对比数据表，可横向滚动">
                <table>
                  <caption class="sr-only">CDFM 与最佳基线在 15 个机制族上的 AUROC 对比</caption>
                  <thead>
                    <tr>
                      <th scope="col">机制族</th>
                      <th scope="col" class="num">CDFM</th>
                      <th scope="col" class="num">最佳基线</th>
                      <th scope="col" class="num">领先</th>
                    </tr>
                  </thead>
                  <tbody id="bench-body"></tbody>
                  <tfoot>
                    <tr>
                      <td colspan="3">15 个机制族的平均领先</td>
                      <td class="num" id="bench-avg">—</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            </div>
          </div>
          <aside class="aside">
            <div class="card" data-reveal="">
              <h3 class="card-title">仓库概况</h3>
              <dl class="meta-list">
                <div><dt>定位</dt><dd>因果发现基础模型</dd></div>
                <div><dt>许可</dt><dd>Apache-2.0</dd></div>
                <div><dt>语言</dt><dd>Python 3.10+</dd></div>
                <div><dt>星标</dt><dd>63</dd></div>
                <div><dt>最近更新</dt><dd>2026-09-15</dd></div>
                <div><dt>权重</dt><dd>HuggingFace 上的 DMIRLAB/CDFM</dd></div>
              </dl>
            </div>
            <div class="card" data-reveal="">
              <h3 class="card-title">入口</h3>
              <ul class="linklist">
                <li><a href="https://arxiv.org/abs/2607.11508" target="_blank" rel="noopener">论文 arXiv 2607.11508<span class="sr-only">（在新窗口打开）</span><svg aria-hidden="true"><use href="#ic-out"></use></svg></a></li>
                <li><a href="https://huggingface.co/DMIRLAB/CDFM" target="_blank" rel="noopener">模型权重<span class="sr-only">（在新窗口打开）</span><svg aria-hidden="true"><use href="#ic-out"></use></svg></a></li>
                <li><a href="https://github.com/DMIRLAB-Group/CDFM" target="_blank" rel="noopener">GitHub 仓库<span class="sr-only">（在新窗口打开）</span><svg aria-hidden="true"><use href="#ic-out"></use></svg></a></li>
                <li><a href="https://github.com/DMIRLAB-Group/CDFM/blob/main/docs/figures/benchmark_overview.png" target="_blank" rel="noopener">基准图原文件<span class="sr-only">（在新窗口打开）</span><svg aria-hidden="true"><use href="#ic-out"></use></svg></a></li>
              </ul>
            </div>
          </aside>
        </div>
      </div>
    </section>

    <section class="section module" id="library" aria-labelledby="library-title">
      <div class="wrap">
        <div class="section-head">
          <span class="tag">算法库 · 开源组织</span>
          <h2 class="section-title" id="library-title">DMIRLAB-Group：59 个公开算法</h2>
          <p class="section-lede">实验室从 2016 年 11 月开始在 GitHub 上公开代码，组织全名 DMIRLAB，目前有 59 个公开仓库。</p>
        </div>
        <div class="library-grid">
          <div class="card" data-reveal="">
            <h3 class="card-title">仓库说明里带会议或论文的仓库</h3>
            <ul class="paperlist">
              <li><a href="https://github.com/DMIRLAB-Group/CDFM" target="_blank" rel="noopener"><span class="paper-name">CDFM</span><span class="paper-venue">arXiv 2607.11508，2026</span></a></li>
              <li><a href="https://github.com/DMIRLAB-Group/CausalAgent" target="_blank" rel="noopener"><span class="paper-name">CausalAgent</span><span class="paper-venue">IUI 2026</span></a></li>
              <li><a href="https://github.com/DMIRLAB-Group/HCKT" target="_blank" rel="noopener"><span class="paper-name">HCKT</span><span class="paper-venue">ICDM 2026</span></a></li>
              <li><a href="https://github.com/DMIRLAB-Group/LC-PB-SCM" target="_blank" rel="noopener"><span class="paper-name">LC-PB-SCM</span><span class="paper-venue">ICML 2026</span></a></li>
              <li><a href="https://github.com/DMIRLAB-Group/TSDR" target="_blank" rel="noopener"><span class="paper-name">TSDR</span><span class="paper-venue">IJCAI 2026</span></a></li>
              <li><a href="https://github.com/DMIRLAB-Group/CACA" target="_blank" rel="noopener"><span class="paper-name">CACA</span><span class="paper-venue">COLING 2025</span></a></li>
              <li><a href="https://github.com/DMIRLAB-Group/PBSCM-PGF" target="_blank" rel="noopener"><span class="paper-name">PBSCM-PGF</span><span class="paper-venue">NeurIPS 2024</span></a></li>
              <li><a href="https://github.com/DMIRLAB-Group/PBSCM" target="_blank" rel="noopener"><span class="paper-name">PBSCM</span><span class="paper-venue">AAAI 2024</span></a></li>
              <li><a href="https://github.com/DMIRLAB-Group/SHP" target="_blank" rel="noopener"><span class="paper-name">SHP</span><span class="paper-venue">IJCAI 2023</span></a></li>
              <li><a href="https://github.com/DMIRLAB-Group/SADGA" target="_blank" rel="noopener"><span class="paper-name">SADGA</span><span class="paper-venue">NeurIPS 2021</span></a></li>
              <li><a href="https://github.com/DMIRLAB-Group/TAG" target="_blank" rel="noopener"><span class="paper-name">TAG</span><span class="paper-venue">ACL 2020</span></a></li>
              <li><a href="https://github.com/DMIRLAB-Group/CANM" target="_blank" rel="noopener"><span class="paper-name">CANM</span><span class="paper-venue">IJCAI 2019</span></a></li>
              <li><a href="https://github.com/DMIRLAB-Group/DSR" target="_blank" rel="noopener"><span class="paper-name">DSR</span><span class="paper-venue">IJCAI 2019</span></a></li>
              <li><a href="https://github.com/DMIRLAB-Group/SELF" target="_blank" rel="noopener"><span class="paper-name">SELF</span><span class="paper-venue">AAAI 2018</span></a></li>
            </ul>
            <p class="figcap">会议信息取自各仓库自己的说明，未标注的仓库不在这里列出。</p>
          </div>
          <div class="card" data-reveal="">
            <h3 class="card-title">入口</h3>
            <ul class="linklist">
              <li><a href="https://github.com/DMIRLAB-Group" target="_blank" rel="noopener">GitHub 组织页<span class="sr-only">（在新窗口打开）</span><svg aria-hidden="true"><use href="#ic-out"></use></svg></a></li>
              <li><a href="https://github.com/DMIRLAB-Group/CDFM" target="_blank" rel="noopener">CDFM 仓库<span class="sr-only">（在新窗口打开）</span><svg aria-hidden="true"><use href="#ic-out"></use></svg></a></li>
            </ul>
            <p class="figcap">组织创建于 2016-11-25，共有 59 个公开仓库</p>
          </div>
        </div>
      </div>
    </section>

    <section class="section module" id="rag-eval" aria-labelledby="rag-eval-title">
      <div class="wrap">
        <div class="section-head">
          <span class="tag">RAG 测评 · 管理员专用</span>
          <h2 class="section-title" id="rag-eval-title">RAG 测评端：从隔离索引到正式发布</h2>
          <p class="section-lede">面向管理员和 RAG 维护人员的独立工作台，用于准备知识源、验证候选索引与评测结果，再按发布流程管理正式知识库。</p>
        </div>
        <div class="module-body">
          <div class="module-main">
            <div data-reveal="">
              <p class="module-p">测评在隔离的工作索引中进行，由Causalagent提取知识、索引构建和 staged RAG 测试，检查候选内容，最后进入评测与发布流程。</p>
            </div>
            <ul class="keylist" data-reveal="">
              <li>
                <svg aria-hidden="true"><use href="#ic-cube"></use></svg>
                <div>
                  <span class="key-title">知识源与工作索引</span>
                  <span class="key-desc">选择或上传知识源，在隔离环境中摄取内容并构建 staged index。</span>
                </div>
              </li>
              <li>
                <svg aria-hidden="true"><use href="#ic-window"></use></svg>
                <div>
                  <span class="key-title">检索试跑</span>
                  <span class="key-desc">针对候选索引运行检索流程，先观察结果，再决定是否进入评测。</span>
                </div>
              </li>
              <li>
                <svg aria-hidden="true"><use href="#ic-nodes"></use></svg>
                <div>
                  <span class="key-title">题集治理与 Ragas 评测</span>
                  <span class="key-desc">生成和审核候选题集，运行检索评测与 Ragas 评测，并管理对应报告。</span>
                </div>
              </li>
              <li>
                <svg aria-hidden="true"><use href="#ic-check"></use></svg>
                <div>
                  <span class="key-title">正式发布</span>
                  <span class="key-desc">通过正式 release 流程管理候选知识库的发布，不把隔离试验直接当作生产版本。</span>
                </div>
              </li>
            </ul>
            <div data-reveal="">
              <h3 class="card-title">工作流程</h3>
              <ol class="flow">
                <li>选择或上传来源</li>
                <li>构建隔离索引</li>
                <li>试跑与评测</li>
                <li>审核并发布</li>
              </ol>
            </div>
            <div class="state-note" data-reveal="">
              <b>访问与生产边界</b>
              <span>RAG 测评台需要管理员权限。访问权限请看文档内容</span>
            </div>
          </div>
          <aside class="aside">
            <figure class="image-reservation" data-reveal="">
              <img class="shot-image" :src="'/site-assets/media/rag-eval-console.png'" width="1600" height="1000" alt="RAG 测评台评测配置界面。" loading="lazy" decoding="async" />
            </figure>
          </aside>
        </div>
      </div>
    </section>

    <section class="section module" id="admin-console" aria-labelledby="admin-console-title">
      <div class="wrap">
        <div class="section-head">
          <span class="tag">系统管理 · 授权入口</span>
          <h2 class="section-title" id="admin-console-title">管理员端：业务与系统治理</h2>
          <p class="section-lede">管理员端把业务对象管理、数据库状态与审计入口集中在同一套后台，帮助授权人员了解运行情况并处理管理工作。</p>
        </div>
        <div class="module-body">
          <div class="module-main">
            <div data-reveal="">
              <p class="module-p">后台包含业务概览，以及用户、会话、分析任务和文件等管理页面；数据库区域则提供运行看板、自动采集配置和数据库审计入口。</p>
            </div>
            <ul class="keylist" data-reveal="">
              <li>
                <svg aria-hidden="true"><use href="#ic-nodes"></use></svg>
                <div>
                  <span class="key-title">业务概览</span>
                  <span class="key-desc">集中查看用户、会话、消息、文件和分析任务等业务对象的汇总信息。</span>
                </div>
              </li>
              <li>
                <svg aria-hidden="true"><use href="#ic-window"></use></svg>
                <div>
                  <span class="key-title">用户与业务对象</span>
                  <span class="key-desc">提供用户与权限、会话与内容、分析任务以及对话文件的管理入口。</span>
                </div>
              </li>
              <li>
                <svg aria-hidden="true"><use href="#ic-cube"></use></svg>
                <div>
                  <span class="key-title">数据库运行看板</span>
                  <span class="key-desc">查看共享监控快照、数据库运行状态、清理 worker 与 outbox 队列等信息。</span>
                </div>
              </li>
              <li>
                <svg aria-hidden="true"><use href="#ic-check"></use></svg>
                <div>
                  <span class="key-title">采集配置与审计</span>
                  <span class="key-desc">进入自动采集时间配置和 Schema 审计页面，查看系统管理相关信息。</span>
                </div>
              </li>
            </ul>
            <div class="state-note" data-reveal="">
              <b>权限说明</b>
              <span>管理员页面要求相应管理权限；目前仍然对公开访客有一定限制，具体内容请查看文档</span>
            </div>
          </div>
          <aside class="aside">
            <figure class="image-reservation" data-reveal="">
              <img class="shot-image" :src="'/site-assets/media/admin-console.png'" width="1600" height="1000" alt="管理员端业务概览界面。" loading="lazy" decoding="async" />
            </figure>
          </aside>
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
    <svg aria-hidden="true"><use href="#ic-up"></use></svg>
  </button>
</template>
