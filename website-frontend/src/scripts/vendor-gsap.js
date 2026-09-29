/* 官网原型的页面脚本以 window.gsap 与 window.ScrollTrigger 使用这两个库，
   原型目录导出的 vendor 文件缺失。这里按原型脚本的全局约定挂载，
   只有使用到 GSAP 的首页与更新日志页引入，页面脚本保持逐字不改。 */
import { gsap } from 'gsap'
import { ScrollTrigger } from 'gsap/ScrollTrigger'

export function installGsap() {
  globalThis.gsap = gsap
  globalThis.ScrollTrigger = ScrollTrigger
}
