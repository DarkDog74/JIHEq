<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import App from './App.vue'

type Session = { user: { id: string; username: string; role: string }; csrf_token: string }
const base = import.meta.env.BASE_URL.replace(/\/$/, '')
const session = ref<Session | null>(null)
const loading = ref(true)
const submitting = ref(false)
const username = ref('')
const password = ref('')
const error = ref('')
let timer: number | undefined
let generation = 0

function clearSession() {
  generation++
  session.value = null
  password.value = ''
}

async function checkSession() {
  const version = generation
  try {
    const response = await fetch(`${base}/api/auth/me`, { credentials: 'same-origin', cache: 'no-store' })
    if (version !== generation) return
    if (response.ok) {
      const payload = await response.json()
      if (version === generation) session.value = payload
    } else if (response.status === 401) clearSession()
  } catch { /* Preserve an existing view during a brief network interruption. */ }
  finally { loading.value = false }
}

async function login() {
  if (submitting.value) return
  submitting.value = true
  error.value = ''
  generation++
  try {
    const response = await fetch(`${base}/api/auth/login`, {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: username.value, password: password.value }),
    })
    const payload = await response.json()
    if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : '登录失败')
    session.value = payload
    password.value = ''
  } catch (caught) { error.value = caught instanceof Error ? caught.message : '登录失败，请稍后重试' }
  finally { submitting.value = false }
}

async function logout() {
  if (!session.value || submitting.value) return
  submitting.value = true
  error.value = ''
  try {
    const response = await fetch(`${base}/api/auth/logout`, {
      method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': session.value.csrf_token },
    })
    if (!response.ok && response.status !== 401) throw new Error('退出失败，请稍后重试')
    clearSession()
  } catch (caught) { error.value = caught instanceof Error ? caught.message : '退出失败' }
  finally { submitting.value = false }
}

onMounted(() => {
  void checkSession()
  timer = window.setInterval(() => { if (session.value) void checkSession() }, 15000)
})
onBeforeUnmount(() => window.clearInterval(timer))
</script>

<template>
  <div v-if="loading" class="auth-loading" role="status">正在检查登录状态…</div>
  <template v-else-if="session">
    <p v-if="error" class="auth-notice" role="alert">{{ error }}</p>
    <App :key="session.user.id" :user="session.user" :csrf-token="session.csrf_token"
      @logout="logout" @unauthorized="clearSession" />
  </template>
  <main v-else class="login-page">
    <section class="login-card">
      <div class="login-mark" aria-hidden="true">稽</div>
      <p class="login-eyebrow">浙江移动</p>
      <h1>定级备案报告稽核系统</h1>
      <p class="login-description">登录后上传材料，查看你的稽核任务与报告。</p>
      <form @submit.prevent="login">
        <label for="username">账号</label>
        <input id="username" v-model="username" autocomplete="username" maxlength="64" required autofocus placeholder="请输入账号">
        <label for="password">密码</label>
        <input id="password" v-model="password" type="password" autocomplete="current-password" maxlength="256" required placeholder="请输入密码">
        <p v-if="error" class="login-error" role="alert">{{ error }}</p>
        <button type="submit" :disabled="submitting">{{ submitting ? '正在登录…' : '登录' }}</button>
      </form>
      <p class="login-help">账号开通或密码重置，请联系系统管理员。</p>
    </section>
  </main>
</template>

<style scoped>
.auth-loading,.login-page{min-height:100vh;display:grid;place-items:center;background:radial-gradient(ellipse at top left,#e3efff,transparent 65%),#f4f7fc;color:#20334e;font-family:system-ui,sans-serif}
.login-page{padding:24px;box-sizing:border-box}.login-card{box-sizing:border-box;width:min(100%,440px);padding:42px;background:#fff;border:1px solid #e2e8f2;border-radius:22px;box-shadow:0 20px 70px #284b7a12}.login-mark{width:48px;height:48px;display:grid;place-items:center;background:#1769d5;color:#fff;border-radius:14px;font-size:24px;font-weight:700}.login-eyebrow{margin:24px 0 8px;color:#527193;font-size:13px;letter-spacing:2px}.login-card h1{margin:0;font-size:25px;line-height:1.4}.login-description{color:#718198;font-size:14px;line-height:1.8;margin:12px 0 28px}form{display:flex;flex-direction:column;gap:9px}label{font-size:14px;font-weight:600}input{box-sizing:border-box;width:100%;padding:13px 14px;border:1px solid #cad6e5;border-radius:9px;background:#fff;color:#20334e;font-size:15px;margin-bottom:10px}input:focus{outline:2px solid #2e7de84d;border-color:#2e7de8}button{border:0;border-radius:9px;padding:14px;background:#1769d5;color:#fff;font-size:15px;cursor:pointer;margin-top:10px}button:disabled{opacity:.6;cursor:wait}.login-help{font-size:12px;color:#7b899b;line-height:1.8;margin:25px 0 0}.login-error,.auth-notice{color:#b22e3d;background:#fff0f2;padding:10px;border-radius:8px;font-size:13px;line-height:1.6}.auth-notice{position:fixed;bottom:20px;right:20px;z-index:9999}
@media(max-width:480px){.login-card{padding:28px}.login-card h1{font-size:22px}}
</style>
