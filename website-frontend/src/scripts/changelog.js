/* 交互逻辑逐字取自官网原型 changelog.html；导出初始化函数，由页面组件在挂载后调用。 */
export function initChangelog() {
  (function () {
    "use strict";

    var reduceMotion = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    var hasGsap = typeof window.gsap !== "undefined";
    var hasST = typeof window.ScrollTrigger !== "undefined";
    var gsap = window.gsap;
    if (hasGsap && hasST) { gsap.registerPlugin(window.ScrollTrigger); }

    function el(id) { return document.getElementById(id); }
    function list(sel) { return Array.prototype.slice.call(document.querySelectorAll(sel)); }

    /* ---------- 通告条 ---------- */
    var notice = el("notice");
    var noticeClose = el("notice-close");
    if (notice && noticeClose) {
      noticeClose.addEventListener("click", function () {
        notice.classList.add("is-closed");
        window.setTimeout(function () { notice.hidden = true; }, reduceMotion ? 0 : 340);
      });
    }

    /* ---------- 滚动进度、顶栏下边线、回到顶部 ---------- */
    var topnav = el("topnav");
    var progress = el("progress");
    var toTop = el("to-top");
    var ticking = false;

    function onScroll() {
      if (ticking) { return; }
      ticking = true;
      window.requestAnimationFrame(function () {
        ticking = false;
        var y = window.pageYOffset || document.documentElement.scrollTop || 0;
        var max = document.documentElement.scrollHeight - window.innerHeight;
        if (progress) { progress.style.transform = "scaleX(" + (max > 0 ? Math.min(1, y / max) : 0).toFixed(4) + ")"; }
        if (topnav) { topnav.classList.toggle("is-scrolled", y > 8); }
        if (toTop) { toTop.classList.toggle("is-on", y > window.innerHeight * 0.6); }
      });
    }

    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    onScroll();

    if (toTop) {
      toTop.addEventListener("click", function () {
        if (reduceMotion) { window.scrollTo(0, 0); return; }
        window.scrollTo({ top: 0, behavior: "smooth" });
      });
    }

    /* ---------- 入场：只在允许动态时执行，减少动态时内容直接是终态 ---------- */
    if (hasGsap && !reduceMotion) {
      list("[data-reveal]").forEach(function (node) {
        gsap.from(node, {
          y: 18, opacity: 0, duration: 0.55, ease: "power2.out",
          scrollTrigger: { trigger: node, start: "top 88%", once: true }
        });
      });
    }

    if (hasST) { window.ScrollTrigger.refresh(); }
  })();
}
