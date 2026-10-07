<script setup lang="ts">
/* 本页结构与交互取自官网原型 auth/sign-in.html，表单提交接到真实的 /api/login。 */
import { onMounted, ref } from 'vue'
import { login, resolveRedirect } from '../api/auth'
import { readQueryValue } from '../routes'
import { initCausalMap } from '../scripts/causal-map.js'
import '../styles/pages/auth.css'

const props = defineProps<{ loggedInUsername?: string | null }>()

const next = readQueryValue(globalThis.location.search, 'next')
const notice = readQueryValue(globalThis.location.search, 'notice')

const username = ref('')
const password = ref('')
const passwordVisible = ref(false)
const busy = ref(false)
const usernameError = ref('')
const passwordError = ref('')
const formError = ref(notice === 'admin_required' ? '当前账号没有管理员权限，请使用管理员账号登录。' : '')

function setUsernameError(message: string): boolean {
  usernameError.value = message
  return Boolean(message)
}

function setPasswordError(message: string): boolean {
  passwordError.value = message
  return Boolean(message)
}

function togglePassword(): void {
  passwordVisible.value = !passwordVisible.value
}

async function submit(): Promise<void> {
  if (busy.value) return
  formError.value = ''
  const invalidUsername = setUsernameError(username.value.trim() ? '' : '请填写用户名。')
  const invalidPassword = setPasswordError(password.value ? '' : '请填写密码。')
  if (invalidUsername || invalidPassword) {
    formError.value = '请填写用户名和密码。'
    return
  }
  busy.value = true
  try {
    const payload = await login(username.value, password.value, next)
    globalThis.location.assign(resolveRedirect(payload.redirect_to, next))
  } catch (cause) {
    formError.value = cause instanceof Error ? cause.message : '登录失败，请稍后再试。'
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  initCausalMap()
})
</script>

