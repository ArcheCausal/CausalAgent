/* 交互逻辑逐字取自官网原型 product.html；导出初始化函数，由页面组件在挂载后调用。 */
export function initProduct() {
  document.documentElement.classList.add("js");

  (function () {
    "use strict";

    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    /* ---------- 首屏高度：减掉导航与通告条的实际占用 ---------- */
    var noticeBar = document.getElementById("notice");
    function measureNotice() {
      var height = (!noticeBar || noticeBar.hidden) ? 0 : noticeBar.offsetHeight;
      document.documentElement.style.setProperty("--notice-live", height + "px");
    }
    measureNotice();
    window.addEventListener("resize", measureNotice, { passive: true });

    /* ---------- 通告条 ---------- */
    var notice = noticeBar;
    var noticeClose = document.getElementById("notice-close");
    if (noticeClose) {
      noticeClose.addEventListener("click", function () {
        notice.classList.add("is-closed");
        window.setTimeout(function () { notice.hidden = true; measureNotice(); }, reduce ? 0 : 320);
      });
    }

    /* ---------- 首屏舞台：五屏左右切换与慢速自动播放 ---------- */
    var stage = document.querySelector(".stage");
    if (stage) {
      var stageViewport = document.getElementById("stage-viewport");
      var stageTrack = document.getElementById("stage-track");
      var stageSlides = Array.prototype.slice.call(stage.querySelectorAll(".slide"));
      var stageIndex = 0;
      var dragOrigin = null;
      var dragOffset = 0;
      var dragMoved = false;
      var wheelLock = 0;
      var wheelSum = 0;
  var wheelIdle = 0;
      var autoplayTimer = null;
      var autoplayDelay = 8000;

      function stopAutoplay() {
        if (autoplayTimer === null) { return; }
        window.clearTimeout(autoplayTimer);
        autoplayTimer = null;
      }

      function scheduleAutoplay() {
        stopAutoplay();
        if (reduce || stageSlides.length < 2 || document.hidden || stage.matches(":hover") || stage.contains(document.activeElement)) { return; }
        autoplayTimer = window.setTimeout(function () {
          autoplayTimer = null;
          if (document.hidden || stage.matches(":hover") || stage.contains(document.activeElement) || dragOrigin || !stageFillsViewport()) {
            scheduleAutoplay();
            return;
          }
          showSlide((stageIndex + 1) % stageSlides.length, false);
        }, autoplayDelay);
      }

      function showSlide(target, focusPanel) {
        var next = Math.max(0, Math.min(stageSlides.length - 1, target));
        stageIndex = next;
        stageTrack.style.setProperty("--stage-i", String(next));
        dragOffset = 0;
        stageTrack.style.setProperty("--stage-drag", "0px");
        stageSlides.forEach(function (slide, i) {
          var on = i === next;
          slide.classList.toggle("is-live", on);
          slide.tabIndex = on ? 0 : -1;
          if (on) { slide.removeAttribute("aria-hidden"); }
          else { slide.setAttribute("aria-hidden", "true"); }
          Array.prototype.forEach.call(slide.querySelectorAll("a[href], button"), function (item) {
            if (on) { item.removeAttribute("tabindex"); }
            else { item.setAttribute("tabindex", "-1"); }
          });
        });
        if (focusPanel) { stageSlides[next].focus({ preventScroll: true }); }
        scheduleAutoplay();
      }

      function moveDrag(px) {
        dragOffset = px;
        stageTrack.style.setProperty("--stage-drag", px.toFixed(1) + "px");
      }

      stageViewport.addEventListener("pointerdown", function (event) {
        if (event.pointerType === "mouse" && event.button !== 0) { return; }
        dragOrigin = { x: event.clientX, y: event.clientY, id: event.pointerId };
        dragMoved = false;
      });

      stageViewport.addEventListener("pointermove", function (event) {
        if (!dragOrigin || event.pointerId !== dragOrigin.id) { return; }
        var dx = event.clientX - dragOrigin.x;
        if (!dragMoved) {
          if (Math.abs(dx) < 8) { return; }
          if (Math.abs(event.clientY - dragOrigin.y) > Math.abs(dx)) { dragOrigin = null; return; }
          dragMoved = true;
          stageViewport.classList.add("is-dragging");
          /* 只有确实开始拖动才接管指针，单击时不动事件的落点，屏内链接照常可点 */
          stageViewport.setPointerCapture(event.pointerId);
        }
        var width = stageViewport.clientWidth;
        if ((stageIndex === 0 && dx > 0) || (stageIndex === stageSlides.length - 1 && dx < 0)) { dx *= 0.32; }
        moveDrag(Math.max(-width, Math.min(width, dx)));
      });

      function endDrag() {
        if (!dragOrigin) { return; }
        var origin = dragOrigin;
        dragOrigin = null;
        stageViewport.classList.remove("is-dragging");
        if (stageViewport.hasPointerCapture(origin.id)) { stageViewport.releasePointerCapture(origin.id); }
        if (!dragMoved) { return; }
        var threshold = Math.max(56, stageViewport.clientWidth * 0.18);
        if (dragOffset <= -threshold) { showSlide(stageIndex + 1, false); }
        else if (dragOffset >= threshold) { showSlide(stageIndex - 1, false); }
        else { moveDrag(0); }
      }

      stageViewport.addEventListener("pointerup", endDrag);
      stageViewport.addEventListener("pointercancel", function () {
        if (!dragOrigin) { return; }
        dragOrigin = null;
        dragMoved = false;
        stageViewport.classList.remove("is-dragging");
        moveDrag(0);
      });

      /* 拖动结束后浏览器仍会补一次 click，这里把它挡掉，免得误触屏内的链接 */
      stageViewport.addEventListener("click", function (event) {
        if (!dragMoved) { return; }
        event.preventDefault();
        event.stopPropagation();
        dragMoved = false;
      }, true);

      /* 首屏完整落在视口里时才接管滚轮：往下滚看下一件，滚到最后一件就把滚动交还页面 */
      function stageFillsViewport() {
        var rect = stage.getBoundingClientRect();
        var visible = Math.min(rect.bottom, window.innerHeight) - Math.max(rect.top, 0);
        return visible >= Math.min(rect.height, window.innerHeight) * 0.85;
      }
      stage.addEventListener("wheel", function (event) {
        if (event.ctrlKey || event.metaKey) { return; }
        var dx = event.deltaX;
        var dy = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 100 : 1);
        var horizontal = Math.abs(dx) > Math.abs(dy);
        var step = (horizontal ? dx : dy) > 0 ? 1 : -1;
        var target = stageIndex + step;
        /* 不在首屏范围内，或者已经是第一件／最后一件：不拦，交给页面正常滚动 */
        if (!stageFillsViewport() || target < 0 || target > stageSlides.length - 1) {
          wheelSum = 0;
          return;
        }
        event.preventDefault();
        var now = Date.now();
        if (now < wheelLock) { return; }
        if (horizontal) {
          wheelSum = 0;
          wheelLock = now + 420;
          showSlide(target, false);
          return;
        }
        /* 纵向滚轮要攒够一段距离才换屏，免得轻轻一碰就跳 */
        if (now - wheelIdle > 240) { wheelSum = 0; }
        wheelIdle = now;
        wheelSum += dy;
        if (Math.abs(wheelSum) < 70) { return; }
        wheelSum = 0;
        wheelLock = now + 700;
        showSlide(target, false);
      }, { passive: false });

      stage.addEventListener("keydown", function (event) {
        /* 焦点在首屏里时方向键换屏；换完把焦点交给新的一屏，免得停在已经隐藏的那一屏上 */
        if (event.key === "ArrowRight") { event.preventDefault(); showSlide(stageIndex + 1, true); }
        else if (event.key === "ArrowLeft") { event.preventDefault(); showSlide(stageIndex - 1, true); }
      });

  stage.addEventListener("mouseenter", stopAutoplay);
      stage.addEventListener("mouseleave", scheduleAutoplay);
      stage.addEventListener("focusin", stopAutoplay);
      stage.addEventListener("focusout", function (event) {
        if (!stage.contains(event.relatedTarget)) { scheduleAutoplay(); }
      });
      document.addEventListener("visibilitychange", function () {
        if (document.hidden) { stopAutoplay(); }
        else { scheduleAutoplay(); }
      });

      showSlide(0, false);
    }

    /* ---------- 滚动进度与回到顶部 ---------- */
    var progress = document.getElementById("progress");
    var toTop = document.getElementById("to-top");
    function onScroll() {
      var doc = document.documentElement;
      var max = doc.scrollHeight - doc.clientHeight;
      var ratio = max > 0 ? doc.scrollTop / max : 0;
      progress.style.transform = "scaleX(" + ratio.toFixed(4) + ")";
      toTop.classList.toggle("is-on", doc.scrollTop > 640);
    }
    window.addEventListener("scroll", function () { onScroll(); setCurrent(); }, { passive: true });
    onScroll();
    toTop.addEventListener("click", function () {
      var heading = document.querySelector(".hero-title");
      if (heading) { heading.setAttribute("tabindex", "-1"); }
      window.scrollTo({ top: 0, behavior: reduce ? "auto" : "smooth" });
      if (heading) { heading.focus({ preventScroll: true }); }
    });

    /* ---------- 入场：脚本就绪才做位移，减少动态时直接呈现终态 ---------- */
    var reveals = Array.prototype.slice.call(document.querySelectorAll("[data-reveal]"));
    if (reduce || !("IntersectionObserver" in window)) {
      reveals.forEach(function (el) { el.classList.add("is-in"); });
    } else {
      var revealObserver = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-in");
            revealObserver.unobserve(entry.target);
          }
        });
      }, { rootMargin: "0px 0px -10% 0px", threshold: 0.06 });
      reveals.forEach(function (el) { revealObserver.observe(el); });
    }

    /* ---------- 章节索引：当前读到哪一节 ---------- */
    var subnavLinks = Array.prototype.slice.call(document.querySelectorAll("#subnav a"));
    var subnavSections = subnavLinks.map(function (link) {
      return document.querySelector(link.getAttribute("href"));
    });
    function setCurrent() {
      var atTop = document.documentElement.scrollTop < 240;
      var line = window.innerHeight * 0.32;
      var active = -1;
      subnavSections.forEach(function (section, i) {
        if (section && section.getBoundingClientRect().top <= line) { active = i; }
      });
      subnavLinks.forEach(function (link, i) {
        if (!atTop && i === active) { link.setAttribute("aria-current", "location"); }
        else { link.removeAttribute("aria-current"); }
      });
    }
    setCurrent();

    /* ---------- 代码示例的三个标签页 ---------- */
    var tablist = document.querySelector(".tabs");
    if (tablist) {
      var tabs = Array.prototype.slice.call(tablist.querySelectorAll('[role="tab"]'));
      function selectTab(index, shouldFocus) {
        tabs.forEach(function (tab, i) {
          var on = i === index;
          tab.setAttribute("aria-selected", on ? "true" : "false");
          tab.tabIndex = on ? 0 : -1;
          var panel = document.getElementById(tab.getAttribute("aria-controls"));
          if (panel) { panel.hidden = !on; }
        });
        if (shouldFocus) { tabs[index].focus(); }
      }
      tabs.forEach(function (tab, i) {
        tab.addEventListener("click", function () { selectTab(i, false); });
        tab.addEventListener("keydown", function (event) {
          var next = null;
          if (event.key === "ArrowRight") { next = (i + 1) % tabs.length; }
          else if (event.key === "ArrowLeft") { next = (i - 1 + tabs.length) % tabs.length; }
          else if (event.key === "Home") { next = 0; }
          else if (event.key === "End") { next = tabs.length - 1; }
          if (next !== null) { event.preventDefault(); selectTab(next, true); }
        });
      });
      selectTab(0, false);
    }

    /* ---------- 复制当前可见的代码 ---------- */
    var copyButton = document.getElementById("copy-code");
    var copyStatus = document.getElementById("copy-status");
    var copyTimer = null;
    function say(message) {
      copyStatus.textContent = message;
      if (copyTimer) { window.clearTimeout(copyTimer); }
      copyTimer = window.setTimeout(function () { copyStatus.textContent = ""; }, 4000);
    }
    function legacyCopy(text) {
      var area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.top = "-1000px";
      document.body.appendChild(area);
      area.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (error) { ok = false; }
      document.body.removeChild(area);
      return ok;
    }
    if (copyButton) {
      copyButton.addEventListener("click", function () {
        var panel = document.querySelector(".codepanel:not([hidden])");
        var text = panel ? panel.textContent.replace(/\s+$/, "") : "";
        function report(ok) { say(ok ? "当前示例已复制" : "复制失败，请手动选中代码"); }
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(function () { report(true); }, function () { report(legacyCopy(text)); });
        } else {
          report(legacyCopy(text));
        }
      });
    }

    /* ---------- 基准数据表：三种排序 ---------- */
    var BENCH = [
      { family: "CAM", cdfm: 0.863, base: 0.794 },
      { family: "CPT", cdfm: 0.767, base: 0.725 },
      { family: "Disc-ANM", cdfm: 0.881, base: 0.783 },
      { family: "Linear", cdfm: 0.939, base: 0.929 },
      { family: "Lin-Het", cdfm: 0.927, base: 0.904 },
      { family: "Lin-Ord", cdfm: 0.862, base: 0.714 },
      { family: "MeasErr", cdfm: 0.882, base: 0.827 },
      { family: "Physical", cdfm: 0.833, base: 0.763 },
      { family: "Phys-Het", cdfm: 0.870, base: 0.740 },
      { family: "PNL", cdfm: 0.901, base: 0.813 },
      { family: "RFF", cdfm: 0.947, base: 0.918 },
      { family: "RFF-Het", cdfm: 0.938, base: 0.906 },
      { family: "RFF-Ord", cdfm: 0.860, base: 0.736 },
      { family: "Rounded", cdfm: 0.881, base: 0.787 },
      { family: "TimeLag", cdfm: 0.854, base: 0.757 }
    ];
    var benchBody = document.getElementById("bench-body");
    var benchAvg = document.getElementById("bench-avg");
    var benchButtons = Array.prototype.slice.call(document.querySelectorAll(".sortgroup button"));
    var benchSort = "family";
    function renderBench() {
      var rows = BENCH.slice();
      if (benchSort === "cdfm") { rows.sort(function (a, b) { return b.cdfm - a.cdfm; }); }
      else if (benchSort === "lead") { rows.sort(function (a, b) { return (b.cdfm - b.base) - (a.cdfm - a.base); }); }
      benchBody.innerHTML = rows.map(function (row) {
        return '<tr><th scope="row">' + row.family + '</th>' +
          '<td class="num">' + row.cdfm.toFixed(3) + '</td>' +
          '<td class="num">' + row.base.toFixed(3) + '</td>' +
          '<td class="num">+' + (row.cdfm - row.base).toFixed(3) + '</td></tr>';
      }).join("");
      var total = BENCH.reduce(function (sum, row) { return sum + (row.cdfm - row.base); }, 0);
      benchAvg.textContent = "+" + (total / BENCH.length).toFixed(3);
    }
    benchButtons.forEach(function (button) {
      button.addEventListener("click", function () {
        benchSort = button.getAttribute("data-sort");
        benchButtons.forEach(function (other) {
          other.setAttribute("aria-pressed", other === button ? "true" : "false");
        });
        renderBench();
      });
    });
    renderBench();

  })();
}
