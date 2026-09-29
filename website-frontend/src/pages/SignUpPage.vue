<script setup lang="ts">
/* 本页结构与交互取自官网原型 auth/sign-up.html，表单提交接到真实的 /api/register。 */
import { computed, onMounted, ref } from 'vue'
import { register } from '../api/auth'
import { readQueryValue } from '../routes'
import { initCausalMap } from '../scripts/causal-map.js'
import '../styles/pages/auth.css'

const next = readQueryValue(globalThis.location.search, 'next')
const signInHref = computed(() => (next ? '/auth/sign-in?next=' + encodeURIComponent(next) : '/auth/sign-in'))

const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const passwordVisible = ref(false)
const busy = ref(false)
const done = ref(false)
const usernameError = ref('')
const passwordError = ref('')
const confirmError = ref('')
const formStatus = ref('')

const lengthMet = computed(() => password.value.length >= 6)
const numberMet = computed(() => /[0-9]/.test(password.value))

function togglePassword(): void {
  passwordVisible.value = !passwordVisible.value
}

async function submit(): Promise<void> {
  if (busy.value) return
  formStatus.value = ''
  usernameError.value = username.value.trim().length >= 3 ? '' : '用户名至少需要 3 个字符。'
  if (password.value.length < 6) passwordError.value = '密码至少需要 6 个字符。'
  else if (!numberMet.value) passwordError.value = '密码必须包含至少一个数字。'
  else passwordError.value = ''
  confirmError.value = confirmPassword.value === password.value ? '' : '两次输入的密码不一致。'
  if (usernameError.value || passwordError.value || confirmError.value) return
  busy.value = true
  try {
    await register(username.value.trim(), password.value)
    done.value = true
  } catch (cause) {
    formStatus.value = cause instanceof Error ? cause.message : '注册失败，请稍后再试。'
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
      <a class="auth-topbar__link" href="/auth/sign-in">已有账号？直接登录</a>
    </div>
  </header>

  <main class="auth-main" id="main">
    <div class="auth-shell">
      <section class="auth-intro" aria-labelledby="intro-title">
        <p class="auth-eyebrow">一次简单的分析，解决一个困难的问题</p>
        <h1 id="intro-title">只需一次简单的分析 即可给出专业的结论</h1>
        <p class="auth-intro__lede">免费注册、上传数据、运行任务、解决问题，探索属于你的世界</p>

        <figure class="causal-map" data-causal-map aria-label="动态三维因果路径示意图：从问题和数据经过方法到结论与报告">
          <div class="causal-map__stage">
            <svg viewBox="0 0 520 190" role="img" aria-hidden="true">
              <defs>
                <marker id="arrow-sign-up" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
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
                <path class="causal-map__line causal-map__line--strong" d="M82 55C146 55 160 95 222 95" pathLength="1" marker-end="url(#arrow-sign-up)"></path>
                <path class="causal-map__line" d="M82 132C146 132 160 95 222 95" pathLength="1"></path>
                <path class="causal-map__line causal-map__line--strong" d="M298 95C354 95 370 55 438 55" pathLength="1" marker-end="url(#arrow-sign-up)"></path>
                <path class="causal-map__line causal-map__line--strong" d="M298 95C354 95 370 135 438 135" pathLength="1" marker-end="url(#arrow-sign-up)"></path>
                <path class="causal-map__line causal-map__line--flow" d="M82 55C146 55 160 95 222 95" pathLength="1"></path>
                <path class="causal-map__line causal-map__line--flow" d="M82 132C146 132 160 95 222 95" pathLength="1"></path>
                <path class="causal-map__line causal-map__line--flow" d="M298 95C354 95 370 55 438 55" pathLength="1"></path>
                <path class="causal-map__line causal-map__line--flow" d="M298 95C354 95 370 135 438 135" pathLength="1"></path>
              </g>
              <g class="causal-map__node-group" style="--node-delay: -0.6s">
                <ellipse class="causal-map__node-shadow" cx="72" cy="64" rx="20" ry="5"></ellipse>
                <circle class="causal-map__node" cx="72" cy="55" r="22"></circle>
                <text class="causal-map__label" x="72" y="59" text-anchor="middle">问题</text>
              </g>
              <g class="causal-map__node-group" style="--node-delay: -1.8s">
                <ellipse class="causal-map__node-shadow" cx="72" cy="141" rx="20" ry="5"></ellipse>
                <circle class="causal-map__node" cx="72" cy="132" r="22"></circle>
                <text class="causal-map__label" x="72" y="136" text-anchor="middle">数据</text>
              </g>
              <g class="causal-map__node-group" style="--node-delay: -2.6s">
                <ellipse class="causal-map__node-shadow" cx="260" cy="108" rx="29" ry="7"></ellipse>
                <circle class="causal-map__node causal-map__node--core" cx="260" cy="95" r="32"></circle>
                <text class="causal-map__label causal-map__label--core" x="260" y="99" text-anchor="middle">方法</text>
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

        <div class="auth-intro__meta od-cluster" aria-label="注册后的内容保存方式"></div>
      </section>

      <section class="auth-panel" aria-labelledby="panel-title">
        <div class="auth-panel__head">
          <h2 id="panel-title">创建账号</h2>
          <p class="auth-panel__subtitle">注册后即可进入工作区，历史会话与上传的数据按账号保存。</p>
        </div>

        <form class="auth-form" id="sign-up-form" novalidate @submit.prevent="submit">
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
            <p class="auth-help" id="username-help">至少 3 个字符。</p>
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
                autocomplete="new-password"
                required
                aria-describedby="password-rules password-error"
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
            <ul class="auth-rules" id="password-rules" aria-label="密码要求">
              <li class="auth-rule" data-rule="length" :class="{ 'is-met': lengthMet }">至少 6 个字符</li>
              <li class="auth-rule" data-rule="number" :class="{ 'is-met': numberMet }">包含至少一个数字</li>
            </ul>
            <p class="auth-error" id="password-error" :hidden="!passwordError">{{ passwordError }}</p>
          </div>

          <div class="auth-field od-field" id="confirm-field" :class="{ 'is-error': confirmError }">
            <label for="confirm-password">确认密码</label>
            <div class="auth-input-shell">
              <input
                class="auth-input"
                id="confirm-password"
                name="confirm-password"
                type="password"
                autocomplete="new-password"
                required
                aria-describedby="confirm-help confirm-error"
                :aria-invalid="confirmError ? 'true' : 'false'"
                v-model="confirmPassword"
              />
            </div>
            <p class="auth-help" id="confirm-help">再次输入相同的密码。</p>
            <p class="auth-error" id="confirm-error" :hidden="!confirmError">{{ confirmError }}</p>
          </div>

          <div class="auth-form-footer od-stack" style="--od-gap: 16px">
            <p class="auth-status auth-status--error" id="form-status" role="alert" :hidden="!formStatus">{{ formStatus }}</p>
            <p class="auth-status auth-status--success" id="form-success" role="status" :hidden="!done">
              <span>注册成功，请使用新账号登录。</span>
              <a class="auth-status-link" :href="signInHref">前往登录</a>
            </p>
            <button class="auth-submit" id="submit-button" type="submit" :disabled="busy">
              <span id="submit-label" v-show="!busy">注册</span>
              <span id="submit-loading" v-show="busy">正在注册…</span>
            </button>
          </div>
        </form>

        <p class="auth-switch">已有账号？ <a class="auth-inline-link" :href="signInHref">直接登录</a></p>
        <p class="auth-footnote">注册后使用同一账号登录网页端或桌面端。</p>
      </section>
    </div>
  </main>
</template>
