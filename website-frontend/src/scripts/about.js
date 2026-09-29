/* 交互逻辑逐字取自官网原型 about.html；导出初始化函数，由页面组件在挂载后调用。 */
export function initAbout() {
  document.documentElement.classList.add("js");

  (function () {
    "use strict";

    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var notice = document.getElementById("notice");
    var noticeClose = document.getElementById("notice-close");
    var progress = document.getElementById("progress");
    var toTop = document.getElementById("to-top");

    if (noticeClose) {
      noticeClose.addEventListener("click", function () {
        notice.classList.add("is-closed");
        window.setTimeout(function () { notice.hidden = true; }, reduce ? 0 : 220);
      });
    }

    function onScroll() {
      var doc = document.documentElement;
      var max = doc.scrollHeight - doc.clientHeight;
      var ratio = max > 0 ? doc.scrollTop / max : 0;
      progress.style.transform = "scaleX(" + ratio.toFixed(4) + ")";
      toTop.classList.toggle("is-on", doc.scrollTop > 520);
    }

    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();

    toTop.addEventListener("click", function () {
      window.scrollTo({ top: 0, behavior: reduce ? "auto" : "smooth" });
    });
  })();


  (function () {
    "use strict";

    var host = document.querySelector(".starfield");
    if (!host) { return; }

    var baseLayer = document.getElementById("st-base");
    var glowLayers = [
      document.getElementById("st-glow-1"),
      document.getElementById("st-glow-2"),
      document.getElementById("st-glow-3")
    ].filter(function (layer) { return layer && layer.getContext; });

    if (!baseLayer || !baseLayer.getContext || glowLayers.length === 0) { return; }

    var maxDpr = Math.min(window.devicePixelRatio || 1, 2);
    var pending = 0;
    var onScreen = true;
    var TAU = Math.PI * 2;

    /* 星点色温：以纯白为主，少量偏冷与偏暖，整体保持安静 */
    var TINTS = [
      [255, 255, 255],
      [208, 224, 255],
      [255, 243, 226]
    ];
    var sprites = [];

    function rgbOf(tint) {
      return "rgb(" + tint[0] + ", " + tint[1] + ", " + tint[2] + ")";
    }

    /* 一张软边圆点贴图；放大后绘制就成为星点周围的光晕 */
    function spriteFor(index) {
      if (sprites[index]) { return sprites[index]; }
      var size = 64;
      var sheet = document.createElement("canvas");
      sheet.width = size;
      sheet.height = size;
      var pen = sheet.getContext("2d");
      var tint = TINTS[index] || TINTS[0];
      var head = "rgba(" + tint[0] + ", " + tint[1] + ", " + tint[2] + ", ";
      var grad = pen.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
      grad.addColorStop(0, head + "1)");
      grad.addColorStop(0.16, head + "0.5)");
      grad.addColorStop(0.44, head + "0.16)");
      grad.addColorStop(1, head + "0)");
      pen.fillStyle = grad;
      pen.fillRect(0, 0, size, size);
      sprites[index] = sheet;
      return sheet;
    }

    function pickTint() {
      var roll = Math.random();
      if (roll < 0.68) { return 0; }
      if (roll < 0.88) { return 1; }
      return 2;
    }

    /* 远处细碎的星点：不画光晕，只留一个很小的亮点 */
    function paintFaint(ctx, w, h, count) {
      ctx.globalCompositeOperation = "source-over";
      for (var i = 0; i < count; i += 1) {
        ctx.globalAlpha = 0.16 + Math.random() * 0.3;
        ctx.fillStyle = rgbOf(TINTS[pickTint()]);
        ctx.beginPath();
        ctx.arc(Math.random() * w, Math.random() * h, 0.5 + Math.random() * 0.7, 0, TAU);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    }

    /* 较亮的星：一点光晕加一个实心星核，大小拉开差别，呼吸交给 CSS */
    function paintGlow(ctx, w, h, count) {
      ctx.globalCompositeOperation = "lighter";
      for (var i = 0; i < count; i += 1) {
        var tintIndex = pickTint();
        var tint = TINTS[tintIndex];
        var x = Math.random() * w;
        var y = Math.random() * h;
        var r = 0.9 + Math.pow(Math.random(), 2.2) * 1.6;
        var alpha = 0.4 + Math.random() * 0.42;
        ctx.globalAlpha = alpha * 0.32;
        ctx.drawImage(spriteFor(tintIndex), x - r * 4, y - r * 4, r * 8, r * 8);
        ctx.globalAlpha = Math.min(1, alpha + 0.14);
        ctx.fillStyle = rgbOf(tint);
        ctx.beginPath();
        ctx.arc(x, y, Math.max(0.65, r * 0.78), 0, TAU);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = "source-over";
    }

    function sizeLayer(layer, w, h, dpr) {
      layer.width = Math.max(1, Math.round(w * dpr));
      layer.height = Math.max(1, Math.round(h * dpr));
      var ctx = layer.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      return ctx;
    }

    /* 画幅很大时降低绘制倍率，四层画布不至于占用过多内存 */
    function dprFor(w, h) {
      var limit = Math.sqrt(3400000 / Math.max(1, w * h));
      return Math.min(maxDpr, Math.max(1, limit));
    }

    function render() {
      var rect = host.getBoundingClientRect();
      var w = Math.round(rect.width);
      var h = Math.round(rect.height);
      if (w < 8 || h < 8) { return; }
      var dpr = dprFor(w, h);
      var area = w * h;
      var faintCount = Math.round(area / 5400);
      var glowCount = Math.round(area / 18000);
      if (faintCount < 36) { faintCount = 36; }
      if (faintCount > 300) { faintCount = 300; }
      if (glowCount < 15) { glowCount = 15; }
      if (glowCount > 84) { glowCount = 84; }

      paintFaint(sizeLayer(baseLayer, w, h, dpr), w, h, faintCount);

      var perLayer = Math.ceil(glowCount / glowLayers.length);
      for (var i = 0; i < glowLayers.length; i += 1) {
        paintGlow(sizeLayer(glowLayers[i], w, h, dpr), w, h, perLayer);
      }
    }

    function syncPause() {
      host.classList.toggle("is-paused", document.hidden || !onScreen);
    }

    function schedule() {
      if (pending) { return; }
      pending = window.requestAnimationFrame(function () {
        pending = 0;
        render();
      });
    }

    render();

    window.addEventListener("resize", schedule, { passive: true });
    if (window.ResizeObserver) {
      new ResizeObserver(schedule).observe(host);
    }

    document.addEventListener("visibilitychange", syncPause);

    if (window.IntersectionObserver) {
      new IntersectionObserver(function (entries) {
        onScreen = entries[0].isIntersecting;
        syncPause();
      }, { threshold: 0 }).observe(baseLayer);
    }
  })();
}
