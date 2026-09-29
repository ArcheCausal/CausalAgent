/* 交互逻辑逐字取自官网原型 docs.html；导出初始化函数，由页面组件在挂载后调用。 */
export function initDocs() {
  (function () {
    "use strict";

    var reduce = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

    /* ---------- 文档清单：顺序决定上一篇与下一篇 ---------- */
    var DOCS = [
      { id: "overview", title: "概览", keywords: "工作台 因果 分析 链路 预处理 结构学习 后处理 报告 边界 输入 输出 权限 平台事实" },
      { id: "quickstart", title: "快速开始", keywords: "入口 地址 权限 注册 用户名 密码 登录 回跳 Cookie 浏览器" },
      { id: "workspace", title: "工作区", keywords: "会话 地址 前进 后退 文件列表 设置 退出 语言 用户协议 用户手册 检查更新" },
      { id: "materials", title: "材料与文件", keywords: "CSV 上传 文件 知识库 联网检索 arXiv Crossref OpenAlex 引用 提问 降级" },
      { id: "tasks", title: "任务执行与过程", keywords: "阶段 决策 工具调用 停止 取消 等待输入 继续 断线 重连 预算 上限 超时" },
      { id: "results", title: "结果与报告", keywords: "因果图 节点 缩放 拖动 图表 直方图 柱状图 热力图 报告 章节 来源 证据 局限 追问" },
      { id: "algorithms", title: "算法说明", keywords: "PC DirectLiNGAM CDFM threshold 假设 潜在混杂 边权 因果顺序 零样本 后处理 环路 校验" },
      { id: "account", title: "账号与权限", keywords: "权限 dashboard.access rag_eval.access admin.access 禁用 失效 重置 密码 桌面客户端" },
      { id: "faq", title: "常见问题", keywords: "变量 结论 数据 保存 检索 失败 导出 停止 会话" }
    ];
    var BY_ID = {};
    DOCS.forEach(function (doc) { BY_ID[doc.id] = doc; });
    var DEFAULT_ID = DOCS[0].id;

    var articles = Array.prototype.slice.call(document.querySelectorAll(".doc"));
    var treeLinks = Array.prototype.slice.call(document.querySelectorAll("[data-doc-link]"));
    var outlineList = document.getElementById("outline");
    var main = document.getElementById("docs-main");
    var nav = document.getElementById("topnav");
    var current = null;
    var spyTargets = [];

    function navOffset() {
      return (nav ? nav.offsetHeight : 72) + 24;
    }
    function scrollBehavior() {
      return reduce ? "auto" : "smooth";
    }

    /* ---------- 切换文档 ---------- */
    function activate(id, opts) {
      var doc = BY_ID[id] || BY_ID[DEFAULT_ID];
      var next = doc.id;
      var article = document.getElementById("doc-" + next);

      current = next;
      articles.forEach(function (item) {
        item.hidden = item.getAttribute("data-doc") !== next;
      });
      treeLinks.forEach(function (link) {
        if (link.getAttribute("data-doc-link") === next) {
          link.setAttribute("aria-current", "true");
        } else {
          link.removeAttribute("aria-current");
        }
      });
      document.title = doc.title + " · 使用文档 · CausalAgent";
      buildOutline();
      updatePager();

      if (opts && opts.scroll && article) {
        var top = article.getBoundingClientRect().top + window.scrollY - navOffset();
        window.scrollTo({ top: Math.max(0, top), behavior: scrollBehavior() });
      }
      if (opts && opts.focus && main) {
        main.focus({ preventScroll: true });
      }
    }

    function indexOfDoc(id) {
      for (var i = 0; i < DOCS.length; i += 1) {
        if (DOCS[i].id === id) return i;
      }
      return 0;
    }

    /* ---------- 上一篇与下一篇 ---------- */
    var pagerPrev = document.getElementById("pager-prev");
    var pagerNext = document.getElementById("pager-next");
    var pagerPrevTitle = document.getElementById("pager-prev-title");
    var pagerNextTitle = document.getElementById("pager-next-title");

    function setPager(link, titleEl, doc) {
      if (!doc) {
        link.hidden = true;
        return;
      }
      link.hidden = false;
      link.setAttribute("href", "#/" + doc.id);
      titleEl.textContent = doc.title;
    }

    function updatePager() {
      var index = indexOfDoc(current);
      setPager(pagerPrev, pagerPrevTitle, DOCS[index - 1]);
      setPager(pagerNext, pagerNextTitle, DOCS[index + 1]);
    }

    /* ---------- 本页目录与跟随 ---------- */
    function scrollToAnchor(id) {
      var el = document.getElementById(id);
      if (!el) return;
      el.scrollIntoView({ behavior: scrollBehavior(), block: "start" });
    }

    function buildOutline() {
      var article = document.querySelector('.doc[data-doc="' + current + '"]');
      outlineList.innerHTML = "";
      spyTargets = [];
      if (!article) return;
      Array.prototype.slice.call(article.querySelectorAll("h2[id], h3[id]")).forEach(function (head) {
        var link = document.createElement("a");
        link.setAttribute("href", "#" + head.id);
        if (head.tagName === "H3") link.className = "is-sub";
        link.textContent = head.textContent;
        link.addEventListener("click", function (event) {
          event.preventDefault();
          if (window.history && window.history.replaceState) {
            window.history.replaceState(null, "", "#" + head.id);
          }
          scrollToAnchor(head.id);
        });
        var item = document.createElement("li");
        item.appendChild(link);
        outlineList.appendChild(item);
        spyTargets.push({ el: head, link: link });
      });
      updateSpy();
    }

    function updateSpy() {
      if (!spyTargets.length) return;
      var line = window.scrollY + navOffset() + 8;
      var active = spyTargets[0];
      spyTargets.forEach(function (target) {
        if (target.el.getBoundingClientRect().top + window.scrollY <= line) active = target;
      });
      spyTargets.forEach(function (target) {
        if (target === active) target.link.setAttribute("aria-current", "true");
        else target.link.removeAttribute("aria-current");
      });
    }

    /* ---------- 地址与当前文档 ---------- */
    function resolveHash() {
      var hash = window.location.hash || "";
      if (hash.indexOf("#/") === 0) {
        var id = hash.slice(2);
        return { doc: BY_ID[id] ? id : DEFAULT_ID, anchor: null };
      }
      if (hash.length > 1) {
        var el = document.getElementById(hash.slice(1));
        if (el && el.closest) {
          var owner = el.closest(".doc");
          if (owner) return { doc: owner.getAttribute("data-doc"), anchor: hash.slice(1) };
        }
      }
      return { doc: DEFAULT_ID, anchor: null };
    }

    function applyHash(userDriven) {
      var resolved = resolveHash();
      activate(resolved.doc, { scroll: userDriven && !resolved.anchor, focus: userDriven });
      if (resolved.anchor && userDriven) scrollToAnchor(resolved.anchor);
    }

    /* ---------- 文档树与翻页链接 ---------- */
    treeLinks.forEach(function (link) {
      link.addEventListener("click", function (event) {
        event.preventDefault();
        var target = "#/" + link.getAttribute("data-doc-link");
        if (window.location.hash === target) applyHash(true);
        else window.location.hash = target;
        closeSideOnNarrow();
      });
    });

    [pagerPrev, pagerNext].forEach(function (link) {
      link.addEventListener("click", function (event) {
        if (link.hidden) return;
        event.preventDefault();
        var target = link.getAttribute("href");
        if (window.location.hash === target) applyHash(true);
        else window.location.hash = target;
      });
    });

    window.addEventListener("hashchange", function () { applyHash(true); });

    /* ---------- 搜索 ---------- */
    var searchInput = document.getElementById("doc-search");
    var searchClear = document.getElementById("search-clear");
    var searchStatus = document.getElementById("search-status");
    var searchEmpty = document.getElementById("search-empty");
    var tree = document.getElementById("docs-tree");
    var groups = Array.prototype.slice.call(tree.querySelectorAll(".tree-group"));

    function resetSearch() {
      treeLinks.forEach(function (link) { link.hidden = false; });
      groups.forEach(function (group) { group.hidden = false; });
      searchStatus.textContent = "";
      searchEmpty.hidden = true;
      searchClear.hidden = true;
    }

    function runSearch() {
      var query = searchInput.value.trim().toLowerCase();
      if (!query) {
        resetSearch();
        return;
      }
      searchClear.hidden = false;
      var hits = 0;

      groups.forEach(function (group) {
        var groupMatches = group.getAttribute("data-group").toLowerCase().indexOf(query) >= 0;
        var groupHits = 0;
        Array.prototype.slice.call(group.querySelectorAll("[data-doc-link]")).forEach(function (link) {
          var doc = BY_ID[link.getAttribute("data-doc-link")];
          var haystack = (doc.title + " " + doc.keywords).toLowerCase();
          var hit = groupMatches || haystack.indexOf(query) >= 0;
          link.hidden = !hit;
          if (hit) groupHits += 1;
        });
        group.hidden = groupHits === 0;
        hits += groupHits;
      });

      if (hits === 0) {
        searchStatus.textContent = "";
        searchEmpty.hidden = false;
      } else {
        searchEmpty.hidden = true;
        searchStatus.textContent = "在 " + hits + " 篇文档里找到匹配";
      }
    }

    searchInput.addEventListener("input", runSearch);
    searchInput.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        searchInput.value = "";
        resetSearch();
      }
    });
    searchClear.addEventListener("click", function () {
      searchInput.value = "";
      resetSearch();
      searchInput.focus();
    });

    /* ---------- 算法说明：分段切换 ---------- */
    var tablist = document.querySelector('[role="tablist"]');
    if (tablist) {
      var tabs = Array.prototype.slice.call(tablist.querySelectorAll('[role="tab"]'));
      var selectTab = function (tab, focus) {
        tabs.forEach(function (item) {
          var selected = item === tab;
          item.setAttribute("aria-selected", selected ? "true" : "false");
          item.tabIndex = selected ? 0 : -1;
          var panel = document.getElementById(item.getAttribute("aria-controls"));
          if (panel) panel.hidden = !selected;
        });
        if (focus) tab.focus();
      };
      tabs.forEach(function (tab, index) {
        tab.addEventListener("click", function () { selectTab(tab, false); });
        tab.addEventListener("keydown", function (event) {
          var key = event.key;
          if (key === "ArrowRight" || key === "ArrowLeft") {
            event.preventDefault();
            var step = key === "ArrowRight" ? 1 : -1;
            selectTab(tabs[(index + step + tabs.length) % tabs.length], true);
          } else if (key === "Home") {
            event.preventDefault();
            selectTab(tabs[0], true);
          } else if (key === "End") {
            event.preventDefault();
            selectTab(tabs[tabs.length - 1], true);
          }
        });
      });
    }

    /* ---------- 复制提问示例 ---------- */
    var copyStatus = document.getElementById("copy-status");
    var copyTimer = null;

    function sayCopy(message) {
      if (!copyStatus) return;
      copyStatus.textContent = message;
      if (copyTimer) window.clearTimeout(copyTimer);
      copyTimer = window.setTimeout(function () { copyStatus.textContent = ""; }, 4000);
    }

    Array.prototype.slice.call(document.querySelectorAll("[data-copy]")).forEach(function (button) {
      button.addEventListener("click", function () {
        var source = document.getElementById(button.getAttribute("data-copy"));
        if (!source) return;
        var text = source.textContent.trim();
        var report = function (ok) {
          sayCopy(ok ? "这一段已经复制" : "复制失败，请手动选中文字");
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(function () { report(true); }, function () { report(false); });
          return;
        }
        var helper = document.createElement("textarea");
        helper.value = text;
        helper.setAttribute("readonly", "");
        helper.style.position = "fixed";
        helper.style.top = "-1000px";
        document.body.appendChild(helper);
        helper.select();
        var ok = false;
        try { ok = document.execCommand("copy"); } catch (error) { ok = false; }
        document.body.removeChild(helper);
        report(ok);
      });
    });

    /* ---------- 窄屏的目录开关 ---------- */
    var sideToggle = document.getElementById("side-toggle");
    var side = document.getElementById("docs-side");

    function closeSideOnNarrow() {
      if (!sideToggle || !side) return;
      if (window.matchMedia && window.matchMedia("(max-width: 1023px)").matches) {
        side.classList.remove("is-open");
        sideToggle.setAttribute("aria-expanded", "false");
      }
    }

    if (sideToggle && side) {
      sideToggle.addEventListener("click", function () {
        var open = side.classList.toggle("is-open");
        sideToggle.setAttribute("aria-expanded", open ? "true" : "false");
      });
    }

    /* ---------- 滚动：本页目录跟随与回到顶部 ---------- */
    var toTop = document.getElementById("to-top");
    var ticking = false;

    function onScroll() {
      if (ticking) return;
      ticking = true;
      window.requestAnimationFrame(function () {
        updateSpy();
        if (toTop) {
          if (window.scrollY > 400) toTop.classList.add("is-on");
          else toTop.classList.remove("is-on");
        }
        ticking = false;
      });
    }

    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);

    if (toTop) {
      toTop.addEventListener("click", function () {
        window.scrollTo({ top: 0, behavior: scrollBehavior() });
      });
    }

    /* ---------- 首次进入 ---------- */
    applyHash(false);
  })();
}
