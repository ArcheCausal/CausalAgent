<script setup lang="ts">
/* 本页结构与交互逐字取自官网原型 docs.html 的内联内容。
   每个公开地址都是整页加载，同一个文档只会挂载一个页面，
   因此页面样式按自身作用范围生效，不再额外做作用域限定。 */
import { onMounted } from 'vue'
import { initDocs } from '../scripts/docs.js'
import '../styles/pages/docs.css'

onMounted(() => {
  initDocs()
})
</script>

<template>
  <svg width="0" height="0" aria-hidden="true" focusable="false"><defs>
    <symbol id="ic-search" viewBox="0 0 16 16"><circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" stroke-width="1.4"></circle><path d="M10.5 10.5 L14 14" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"></path></symbol>
    <symbol id="ic-close" viewBox="0 0 12 12"><path d="M1.5 1.5 l9 9 M10.5 1.5 L1.5 10.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"></path></symbol>
    <symbol id="ic-chev" viewBox="0 0 16 16"><path d="M3.5 6 L8 10.5 L12.5 6" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"></path></symbol>
    <symbol id="ic-list" viewBox="0 0 16 16"><path d="M2 4 h12 M2 8 h12 M2 12 h12" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"></path></symbol>
    <symbol id="ic-left" viewBox="0 0 16 16"><path d="M10 3.5 L5.5 8 L10 12.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"></path></symbol>
    <symbol id="ic-right" viewBox="0 0 16 16"><path d="M6 3.5 L10.5 8 L6 12.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"></path></symbol>
    <symbol id="ic-up" viewBox="0 0 14 14"><path d="M7 12 L7 2.5 M3 6.5 L7 2.5 L11 6.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"></path></symbol>
    <symbol id="ic-out" viewBox="0 0 14 14"><path d="M4 10 L10 4 M5.5 4 H10 V8.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"></path></symbol>
  </defs></svg>

  <a class="skip" href="#docs-main">跳到正文</a>

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
        <a href="/docs" aria-current="page">文档</a>
        <a href="/changelog">更新日志</a>
        <a href="/about">关于</a>
      </nav>
      <div class="nav-actions">
        <a class="pill" href="/dashboard">打开工作区</a>
      </div>
    </div>
  </header>

  <div class="wrap docs-shell">

    <button class="docs-side-toggle" type="button" id="side-toggle" aria-expanded="false" aria-controls="docs-side">
      <svg aria-hidden="true"><use href="#ic-list"></use></svg>
      <span class="od-fill">文档目录</span>
      <svg class="chev" aria-hidden="true"><use href="#ic-chev"></use></svg>
    </button>

    <aside class="docs-side" id="docs-side">
      <div class="search-field">
        <label for="doc-search">搜索文档</label>
        <div class="search-input-wrap">
          <svg aria-hidden="true"><use href="#ic-search"></use></svg>
          <input id="doc-search" type="search" autocomplete="off" placeholder="关键词，例如 权限、CSV、CDFM">
          <button class="search-clear" id="search-clear" type="button" aria-label="清空搜索" hidden>
            <svg aria-hidden="true"><use href="#ic-close"></use></svg>
          </button>
        </div>
        <p class="search-status" id="search-status" role="status" aria-live="polite"></p>
      </div>

      <div class="search-empty" id="search-empty" hidden>
        <p class="search-empty-title">没有匹配的内容</p>
        <p>换一个关键词，或者从下面的目录直接打开一篇文档。</p>
      </div>

      <nav class="docs-tree" id="docs-tree" aria-label="文档目录">
        <div class="tree-group" data-group="开始">
          <p class="tree-title">开始</p>
          <a class="tree-link" href="#/overview" data-doc-link="overview">概览</a>
          <a class="tree-link" href="#/quickstart" data-doc-link="quickstart">快速开始</a>
        </div>
        <div class="tree-group" data-group="使用">
          <p class="tree-title">使用</p>
          <a class="tree-link" href="#/workspace" data-doc-link="workspace">工作区</a>
          <a class="tree-link" href="#/materials" data-doc-link="materials">材料与文件</a>
          <a class="tree-link" href="#/tasks" data-doc-link="tasks">任务执行与过程</a>
        </div>
        <div class="tree-group" data-group="结果">
          <p class="tree-title">结果</p>
          <a class="tree-link" href="#/results" data-doc-link="results">结果与报告</a>
          <a class="tree-link" href="#/algorithms" data-doc-link="algorithms">算法说明</a>
        </div>
        <div class="tree-group" data-group="参考">
          <p class="tree-title">参考</p>
          <a class="tree-link" href="#/account" data-doc-link="account">账号与权限</a>
          <a class="tree-link" href="#/faq" data-doc-link="faq">常见问题</a>
        </div>
      </nav>
    </aside>

    <main class="docs-main" id="docs-main" tabindex="-1">

      <header class="docs-hero">
        <p class="docs-kicker">文档</p>
        <h1 class="docs-title" id="docs-title">使用文档</h1>
        <p class="docs-lede">这里介绍公开可用的功能与常见操作：从注册登录、上传数据，到任务执行、结果复核与账号权限。仓库内部的开发文档不随本页发布。</p>
        <div class="docs-meta">
          <span class="badge">0.2.0 当前版本</span>

        </div>
      </header>

      <article class="doc" data-doc="overview" id="doc-overview">
        <header class="doc-head">
          <p class="doc-group">开始</p>
          <h2 id="sec-overview">概览</h2>
          <p class="doc-lede">CausalAgent 是什么，一次分析经过哪些步骤，结论的边界在哪里。</p>
        </header>

        <h3 id="sec-overview-what">CausalAgent 是什么</h3>
        <p>CausalAgent 是一个对话式的因果分析工作台。上传表格数据，用自然语言说明想分析什么，智能体自己检查数据、选择合适的因果方法、运行算法，最后给出因果图、指标和一份结构化报告。整个过程留在一个会话里，可以随时回来追问。</p>

        <h3 id="sec-overview-chain">一条链路完成分析</h3>
        <ol class="chain">
          <li>上传数据</li>
          <li>预处理与数据体检</li>
          <li>因果结构学习</li>
          <li>后处理与质量提升</li>
          <li>报告与可视化输出</li>
        </ol>
        <p class="chain-caption">结构示意。每一步用哪种算法由智能体按数据特征选择，选择依据会随任务过程公开。</p>
        <p>预处理会给出每一列的缺失率、唯一值和是否常量，推断它属于连续、分类、时间还是疑似 ID，并按适用性分组出优先用于因果分析的候选变量。高缺失、常数列这类问题字段会被标出来，不会直接进入因果分析。</p>

        <h3 id="sec-overview-boundary">结论的边界</h3>
        <p>结论是对现有数据的关联与假设。DirectLiNGAM 要求误差相互独立、没有潜在混杂；CDFM 只给出边和方向，没有权重和置信度，方向还可能随阈值变化。报告里会写明这次分析的局限，涉及判断的地方请结合领域知识复核。</p>
        <div class="note">
          <p class="note-label">平台事实</p>
          <p>输入是表格数据文件、部署好的知识库与按需开启的联网检索；输出是因果图、指标表与结构化报告；会话与文件随账号保留；角色与权限分开，按功能授权。</p>
        </div>
      </article>

      <article class="doc" data-doc="quickstart" id="doc-quickstart" hidden>
        <header class="doc-head">
          <p class="doc-group">开始</p>
          <h2 id="sec-quickstart">快速开始</h2>
          <p class="doc-lede">四种入口、账号规则，以及登录之后会去到哪里。</p>
        </header>

        <h3 id="sec-quickstart-entries">四种入口</h3>
        <div class="table-wrap">
          <table class="doc-table">
            <caption>入口地址与权限要求</caption>
            <thead>
              <tr><th scope="col">入口</th><th scope="col">地址</th><th scope="col">权限要求</th></tr>
            </thead>
            <tbody>
              <tr>
                <th scope="row">官网</th>
                <td data-label="地址"><code>/</code></td>
                <td data-label="权限要求">不需要登录</td>
              </tr>
              <tr>
                <th scope="row">登录与注册</th>
                <td data-label="地址"><code>/auth/sign-in</code>、<code>/auth/sign-up</code></td>
                <td data-label="权限要求">不需要登录</td>
              </tr>
              <tr>
                <th scope="row">普通用户工作区</th>
                <td data-label="地址"><code>/dashboard</code></td>
                <td data-label="权限要求"><code>dashboard.access</code></td>
              </tr>
              <tr>
                <th scope="row">RAG 评测台</th>
                <td data-label="地址"><code>/rag-eval</code></td>
                <td data-label="权限要求"><code>rag_eval.access</code></td>
              </tr>
              <tr>
                <th scope="row">管理后台</th>
                <td data-label="地址"><code>/admin/database</code></td>
                <td data-label="权限要求"><code>admin.access</code></td>
              </tr>
            </tbody>
          </table>
        </div>

        <h3 id="sec-quickstart-account">创建账号</h3>
        <ul>
          <li>用户名至少 3 个字符。</li>
          <li>密码至少 6 位，并且要包含至少一个数字。</li>
          <li>工作区不提供未登录的预览，注册和登录都在官网完成。</li>
        </ul>

        <h3 id="sec-quickstart-after-login">登录之后去哪里</h3>
        <p>登录后按权限回跳：拥有 <code>admin.access</code> 的账号回到管理后台，普通账号回到工作区。回跳地址只接受站内已经登记的路径，外部地址会被忽略。</p>

        <h3 id="sec-quickstart-browser">浏览器要求</h3>
        <p>使用任意现代浏览器即可。保持登录状态需要允许本站使用 Cookie；账号被禁用或权限发生变化后，已经登录的会话会在下一次请求时失效，需要重新登录。</p>
      </article>

      <article class="doc" data-doc="workspace" id="doc-workspace" hidden>
        <header class="doc-head">
          <p class="doc-group">使用</p>
          <h2 id="sec-workspace">工作区</h2>
          <p class="doc-lede">工作区的三个地址、会话怎么切换，以及设置里有哪几项。</p>
        </header>

        <h3 id="sec-workspace-urls">三个地址</h3>
        <ul>
          <li><code>/dashboard</code>：工作区首页，包含会话列表、文件列表与输入区。</li>
          <li><code>/dashboard/session/&lt;session_id&gt;</code>：某一个会话，分析过程和结果都在这里。</li>
          <li><code>/dashboard/settings</code>：设置页面。</li>
        </ul>

        <h3 id="sec-workspace-routing">地址就是当前会话</h3>
        <p>地址决定你看到哪一个会话。新建、选中或删除会话都会更新地址；浏览器前进和后退会重新加载对应的会话；地址里的会话标识不合法时会回到工作区首页。</p>

        <h3 id="sec-workspace-rail">左侧栏与输入区</h3>
        <p>会话按今天、昨天和更早分组，随时可以回到历史会话继续追问。文件列表保存这次会话用到的数据文件，可以复用，也可以删除。在输入区发送内容之后进入运行态，分析过程和结果都出现在同一条会话里。</p>

        <h3 id="sec-workspace-settings">设置里有哪几项</h3>
        <ul>
          <li>用户协议与用户手册。</li>
          <li>检查更新。</li>
          <li>切换界面语言，目前有中文与 English 两种。</li>
        </ul>

        <h3 id="sec-workspace-signout">退出登录</h3>
        <p>退出会清空当前的会话、文件、任务与草稿状态，然后回到官网首页。</p>
      </article>

      <article class="doc" data-doc="materials" id="doc-materials" hidden>
        <header class="doc-head">
          <p class="doc-group">使用</p>
          <h2 id="sec-materials">材料与文件</h2>
          <p class="doc-lede">三种可以带进分析的材料，以及它们各自要注意的地方。</p>
        </header>

        <h3 id="sec-materials-kinds">三种材料</h3>
        <ul>
          <li>数据文件：本次分析要用的表格文件。</li>
          <li>知识库：部署好的知识库，用来补充理论和方法描述。</li>
          <li>联网检索：按需开启，用来补充公开资料。</li>
        </ul>

        <h3 id="sec-materials-upload">上传数据文件</h3>
        <p>输入区的上传按钮只接受 <code>.csv</code> 文件。上传之后文件进入左侧的文件列表，随会话保留，可以复用或删除。同一份数据可以在多个会话里反复使用，不需要重新上传。</p>

        <h3 id="sec-materials-search">开启联网检索</h3>
        <p>在发起分析之前打开联网检索。检索会聚合 arXiv、Crossref 与 OpenAlex 等学术来源，只把有限数量的引用随报告公开。规划、检索或解析失败时会返回统一的降级结果，不会中断主分析流程。</p>

        <h3 id="sec-materials-kb">知识库从哪里来</h3>
        <p>知识库检索结合向量检索与稀疏检索，摄取支持 PDF、文本、表格与图片。回答和报告里会带上来源，可以逐条回溯到原文片段。</p>

        <h3 id="sec-materials-ask">怎么提问</h3>
        <div class="example">
          <div class="example-bar">提问示例</div>
          <div class="example-body">
            <div class="od-row-top example-row">
              <p class="od-fill example-text" id="copy-ask-1">用这份门店数据估计促销对复购的影响，控制门店规模和季节。</p>
              <button class="copy-btn od-fixed" type="button" data-copy="copy-ask-1">复制</button>
            </div>
            <div class="od-row-top example-row">
              <p class="od-fill example-text" id="copy-ask-2">先检查变量之间是否存在混杂，再决定要不要做效应估计。</p>
              <button class="copy-btn od-fixed" type="button" data-copy="copy-ask-2">复制</button>
            </div>
          </div>
          <p class="copy-status" id="copy-status" role="status" aria-live="polite"></p>
        </div>
        <p>问题写得越具体，越容易选对方法：先说清要估计什么关系，再说要看住哪些变量，最后说明数据的来源和口径。</p>
      </article>

      <article class="doc" data-doc="tasks" id="doc-tasks" hidden>
        <header class="doc-head">
          <p class="doc-group">使用</p>
          <h2 id="sec-tasks">任务执行与过程</h2>
          <p class="doc-lede">运行中能看到什么，怎么停止、怎么继续，断线之后会发生什么。</p>
        </header>

        <h3 id="sec-tasks-stages">任务经过哪些阶段</h3>
        <ol>
          <li>预处理与数据体检：统计行列与字段，检查缺失、常量和疑似 ID，给出候选变量。</li>
          <li>因果结构学习：按数据特征选择因果发现方法，得到变量之间的结构。</li>
          <li>后处理：检查环路、评估边的合理性，必要时融合业务先验。</li>
          <li>报告生成：把背景、数据、方法、结论与局限写成结构化报告。</li>
        </ol>

        <h3 id="sec-tasks-progress">运行中能看到什么</h3>
        <p>执行过程按阶段展开，每个阶段下面挂着进度、决策和工具调用。决策按类型显示为「算法决策」「检索决策」与「最终决策」三种前缀；同一个工具的开始与结果会按顺序补在对应决策的后面。历史会话里的执行记录同样保留。</p>

        <h3 id="sec-tasks-stop">停止一次任务</h3>
        <p>运行中发送键会变成停止键，再按一次就调用取消。取消只终止目标那一次算法调用，同一任务里并行的其它调用会继续执行；如果任务在取消的同时已经进入终态，页面会按服务端返回的状态对账，不会显示成两个结果。</p>

        <h3 id="sec-tasks-resume">等待输入与继续</h3>
        <p>需要补充信息时，任务会停在等待输入，补上内容之后从同一处继续，已经完成的部分不会重跑。</p>

        <h3 id="sec-tasks-reconnect">断线之后</h3>
        <p>网络错误和服务端的 5xx 会有界退避重连，恢复后从上次收到的事件继续，不会漏掉中间的阶段。协议层面的错误不重试，会进入稳定的错误提示路径。</p>

        <h3 id="sec-tasks-budget">一次任务能跑多长</h3>
        <p>模型调用次数、工具调用次数、递归深度、工具超时和单任务的并行工具数都有固定上限，超出上限会走受控失败，并在过程里给出说明，不会一直停在运行中。</p>
      </article>

      <article class="doc" data-doc="results" id="doc-results" hidden>
        <header class="doc-head">
          <p class="doc-group">结果</p>
          <h2 id="sec-results">结果与报告</h2>
          <p class="doc-lede">结论文档的三种形态、报告的结构，以及来源和证据怎么看。</p>
        </header>

        <h3 id="sec-results-views">结论文档的三种形态</h3>
        <p>因果图、指标表与报告在同一处切换查看，三种视图对应同一份分析结果。</p>

        <h3 id="sec-results-graph">因果图</h3>
        <p>节点可以拖动和缩放，点开节点可以看到变量说明，也可以针对某个变量直接追问。图里边的方向来自所选算法，具体含义和适用前提见「算法说明」。</p>

        <h3 id="sec-results-charts">图表</h3>
        <p>图表包含直方图、分类柱状图与相关性热力图，由页面自己绘制。当前不提供缩放、拖动与单独导出。</p>

        <h3 id="sec-results-report">报告结构</h3>
        <ol>
          <li>分析背景：这次分析要回答什么问题。</li>
          <li>数据概况：行列规模、字段清单与体检结论。</li>
          <li>方法说明：选择了哪些方法，依据是什么。</li>
          <li>因果发现：结构与效应估计的结果。</li>
          <li>结论与建议：可以取用的结论与下一步。</li>
          <li>局限性：这次分析不覆盖的范围与需要复核的地方。</li>
        </ol>

        <h3 id="sec-results-sources">来源与证据</h3>
        <p>报告底部按来源分组展示证据。知识库的长引用默认收起，点箭头展开；正文引用到某条证据时，会有「定位正文」的入口回到对应段落。开启联网检索时，公开来源与报告里的引用一一对应。</p>

        <h3 id="sec-results-followup">历史记录与追问</h3>
        <p>历史会话保留完整的执行记录，可以直接在同一会话里追问，补充数据或换一个变量重新分析，都不需要新建会话。</p>
      </article>

      <article class="doc" data-doc="algorithms" id="doc-algorithms" hidden>
        <header class="doc-head">
          <p class="doc-group">结果</p>
          <h2 id="sec-algorithms">算法说明</h2>
          <p class="doc-lede">三种默认算法适合什么数据、输出什么、有哪些前提。</p>
        </header>

        <h3 id="sec-algorithms-defaults">默认启用哪三种</h3>
        <p>默认对模型开放三种算法工具，由它按数据特征决定调用顺序与组合；算法之间互不依赖的调用可以并行，但受单任务的并发与预算上限约束。</p>

        <div class="seg" role="tablist" aria-label="算法说明">
          <button type="button" role="tab" id="tab-pc" aria-controls="panel-pc" aria-selected="true" tabindex="0">PC</button>
          <button type="button" role="tab" id="tab-lingam" aria-controls="panel-lingam" aria-selected="false" tabindex="-1">DirectLiNGAM</button>
          <button type="button" role="tab" id="tab-cdfm" aria-controls="panel-cdfm" aria-selected="false" tabindex="-1">CDFM</button>
        </div>

        <div class="alg-panel" role="tabpanel" id="panel-pc" aria-labelledby="tab-pc" tabindex="0">
          <h3 id="sec-alg-pc">PC：基于条件独立检验的结构学习</h3>
          <dl class="alg-facts">
            <div><dt>适合</dt><dd>先确定哪些变量之间存在边，再讨论效应大小时的起点。</dd></div>
            <div><dt>输出</dt><dd>变量之间的边，以及定向之后的有向结构。</dd></div>
            <div><dt>前提</dt><dd>结论依赖条件独立检验，样本量和变量类型会影响检验的稳定性。</dd></div>
          </dl>
        </div>

        <div class="alg-panel" role="tabpanel" id="panel-lingam" aria-labelledby="tab-lingam" tabindex="0" hidden>
          <h3 id="sec-alg-lingam">DirectLiNGAM：连续数值数据的线性非高斯因果发现</h3>
          <dl class="alg-facts">
            <div><dt>适合</dt><dd>连续数值数据，希望同时得到因果顺序和带权有向图。</dd></div>
            <div><dt>输出</dt><dd>变量之间的因果顺序，以及带权重的有向边。</dd></div>
            <div><dt>前提</dt><dd>使用结果需要满足误差相互独立、没有潜在混杂等模型假设。边权是模型估计出来的系数，不等同于实验验证得到的效应。</dd></div>
          </dl>
        </div>

        <div class="alg-panel" role="tabpanel" id="panel-cdfm" aria-labelledby="tab-cdfm" tabindex="0" hidden>
          <h3 id="sec-alg-cdfm">CDFM：预训练的因果发现基础模型</h3>
          <dl class="alg-facts">
            <div><dt>适合</dt><dd>连续数值表格数据；机制未知或明显非线性时，比只依赖单一假设的方法更合适。</dd></div>
            <div><dt>输出</dt><dd>一次调用返回整张候选有向图，只有边和方向，没有权重与置信度。</dd></div>
            <div><dt>参数</dt><dd>只公开 threshold 一个参数，不传时由服务端按模型自带的校准器决定判决边界。缺失值按 NaN 与正负无穷的掩码规则传递，分类变量不适用。</dd></div>
            <div><dt>前提</dt><dd>同一份权重覆盖线性、非线性、异方差、有序或离散观测、测量误差与潜在混杂等多类机制。方向可能随阈值变化，不可识别的场景下方向证据不足，两条相反方向的边也可能来自潜在混杂，结果不代表准确率或生产能力的证明。</dd></div>
          </dl>
        </div>

        <h3 id="sec-algorithms-post">后处理</h3>
        <p>后处理会检查学到的结构里是否存在环路并给出断边建议，对每条边做强度与合理性评估，还可以把业务先验带进结构，例如「某个变量不可能被另一个变量影响」。这一步的结论同样会写进报告。</p>

        <h3 id="sec-algorithms-verify">结论的校验</h3>
        <p>报告生成前有一次终态校验。这一步不调用模型，只检查最终决策引用的算法结果是否存在、结果与工具调用记录是否一致，以及结果是否属于当前任务。第一次不通过会把具体违反的引用规则交回去修正一次；第二次仍不通过就降级为只基于已验证输入的报告，不公开未经校验的选择。</p>
      </article>

      <article class="doc" data-doc="account" id="doc-account" hidden>
        <header class="doc-head">
          <p class="doc-group">参考</p>
          <h2 id="sec-account">账号与权限</h2>
          <p class="doc-lede">权限怎么分、什么时候会失效，以及忘记密码怎么办。</p>
        </header>

        <h3 id="sec-account-permissions">角色与权限分开</h3>
        <p>账号的角色与权限由服务端决定，页面上只显示当前账号确实拥有的入口。三个权限名分别对应三块功能：</p>
        <div class="table-wrap">
          <table class="doc-table">
            <caption>权限与对应入口</caption>
            <thead>
              <tr><th scope="col">权限</th><th scope="col">对应入口</th></tr>
            </thead>
            <tbody>
              <tr><th scope="row"><code>dashboard.access</code></th><td data-label="对应入口">普通用户工作区 <code>/dashboard</code></td></tr>
              <tr><th scope="row"><code>rag_eval.access</code></th><td data-label="对应入口">RAG 评测台 <code>/rag-eval</code></td></tr>
              <tr><th scope="row"><code>admin.access</code></th><td data-label="对应入口">管理后台 <code>/admin/database</code></td></tr>
            </tbody>
          </table>
        </div>

        <h3 id="sec-account-change">权限变化之后</h3>
        <p>账号被禁用或权限改变后，已经登录的浏览器会话会在下一次请求时失效，需要重新登录。登录后发现入口变少，通常是权限被调整过。</p>

        <h3 id="sec-account-password">忘记密码</h3>
        <p>目前需要联系管理员重置，重置之后用新密码重新登录。</p>

        <h3 id="sec-account-clients">两种使用方式</h3>
        <p>用浏览器打开网站登录即可使用；Windows 桌面客户端是独立窗口，登录后进入同一个工作区，安装包在发布页获取。两边的会话与文件都属于同一个账号。</p>
      </article>

      <article class="doc" data-doc="faq" id="doc-faq" hidden>
        <header class="doc-head">
          <p class="doc-group">参考</p>
          <h2 id="sec-faq">常见问题</h2>
          <p class="doc-lede">八个经常被问到的问题。</p>
        </header>

        <div class="faq">
          <details class="faq-item">
            <summary>报告里为什么没有我期待的某个变量？<svg aria-hidden="true"><use href="#ic-chev"></use></svg></summary>
            <div class="faq-body">
              <p>预处理会把高缺失率、常数列和疑似 ID 标出来，这些变量不会进入候选；另外 CDFM 只输出边和方向，条件不足时不会给出方向。变量可能出现在数据概况里，却没有出现在因果图上。</p>
            </div>
          </details>
          <details class="faq-item">
            <summary>分析结果可以直接当结论用吗？<svg aria-hidden="true"><use href="#ic-chev"></use></svg></summary>
            <div class="faq-body">
              <p>不能。结果是对现有数据的关联与假设，DirectLiNGAM 和 CDFM 都有各自的前提，报告里会写明这次分析的局限。演示界面上的数据只用于说明排版，不是真实分析结果。</p>
            </div>
          </details>
          <details class="faq-item">
            <summary>上传的数据保存在哪里？<svg aria-hidden="true"><use href="#ic-chev"></use></svg></summary>
            <div class="faq-body">
              <p>上传的 CSV 文件随会话保留在账号下，可以在左侧文件列表里复用或删除，不需要每次都重新上传。</p>
            </div>
          </details>
          <details class="faq-item">
            <summary>联网检索失败会中断分析吗？<svg aria-hidden="true"><use href="#ic-chev"></use></svg></summary>
            <div class="faq-body">
              <p>不会。规划、检索或解析失败时会返回统一的降级结果，主分析流程继续执行，报告里只会少掉对应的来源。</p>
            </div>
          </details>
          <details class="faq-item">
            <summary>支持哪些输入？<svg aria-hidden="true"><use href="#ic-chev"></use></svg></summary>
            <div class="faq-body">
              <p>表格数据文件（CSV），加上部署好的知识库与按需开启的联网检索。</p>
            </div>
          </details>
          <details class="faq-item">
            <summary>图表能导出吗？<svg aria-hidden="true"><use href="#ic-chev"></use></svg></summary>
            <div class="faq-body">
              <p>报告页面里的图表由页面自己绘制，当前不提供缩放、拖动与单独导出。</p>
            </div>
          </details>
          <details class="faq-item">
            <summary>换一台电脑还能看到历史会话吗？<svg aria-hidden="true"><use href="#ic-chev"></use></svg></summary>
            <div class="faq-body">
              <p>会话与文件随账号保留，用同一个账号在浏览器或桌面客户端登录都能继续。</p>
            </div>
          </details>
          <details class="faq-item">
            <summary>任务为什么停下来了？<svg aria-hidden="true"><use href="#ic-chev"></use></svg></summary>
            <div class="faq-body">
              <p>可能停在等待输入，补上内容就会从原处继续；也可能碰到了模型调用、工具调用或递归深度的上限，这时会走受控失败，并在过程里给出说明。</p>
            </div>
          </details>
        </div>
      </article>

      <nav class="pager" aria-label="上一篇与下一篇">
        <a class="pager-link" id="pager-prev" href="#/overview">
          <svg aria-hidden="true"><use href="#ic-left"></use></svg>
          <span class="pager-text">
            <span class="pager-label">上一篇</span>
            <span class="pager-title" id="pager-prev-title">概览</span>
          </span>
        </a>
        <a class="pager-link pager-next" id="pager-next" href="#/quickstart">
          <span class="pager-text">
            <span class="pager-label">下一篇</span>
            <span class="pager-title" id="pager-next-title">快速开始</span>
          </span>
          <svg aria-hidden="true"><use href="#ic-right"></use></svg>
        </a>
      </nav>

      <section class="more" aria-labelledby="more-title">
        <h2 class="more-title" id="more-title">继续了解</h2>
        <ul class="more-list">
          <li>
            <a href="/product">产品能力<svg aria-hidden="true"><use href="#ic-out"></use></svg></a>
            <p class="more-desc">看三件产品的位置，以及各自解决什么问题。</p>
          </li>
          <li>
            <a href="/dashboard">进入工作区<svg aria-hidden="true"><use href="#ic-out"></use></svg></a>
            <p class="more-desc">看工作区的界面原型：会话栏、执行阶段、结论文档与输入区。</p>
          </li>
          <li>
            <a href="/">回到官网首页<svg aria-hidden="true"><use href="#ic-out"></use></svg></a>
            <p class="more-desc">看 CausalAgent 的整体介绍与核心能力。</p>
          </li>
        </ul>
      </section>

    </main>

    <nav class="docs-outline" id="docs-outline" aria-labelledby="outline-title">
      <p class="outline-title" id="outline-title">本页目录</p>
      <ul class="outline-list" id="outline"></ul>
    </nav>

  </div>

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