<template>
  <a class="skip-link" href="#main">跳到主要内容</a>

  <header class="auth-topbar">
    <div class="auth-topbar__inner">
      <a class="auth-brand" href="/" aria-label="返回 CausalAgent 首页">
        <img class="auth-brand__mark" :src="'/site-assets/brand/causalagent-mark.svg'" alt="" width="34" height="34" />
        <span class="auth-brand__name">CausalAgent</span>
      </a>
      <a class="auth-topbar__link" href="/auth/sign-up">没有账号？立即注册</a>
    </div>
  </header>

  <main class="auth-main" id="main">
    <div class="auth-shell">
      <section class="auth-intro" aria-labelledby="intro-title">
        <p class="auth-eyebrow">进入 CausalAgent</p>
        <h1 id="intro-title">继续探索上一次没有完成的任务</h1>
        <p class="auth-intro__lede">登录后回到工作区，继续查看历史会话、数据文件和已经留下的分析过程。</p>

        <figure class="causal-map" data-causal-map aria-label="动态三维因果路径示意图：从材料和知识经过分析到结论与报告">
          <div class="causal-map__stage">
            <svg viewBox="0 0 520 190" role="img" aria-hidden="true">
              <defs>
                <marker id="arrow-sign-in" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M0 0L8 4L0 8Z" fill="#171717"></path>
                </marker>
              </defs>
              <path class="causal-map__plane" d="M32 152L260 177L488 152L260 127Z"></path>
              <g class="causal-map__grid" aria-hidden="true">
                <path d="M58 150L260 172L462 150"></path>
                <path d="M92 146L260 164L428 146"></path>
                <path d="M126 142L260 156L394 142"></path>
                <path d="M126 142L126 160"></path>
                <path d="M260 156L260 177"></path>
                <path d="M394 142L394 160"></path>
              </g>
              <g class="causal-map__depth-layer" aria-hidden="true">
                <path class="causal-map__line" d="M82 55C146 55 160 95 222 95"></path>
                <path class="causal-map__line" d="M82 132C146 132 160 95 222 95"></path>
                <path class="causal-map__line" d="M298 95C354 95 370 55 438 55"></path>
                <path class="causal-map__line" d="M298 95C354 95 370 135 438 135"></path>
              </g>
              <g>
                <path class="causal-map__line causal-map__line--strong" d="M82 55C146 55 160 95 222 95" pathLength="1"></path>
                <path class="causal-map__line causal-map__line--strong" d="M82 132C146 132 160 95 222 95" pathLength="1" marker-end="url(#arrow-sign-in)"></path>
                <path class="causal-map__line causal-map__line--strong" d="M298 95C354 95 370 55 438 55" pathLength="1" marker-end="url(#arrow-sign-in)"></path>
                <path class="causal-map__line" d="M298 95C354 95 370 135 438 135" pathLength="1" marker-end="url(#arrow-sign-in)"></path>
                <path class="causal-map__line causal-map__line--flow" d="M82 55C146 55 160 95 222 95" pathLength="1"></path>
                <path class="causal-map__line causal-map__line--flow" d="M82 132C146 132 160 95 222 95" pathLength="1"></path>
                <path class="causal-map__line causal-map__line--flow" d="M298 95C354 95 370 55 438 55" pathLength="1"></path>
                <path class="causal-map__line causal-map__line--flow" d="M298 95C354 95 370 135 438 135" pathLength="1"></path>
              </g>
              <g class="causal-map__node-group" style="--node-delay: -0.6s">
                <ellipse class="causal-map__node-shadow" cx="72" cy="64" rx="20" ry="5"></ellipse>
                <circle class="causal-map__node" cx="72" cy="55" r="22"></circle>
                <text class="causal-map__label" x="72" y="59" text-anchor="middle">材料</text>
              </g>
              <g class="causal-map__node-group" style="--node-delay: -1.8s">
                <ellipse class="causal-map__node-shadow" cx="72" cy="141" rx="20" ry="5"></ellipse>
                <circle class="causal-map__node" cx="72" cy="132" r="22"></circle>
                <text class="causal-map__label" x="72" y="136" text-anchor="middle">知识</text>
              </g>
              <g class="causal-map__node-group" style="--node-delay: -2.6s">
                <ellipse class="causal-map__node-shadow" cx="260" cy="108" rx="29" ry="7"></ellipse>
                <circle class="causal-map__node causal-map__node--core" cx="260" cy="95" r="32"></circle>
                <text class="causal-map__label causal-map__label--core" x="260" y="99" text-anchor="middle">分析</text>
              </g>
              <g class="causal-map__node-group" style="--node-delay: -1.1s">
                <ellipse class="causal-map__node-shadow" cx="452" cy="64" rx="20" ry="5"></ellipse>
                <circle class="causal-map__node" cx="452" cy="55" r="22"></circle>
                <text class="causal-map__label" x="452" y="59" text-anchor="middle">结论</text>
              </g>
              <g class="causal-map__node-group" style="--node-delay: -3.4s">
                <ellipse class="causal-map__node-shadow" cx="452" cy="144" rx="20" ry="5"></ellipse>
                <circle class="causal-map__node" cx="452" cy="135" r="22"></circle>
                <text class="causal-map__label" x="452" y="139" text-anchor="middle">报告</text>
              </g>
            </svg>
          </div>
        </figure>

        <div class="auth-intro__meta od-cluster" aria-label="登录后可使用的内容">
          <span>会话随账号保存</span>
          <span>文件与报告可回看</span>
        </div>
      </section>

      <section class="auth-panel" aria-labelledby="panel-title">
        <div class="auth-panel__head">
          <h2 id="panel-title">登录账号</h2>
          <p class="auth-panel__subtitle">使用账号进入工作区；管理员账号登录后按权限继续。</p>
        </div>

        <p v-if="props.loggedInUsername" class="auth-help">当前浏览器已登录账号 {{ props.loggedInUsername }}。</p>

        <form class="auth-form" id="sign-in-form" novalidate @submit.prevent="submit">
          <div class="auth-field od-field" id="username-field" :class="{ 'is-error': usernameError }">
            <label for="username">用户名</label>
            <div class="auth-input-shell">
              <input
                class="auth-input"
                id="username"
                name="username"
                type="text"
                autocomplete="username"
                required
                aria-describedby="username-help username-error"
                :aria-invalid="usernameError ? 'true' : 'false'"
                v-model="username"
              />
            </div>
            <p class="auth-help" id="username-help">请输入注册时使用的用户名。</p>
            <p class="auth-error" id="username-error" :hidden="!usernameError">{{ usernameError }}</p>
          </div>

          <div class="auth-field od-field" id="password-field" :class="{ 'is-error': passwordError }">
            <label for="password">密码</label>
            <div class="auth-input-shell">
              <input
                class="auth-input auth-input--password"
                id="password"
                name="password"
                :type="passwordVisible ? 'text' : 'password'"
                autocomplete="current-password"
                required
                aria-describedby="password-help password-error"
                :aria-invalid="passwordError ? 'true' : 'false'"
                v-model="password"
              />
              <button
                class="auth-input-action od-touch"
                type="button"
                id="password-toggle"
                aria-controls="password"
                :aria-pressed="passwordVisible"
                @click="togglePassword"
              >{{ passwordVisible ? '隐藏' : '显示' }}</button>
            </div>
            <p class="auth-help" id="password-help">请输入当前账号密码。</p>
            <p class="auth-error" id="password-error" :hidden="!passwordError">{{ passwordError }}</p>
          </div>

          <div class="auth-form-footer od-stack" style="--od-gap: 16px">
            <p class="auth-status auth-status--error form-error" id="form-status" role="alert" :hidden="!formError">{{ formError }}</p>
            <button class="auth-submit" id="submit-button" type="submit" :disabled="busy">
              <span id="submit-label" v-show="!busy">登录</span>
              <span id="submit-loading" v-show="busy">正在登录…</span>
            </button>
          </div>
        </form>

        <p class="auth-switch">还没有账号？ <a class="auth-inline-link" href="/auth/sign-up">立即注册</a></p>
        <p class="auth-footnote">登录后默认进入工作区，退出登录后会回到官网首页。</p>
      </section>
    </div>
  </main>
</template>
