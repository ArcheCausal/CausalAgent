/* 交互逻辑逐字取自官网原型 auth/causal-map.js；导出初始化函数，由认证页面在挂载后调用。 */
export function initCausalMap() {
  var maps = document.querySelectorAll('[data-causal-map]');
  var motionQuery = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;

  Array.prototype.forEach.call(maps, function (map) {
    var stage = map.querySelector('.causal-map__stage');
    if (!stage) return;

    if (motionQuery && motionQuery.matches) {
      map.classList.add('is-reduced');
      return;
    }

    var targetX = 2;
    var targetY = -3;
    var currentX = targetX;
    var currentY = targetY;
    var pointerActive = false;
    var previousTime = 0;

    function updateTarget(event) {
      if (event.pointerType === 'touch') return;
      var rect = map.getBoundingClientRect();
      var x = (event.clientX - rect.left) / rect.width - 0.5;
      var y = (event.clientY - rect.top) / rect.height - 0.5;
      targetX = Math.max(-5, Math.min(5, y * -8));
      targetY = Math.max(-7, Math.min(7, x * 10));
      pointerActive = true;
    }

    function releasePointer() {
      pointerActive = false;
    }

    function paint(time) {
      var elapsed = previousTime ? Math.min(64, time - previousTime) : 16;
      previousTime = time;
      if (!pointerActive) {
        targetX = 2 + Math.sin(time / 2400) * 1.8;
        targetY = -3 + Math.cos(time / 2900) * 2.6;
      }
      var easing = Math.min(1, elapsed / 140);
      currentX += (targetX - currentX) * easing;
      currentY += (targetY - currentY) * easing;
      stage.style.setProperty('--map-rotate-x', currentX.toFixed(2) + 'deg');
      stage.style.setProperty('--map-rotate-y', currentY.toFixed(2) + 'deg');
      window.requestAnimationFrame(paint);
    }

    map.addEventListener('pointermove', updateTarget, { passive: true });
    map.addEventListener('pointerleave', releasePointer, { passive: true });
    window.requestAnimationFrame(paint);
  });
}
