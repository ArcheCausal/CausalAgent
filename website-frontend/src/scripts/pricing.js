/* 交互逻辑逐字取自官网原型 pricing.html；导出初始化函数，由页面组件在挂载后调用。 */
export function initPricing() {
  (function () {
      "use strict";
      var notice = document.getElementById("notice");
      var noticeClose = document.getElementById("notice-close");
      var topnav = document.getElementById("topnav");
      var progress = document.getElementById("progress");
      var toTop = document.getElementById("to-top");

      if (notice && noticeClose) {
        noticeClose.addEventListener("click", function () { notice.classList.add("is-closed"); });
      }

      function onScroll() {
        var y = window.pageYOffset || document.documentElement.scrollTop || 0;
        var max = document.documentElement.scrollHeight - window.innerHeight;
        if (topnav) { topnav.classList.toggle("is-scrolled", y > 8); }
        if (progress) { progress.style.transform = "scaleX(" + (max > 0 ? y / max : 0) + ")"; }
        if (toTop) { toTop.classList.toggle("is-on", y > window.innerHeight * 0.7); }
      }

      if (toTop) {
        toTop.addEventListener("click", function () {
          window.scrollTo({ top: 0, behavior: window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
        });
      }
      window.addEventListener("scroll", onScroll, { passive: true });
      window.addEventListener("resize", onScroll);
      onScroll();
    }());
}
