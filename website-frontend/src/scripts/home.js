/* 交互逻辑逐字取自官网原型 index.html；导出初始化函数，由页面组件在挂载后调用。 */
export function initHome() {
  (function () {
    "use strict";

    var reduceMotion = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    var hasGsap = typeof window.gsap !== "undefined";
    var hasST = typeof window.ScrollTrigger !== "undefined";
    var gsap = window.gsap;
    if (hasGsap && hasST) { gsap.registerPlugin(window.ScrollTrigger); }

    function el(id) { return document.getElementById(id); }
    function list(sel) { return Array.prototype.slice.call(document.querySelectorAll(sel)); }

    /* ---------- 研究成果：鼠标拖动、触控与按键切换论文 ---------- */
    (function () {
      var carousel = el("research-carousel");
      var viewport = el("research-viewport");
      var track = el("research-track");
      var status = el("research-current-page");
      var previous = carousel && carousel.querySelector("[data-research-prev]");
      var next = carousel && carousel.querySelector("[data-research-next]");
      var slides = list("#research-track .research-entry");
      var dots = list("#research-pagination .research-dot");
      if (!carousel || !viewport || !track || !status || !previous || !next || !slides.length || slides.length !== dots.length) { return; }

      var labels = ["CausalAgent", "CDFM", "VIGOR+"];
      var active = 0;

      function showSlide(index) {
        active = Math.max(0, Math.min(slides.length - 1, index));
        track.style.transform = "translate3d(-" + (active * 100 / slides.length) + "%, 0, 0)";
        status.textContent = "第 " + (active + 1) + " 篇 / 共 " + slides.length + " 篇 · " + labels[active];
        previous.disabled = active === 0;
        next.disabled = active === slides.length - 1;
        for (var i = 0; i < slides.length; i++) {
          slides[i].setAttribute("aria-hidden", String(i !== active));
          dots[i].setAttribute("aria-pressed", String(i === active));
        }
      }

      var wheelDistance = 0;
      var wheelDirection = 0;
      var wheelLockedUntil = 0;
      var wheelResetTimer = 0;
      viewport.addEventListener("wheel", function (event) {
        var delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
        if (!delta) { return; }
        var direction = delta > 0 ? 1 : -1;
        if ((direction < 0 && active === 0) || (direction > 0 && active === slides.length - 1)) {
          wheelDistance = 0;
          wheelDirection = 0;
          window.clearTimeout(wheelResetTimer);
          return;
        }
        event.preventDefault();

        var multiplier = event.deltaMode === 1 ? 16 : (event.deltaMode === 2 ? viewport.clientHeight : 1);
        if (direction !== wheelDirection) { wheelDistance = 0; wheelDirection = direction; }
        if (Date.now() < wheelLockedUntil) { return; }

        wheelDistance += Math.abs(delta) * multiplier;
        window.clearTimeout(wheelResetTimer);
        wheelResetTimer = window.setTimeout(function () { wheelDistance = 0; wheelDirection = 0; }, 180);
        if (wheelDistance < 48) { return; }

        showSlide(active + direction);
        wheelDistance = 0;
        wheelLockedUntil = Date.now() + 320;
      }, { passive: false });

      previous.addEventListener("click", function () { showSlide(active - 1); });
      next.addEventListener("click", function () { showSlide(active + 1); });
      for (var i = 0; i < dots.length; i++) {
        (function (index) {
          dots[index].addEventListener("click", function () { showSlide(index); });
        })(i);
      }

      carousel.addEventListener("keydown", function (event) {
        if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) { return; }
        if (event.key === "ArrowLeft" && active > 0) { event.preventDefault(); showSlide(active - 1); }
        else if (event.key === "ArrowRight" && active < slides.length - 1) { event.preventDefault(); showSlide(active + 1); }
      });

      var pointer = null;
      var suppressedLink = null;
      function removePointerListeners() {
        window.removeEventListener("pointermove", movePointer);
        window.removeEventListener("pointerup", finishPointer);
        window.removeEventListener("pointercancel", cancelPointer);
      }
      function movePointer(event) {
        if (!pointer || event.pointerId !== pointer.id || pointer.vertical) { return; }
        var dx = event.clientX - pointer.x;
        var dy = event.clientY - pointer.y;
        if (Math.abs(dy) > 8 && Math.abs(dy) > Math.abs(dx) * 1.1) { pointer.vertical = true; return; }
        if (Math.abs(dx) < 6) { return; }
        pointer.moved = true;
        event.preventDefault();
        carousel.classList.add("is-dragging");
        var shift = dx;
        if ((active === 0 && shift > 0) || (active === slides.length - 1 && shift < 0)) { shift *= 0.25; }
        var base = active * 100 / slides.length;
        track.style.transform = "translate3d(calc(-" + base + "% + " + shift.toFixed(1) + "px), 0, 0)";
      }
      function finishPointer(event) {
        if (!pointer || event.pointerId !== pointer.id) { return; }
        var ended = pointer;
        var dx = event.clientX - ended.x;
        var dy = event.clientY - ended.y;
        pointer = null;
        removePointerListeners();
        carousel.classList.remove("is-dragging");
        if (!ended.moved) { return; }
        if (ended.link) {
          suppressedLink = ended.link;
          window.setTimeout(function () { if (suppressedLink === ended.link) { suppressedLink = null; } }, 0);
        }
        var target = active;
        if (Math.abs(dx) >= 48 && Math.abs(dx) > Math.abs(dy) * 1.2) {
          if (dx < 0 && active < slides.length - 1) { target = active + 1; }
          else if (dx > 0 && active > 0) { target = active - 1; }
        }
        showSlide(target);
      }
      function cancelPointer() {
        if (!pointer) { return; }
        pointer = null;
        removePointerListeners();
        carousel.classList.remove("is-dragging");
        showSlide(active);
      }
      viewport.addEventListener("pointerdown", function (event) {
        if (pointer || event.isPrimary === false || (typeof event.button === "number" && event.button !== 0)) { return; }
        var link = event.target && event.target.closest ? event.target.closest("a") : null;
        pointer = { id: event.pointerId, x: event.clientX, y: event.clientY, moved: false, vertical: false, link: link };
        window.addEventListener("pointermove", movePointer, { passive: false });
        window.addEventListener("pointerup", finishPointer);
        window.addEventListener("pointercancel", cancelPointer);
      });
      carousel.addEventListener("click", function (event) {
        if (!suppressedLink) { return; }
        var link = event.target && event.target.closest ? event.target.closest("a") : null;
        if (link === suppressedLink) { event.preventDefault(); event.stopPropagation(); }
        suppressedLink = null;
      }, true);

      showSlide(0);
    })();

    /* ---------- 隐私与自主管理：同步切换三个产品截图页面 ---------- */
    (function () {
      var carousel = el("privacy-carousel");
      var track = el("privacy-track");
      var viewport = el("privacy-viewport");
      var status = el("privacy-current-page");
      var previous = carousel && carousel.querySelector("[data-privacy-prev]");
      var next = carousel && carousel.querySelector("[data-privacy-next]");
      var slides = list("#privacy-track .privacy-slide");
      var dots = list("#privacy-pagination .privacy-dot");
      if (!carousel || !track || !viewport || !status || !slides.length || slides.length !== dots.length) { return; }

      var labels = ["管理端", "RAG 端", "日志端"];
      var active = 0;

      function showSlide(index) {
        active = (index + slides.length) % slides.length;
        track.style.transform = "translate3d(-" + (active * 100 / slides.length) + "%, 0, 0)";
        status.textContent = "第 " + (active + 1) + " 页 / 共 " + slides.length + " 页 · " + labels[active];
        for (var i = 0; i < slides.length; i++) {
          slides[i].setAttribute("aria-hidden", String(i !== active));
          dots[i].setAttribute("aria-pressed", String(i === active));
        }
      }

      previous.addEventListener("click", function () { showSlide(active - 1); });
      next.addEventListener("click", function () { showSlide(active + 1); });
      for (var i = 0; i < dots.length; i++) {
        (function (index) {
          dots[index].addEventListener("click", function () { showSlide(index); });
        })(i);
      }

      carousel.addEventListener("keydown", function (event) {
        if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) { return; }
        if (event.key === "ArrowLeft") { event.preventDefault(); showSlide(active - 1); }
        else if (event.key === "ArrowRight") { event.preventDefault(); showSlide(active + 1); }
      });

      var pointerStart = null;
      viewport.addEventListener("pointerdown", function (event) {
        if (event.isPrimary === false || (typeof event.button === "number" && event.button !== 0)) { return; }
        pointerStart = { id: event.pointerId, x: event.clientX, y: event.clientY };
        if (viewport.setPointerCapture) { viewport.setPointerCapture(event.pointerId); }
      });
      viewport.addEventListener("pointerup", function (event) {
        if (!pointerStart || pointerStart.id !== event.pointerId) { return; }
        var dx = event.clientX - pointerStart.x;
        var dy = event.clientY - pointerStart.y;
        pointerStart = null;
        if (Math.abs(dx) < 48 || Math.abs(dx) < Math.abs(dy) * 1.2) { return; }
        showSlide(active + (dx < 0 ? 1 : -1));
      });
      viewport.addEventListener("pointercancel", function () { pointerStart = null; });

      var wheelDistance = 0;
      var wheelDirection = 0;
      var wheelLockedUntil = 0;
      var wheelResetTimer = 0;
      viewport.addEventListener("wheel", function (event) {
        var delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
        if (!delta) { return; }
        event.preventDefault();

        var multiplier = event.deltaMode === 1 ? 16 : (event.deltaMode === 2 ? viewport.clientHeight : 1);
        var direction = delta > 0 ? 1 : -1;
        if (direction !== wheelDirection) { wheelDistance = 0; wheelDirection = direction; }
        if (Date.now() < wheelLockedUntil) { return; }

        wheelDistance += Math.abs(delta) * multiplier;
        window.clearTimeout(wheelResetTimer);
        wheelResetTimer = window.setTimeout(function () { wheelDistance = 0; wheelDirection = 0; }, 180);
        if (wheelDistance < 48) { return; }

        showSlide(active + direction);
        wheelDistance = 0;
        wheelLockedUntil = Date.now() + 320;
      }, { passive: false });

      showSlide(0);
    })();

    /* ---------- 会话与阶段：数据在先，界面只是它的投影 ---------- */
    var STAGE_LABEL = ["第一阶段", "第二阶段", "第三阶段"];
    var STATE_TEXT = { done: "已完成", doing: "进行中", failed: "失败", todo: "待开始" };
    var SESSIONS = [
      {
        id: "promo",
        title: "门店促销效果评估",
        chip: { text: "进行中", state: "running" },
        stages: [
          { name: "描述问题", state: "done", note: "上传数据文件并说明分析目标，材料随会话保留。" },
          { name: "智能体执行", state: "doing", note: "检查数据分布与约束，再在因果发现与效应估计之间分步推进。" },
          { name: "复核结论", state: "todo", note: "查看因果图、指标与结构化报告，随时回到原始步骤。" }
        ]
      },
      {
        id: "channel",
        title: "渠道投放归因",
        chip: { text: "失败可恢复", state: "failed" },
        stages: [
          { name: "描述问题", state: "done", note: "投放明细与门店名单已经进入这个会话。" },
          { name: "智能体执行", state: "failed", note: "第二阶段在共线性检查处中断，材料本身没有问题，可以重试。" },
          { name: "复核结论", state: "todo", note: "重试跑完之后再进入复核。" }
        ]
      },
      {
        id: "price",
        title: "定价调整影响",
        chip: { text: "已完成", state: "done" },
        stages: [
          { name: "描述问题", state: "done", note: "价格调整记录与销量数据已经配对完成。" },
          { name: "智能体执行", state: "done", note: "三段已经跑完，结论与区间一起生成。" },
          { name: "复核结论", state: "done", note: "结论已经复核过一遍，三条结论都能回到原始步骤。" }
        ]
      }
    ];

    var stepsWrap = el("steps");
    var stageNote = el("stage-note");
    var taskName = el("task-name");
    var taskChip = el("task-chip");
    var runBtn = el("run-btn");
    var runFill = el("run-fill");
    var runHint = el("run-hint");
    var outputWrap = el("output");
    var sessionsWrap = el("sessions");
    var checkboxes = list(".material input");
    var sessionBtns = list(".session");
    var current = "promo";
    var currentSession = null;
    var activeStage = 1;
    var activePane = "dag";
    var running = false;

    function sessionById(id) {
      for (var i = 0; i < SESSIONS.length; i++) { if (SESSIONS[i].id === id) { return SESSIONS[i]; } }
      return null;
    }

    var outputEmpty = document.createElement("p");
    outputEmpty.className = "output-empty";
    outputEmpty.hidden = true;
    outputEmpty.textContent = "这个会话还没有产生结论文档。运行一次之后，这里会出现因果图、指标表与结构化报告。";
    if (outputWrap) { outputWrap.appendChild(outputEmpty); }

    function renderSteps(s) {
      if (!stepsWrap) { return; }
      stepsWrap.innerHTML = "";
      for (var i = 0; i < s.stages.length; i++) {
        (function (idx) {
          var st = s.stages[idx];
          var b = document.createElement("button");
          b.type = "button";
          b.className = "step";
          b.setAttribute("data-state", st.state);
          b.setAttribute("aria-current", String(idx === activeStage));
          var a = document.createElement("span"); a.className = "idx"; a.textContent = STAGE_LABEL[idx] || "阶段";
          var w = document.createElement("span"); w.className = "what"; w.textContent = st.name;
          var v = document.createElement("span"); v.className = "state"; v.textContent = STATE_TEXT[st.state] || st.state;
          b.appendChild(a); b.appendChild(w); b.appendChild(v);
          b.addEventListener("click", function () {
            activeStage = idx;
            renderSteps(s);
            if (stageNote) { stageNote.textContent = st.note; }
          });
          stepsWrap.appendChild(b);
        })(i);
      }
    }

    function checkedMaterials() {
      var out = [];
      for (var i = 0; i < checkboxes.length; i++) { if (checkboxes[i].checked) { out.push(checkboxes[i].getAttribute("data-material")); } }
      return out;
    }

    function syncRun() {
      if (!runBtn) { return; }
      var picked = checkedMaterials();
      runBtn.disabled = running || picked.length === 0;
      var second = currentSession && currentSession.stages[1] ? currentSession.stages[1].state : "";
      if (running) { runBtn.textContent = "运行中"; }
      else if (second === "failed") { runBtn.textContent = "重试第二阶段"; }
      else { runBtn.textContent = "运行任务"; }
      if (runHint) {
        if (running) { runHint.textContent = "正在推进执行阶段，进度走完会停在复核这一步。"; }
        else if (picked.length === 0) { runHint.textContent = "至少选择一类材料才能开始运行。"; }
        else { runHint.textContent = "已选择 " + picked.length + " 类材料，可以开始。"; }
      }
    }

    function syncPanes() {
      var panes = list(".output-pane");
      var empty = !!(currentSession && currentSession.stages[1] && currentSession.stages[1].state === "todo");
      outputEmpty.hidden = !empty;
      for (var i = 0; i < panes.length; i++) {
        panes[i].hidden = empty || panes[i].id !== "pane-" + activePane;
      }
    }

    function selectSession(id) {
      var s = sessionById(id);
      if (!s) { return; }
      current = id;
      currentSession = s;
      for (var i = 0; i < sessionBtns.length; i++) {
        sessionBtns[i].setAttribute("aria-pressed", String(sessionBtns[i].getAttribute("data-session") === id));
      }
      activeStage = s.stages.length - 1;
      for (var j = 0; j < s.stages.length; j++) {
        if (s.stages[j].state !== "done") { activeStage = j; break; }
      }
      if (taskName) { taskName.textContent = s.title; }
      if (taskChip) { taskChip.textContent = s.chip.text; taskChip.setAttribute("data-state", s.chip.state); }
      renderSteps(s);
      if (stageNote) { stageNote.textContent = s.stages[activeStage] ? s.stages[activeStage].note : ""; }
      syncPanes();
      syncRun();
    }

    function runSequence() {
      var s = currentSession;
      if (!s || running) { return; }
      running = true;
      activeStage = 1;
      renderSteps(s);
      if (stageNote) { stageNote.textContent = "正在检查数据分布与约束，随后给出候选因果图。"; }
      if (runFill) {
        runFill.style.transition = "none";
        runFill.style.transform = "scaleX(0)";
        window.setTimeout(function () { runFill.style.transition = ""; runFill.style.transform = "scaleX(1)"; }, 40);
      }
      syncRun();
      window.setTimeout(function () {
        running = false;
        s.stages[0].state = "done";
        s.stages[1].state = "done";
        s.stages[2].state = "doing";
        s.chip = { text: "待复核", state: "running" };
        activeStage = 2;
        renderSteps(s);
        if (taskChip) { taskChip.textContent = s.chip.text; taskChip.setAttribute("data-state", s.chip.state); }
        if (stageNote) { stageNote.textContent = "执行阶段已经跑完，结论与区间一起生成，接下来进入复核。"; }
        syncPanes();
        syncRun();
      }, 1500);
    }

    if (runBtn) { runBtn.addEventListener("click", runSequence); }

    for (var ci = 0; ci < checkboxes.length; ci++) { checkboxes[ci].addEventListener("change", syncRun); }

    for (var si = 0; si < sessionBtns.length; si++) {
      (function (b) {
        b.addEventListener("click", function () { selectSession(b.getAttribute("data-session")); });
      })(sessionBtns[si]);
    }

    var newBtn = el("new-session");
    if (newBtn) {
      newBtn.addEventListener("click", function () {
        var s = {
          id: "draft-" + (SESSIONS.length + 1),
          title: "未命名会话",
          chip: { text: "待开始", state: "todo" },
          stages: [
            { name: "描述问题", state: "todo", note: "这是一个新会话，先上传数据文件并写清分析目标。" },
            { name: "智能体执行", state: "todo", note: "目标确认之后才会进入执行阶段。" },
            { name: "复核结论", state: "todo", note: "执行完成之后才谈复核。" }
          ]
        };
        SESSIONS.push(s);
        var b = document.createElement("button");
        b.type = "button";
        b.className = "session";
        b.setAttribute("data-session", s.id);
        b.setAttribute("aria-pressed", "false");
        var inner = document.createElement("span");
        inner.className = "od-truncate";
        inner.textContent = s.title;
        b.appendChild(inner);
        b.addEventListener("click", function () { selectSession(s.id); });
        newBtn.parentNode.insertBefore(b, newBtn);
        sessionBtns.push(b);
        selectSession(s.id);
        b.focus();
      });
    }

    /* ---------- 输出页签：方向键可以在三个页签之间移动 ---------- */
    var tabButtons = list('[role="tab"]');
    function selectTab(btn) {
      activePane = btn.id.replace("tab-", "");
      for (var i = 0; i < tabButtons.length; i++) {
        var on = tabButtons[i] === btn;
        tabButtons[i].setAttribute("aria-selected", String(on));
        tabButtons[i].tabIndex = on ? 0 : -1;
      }
      syncPanes();
    }
    for (var ti = 0; ti < tabButtons.length; ti++) {
      (function (btn, idx) {
        btn.addEventListener("click", function () { selectTab(btn); });
        btn.addEventListener("keydown", function (ev) {
          var next = -1;
          if (ev.key === "ArrowRight") { next = (idx + 1) % tabButtons.length; }
          else if (ev.key === "ArrowLeft") { next = (idx - 1 + tabButtons.length) % tabButtons.length; }
          else if (ev.key === "Home") { next = 0; }
          else if (ev.key === "End") { next = tabButtons.length - 1; }
          if (next >= 0) { ev.preventDefault(); tabButtons[next].focus(); selectTab(tabButtons[next]); }
        });
      })(tabButtons[ti], ti);
    }

    /* ---------- 首页普通用户工作区演示 ---------- */
    (function () {
      var demo = el("app-demo");
      if (!demo) { return; }

      var playButton = el("demo-play");
      var menuButton = el("demo-menu-toggle");
      var collapseButton = el("demo-collapse");
      var scrim = el("demo-scrim");
      var newChatButton = el("demo-new-chat");
      var avatarButton = el("demo-avatar");
      var userMenu = el("demo-user-menu");
      var userClose = el("demo-user-close");
      var input = el("demo-input");
      var sendButton = el("demo-send");
      var searchButton = el("demo-search");
      var uploadButton = el("demo-upload");
      var selectedFile = el("demo-selected-file");
      var fileRemove = el("demo-file-remove");
      var messageRow = el("demo-message-row");
      var userText = el("demo-user-text");
      var conversation = el("demo-conversation");
      var thinking = el("demo-thinking");
      var thinkingToggle = el("demo-thinking-toggle");
      var thinkingStatus = el("demo-thinking-status");
      var thinkingDuration = el("demo-thinking-duration");
      var thinkingDots = el("demo-thinking-dots");
      var reportRow = el("demo-report-row");
      var graphReadout = el("demo-graph-readout");
      var running = false;
      var paused = false;
      var demoFrame = 0;
      var demoElapsed = 0;
      var demoLastFrame = 0;
      var demoPhase = -1;
      var idleButtonLabel = "播放演示";
      var selectedFileName = "";
      var defaultPrompt = "请分析强化康复方案对患者 90 天功能恢复的影响。";
      var demoPrompt = defaultPrompt;
      var activeReportKey = "medical";
      var activeGraphNode = null;
      var reportStreamNodes = [];
      var reportStreamIndex = -1;
      var reportStreamComplete = false;
      var reportScrollFrom = 0;
      var reportScrollTo = 0;
      var reportScrollStart = 0;
      var reportScrollDuration = 0;
      var reportScrollActive = false;

      var steps = Array.prototype.slice.call(demo.querySelectorAll("[data-demo-step]"));
      var sessionButtons = Array.prototype.slice.call(demo.querySelectorAll("[data-demo-session]"));
      var fileButtons = Array.prototype.slice.call(demo.querySelectorAll("[data-demo-file]"));

      var DEMO_SEND = 0;
      var DEMO_ANALYZE = 1800;
      var DEMO_REPORT = 3600;
      var DEMO_FINISH = 7600;
      var REPORT_STREAM_STEP = 430;
      var REPORT_STREAM_SCROLL_DELAY = 140;
      var REPORT_STREAM_SCROLL_DURATION = 1120;
      var REPORT_STREAM_LABELS = ["报告标题", "结论摘要", "变量定义", "识别与分析过程", "效应估计", "主要因果关系", "稳健性与边界", "来源与复核"];

      var REPORTS = {
        medical: {
          title: "医学场景 · 干预效果报告",
          summary: "在控制基线病情、年龄与既往治疗后，强化康复方案对患者 90 天功能恢复评分呈正向影响。",
          summaryList: ["平均处理效应：+6.8 分", "95% 置信区间：2.1 ～ 11.4 分，不跨 0", "证据强度：中等，结论适用于本次演示数据范围"],
          treatment: "强化康复方案 vs 常规方案",
          outcome: "90 天功能恢复评分",
          adjustment: "基线病情、年龄、既往治疗史",
          sample: "486 名患者 · 12 周随访",
          steps: ["按患者编号合并基线、干预和随访记录。", "用因果图识别需要调整的共同原因。", "估计平均处理效应，并计算 95% 置信区间。", "进行分层分析和安慰剂时间窗检查。"],
          adjustmentNote: "调整集合保留干预发生前可观测的共同原因，不把治疗后的复诊信息放入模型。",
          effect: "+6.8 分",
          ci: "2.1 ～ 11.4 分",
          period: "入组后 90 天",
          robustness: "分层到轻、中、重度患者后，方向保持一致；仍可能受到未观测康复依从性的影响。",
          robustnessList: ["未将结果解释为个体患者的确定性收益。", "建议回到原始记录复核缺失值和入组标准。"],
          sourceNote: "方法选择：因果图识别共同原因，结合分层分析复核结论。",
          sources: ["clinical-outcomes.csv", "causal-methods.pdf"],
          graphLabel: "基线病情影响强化康复方案和功能恢复，强化康复方案影响功能恢复，功能恢复影响复诊率",
          graph: { size: ["基线病情", "混杂变量", "基线病情，混杂变量"], treatment: ["强化康复方案", "处理变量", "强化康复方案，处理变量"], visit: ["功能恢复评分", "结果变量", "功能恢复评分，结果变量"], repeat: ["复诊率", "结果变量", "复诊率，结果变量"] }
        },
        industrial: {
          title: "工业场景 · 维护策略效果报告",
          summary: "在控制设备工况、累计运行时长与历史告警后，预防性维护策略与未来 30 天停机时长下降相关。",
          summaryList: ["平均处理效应：−14.2 小时", "95% 置信区间：−21.8 ～ −6.5 小时，不跨 0", "证据强度：中等，结论适用于观察期内的设备样本"],
          treatment: "预防性维护 vs 故障后维修",
          outcome: "未来 30 天停机时长",
          adjustment: "设备工况、累计运行时长、历史告警",
          sample: "1,248 台设备 · 6 周观察期",
          steps: ["按设备编号合并传感器、工单和停机记录。", "用因果图识别工况与维护策略的共同原因。", "估计维护策略对停机时长的平均影响。", "按设备类型分层，并检查不同观察窗口。"],
          adjustmentNote: "只使用维护决策之前可观测的工况与历史告警，不把维护后的告警次数作为调整变量。",
          effect: "−14.2 小时",
          ci: "−21.8 ～ −6.5 小时",
          period: "维护后 30 天",
          robustness: "按设备类型和运行负荷分层后，效果方向保持一致；仍需结合未记录的现场操作差异复核。",
          robustnessList: ["停机时长下降不等同于所有设备都能减少相同时间。", "建议回到工单和传感器原始记录复核异常设备。"],
          sourceNote: "方法选择：因果图识别设备工况混杂，结合设备类型分层复核结论。",
          sources: ["factory-maintenance.csv", "causal-methods.pdf"],
          graphLabel: "设备工况影响维护策略和停机时长，维护策略影响停机时长，停机时长影响返修率",
          graph: { size: ["设备工况", "混杂变量", "设备工况，混杂变量"], treatment: ["预防性维护", "处理变量", "预防性维护，处理变量"], visit: ["停机时长", "结果变量", "停机时长，结果变量"], repeat: ["返修率", "结果变量", "返修率，结果变量"] }
        }
      };

      function stopDemoLoop() {
        if (demoFrame) { window.cancelAnimationFrame(demoFrame); }
        demoFrame = 0;
        demoLastFrame = 0;
      }

      function syncPlayButton() {
        if (!playButton) { return; }
        playButton.hidden = !running;
        playButton.disabled = false;
        playButton.textContent = running ? (paused ? "继续演示" : "暂停演示") : idleButtonLabel;
        playButton.setAttribute("aria-pressed", String(running && paused));
      }

      function toggleDemoPause() {
        if (!running) { return; }
        paused = !paused;
        demo.classList.toggle("is-paused", paused);
        syncPlayButton();
      }

      function setSendState(isRunning) {
        if (!sendButton) { return; }
        sendButton.classList.toggle("is-running", isRunning);
        sendButton.setAttribute("aria-label", isRunning ? "取消任务" : "发送");
        if (!isRunning) { sendButton.disabled = !(input && input.value.trim()); }
        else { sendButton.disabled = false; }
      }

      function syncDraftState() {
        if (!running) { setSendState(false); }
      }

      function setSteps(states) {
        for (var i = 0; i < steps.length; i++) {
          var state = states[i] || "todo";
          var step = steps[i];
          if (!step) { continue; }
          step.setAttribute("data-state", state);
          var time = step.querySelector(".app-demo-step-time");
          if (time) {
            time.textContent = state === "active" ? "进行中" : state === "done" ? (i === 0 ? "0.4s" : i === 1 ? "1.2s" : "0.8s") : state === "failed" ? "失败" : "待开始";
          }
        }
      }

      function setThinking(status, duration, active) {
        if (thinkingStatus) { thinkingStatus.textContent = status; }
        if (thinkingDuration) { thinkingDuration.textContent = duration; }
        if (thinkingDots) { thinkingDots.hidden = !active; }
        var line = el("demo-thinking-line");
        if (line) { line.hidden = !active; }
      }

      function getReportStreamNodes() {
        if (!reportRow) { return []; }
        var title = reportRow.querySelector(".app-demo-report-title");
        var sections = Array.prototype.slice.call(reportRow.querySelectorAll(".app-demo-report-section"));
        return title ? [title].concat(sections) : sections;
      }

      function resetReportStream() {
        reportStreamNodes = getReportStreamNodes();
        reportStreamIndex = -1;
        reportStreamComplete = false;
        reportScrollActive = false;
        if (reportRow) { reportRow.classList.remove("is-streaming"); }
        for (var i = 0; i < reportStreamNodes.length; i++) { reportStreamNodes[i].removeAttribute("data-stream-state"); }
      }

      function startReportStream() {
        reportStreamNodes = getReportStreamNodes();
        reportStreamIndex = -1;
        reportStreamComplete = false;
        reportScrollActive = false;
        if (reportRow) { reportRow.classList.add("is-streaming"); }
        for (var i = 0; i < reportStreamNodes.length; i++) { reportStreamNodes[i].setAttribute("data-stream-state", "pending"); }
        if (conversation) { conversation.scrollTop = 0; }
      }

      function queueReportScroll(node, elapsed) {
        if (!conversation || !node) { return; }
        var conversationRect = conversation.getBoundingClientRect();
        var nodeRect = node.getBoundingClientRect();
        var targetTop = conversation.scrollTop + nodeRect.top - conversationRect.top - 12;
        var maxTop = Math.max(0, conversation.scrollHeight - conversation.clientHeight);
        targetTop = Math.max(0, Math.min(targetTop, maxTop));
        if (reduceMotion) {
          conversation.scrollTop = targetTop;
          reportScrollActive = false;
          return;
        }
        reportScrollFrom = conversation.scrollTop;
        reportScrollTo = targetTop;
        reportScrollStart = elapsed + REPORT_STREAM_SCROLL_DELAY;
        reportScrollDuration = REPORT_STREAM_SCROLL_DURATION;
        reportScrollActive = Math.abs(reportScrollTo - reportScrollFrom) > 0.5;
      }

      function paintReportScroll(elapsed) {
        if (!conversation || !reportScrollActive) { return; }
        var progress = Math.max(0, Math.min(1, (elapsed - reportScrollStart) / reportScrollDuration));
        var eased = 1 - Math.pow(1 - progress, 3);
        conversation.scrollTop = reportScrollFrom + (reportScrollTo - reportScrollFrom) * eased;
        if (progress >= 1) { reportScrollActive = false; }
      }

      function scrollConversationToBottom(behavior) {
        if (!conversation) { return; }
        var targetTop = conversation.scrollHeight;
        if (conversation.scrollTo) { conversation.scrollTo({ top: targetTop, behavior: behavior }); }
        else { conversation.scrollTop = targetTop; }
      }

      function completeReportStream() {
        if (reportStreamComplete) { return; }
        reportStreamComplete = true;
        reportScrollActive = false;
        for (var i = 0; i < reportStreamNodes.length; i++) { reportStreamNodes[i].setAttribute("data-stream-state", "done"); }
        if (reportRow) { reportRow.classList.remove("is-streaming"); }
        scrollConversationToBottom(reduceMotion ? "auto" : "smooth");
      }

      function paintReportStream(elapsed) {
        if (!reportRow || !reportRow.classList.contains("is-streaming") || !reportStreamNodes.length || reportStreamComplete) { return; }
        paintReportScroll(elapsed);
        var streamElapsed = Math.max(0, elapsed - DEMO_REPORT);
        var nextIndex = Math.min(reportStreamNodes.length - 1, Math.floor(streamElapsed / REPORT_STREAM_STEP));
        if (nextIndex !== reportStreamIndex) {
          reportStreamIndex = nextIndex;
          for (var i = 0; i < reportStreamNodes.length; i++) {
            reportStreamNodes[i].setAttribute("data-stream-state", i < nextIndex ? "done" : i === nextIndex ? "active" : "pending");
          }
          setThinking("正在生成报告 · " + (REPORT_STREAM_LABELS[nextIndex] || "报告内容"), (1.6 + streamElapsed / 1000).toFixed(1) + "s", true);
          queueReportScroll(reportStreamNodes[nextIndex], elapsed);
        }
        if (streamElapsed >= reportStreamNodes.length * REPORT_STREAM_STEP) { completeReportStream(); }
      }

      function showReportImmediately() {
        resetReportStream();
        if (reportRow) { reportRow.hidden = false; }
        scrollConversationToBottom("auto");
      }

      function enterDemoPhase(phase) {
        if (demoPhase >= phase) { return; }
        demoPhase = phase;
        if (phase === 0) {
          setThinking("等待发送", "0.0s", false);
        } else if (phase === 1) {
          if (input) { input.value = demoPrompt; }
          showConversation(demoPrompt);
          setSendState(true);
          setSteps(["active", "todo", "todo"]);
          setThinking("正在读取材料", "0.4s", true);
        } else if (phase === 2) {
          setSteps(["done", "active", "todo"]);
          setThinking("正在执行因果分析", "1.2s", true);
        } else if (phase === 3) {
          if (reportRow) { reportRow.hidden = false; }
          startReportStream();
          setSteps(["done", "done", "active"]);
          setThinking("正在生成报告", "1.6s", true);
        }
      }

      function paintDemo(elapsed) {
        if (!running) { return; }
        if (elapsed >= DEMO_SEND) { enterDemoPhase(1); }
        if (elapsed >= DEMO_ANALYZE) { enterDemoPhase(2); }
        if (elapsed >= DEMO_REPORT) { enterDemoPhase(3); }
        paintReportStream(elapsed);
        if (elapsed >= DEMO_FINISH) { finishDemo(); }
      }

      function tickDemo(now) {
        if (!running) { return; }
        if (!demoLastFrame) { demoLastFrame = now; }
        if (!paused) { demoElapsed += Math.min(80, now - demoLastFrame); }
        demoLastFrame = now;
        paintDemo(demoElapsed);
        if (running) { demoFrame = window.requestAnimationFrame(tickDemo); }
      }

      function startDemoLoop() {
        stopDemoLoop();
        demoFrame = window.requestAnimationFrame(tickDemo);
      }

      function hideUserMenu() {
        if (userMenu) { userMenu.hidden = true; }
        if (avatarButton) { avatarButton.setAttribute("aria-expanded", "false"); }
      }

      function closeMenu() { demo.classList.remove("is-menu-open"); }

      function markSession(key) {
        for (var i = 0; i < sessionButtons.length; i++) {
          sessionButtons[i].setAttribute("aria-pressed", String(sessionButtons[i].getAttribute("data-demo-session") === key));
        }
      }

      function resetComposer() {
        if (input) { input.disabled = false; }
        setSendState(false);
        syncPlayButton();
      }

      function resetGraph() {
        var nodes = Array.prototype.slice.call(demo.querySelectorAll("#demo-report-graph [data-demo-node]"));
        var edges = Array.prototype.slice.call(demo.querySelectorAll("#demo-report-graph [data-demo-edge]"));
        activeGraphNode = null;
        for (var i = 0; i < nodes.length; i++) {
          nodes[i].classList.remove("is-breathe");
          nodes[i].setAttribute("aria-pressed", "false");
        }
        for (var j = 0; j < edges.length; j++) { edges[j].classList.remove("is-related"); }
        if (graphReadout) { graphReadout.textContent = "点击一个节点，查看它在报告中的直接关系。"; }
      }

      function renderReportList(target, items) {
        if (!target) { return; }
        while (target.firstChild) { target.removeChild(target.firstChild); }
        for (var i = 0; i < items.length; i++) {
          var item = document.createElement("li");
          item.textContent = items[i];
          target.appendChild(item);
        }
      }

      function renderReport(key) {
        var report = REPORTS[key] || REPORTS.medical;
        activeReportKey = REPORTS[key] ? key : "medical";
        var textMap = {
          "demo-report-title": report.title,
          "demo-report-summary": report.summary,
          "demo-report-treatment": report.treatment,
          "demo-report-outcome": report.outcome,
          "demo-report-adjustment": report.adjustment,
          "demo-report-sample": report.sample,
          "demo-report-adjustment-note": report.adjustmentNote,
          "demo-report-effect": report.effect,
          "demo-report-ci": report.ci,
          "demo-report-period": report.period,
          "demo-report-robustness": report.robustness,
          "demo-report-source-note": report.sourceNote
        };
        for (var id in textMap) {
          if (!Object.prototype.hasOwnProperty.call(textMap, id)) { continue; }
          var node = el(id);
          if (node) { node.textContent = textMap[id]; }
        }
        renderReportList(el("demo-report-summary-list"), report.summaryList);
        renderReportList(el("demo-report-steps"), report.steps);
        renderReportList(el("demo-report-robustness-list"), report.robustnessList);
        renderReportList(el("demo-report-sources"), report.sources);
        var graph = el("demo-report-graph");
        if (graph) { graph.setAttribute("aria-label", report.graphLabel); }
        var graphIds = {
          size: ["demo-graph-size-name", "demo-graph-size-sub"],
          treatment: ["demo-graph-treatment-name", "demo-graph-treatment-sub"],
          visit: ["demo-graph-visit-name", "demo-graph-visit-sub"],
          repeat: ["demo-graph-repeat-name", "demo-graph-repeat-sub"]
        };
        for (var graphKey in graphIds) {
          if (!Object.prototype.hasOwnProperty.call(graphIds, graphKey)) { continue; }
          var values = report.graph[graphKey];
          var label = el(graphIds[graphKey][0]);
          var sub = el(graphIds[graphKey][1]);
          var group = demo.querySelector('[data-demo-node="' + graphKey + '"]');
          if (label) { label.textContent = values[0]; }
          if (sub) { sub.textContent = values[1]; }
          if (group) { group.setAttribute("aria-label", values[2]); }
        }
        resetGraph();
      }

      function resetNewChat() {
        stopDemoLoop();
        running = false;
        paused = false;
        demoElapsed = 0;
        demoPhase = -1;
        demo.classList.remove("is-conversation", "is-menu-open", "is-paused");
        if (messageRow) { messageRow.hidden = true; }
        if (reportRow) { reportRow.hidden = true; }
        resetReportStream();
        if (userText) { userText.textContent = defaultPrompt; }
        if (input) { input.value = defaultPrompt; }
        renderReport("medical");
        if (thinking) { thinking.classList.remove("is-collapsed"); }
        if (thinkingToggle) { thinkingToggle.setAttribute("aria-expanded", "true"); }
        setThinking("等待输入", "0.0s", false);
        setSteps(["todo", "todo", "todo"]);
        if (selectedFile) { selectedFile.hidden = true; }
        selectedFileName = "";
        for (var i = 0; i < fileButtons.length; i++) { fileButtons[i].setAttribute("aria-pressed", "false"); }
        resetGraph();
        markSession("new");
        idleButtonLabel = "播放演示";
        hideUserMenu();
        resetComposer();
        syncDraftState();
      }

      function showConversation(prompt) {
        demo.classList.add("is-conversation");
        if (messageRow) { messageRow.hidden = false; }
        if (userText) { userText.textContent = prompt; }
        if (conversation) { conversation.scrollTop = conversation.scrollHeight; }
      }

      function finishDemo() {
        stopDemoLoop();
        running = false;
        paused = false;
        demo.classList.remove("is-paused");
        setSteps(["done", "done", "done"]);
        setThinking("已处理", "7.6s", false);
        if (reportRow) { reportRow.hidden = false; }
        completeReportStream();
        idleButtonLabel = "重播演示";
        resetComposer();
        scrollConversationToBottom(reduceMotion ? "auto" : "smooth");
      }

      function cancelDemo() {
        stopDemoLoop();
        running = false;
        paused = false;
        demo.classList.remove("is-paused");
        setSteps(["done", "failed", "todo"]);
        setThinking("任务已取消", "0.9s", false);
        if (reportRow) { reportRow.hidden = true; }
        resetReportStream();
        idleButtonLabel = "重新播放";
        resetComposer();
      }

      function runDemo() {
        if (running) { return; }
        var prompt = input ? input.value.trim() : "";
        if (!prompt) { prompt = defaultPrompt; if (input) { input.value = prompt; } }
        demoPrompt = prompt;
        stopDemoLoop();
        running = true;
        paused = false;
        demoElapsed = 0;
        demoLastFrame = 0;
        demoPhase = -1;
        demo.classList.remove("is-conversation", "is-menu-open", "is-paused");
        renderReport(activeReportKey);
        if (input) { input.value = prompt; }
        if (messageRow) { messageRow.hidden = true; }
        if (reportRow) { reportRow.hidden = true; }
        resetReportStream();
        if (input) { input.disabled = true; }
        if (sendButton) {
          sendButton.classList.remove("is-running");
          sendButton.disabled = true;
          sendButton.setAttribute("aria-label", "发送");
        }
        setSteps(["todo", "todo", "todo"]);
        setThinking("等待输入", "0.0s", false);
        idleButtonLabel = "播放演示";
        syncPlayButton();
        enterDemoPhase(0);
        if (reduceMotion) {
          demoElapsed = DEMO_FINISH;
          paintDemo(demoElapsed);
          return;
        }
        startDemoLoop();
      }

      function showMedical() {
        stopDemoLoop();
        running = false;
        paused = false;
        demo.classList.remove("is-paused");
        renderReport("medical");
        showConversation("请评估强化康复方案对患者 90 天功能恢复的影响。");
        setSteps(["done", "done", "done"]);
        setThinking("已处理", "4.0s", false);
        showReportImmediately();
        idleButtonLabel = "重播演示";
        resetComposer();
      }

      function showIndustrial() {
        stopDemoLoop();
        running = false;
        paused = false;
        demo.classList.remove("is-paused");
        renderReport("industrial");
        showConversation("请评估预防性维护对未来 30 天停机时长的影响。");
        setSteps(["done", "done", "done"]);
        setThinking("已处理", "4.6s", false);
        showReportImmediately();
        idleButtonLabel = "播放演示";
        resetComposer();
      }

      function selectSession(key) {
        markSession(key);
        closeMenu();
        if (key === "medical") { showMedical(); }
        else if (key === "industrial") { showIndustrial(); }
        else { resetNewChat(); }
      }

      function setSelectedFile(name) {
        if (!selectedFile) { return; }
        selectedFileName = name;
        selectedFile.hidden = false;
        var kind = selectedFile.querySelector(".app-demo-file-kind");
        var strong = selectedFile.querySelector("strong");
        var meta = selectedFile.querySelector("small");
        var extension = name.split(".").pop().toUpperCase();
        if (kind) { kind.textContent = extension; }
        if (strong) { strong.textContent = name; }
        if (meta) { meta.textContent = extension + " · 128 KB"; }
        for (var i = 0; i < fileButtons.length; i++) { fileButtons[i].setAttribute("aria-pressed", String(fileButtons[i].getAttribute("data-demo-file") === name)); }
      }

      function toggleGraph(node) {
        var key = node ? node.getAttribute("data-demo-node") : "";
        var nodes = Array.prototype.slice.call(demo.querySelectorAll("#demo-report-graph [data-demo-node]"));
        var edges = Array.prototype.slice.call(demo.querySelectorAll("#demo-report-graph [data-demo-edge]"));
        var edgeMap = {
          size: ["size-treatment", "size-visit"],
          treatment: ["size-treatment", "treatment-visit"],
          visit: ["size-visit", "treatment-visit", "visit-repeat"],
          repeat: ["visit-repeat"]
        };
        activeGraphNode = node && activeGraphNode !== node ? node : null;
        var activeKey = activeGraphNode ? activeGraphNode.getAttribute("data-demo-node") : "";
        var activeEdges = edgeMap[activeKey] || [];
        for (var i = 0; i < nodes.length; i++) {
          var isActive = nodes[i] === activeGraphNode;
          nodes[i].classList.toggle("is-breathe", isActive);
          nodes[i].setAttribute("aria-pressed", String(isActive));
        }
        for (var j = 0; j < edges.length; j++) { edges[j].classList.toggle("is-related", activeEdges.indexOf(edges[j].getAttribute("data-demo-edge")) >= 0); }
        if (graphReadout) {
          graphReadout.textContent = activeGraphNode ? activeGraphNode.getAttribute("aria-label") + "：节点以呼吸提示，虚线表示直接关系。" : "点击一个节点，查看它在报告中的直接关系。";
        }
      }

      function drawNetwork() {
        var canvas = el("demo-network");
        if (!canvas || !canvas.parentElement) { return; }
        var width = canvas.parentElement.clientWidth;
        var height = canvas.parentElement.clientHeight;
        if (!width || !height) { return; }
        var pixelRatio = window.devicePixelRatio || 1;
        canvas.width = Math.round(width * pixelRatio);
        canvas.height = Math.round(height * pixelRatio);
        var ctx = canvas.getContext("2d");
        if (!ctx) { return; }
        ctx.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
        ctx.clearRect(0, 0, width, height);
        var nodes = [[0.08, 0.18], [0.08, 0.52], [0.13, 0.84], [0.28, 0.34], [0.29, 0.72], [0.48, 0.5], [0.67, 0.2], [0.68, 0.8], [0.86, 0.43], [0.94, 0.2], [0.94, 0.74]];
        var edges = [[0, 3], [1, 3], [1, 4], [2, 4], [3, 5], [4, 5], [5, 6], [5, 7], [6, 8], [7, 8], [8, 9], [8, 10]];
        ctx.strokeStyle = "rgba(23, 23, 23, 0.11)";
        ctx.lineWidth = 1;
        for (var i = 0; i < edges.length; i++) {
          var from = nodes[edges[i][0]];
          var to = nodes[edges[i][1]];
          ctx.beginPath();
          ctx.moveTo(from[0] * width, from[1] * height);
          ctx.lineTo(to[0] * width, to[1] * height);
          ctx.stroke();
        }
        ctx.fillStyle = "rgba(23, 23, 23, 0.17)";
        for (var j = 0; j < nodes.length; j++) {
          ctx.beginPath();
          ctx.arc(nodes[j][0] * width, nodes[j][1] * height, j === 5 ? 3.5 : 2.3, 0, Math.PI * 2);
          ctx.fill();
        }
      }

      if (playButton) {
        playButton.addEventListener("click", function () {
          if (running) { toggleDemoPause(); }
        });
      }
      if (sendButton) { sendButton.addEventListener("click", function () { if (running) { cancelDemo(); } else { runDemo(); } }); }
      if (input) {
        input.addEventListener("input", syncDraftState);
        input.addEventListener("keydown", function (event) {
          if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); if (!running) { runDemo(); } }
        });
      }
      if (searchButton) {
        searchButton.addEventListener("click", function () {
          var active = searchButton.getAttribute("aria-pressed") !== "true";
          searchButton.setAttribute("aria-pressed", String(active));
          searchButton.classList.toggle("is-active", active);
        });
      }
      if (uploadButton) { uploadButton.addEventListener("click", function () { setSelectedFile(selectedFileName || "clinical-outcomes.csv"); }); }
      if (fileRemove) { fileRemove.addEventListener("click", function () { if (selectedFile) { selectedFile.hidden = true; } selectedFileName = ""; }); }
      if (newChatButton) { newChatButton.addEventListener("click", resetNewChat); }
      if (menuButton) { menuButton.addEventListener("click", function () { demo.classList.toggle("is-menu-open"); }); }
      if (scrim) { scrim.addEventListener("click", closeMenu); }
      if (collapseButton) {
        collapseButton.addEventListener("click", function () {
          var collapsed = demo.classList.toggle("is-collapsed");
          collapseButton.setAttribute("aria-expanded", String(!collapsed));
          collapseButton.setAttribute("aria-label", collapsed ? "展开侧边栏" : "收起侧边栏");
        });
      }
      var railActions = Array.prototype.slice.call(demo.querySelectorAll("[data-demo-action]"));
      for (var ri = 0; ri < railActions.length; ri++) {
        railActions[ri].addEventListener("click", function () {
          var action = this.getAttribute("data-demo-action");
          if (action === "new") { resetNewChat(); }
          else { demo.classList.remove("is-collapsed"); }
        });
      }
      for (var si = 0; si < sessionButtons.length; si++) {
        sessionButtons[si].addEventListener("click", function () { selectSession(this.getAttribute("data-demo-session")); });
      }
      for (var fi = 0; fi < fileButtons.length; fi++) {
        fileButtons[fi].addEventListener("click", function () { setSelectedFile(this.getAttribute("data-demo-file")); });
      }
      if (thinkingToggle) {
        thinkingToggle.addEventListener("click", function () {
          var expanded = thinkingToggle.getAttribute("aria-expanded") !== "true";
          thinkingToggle.setAttribute("aria-expanded", String(expanded));
          if (thinking) { thinking.classList.toggle("is-collapsed", !expanded); }
        });
      }
      var stepButtons = Array.prototype.slice.call(demo.querySelectorAll(".app-demo-step-header"));
      for (var pi = 0; pi < stepButtons.length; pi++) {
        stepButtons[pi].setAttribute("aria-expanded", "false");
        stepButtons[pi].addEventListener("click", function () {
          var parent = this.parentElement;
          var open = parent.classList.toggle("is-expanded");
          this.setAttribute("aria-expanded", String(open));
        });
      }
      if (avatarButton) {
        avatarButton.addEventListener("click", function () {
          if (!userMenu) { return; }
          userMenu.hidden = !userMenu.hidden;
          avatarButton.setAttribute("aria-expanded", String(!userMenu.hidden));
        });
      }
      if (userClose) { userClose.addEventListener("click", hideUserMenu); }
      var graphNodes = Array.prototype.slice.call(demo.querySelectorAll("#demo-report-graph [data-demo-node]"));
      for (var gi = 0; gi < graphNodes.length; gi++) {
        (function (node) {
          node.addEventListener("click", function () { toggleGraph(node); });
          node.addEventListener("keydown", function (event) { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggleGraph(node); } });
        })(graphNodes[gi]);
      }
      window.setTimeout(drawNetwork, 0);
      window.addEventListener("resize", drawNetwork);
      resetNewChat();
    })();

    /* ---------- 因果图：只留下与当前节点直接相连的部分 ---------- */
    var dag = el("dag");
    var dagReadout = el("dag-readout");
    if (dag) {
      var dagNodes = list("#dag [data-node]");
      var dagEdges = list("#dag [data-dag-edge]");
      var activateDag = function (node) {
        var keys = node ? String(node.getAttribute("data-links")).split(",") : [];
        dag.classList.toggle("is-dim", !!node);
        for (var i = 0; i < dagNodes.length; i++) {
          dagNodes[i].classList.toggle("is-on", keys.indexOf(dagNodes[i].getAttribute("data-node")) >= 0);
        }
        for (var j = 0; j < dagEdges.length; j++) {
          dagEdges[j].classList.toggle("is-on", keys.indexOf(dagEdges[j].id) >= 0);
        }
        if (dagReadout) {
          dagReadout.textContent = node ? node.getAttribute("aria-label") : "选择或聚焦任意节点，查看它的变量角色与直接关系。";
        }
      };
      for (var di = 0; di < dagNodes.length; di++) {
        (function (node) {
          node.addEventListener("mouseenter", function () { activateDag(node); });
          node.addEventListener("mouseleave", function () { activateDag(null); });
          node.addEventListener("focus", function () { activateDag(node); });
          node.addEventListener("blur", function () { activateDag(null); });
          node.addEventListener("keydown", function (ev) {
            if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); activateDag(node); }
          });
        })(dagNodes[di]);
      }
    }

    /* ---------- 效应估计：每一行的读数 ---------- */
    var ciRows = list(".ci-row");
    var ciReadout = el("ci-readout");
    for (var ri = 0; ri < ciRows.length; ri++) {
      (function (row) {
        var read = row.getAttribute("data-read") || "";
        row.setAttribute("role", "group");
        row.setAttribute("aria-label", read);
        var on = function () { row.classList.add("is-on"); if (ciReadout) { ciReadout.textContent = read; } };
        var off = function () { row.classList.remove("is-on"); };
        row.addEventListener("mouseenter", on);
        row.addEventListener("mouseleave", off);
        row.addEventListener("focus", on);
        row.addEventListener("blur", off);
      })(ciRows[ri]);
    }

    /* ---------- 平行趋势：切换情景时逐帧插值，线条真的在动 ---------- */
    var TREND = {
      control: [100, 100, 101, 100, 101, 100, 101, 100, 102, 103, 102, 104],
      promo: [100, 101, 99, 102, 100, 101, 103, 100, 108, 114, 119, 124],
      discount: [100, 99, 101, 100, 102, 99, 101, 100, 97, 95, 93, 92],
      channel: [100, 102, 101, 103, 102, 104, 103, 102, 110, 115, 118, 122]
    };
    var SCEN = {
      promo: { label: "促销力度", effect: 4.6 },
      discount: { label: "折扣深度", effect: -1.8 },
      channel: { label: "渠道投放", effect: 5.1 }
    };
    var trendTreated = el("trend-treated");
    var trendControl = el("trend-control");
    var trendReadout = el("trend-readout");
    var trendValues = TREND.promo.slice();
    var trendAnim = 0;

    function trendX(i) { return 60 + i * (630 / 11); }
    function trendY(v) { return 270 - (v - 88) * (240 / 44); }
    function trendPath(values) {
      var parts = [];
      for (var i = 0; i < values.length; i++) {
        parts.push((i ? "L" : "M") + trendX(i).toFixed(1) + " " + trendY(values[i]).toFixed(1));
      }
      return parts.join(" ");
    }
    function drawTrend(values) { if (trendTreated) { trendTreated.setAttribute("d", trendPath(values)); } }
    if (trendControl) { trendControl.setAttribute("d", trendPath(TREND.control)); }
    drawTrend(trendValues);

    function setScenario(key, animate) {
      var target = TREND[key];
      if (!target) { return; }
      if (trendReadout) {
        trendReadout.textContent = SCEN[key].label + " · 干预之后相对干预之前的差额：" + (SCEN[key].effect > 0 ? "+" : "") + SCEN[key].effect.toFixed(1) + "%（演示数据）。";
      }
      if (!animate || reduceMotion) {
        trendValues = target.slice();
        drawTrend(trendValues);
        return;
      }
      var from = trendValues.slice();
      var start = 0;
      var duration = 460;
      trendAnim += 1;
      var ticket = trendAnim;
      var step = function (now) {
        if (ticket !== trendAnim) { return; }
        if (!start) { start = now; }
        var t = Math.min(1, (now - start) / duration);
        var e = 1 - Math.pow(1 - t, 3);
        var mid = [];
        for (var i = 0; i < target.length; i++) { mid.push(from[i] + (target[i] - from[i]) * e); }
        drawTrend(mid);
        trendValues = mid;
        if (t < 1) { window.requestAnimationFrame(step); }
        else { trendValues = target.slice(); drawTrend(trendValues); }
      };
      window.requestAnimationFrame(step);
    }

    var scenBtns = list("#scenario button");
    for (var bi = 0; bi < scenBtns.length; bi++) {
      (function (btn) {
        btn.addEventListener("click", function () {
          for (var i = 0; i < scenBtns.length; i++) { scenBtns[i].setAttribute("aria-pressed", String(scenBtns[i] === btn)); }
          setScenario(btn.getAttribute("data-scenario"), true);
        });
      })(scenBtns[bi]);
    }

    /* ---------- 两种进入方式 ---------- */
    var accessBtns = list("#access-seg button");
    for (var ai = 0; ai < accessBtns.length; ai++) {
      (function (btn) {
        btn.addEventListener("click", function () {
          var key = btn.getAttribute("data-access");
          for (var i = 0; i < accessBtns.length; i++) { accessBtns[i].setAttribute("aria-pressed", String(accessBtns[i] === btn)); }
          var panes = [el("access-desktop"), el("access-browser")];
          for (var j = 0; j < panes.length; j++) {
            if (panes[j]) { panes[j].hidden = panes[j].id !== "access-" + key; }
          }
        });
      })(accessBtns[ai]);
    }

    /* ---------- 只看差异 ---------- */
    var table = el("compare-table");
    var diffBtn = el("only-diff");
    var diffStatus = el("diff-status");
    if (table && diffBtn) {
      function markComparisonRows() {
        var rows = table.tBodies.length ? table.tBodies[0].rows : [];
        for (var ri = 0; ri < rows.length; ri++) {
          var cells = rows[ri].cells;
          var same = cells.length > 1;
          var firstValue = same ? cells[1].textContent.replace(/\s+/g, " ").trim() : "";
          for (var ci = 2; same && ci < cells.length; ci++) {
            same = cells[ci].textContent.replace(/\s+/g, " ").trim() === firstValue;
          }
          rows[ri].classList.toggle("is-same", same);
          rows[ri].classList.toggle("is-diff", !same);
        }
      }

      function updateDiffStatus(on) {
        if (!diffStatus) { return; }
        var rows = table.tBodies.length ? table.tBodies[0].rows : [];
        var diffCount = 0;
        for (var ri = 0; ri < rows.length; ri++) { if (rows[ri].classList.contains("is-diff")) { diffCount += 1; } }
        diffStatus.textContent = on ? "已筛选：显示 " + diffCount + " 项差异" : "显示全部 " + rows.length + " 项对比";
      }

      diffBtn.addEventListener("click", function () {
        var on = diffBtn.getAttribute("aria-pressed") !== "true";
        diffBtn.setAttribute("aria-pressed", String(on));
        markComparisonRows();
        table.classList.toggle("only-diff", on);
        diffBtn.textContent = on ? "显示全部" : "只看差异";
        updateDiffStatus(on);
      });

      markComparisonRows();
      updateDiffStatus(false);
    }

    /* ---------- 通告条 ---------- */
    var notice = el("notice");
    var noticeClose = el("notice-close");
    if (notice && noticeClose) {
      noticeClose.addEventListener("click", function () {
        notice.classList.add("is-closed");
        window.setTimeout(function () { notice.hidden = true; }, reduceMotion ? 0 : 340);
      });
    }

    /* ---------- 切页弹簧 ----------
       站内所有「切到某一页」的动作——章节索引、跳过链接、回到顶部、链路索引，
       以及滚动停住后的落点吸附——共用同一条临界阻尼弹簧。
       每一帧按真实经过的时间解一次解析解，而不是按帧数往上累加：
       60Hz、120Hz 与中途掉一帧走的是同一条轨迹，运动节奏不会随屏幕刷新率变化。
       固有频率随行程远近调整，短程干脆落停，长程把峰值速度压住，内容不会被拖成一片虚影。
       滚轮、触摸、按键、指针按下与拖动滚动条都会立刻接管；中途改切别的页时速度连续、不跳变。
       系统偏好减少动态时不启用，一律退回即时跳转。 */
    var springNav = (function () {
      var root = document.documentElement;
      var OMEGA_MAX = 10.5; /* 短程的固有频率：越高收束越快 */
      var OMEGA_MIN = 6.5;  /* 长程的下限：再低，最后几十像素会拖成一段慢爬 */
      var V_PEAK = 7000;    /* 目标峰值速度，px/s；60Hz 下每秒约 117px */
      /* 临界阻尼从静止起步，峰值速度约为 0.368 × 频率 × 行程，用它把频率反推出来 */
      var PEAK_RATIO = 1 / Math.E;
      var raf = 0;
      var pos = 0;
      var vel = 0;
      var goal = 0;
      var omega = OMEGA_MAX;
      var lastTs = 0;
      var arrive = null;

      function restore() { root.style.scrollBehavior = ""; }

      function stop() {
        if (raf) { window.cancelAnimationFrame(raf); raf = 0; }
        vel = 0;
        restore();
        arrive = null;
      }

      function settle() {
        var done = arrive;
        pos = goal;
        window.scrollTo(0, goal);
        stop();
        if (done) { done(); }
      }

      /* 行程越远，频率越低：峰值速度被 V_PEAK 压住，短程仍用上限 */
      function freqFor(distance) {
        var w = V_PEAK / (PEAK_RATIO * Math.max(1, distance));
        if (w > OMEGA_MAX) { return OMEGA_MAX; }
        if (w < OMEGA_MIN) { return OMEGA_MIN; }
        return w;
      }

      /* 临界阻尼的解析解：代入位移与速度，一次算出 dt 之后的位移与速度。
         x 是「当前位置减目标」，所以收束就是 x 与 v 一起衰减到零。 */
      function advance(x, v, w, dt) {
        var decay = Math.exp(-w * dt);
        var b = v + w * x;
        return { x: (x + b * dt) * decay, v: (v - w * b * dt) * decay };
      }

      function frame(ts) {
        raf = 0;
        var actual = window.pageYOffset || root.scrollTop || 0;
        /* 拖动滚动条这类脚本外的滚动出现时，立刻把控制权交回去 */
        if (Math.abs(actual - pos) > 96) { stop(); return; }
        var dt = lastTs ? (ts - lastTs) / 1000 : 0.008;
        lastTs = ts;
        if (!(dt > 0)) { dt = 0.008; }
        /* 掉帧或从别的标签页切回来时，不让弹簧一步跨过整段行程 */
        if (dt > 0.05) { dt = 0.05; }
        var next = advance(pos - goal, vel, omega, dt);
        pos = goal + next.x;
        vel = next.v;
        window.scrollTo(0, pos);
        /* 滚动位置与跟着它走的那些动画在同一帧里更新，不再各等各的事件与帧 */
        paintScroll();
        if (Math.abs(pos - goal) < 0.5 && Math.abs(vel) < 20) { settle(); return; }
        raf = window.requestAnimationFrame(frame);
      }

      function to(targetY, onArrive) {
        var max = Math.max(0, root.scrollHeight - root.clientHeight);
        var y = Math.max(0, Math.min(max, targetY));
        if (reduceMotion) {
          stop();
          window.scrollTo(0, y);
          if (onArrive) { onArrive(); }
          return;
        }
        pos = window.pageYOffset || root.scrollTop || 0;
        if (!raf && Math.abs(y - pos) < 1) {
          if (onArrive) { onArrive(); }
          return;
        }
        goal = y;
        omega = freqFor(Math.abs(goal - pos));
        arrive = onArrive || null;
        if (!raf) { vel = 0; }
        root.style.scrollBehavior = "auto";
        if (!raf) {
          lastTs = 0;
          raf = window.requestAnimationFrame(frame);
        }
      }

      return {
        to: to,
        cancel: stop,
        running: function () { return raf !== 0; }
      };
    })();

    /* ---------- 滚动状态：进度线、导航细线、章节索引、回到顶部 ----------
       每帧只读一次滚动位置与文档高度，其余判断都用测好的缓存值：
       滚动时逐帧去问每个章节的位置会强制重排，正好和弹簧的每一帧撞在一起。 */
    var progress = el("progress");
    var topnav = el("topnav");
    var toTop = el("to-top");
    var RAIL_HEAD = 150; /* 章节顶边进到视口这个位置以内，就算走到了这一节 */
    var railLinks = list("#chapter-rail a");
    var railSections = [];
    for (var rl = 0; rl < railLinks.length; rl++) {
      var target = document.querySelector(railLinks[rl].getAttribute("href"));
      if (target) { railSections.push({ link: railLinks[rl], node: target, top: 0 }); }
    }

    function measureRail() {
      var base = window.pageYOffset || document.documentElement.scrollTop || 0;
      for (var i = 0; i < railSections.length; i++) {
        railSections[i].top = railSections[i].node.getBoundingClientRect().top + base;
      }
    }

    /* 章节索引与跳过链接：交给切页弹簧送达，落定后再把哈希写进地址栏 */
    var navHead = (topnav ? topnav.offsetHeight : 72) + 16;
    window.addEventListener("resize", function () { navHead = (topnav ? topnav.offsetHeight : 72) + 16; });

    function pageTopOf(node) {
      return Math.max(0, node.getBoundingClientRect().top + (window.pageYOffset || 0) - navHead);
    }

    function bindSpringLink(a) {
      if (!a) { return; }
      var hash = a.getAttribute("href") || "";
      if (hash.charAt(0) !== "#") { return; }
      var node = document.querySelector(hash);
      if (!node) { return; }
      a.addEventListener("click", function (event) {
        if (reduceMotion || event.defaultPrevented) { return; }
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) { return; }
        event.preventDefault();
        springNav.to(pageTopOf(node), function () {
          if (window.history && window.history.pushState) { window.history.pushState(null, "", hash); }
        });
      });
    }

    for (var rlb = 0; rlb < railLinks.length; rlb++) { bindSpringLink(railLinks[rlb]); }
    bindSpringLink(document.querySelector(".skip"));
    bindSpringLink(document.querySelector('.topnav a[href="#top"]'));
    var lastPaintY = -1;
    var lastDocHeight = -1;
    var ticking = false;

    function setCurrent(link, on) {
      var want = on ? "true" : "false";
      if (link.getAttribute("aria-current") !== want) { link.setAttribute("aria-current", want); }
    }

    /* 一次把跟滚动有关的东西全部画完：读布局在前，写样式在后，中间不来回穿插。
       弹簧每一帧也走这里，所以滚过去的那一帧和普通滚动的那一帧没有区别。 */
    function paintScroll(force) {
      var doc = document.documentElement;
      var y = window.pageYOffset || doc.scrollTop || 0;
      if (!force && y === lastPaintY) { return; }
      lastPaintY = y;
      var docHeight = doc.scrollHeight;
      var max = docHeight - doc.clientHeight;
      /* 字体或图片迟到、文档高度变了，章节位置跟着重测一次 */
      if (docHeight !== lastDocHeight) { lastDocHeight = docHeight; measureRail(); }
      if (progress) { progress.style.transform = "scaleX(" + (max > 0 ? Math.min(1, y / max) : 0).toFixed(4) + ")"; }
      if (!reduceMotion) { setHeroProgress(y / heroRevealSpan); }
      if (topnav) { topnav.classList.toggle("is-scrolled", y > 8); }
      if (toTop) { toTop.classList.toggle("is-on", y > window.innerHeight * 0.6); }
      var currentIndex = 0;
      for (var i = 0; i < railSections.length; i++) {
        if (railSections[i].top - RAIL_HEAD <= y) { currentIndex = i; }
      }
      for (var j = 0; j < railSections.length; j++) {
        setCurrent(railSections[j].link, j === currentIndex);
      }
      /* 跟着滚动走的 GSAP 动画（首屏交接、逐字变亮、图形描线）与这一次滚动同帧落笔 */
      if (hasST) { window.ScrollTrigger.update(); }
    }

    function onScroll(force) {
      if (force) { paintScroll(true); return; }
      if (ticking) { return; }
      ticking = true;
      window.requestAnimationFrame(function () { ticking = false; paintScroll(); });
    }

    /* 窗口变化、页面与字体就位之后，章节位置全部重测一遍 */
    function remeasure() { measureRail(); paintScroll(true); }
    window.addEventListener("scroll", function () { onScroll(); }, { passive: true });
    window.addEventListener("resize", remeasure);
    window.addEventListener("load", remeasure);
    if (document.fonts && document.fonts.ready && document.fonts.ready.then) { document.fonts.ready.then(remeasure); }
    if (hasST) { window.ScrollTrigger.addEventListener("refresh", measureRail); }
    measureRail();
    if (toTop) {
      toTop.addEventListener("click", function () {
        if (reduceMotion) { window.scrollTo(0, 0); return; }
        springNav.to(0);
      });
    }

    /* ---------- 分析链路：左侧索引跟随右侧推进 ---------- */
    var pipeBtns = list("#pipe-index button");
    function setPipeActive(index) {
      for (var i = 0; i < pipeBtns.length; i++) { pipeBtns[i].setAttribute("aria-current", String(i === index)); }
    }
    for (var pi = 0; pi < pipeBtns.length; pi++) {
      (function (btn, index) {
        btn.addEventListener("click", function () {
          var row = document.querySelector('.ledger-row[data-ledger="' + index + '"]');
          if (!row) { return; }
          if (reduceMotion) { row.scrollIntoView({ block: "center" }); return; }
          var rect = row.getBoundingClientRect();
          var y = rect.top + (window.pageYOffset || 0) - (window.innerHeight - rect.height) / 2;
          springNav.to(y);
        });
      })(pipeBtns[pi], pi);
    }

    /* ---------- 首屏图形：立体因果图 ----------
       Pearl 体系里最典型的一张图：两个共同原因指向干预变量，干预经两条中介路径到达结果，
       另有一条直接影响。节点、连线、脉冲都先算三维坐标，再按透视投影落到画布上，靠远近
       决定大小、深浅与遮挡关系。参数全部写死，换机器、换刷新顺序，画出来都一样。 */
    var canvas = el("hero-network");
    var ctx = canvas && canvas.getContext ? canvas.getContext("2d") : null;
    var fieldWidth = 0;
    var fieldHeight = 0;
    var pointer = { x: 0, y: 0 };
    var pointerTarget = { x: 0, y: 0 };
    var TAU = Math.PI * 2;

    /* 六个变量：x/y/z 是三维坐标，size 是半径，solid 的两个是干预与结果。
       坐标按当前相机参数反解，投影后六个点落在一个半径约 0.80 的圆上。 */
    var CAUSAL_NODES = [
      { id: "scale",  x: -0.466, y: -0.651, z: 0.000, size: 0.030, solid: false },
      { id: "market", x: -0.909, y:  0.145, z: 0.000, size: 0.028, solid: false },
      { id: "promo",  x: -0.449, y:  0.770, z: 0.000, size: 0.042, solid: true  },
      { id: "visit",  x:  0.455, y:  0.635, z: 0.000, size: 0.031, solid: false },
      { id: "basket", x:  0.932, y: -0.148, z: 0.000, size: 0.029, solid: false },
      { id: "repeat", x:  0.455, y: -0.780, z: 0.000, size: 0.044, solid: true  }
    ];
    var CAUSAL_NODE_BY_ID = {};
    for (var cni = 0; cni < CAUSAL_NODES.length; cni++) {
      CAUSAL_NODE_BY_ID[CAUSAL_NODES[cni].id] = CAUSAL_NODES[cni];
    }

    /* 八条有向边：bowY 与 bowZ 把曲线的控制点往上下、前后推，让每条弦沿圆向外鼓；period 与 offset 决定脉冲节奏。 */
    var CAUSAL_EDGES = [
      { from: "scale",  to: "promo",  bowY: -0.165, bowZ:  0.584, period: 5200, offset: 0.04 },
      { from: "market", to: "promo",  bowY:  0.019, bowZ:  0.439, period: 6100, offset: 0.44 },
      { from: "promo",  to: "visit",  bowY:  0.315, bowZ: -0.002, period: 4200, offset: 0.20 },
      { from: "promo",  to: "basket", bowY:  0.395, bowZ: -0.350, period: 4700, offset: 0.58 },
      { from: "visit",  to: "repeat", bowY:  0.155, bowZ: -0.584, period: 5000, offset: 0.32 },
      { from: "basket", to: "repeat", bowY: -0.018, bowZ: -0.440, period: 5600, offset: 0.78 },
      { from: "promo",  to: "repeat", bowY:  0.252, bowZ: -0.438, period: 6600, offset: 0.12 },
      { from: "scale",  to: "repeat", bowY: -0.315, bowZ:  0.004, period: 7400, offset: 0.66 }
    ];
    var EDGE_STEPS = 20;
    var VIEW = { yaw: -0.54, pitch: -0.30, dist: 3.4 };

    function sizeCanvas() {
      if (!ctx) { return; }
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      var rect = canvas.getBoundingClientRect();
      fieldWidth = rect.width;
      fieldHeight = rect.height;
      canvas.width = Math.floor(fieldWidth * dpr);
      canvas.height = Math.floor(fieldHeight * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function clamp01(v) { return v < 0 ? 0 : (v > 1 ? 1 : v); }

    /* 三维点按当前相机转成正交坐标，k 是透视缩放，也用来判断远近 */
    function project(p) {
      var cy = Math.cos(VIEW.yaw), sy = Math.sin(VIEW.yaw);
      var x1 = p.x * cy + p.z * sy;
      var z1 = -p.x * sy + p.z * cy;
      var cp = Math.cos(VIEW.pitch), sp = Math.sin(VIEW.pitch);
      var y1 = p.y * cp - z1 * sp;
      var z2 = p.y * sp + z1 * cp;
      var k = VIEW.dist / (VIEW.dist - z2);
      return { x: x1, y: y1, k: k };
    }

    function depthOf(k) { return clamp01((k - 0.80) / 0.45); }

    function bezier3(a, b, ctrl, t) {
      var u = 1 - t;
      return {
        x: u * u * a.x + 2 * u * t * ctrl.x + t * t * b.x,
        y: u * u * a.y + 2 * u * t * ctrl.y + t * t * b.y,
        z: u * u * a.z + 2 * u * t * ctrl.z + t * t * b.z
      };
    }

    function sampleEdge(pts, t) {
      var last = pts.length - 1;
      var idx = clamp01(t) * last;
      var i0 = Math.floor(idx);
      var i1 = Math.min(last, i0 + 1);
      var f = idx - i0;
      return {
        x: pts[i0].x + (pts[i1].x - pts[i0].x) * f,
        y: pts[i0].y + (pts[i1].y - pts[i0].y) * f,
        k: pts[i0].k + (pts[i1].k - pts[i0].k) * f
      };
    }

    /* 箭头：沿着曲线最后一段的方向，落在目标球的边缘上 */
    function drawArrowHead(pts, targetR, depth) {
      var last = pts.length - 1;
      var tip = pts[last];
      var prev = pts[last - 3];
      var dx = tip.x - prev.x;
      var dy = tip.y - prev.y;
      var len = Math.sqrt(dx * dx + dy * dy) || 1;
      dx = dx / len;
      dy = dy / len;
      var size = 4.8 * tip.k;
      var tipX = tip.x - dx * (targetR + 1.8);
      var tipY = tip.y - dy * (targetR + 1.8);
      var baseX = tipX - dx * size;
      var baseY = tipY - dy * size;
      var nx = -dy * size * 0.50;
      var ny = dx * size * 0.50;
      var col = "rgba(96, 96, 96, " + (0.28 + 0.28 * depth).toFixed(3) + ")";
      ctx.beginPath();
      ctx.moveTo(tipX, tipY);
      ctx.lineTo(baseX + nx, baseY + ny);
      ctx.lineTo(baseX - nx, baseY - ny);
      ctx.closePath();
      ctx.fillStyle = col;
      ctx.fill();
      /* 用圆角描边把三角形的尖角磨圆，箭头读起来更软 */
      ctx.lineJoin = "round";
      ctx.lineWidth = size * 0.42;
      ctx.strokeStyle = col;
      ctx.stroke();
    }

    /* 脉冲：一个墨点带着逐渐变淡的拖尾沿边前进，读起来是影响在传导 */
    function drawPulse(pts, edge, time, depth) {
      var t = ((time / edge.period) + edge.offset) % 1;
      var head = sampleEdge(pts, t);
      var alpha = 0.40 + 0.60 * depth;
      var s, back, a, r;
      for (s = 5; s >= 1; s--) {
        a = alpha * (0.30 - s * 0.045);
        if (a <= 0.01) { continue; }
        r = (2.6 - s * 0.32) * head.k;
        if (r <= 0.4) { continue; }
        back = sampleEdge(pts, t - s * 0.013);
        ctx.beginPath();
        ctx.arc(back.x, back.y, r, 0, TAU);
        ctx.fillStyle = "rgba(23, 23, 23, " + a.toFixed(3) + ")";
        ctx.fill();
      }
      var glow = ctx.createRadialGradient(head.x, head.y, 0, head.x, head.y, 11 * head.k);
      glow.addColorStop(0, "rgba(23, 23, 23, " + (0.18 * alpha).toFixed(3) + ")");
      glow.addColorStop(1, "rgba(23, 23, 23, 0)");
      ctx.beginPath();
      ctx.arc(head.x, head.y, 11 * head.k, 0, TAU);
      ctx.fillStyle = glow;
      ctx.fill();
      ctx.beginPath();
      ctx.arc(head.x, head.y, 2.6 * head.k, 0, TAU);
      ctx.fillStyle = "rgba(23, 23, 23, " + (0.80 * alpha).toFixed(3) + ")";
      ctx.fill();
    }

    /* 球体：径向渐变给出左上方的受光面，深的两个再多一层浅投影，把它从纸面上托起来 */
    function drawNodeSphere(node, x, y, r, light) {
      var g;
      /* 球体外面垫一层很淡的光晕，边缘不再是一圈硬切的圆 */
      g = ctx.createRadialGradient(x, y, r * 0.55, x, y, r * 2.0);
      g.addColorStop(0, "rgba(23, 23, 23, " + (0.07 * light).toFixed(3) + ")");
      g.addColorStop(1, "rgba(23, 23, 23, 0)");
      ctx.beginPath();
      ctx.arc(x, y, r * 2.0, 0, TAU);
      ctx.fillStyle = g;
      ctx.fill();
      if (node.solid) {
        g = ctx.createRadialGradient(x - r * 0.36, y - r * 0.42, r * 0.08, x, y, r * 1.04);
        g.addColorStop(0, "rgba(108, 108, 108, " + (0.95 * light).toFixed(3) + ")");
        g.addColorStop(0.44, "rgba(46, 46, 46, " + (0.98 * light).toFixed(3) + ")");
        g.addColorStop(1, "rgba(15, 15, 15, " + light.toFixed(3) + ")");
        ctx.save();
        ctx.shadowColor = "rgba(23, 23, 23, " + (0.18 * light).toFixed(3) + ")";
        ctx.shadowBlur = r * 2.4;
        ctx.shadowOffsetY = r * 0.5;
        ctx.beginPath();
        ctx.arc(x, y, r, 0, TAU);
        ctx.fillStyle = g;
        ctx.fill();
        ctx.restore();
        return;
      }
      g = ctx.createRadialGradient(x - r * 0.34, y - r * 0.40, r * 0.06, x, y, r * 1.02);
      g.addColorStop(0, "rgba(255, 255, 255, " + light.toFixed(3) + ")");
      g.addColorStop(0.58, "rgba(246, 246, 246, " + (0.98 * light).toFixed(3) + ")");
      g.addColorStop(1, "rgba(198, 198, 198, " + (0.94 * light).toFixed(3) + ")");
      ctx.beginPath();
      ctx.arc(x, y, r, 0, TAU);
      ctx.fillStyle = g;
      ctx.fill();
      ctx.strokeStyle = "rgba(126, 126, 126, " + (0.28 * light).toFixed(3) + ")";
      ctx.lineWidth = 1;
      ctx.stroke();
    }

    function drawGraph(time) {
      if (!ctx) { return; }
      ctx.clearRect(0, 0, fieldWidth, fieldHeight);
      var unit = Math.min(fieldWidth * 0.34, fieldHeight * 0.56);
      var cx = fieldWidth * 0.56 + pointer.x * 14;
      var cy = fieldHeight * 0.5 + pointer.y * 12;

      /* 相机缓慢摆动；鼠标位置只做很小的偏移，图形不会跟着指针乱晃 */
      VIEW.yaw = -0.54 + Math.sin(time * 0.00016) * 0.13 + pointer.x * 0.07;
      VIEW.pitch = -0.30 + Math.sin(time * 0.00011) * 0.035 - pointer.y * 0.04;

      var placed = [];
      var placedById = {};
      var i, j;
      for (i = 0; i < CAUSAL_NODES.length; i++) {
        var node = CAUSAL_NODES[i];
        var q = project(node);
        var entry = {
          node: node,
          x: cx + q.x * unit,
          y: cy + q.y * unit,
          r: node.size * unit * q.k * (1 + 0.05 * Math.sin(time * 0.0011 + i * 0.9)),
          depth: depthOf(q.k),
          k: q.k
        };
        placed.push(entry);
        placedById[node.id] = entry;
      }

      /* 先画线与脉冲，节点后画：近处的球自然挡住远处的线 */
      for (i = 0; i < CAUSAL_EDGES.length; i++) {
        var edge = CAUSAL_EDGES[i];
        var a = CAUSAL_NODE_BY_ID[edge.from];
        var b = CAUSAL_NODE_BY_ID[edge.to];
        var ctrl = {
          x: (a.x + b.x) / 2,
          y: (a.y + b.y) / 2 + edge.bowY,
          z: (a.z + b.z) / 2 + edge.bowZ
        };
        var pts = [];
        var depthSum = 0;
        for (j = 0; j <= EDGE_STEPS; j++) {
          var pq = project(bezier3(a, b, ctrl, j / EDGE_STEPS));
          pts.push({ x: cx + pq.x * unit, y: cy + pq.y * unit, k: pq.k });
          depthSum += depthOf(pq.k);
        }
        var depth = depthSum / pts.length;
        ctx.beginPath();
        for (j = 0; j < pts.length; j++) {
          if (j === 0) { ctx.moveTo(pts[j].x, pts[j].y); } else { ctx.lineTo(pts[j].x, pts[j].y); }
        }
        ctx.strokeStyle = "rgba(104, 104, 104, " + (0.12 + 0.26 * depth).toFixed(3) + ")";
        ctx.lineWidth = 1.0 + 0.6 * depth;
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        ctx.stroke();
        drawArrowHead(pts, placedById[edge.to].r, depth);
        drawPulse(pts, edge, time, depth);
      }

      placed.sort(function (m, n) { return m.k - n.k; });
      for (i = 0; i < placed.length; i++) {
        var p = placed[i];
        drawNodeSphere(p.node, p.x, p.y, p.r, 0.62 + 0.38 * p.depth);
      }
    }

    var rafId = 0;
    var elapsed = 0;
    var lastTs = 0;

    function heroTick(now) {
      if (!lastTs) { lastTs = now; }
      elapsed += now - lastTs;
      lastTs = now;
      pointer.x += (pointerTarget.x - pointer.x) * 0.05;
      pointer.y += (pointerTarget.y - pointer.y) * 0.05;
      drawGraph(elapsed);
      rafId = window.requestAnimationFrame(heroTick);
    }

    function startHeroDraw() {
      if (rafId || reduceMotion || !ctx) { return; }
      lastTs = 0;
      rafId = window.requestAnimationFrame(heroTick);
    }

    function stopHeroDraw() {
      if (!rafId) { return; }
      window.cancelAnimationFrame(rafId);
      rafId = 0;
    }

    if (ctx) {
      sizeCanvas();
      drawGraph(0);
      window.addEventListener("resize", function () { sizeCanvas(); drawGraph(elapsed); });
      if (!reduceMotion) {
        window.addEventListener("pointermove", function (event) {
          pointerTarget.x = (event.clientX / window.innerWidth - 0.5) * 2;
          pointerTarget.y = (event.clientY / window.innerHeight - 0.5) * 2;
        }, { passive: true });
        /* 首屏滚出视口就停笔，不在后台空转；滚回来接着原来的相位往下走 */
        if (window.IntersectionObserver) {
          new window.IntersectionObserver(function (entries) {
            for (var i = 0; i < entries.length; i++) {
              heroInView = entries[i].isIntersecting;
              syncHeroDraw();
            }
          }, { rootMargin: "180px 0px" }).observe(canvas);
        } else {
          startHeroDraw();
        }
      }
    }

    /* ---------- 首屏滚动交接：标题退场，工作台界面转为清晰 ---------- */
    var heroShowcase = el("top");
    var heroTrack = el("hero-track");
    var heroBackdrop = el("hero-backdrop");
    var heroForeground = el("hero-foreground");
    var heroRevealSpan = 1;
    var heroProgress = 0;
    var heroInView = true;

    /* 前景彻底隐去之后就不必再画，画布不在视口里也停笔 */
    function syncHeroDraw() {
      if (reduceMotion || !ctx) { return; }
      if (heroInView && heroProgress < 0.72) { startHeroDraw(); } else { stopHeroDraw(); }
    }

    /* ---------- 首屏左下角浅色因果结构：独立绘制，不干扰右侧主图 ---------- */
    var ambientCanvas = el("hero-network-ambient");
    var ambientCtx = ambientCanvas && ambientCanvas.getContext ? ambientCanvas.getContext("2d") : null;
    var ambientWidth = 0;
    var ambientHeight = 0;
    var ambientRafId = 0;
    var ambientVisible = true;
    var AMBIENT_NODES = [
      { x: 0.12, y: 0.58, phase: 0.4 },
      { x: 0.28, y: 0.28, phase: 1.6 },
      { x: 0.38, y: 0.78, phase: 2.3 },
      { x: 0.56, y: 0.46, phase: 0.9 },
      { x: 0.73, y: 0.20, phase: 2.8 },
      { x: 0.86, y: 0.68, phase: 1.2 }
    ];
    var AMBIENT_EDGES = [
      [0, 3, 0.08], [1, 3, 0.34], [1, 4, 0.58], [2, 3, 0.76],
      [3, 5, 0.20], [4, 5, 0.46], [0, 2, 0.88]
    ];

    function sizeAmbientCanvas() {
      if (!ambientCtx) { return; }
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      var rect = ambientCanvas.getBoundingClientRect();
      ambientWidth = rect.width;
      ambientHeight = rect.height;
      ambientCanvas.width = Math.floor(ambientWidth * dpr);
      ambientCanvas.height = Math.floor(ambientHeight * dpr);
      ambientCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function ambientCurvePoint(a, b, c, t) {
      var u = 1 - t;
      return {
        x: u * u * a.x + 2 * u * t * c.x + t * t * b.x,
        y: u * u * a.y + 2 * u * t * c.y + t * t * b.y
      };
    }

    function drawAmbient(time) {
      if (!ambientCtx) { return; }
      ambientCtx.clearRect(0, 0, ambientWidth, ambientHeight);
      var nodes = [];
      var i;
      for (i = 0; i < AMBIENT_NODES.length; i++) {
        nodes.push({ x: ambientWidth * AMBIENT_NODES[i].x, y: ambientHeight * AMBIENT_NODES[i].y });
      }

      ambientCtx.lineCap = "round";
      ambientCtx.lineJoin = "round";
      ambientCtx.lineWidth = Math.max(0.55, Math.min(0.9, ambientWidth / 720));
      for (i = 0; i < AMBIENT_EDGES.length; i++) {
        var edge = AMBIENT_EDGES[i];
        var from = nodes[edge[0]];
        var to = nodes[edge[1]];
        var bend = (i % 2 === 0 ? -1 : 1) * ambientHeight * 0.085;
        var ctrl = { x: (from.x + to.x) * 0.5, y: (from.y + to.y) * 0.5 + bend };
        ambientCtx.beginPath();
        ambientCtx.moveTo(from.x, from.y);
        ambientCtx.quadraticCurveTo(ctrl.x, ctrl.y, to.x, to.y);
        ambientCtx.strokeStyle = "rgba(23, 23, 23, 0.16)";
        ambientCtx.stroke();

        var arrow = ambientCurvePoint(from, to, ctrl, 0.94);
        var before = ambientCurvePoint(from, to, ctrl, 0.88);
        var angle = Math.atan2(arrow.y - before.y, arrow.x - before.x);
        var arrowSize = Math.max(2.5, Math.min(4, ambientWidth / 170));
        ambientCtx.beginPath();
        ambientCtx.moveTo(arrow.x, arrow.y);
        ambientCtx.lineTo(arrow.x - Math.cos(angle - 0.48) * arrowSize, arrow.y - Math.sin(angle - 0.48) * arrowSize);
        ambientCtx.moveTo(arrow.x, arrow.y);
        ambientCtx.lineTo(arrow.x - Math.cos(angle + 0.48) * arrowSize, arrow.y - Math.sin(angle + 0.48) * arrowSize);
        ambientCtx.strokeStyle = "rgba(23, 23, 23, 0.13)";
        ambientCtx.stroke();

        var pulseT = (time * 0.000035 + edge[2]) % 1;
        var pulse = ambientCurvePoint(from, to, ctrl, pulseT);
        ambientCtx.beginPath();
        ambientCtx.arc(pulse.x, pulse.y, Math.max(1.2, Math.min(2.2, ambientWidth / 340)), 0, TAU);
        ambientCtx.fillStyle = "rgba(23, 23, 23, 0.24)";
        ambientCtx.fill();
      }

      for (i = 0; i < nodes.length; i++) {
        var node = nodes[i];
        var source = AMBIENT_NODES[i];
        var breathe = 1 + Math.sin(time * 0.0011 + source.phase) * 0.08;
        var radius = Math.max(1.5, Math.min(3, ambientWidth / 260)) * breathe;
        ambientCtx.beginPath();
        ambientCtx.arc(node.x, node.y, radius, 0, TAU);
        ambientCtx.fillStyle = "rgba(23, 23, 23, 0.18)";
        ambientCtx.fill();
        ambientCtx.beginPath();
        ambientCtx.arc(node.x, node.y, radius * 2.1, 0, TAU);
        ambientCtx.strokeStyle = "rgba(23, 23, 23, 0.09)";
        ambientCtx.stroke();
      }
    }

    function ambientTick(now) {
      drawAmbient(now);
      ambientRafId = window.requestAnimationFrame(ambientTick);
    }

    function startAmbientDraw() {
      if (ambientRafId || reduceMotion || !ambientCtx) { return; }
      ambientRafId = window.requestAnimationFrame(ambientTick);
    }

    function stopAmbientDraw() {
      if (!ambientRafId) { return; }
      window.cancelAnimationFrame(ambientRafId);
      ambientRafId = 0;
    }

    function syncAmbientDraw() {
      if (reduceMotion || !ambientCtx || !ambientVisible || heroProgress >= 0.72) {
        stopAmbientDraw();
        return;
      }
      startAmbientDraw();
    }

    if (ambientCtx) {
      sizeAmbientCanvas();
      drawAmbient(0);
      window.addEventListener("resize", function () { sizeAmbientCanvas(); drawAmbient(0); });
      if (!reduceMotion) {
        if (window.IntersectionObserver) {
          new window.IntersectionObserver(function (entries) {
            for (var ai = 0; ai < entries.length; ai++) {
              ambientVisible = entries[ai].isIntersecting;
              syncAmbientDraw();
            }
          }, { rootMargin: "180px 0px" }).observe(ambientCanvas);
        } else {
          syncAmbientDraw();
        }
      }
    }

    function setHeroProgress(ratio) {
      if (!heroShowcase) { return; }
      var p = ratio < 0 ? 0 : (ratio > 1 ? 1 : ratio);
      /* 前景比背景晚半步起步：先按住一小会儿，再和背景的清晰化交棒 */
      var fg = p <= 0.10 ? 0 : (p - 0.10) / 0.86;
      if (fg > 1) { fg = 1; }
      heroProgress = p;
      heroShowcase.style.setProperty("--hero-p", p.toFixed(4));
      heroShowcase.style.setProperty("--hero-q", fg.toFixed(4));
      var sharp = p >= 1;
      if (heroBackdrop) {
        heroBackdrop.classList.toggle("is-interactive", sharp);
        heroBackdrop.inert = !sharp;
      }
      if (heroForeground) { heroForeground.classList.toggle("is-hidden", p >= 0.72); }
      syncHeroDraw();
      syncAmbientDraw();
    }

    /* ---------- 工作台等比缩放：按固定设计框算出系数，交给样式层使用 ---------- */
    var workbenchFit = el("workbench-fit");
    var workbenchScaleQuery = window.matchMedia
      ? window.matchMedia("(min-width: 900px) and (prefers-reduced-motion: no-preference)")
      : null;

    function measureWorkbench() {
      if (!heroShowcase) { return; }
      if (!workbenchFit || (workbenchScaleQuery && !workbenchScaleQuery.matches)) {
        heroShowcase.style.setProperty("--workbench-scale", "1");
        return;
      }
      var box = window.getComputedStyle(workbenchFit);
      /* clientWidth 含内边距，减掉才是真正可用的内容区，免得缩放系数被算大 */
      var availWidth = workbenchFit.clientWidth - parseFloat(box.paddingLeft || 0) - parseFloat(box.paddingRight || 0);
      var availHeight = workbenchFit.clientHeight - parseFloat(box.paddingTop || 0) - parseFloat(box.paddingBottom || 0);
      if (!(availWidth > 0) || !(availHeight > 0)) { return; }
      var fit = Math.min(availWidth / 1280, availHeight / 880, 1);
      if (!(fit > 0)) { return; }
      heroShowcase.style.setProperty("--workbench-scale", fit.toFixed(4));
    }

    if (workbenchScaleQuery) {
      if (workbenchScaleQuery.addEventListener) { workbenchScaleQuery.addEventListener("change", measureWorkbench); }
      else if (workbenchScaleQuery.addListener) { workbenchScaleQuery.addListener(measureWorkbench); }
    }
    if (document.fonts && document.fonts.ready && document.fonts.ready.then) {
      document.fonts.ready.then(measureWorkbench);
    }

    function measureHero() {
      if (!heroShowcase || !heroTrack) { return; }
      measureWorkbench();
      var span = heroShowcase.offsetHeight - heroTrack.offsetHeight;
      if (!(span > 0)) { span = heroTrack.offsetHeight; }
      /* 滚过这段距离，交接就走完，剩下的行程留给工作台界面 */
      heroRevealSpan = span * 0.55;
      if (reduceMotion) { return; }
      var doc = document.documentElement;
      var y = window.pageYOffset || doc.scrollTop || 0;
      setHeroProgress(y / heroRevealSpan);
    }

    measureHero();
    window.addEventListener("resize", measureHero);
    window.addEventListener("load", measureHero);

    /* ---------- 衡量章：逐字拆开，好让滚动能逐字推上去 ---------- */
    var measureText = el("measure-text");
    if (measureText) {
      var raw = measureText.textContent;
      measureText.textContent = "";
      var frag = document.createDocumentFragment();
      for (var mi = 0; mi < raw.length; mi++) {
        var ch = raw.charAt(mi);
        var span = document.createElement("span");
        span.className = "w";
        span.textContent = ch;
        frag.appendChild(span);
      }
      measureText.appendChild(frag);
    }

    /* ---------- 动效：一次写完，减少动态时整段跳过 ---------- */
      if (hasGsap && !reduceMotion) {
      gsap.from("#hero-title .ch", { opacity: 0, duration: 0.5, ease: "power2.out", stagger: 0.026, delay: 0.06 });
      gsap.from("#hero-title", { y: 16, duration: 0.8, ease: "power3.out" });
      gsap.from(".ch-mark-rule", { scaleX: 0, duration: 0.22, ease: "power2.out", delay: 0.38 });
      gsap.from("[data-hero-fade]", { y: 14, opacity: 0, duration: 0.6, ease: "power2.out", stagger: 0.08, delay: 0.32 });

      list("[data-reveal]").forEach(function (node) {
        gsap.from(node, { y: 18, opacity: 0, duration: 0.55, ease: "power2.out", scrollTrigger: { trigger: node, start: "top 80%", once: true } });
      });
      list("[data-reveal-stagger]").forEach(function (group) {
        gsap.from(group.children, { y: 16, opacity: 0, duration: 0.5, ease: "power2.out", stagger: 0.05, scrollTrigger: { trigger: group, start: "top 76%", once: true } });
      });

      var researchSection = el("research");
      var researchEntries = list("#research .research-entry");
      if (researchSection && researchEntries.length && hasST) {
        var researchMotionMs = parseFloat(window.getComputedStyle(document.documentElement).getPropertyValue("--motion-enter")) || 320;
        var researchStaggerMs = parseFloat(window.getComputedStyle(researchSection).getPropertyValue("--research-stagger")) || 40;
        gsap.from(researchEntries, { y: 12, opacity: 0, duration: researchMotionMs / 1000, ease: "power2.out", stagger: researchStaggerMs / 1000, scrollTrigger: { trigger: researchSection, start: "top 78%", once: true } });
      }

      var improvementChartStart = "top 94%";
      list("[data-count]").forEach(function (node) {
        var goal = parseFloat(node.getAttribute("data-count")) || 0;
        var decimals = parseInt(node.getAttribute("data-count-decimals"), 10);
        if (isNaN(decimals)) { decimals = 0; }
        var prefix = node.getAttribute("data-count-prefix") || "";
        var suffix = node.getAttribute("data-count-suffix") || "";
        var grouped = node.getAttribute("data-count-grouped") === "true";
        var chartRow = node.closest ? node.closest(".improvement-chart-row") : null;
        var box = { v: 0 };
        gsap.to(box, {
          v: goal, duration: 1.1, ease: "power2.out", snap: { v: 1 / Math.pow(10, decimals) },
          onUpdate: function () {
            var value = box.v.toFixed(decimals);
            if (grouped) { value = value.replace(/\\B(?=(\\d{3})+(?!\\d))/g, ","); }
            node.textContent = prefix + value + suffix;
          },
          scrollTrigger: { trigger: chartRow || node, start: chartRow ? improvementChartStart : "top 92%", once: true }
        });
      });

      var improvementChartRows = list("#improvement .improvement-chart-row");
      if (hasST) {
        improvementChartRows.forEach(function (row) {
          var path = row.querySelector(".improvement-chart-path");
          var nodes = Array.prototype.slice.call(row.querySelectorAll(".improvement-chart-node"));
          if (path && nodes.length) {
            var nodeProgress = nodes.map(function (node) { return parseFloat(node.getAttribute("data-path-progress")); });
            var drawState = { progress: 0 };
            path.style.strokeDashoffset = "1";
            for (var ni = 0; ni < nodes.length; ni++) { nodes[ni].style.opacity = "0"; }
            gsap.fromTo(drawState, { progress: 0 }, {
              progress: 1,
              duration: 1.2,
              ease: "power2.inOut",
              onUpdate: function () {
                var progress = drawState.progress;
                path.style.strokeDashoffset = String(1 - progress);
                for (var pi = 0; pi < nodes.length; pi++) {
                  nodes[pi].style.opacity = progress >= nodeProgress[pi] ? "1" : "0";
                }
              },
              onComplete: function () {
                path.style.strokeDashoffset = "0";
                for (var pi = 0; pi < nodes.length; pi++) { nodes[pi].style.opacity = "1"; }
              },
              scrollTrigger: { trigger: row, start: improvementChartStart, once: true }
            });
          }
        });
      }

      list("[data-plate]").forEach(function (plate) {
        var inner = plate.querySelector("[data-plate-inner]") || plate;
        gsap.fromTo(plate, { scale: 0.965, opacity: 0.35 }, { scale: 1, opacity: 1, ease: "none", scrollTrigger: { trigger: plate, start: "top 84%", end: "top 56%", scrub: true } });
        gsap.fromTo(inner, { opacity: 1 }, { opacity: 0.25, ease: "none", scrollTrigger: { trigger: plate, start: "bottom 26%", end: "bottom -6%", scrub: true } });
      });

      var edges = list("#dag .dag-edge");
      if (edges.length) {
        gsap.fromTo(edges, { strokeDashoffset: 1200 }, { strokeDashoffset: 0, duration: 1.1, ease: "power2.inOut", stagger: 0.07, scrollTrigger: { trigger: "#fig-dag", start: "top 78%", once: true } });
        gsap.from("#dag .dag-node", { opacity: 0, duration: 0.5, ease: "power2.out", stagger: 0.08, delay: 0.4, scrollTrigger: { trigger: "#fig-dag", start: "top 78%", once: true } });
      }

      var ciLines = list(".ci-line");
      if (ciLines.length) {
        gsap.fromTo(ciLines, { strokeDashoffset: 400 }, { strokeDashoffset: 0, duration: 0.7, ease: "power2.out", stagger: 0.04, scrollTrigger: { trigger: "#fig-ci", start: "top 82%", once: true } });
        gsap.from(".ci-mark", { scale: 0, opacity: 0, duration: 0.45, ease: "back.out(2)", stagger: 0.06, delay: 0.3, transformOrigin: "50% 50%", scrollTrigger: { trigger: "#fig-ci", start: "top 82%", once: true } });
      }

      if (trendTreated) {
        gsap.fromTo(trendTreated, { strokeDashoffset: 900 }, { strokeDashoffset: 0, duration: 1.2, ease: "power2.inOut", scrollTrigger: { trigger: "#fig-trend", start: "top 82%", once: true } });
      }

      gsap.fromTo("#pipe-fill", { scaleY: 0 }, { scaleY: 1, transformOrigin: "50% 0%", ease: "none", scrollTrigger: { trigger: "#pipeline", start: "top 55%", end: "bottom 78%", scrub: true } });

      if (hasST) {
        list(".ledger-row").forEach(function (row, index) {
          window.ScrollTrigger.create({
            trigger: row, start: "top 60%", end: "bottom 45%",
            onEnter: function () { setPipeActive(index); },
            onEnterBack: function () { setPipeActive(index); }
          });
        });
      }

      var words = list("#measure-text .w");
      if (words.length) {
        gsap.fromTo(words, { opacity: 0.12 }, { opacity: 1, ease: "none", stagger: 0.22, scrollTrigger: { trigger: "#measure-text", start: "top 84%", end: "bottom 58%", scrub: true } });
      }

      window.addEventListener("load", function () { if (hasST) { window.ScrollTrigger.refresh(); } });
    }

    /* ---------- 材料卡入场：小节到来，下面两张自己升到停靠位置，不靠继续滚动 ---------- */
    (function () {
      var materials = document.getElementById("materials");
      var slots = list(".stack-slot");
      if (!materials || !slots.length || reduceMotion) { return; }

      var cards = [];
      for (var i = 0; i < slots.length; i++) {
        var card = slots[i].querySelector(".stack-card");
        if (!card) { return; }
        cards.push(card);
      }

      var started = false;
      var timer = 0;

      function start() {
        if (started) { return; }
        started = true;
        window.clearTimeout(timer);
        window.removeEventListener("scroll", check);
        window.removeEventListener("resize", check);
        for (var i = 0; i < cards.length; i++) {
          cards[i].setAttribute("data-rise", "in");
        }
      }

      /* 小节顶边走到视口上五分之一以内就开始，不必等它完全贴顶；
         停在小节里不动时也放一次，免得页面停在中间、三张卡一直留在起点。 */
      function check() {
        if (started) { return; }
        var top = materials.getBoundingClientRect().top;
        var tall = window.innerHeight;
        if (top <= tall * 0.2) { start(); return; }
        window.clearTimeout(timer);
        timer = window.setTimeout(function () {
          if (started) { return; }
          if (materials.getBoundingClientRect().top <= tall * 0.7) { start(); }
        }, 450);
      }

      /* 先落到起点，再等小节到来放开，这样不会先闪一下再升起来 */
      for (var j = 0; j < cards.length; j++) {
        cards[j].style.setProperty("--rise-delay", (j * 80) + "ms");
        cards[j].setAttribute("data-rise", "prep");
      }
      window.addEventListener("scroll", check, { passive: true });
      window.addEventListener("resize", check);
      check();
    })();

    selectSession(current);
    onScroll();
    /* ---------- 每节落点：滚动停住后，同一条切页弹簧把最近的一节送到位 ---------- */
    (function () {
      if (reduceMotion) { return; }
      var main = document.getElementById("main");
      var hero = document.querySelector(".hero-showcase");
      var nav = document.querySelector(".topnav");
      var materials = document.getElementById("materials");
      if (!main || !hero) { return; }
      var stops = [];
      var heroEnd = 0;
      var materialsTop = -1;
      var materialsEnd = -1;
      var timer = 0;

      function measure() {
        var base = window.pageYOffset;
        var head = (nav ? nav.offsetHeight : 72) + 16;
        var rect = hero.getBoundingClientRect();
        heroEnd = rect.top + base + rect.height;
        var mrect = materials ? materials.getBoundingClientRect() : null;
        materialsTop = mrect ? mrect.top + base - head : -1;
        materialsEnd = mrect ? mrect.bottom + base - head : -1;
        stops = list("#main .section, #main .measure, #main .closing").map(function (node) {
          return Math.max(0, node.getBoundingClientRect().top + base - head);
        }).sort(function (a, b) { return a - b; });
      }

      /* 首屏那段交接不参与，里面的工作台要能自由停住；
         材料小节也不参与：三张卡一路叠上来要走完将近两屏的滚动，
         中途一停顿就被吸回节首的话，后面两张永远上不来；
         停到距某一节起点三成屏幕以内时接管，把它送到位 */
      function nearest() {
        var y = window.pageYOffset;
        if (y <= heroEnd + 40) { return -1; }
        if (materialsTop >= 0 && y >= materialsTop && y <= materialsEnd) { return -1; }
        var gap = window.innerHeight * 0.3;
        var best = -1;
        for (var i = 0; i < stops.length; i++) {
          var d = Math.abs(stops[i] - y);
          if (d < gap) { gap = d; best = i; }
        }
        return best;
      }

      function glide() {
        timer = 0;
        if (springNav.running()) { return; }
        var i = nearest();
        if (i < 0) { return; }
        if (Math.abs(stops[i] - window.pageYOffset) < 2) { return; }
        springNav.to(stops[i]);
      }

      function later() {
        if (springNav.running()) { return; }
        window.clearTimeout(timer);
        /* 停手之后再多等一会儿：等浏览器自己的滚动惯性和平滑滚动彻底停稳，
           弹簧才从真正静止的位置起步，不会和还没走完的那点滚动互相拉扯 */
        timer = window.setTimeout(glide, 200);
      }

      function drop() {
        window.clearTimeout(timer);
        springNav.cancel();
      }

      /* 拖动滚动条时立刻交还控制权：滚动条属于 html 本身，
         按在页面内容上的抬手会落在后代元素上，不会进到这里 */
      function dropOnBar(event) {
        if (event.target === document.documentElement) { drop(); }
      }

      measure();
      window.addEventListener("load", measure);
      window.addEventListener("resize", measure);
      window.addEventListener("scroll", later, { passive: true });
      window.addEventListener("pointerdown", dropOnBar, { passive: true });
      window.addEventListener("wheel", drop, { passive: true });
      window.addEventListener("touchstart", drop, { passive: true });
      window.addEventListener("keydown", drop, { passive: true });
    })();
    /* ---------- 全站指针：白色圆点跟随指针，按下时白色细环扩散 ---------- */
    (function () {
      var fine = window.matchMedia && window.matchMedia("(hover: hover) and (pointer: fine)").matches;
      var dot = document.getElementById("cursor-dot");
      if (!fine || !dot) { return; }
      var hot = null;
      var queued = false;
      var px = 0;
      var py = 0;

      function paint() {
        queued = false;
        dot.style.transform = "translate3d(" + px + "px," + py + "px,0)";
      }

      function onMove(event) {
        px = event.clientX;
        py = event.clientY;
        if (!dot.classList.contains("is-visible")) { dot.classList.add("is-visible"); }
        if (!queued) { queued = true; window.requestAnimationFrame(paint); }
        var target = event.target;
        var next = target && target.closest ? target.closest("a, button, [role=tab], input, textarea, select, label") : null;
        if (next !== hot) {
          hot = next;
          dot.classList.toggle("is-hot", !!hot);
        }
      }

      /* 按下时补一圈白色细环；减少动态时不生成，节点在动画结束后自行回收 */
      function onDown(event) {
        if (reduceMotion) { return; }
        var ring = document.createElement("span");
        var core = document.createElement("i");
        ring.className = "cursor-ripple";
        ring.setAttribute("aria-hidden", "true");
        ring.style.transform = "translate3d(" + event.clientX + "px," + event.clientY + "px,0)";
        ring.appendChild(core);
        core.addEventListener("animationend", function () { ring.remove(); });
        document.body.appendChild(ring);
      }

      document.documentElement.classList.add("has-cursor-dot");
      window.addEventListener("pointermove", onMove, { passive: true });
      window.addEventListener("pointerdown", onDown, { passive: true });
      window.addEventListener("blur", function () { dot.classList.remove("is-visible"); });
      document.addEventListener("pointerleave", function (event) {
        if (!event.relatedTarget) { dot.classList.remove("is-visible"); }
      });
    })();
  })();
}
