<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter, RouterLink } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import AuthFrame from '../components/AuthFrame.vue'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const formRef = ref<FormInstance>()
const loading = ref(false)
const error = ref('')
const form = reactive({ email: '', password: '' })
const rules: FormRules = {
  email: [{ required: true, message: '请输入邮箱', trigger: 'blur' }, { type: 'email', message: '请输入有效邮箱地址', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}
async function submit() {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  loading.value = true
  error.value = ''
  try {
    await auth.login(form.email.trim(), form.password)
    ElMessage.success('登录成功，欢迎回来')
    const redirect = typeof route.query.redirect === 'string' && route.query.redirect.startsWith('/app') ? route.query.redirect : '/app/dashboard'
    await router.replace(redirect)
  } catch (err: any) {
    error.value = err.message || '登录失败，请检查网络后重试'
  } finally { loading.value = false }
}
</script>

<template>
  <AuthFrame eyebrow="WELCOME BACK" heading="登录学习空间" description="使用已注册邮箱和密码继续。">
    <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="submit">
      <el-form-item label="邮箱" prop="email"><el-input v-model="form.email" type="email" autocomplete="username" placeholder="name@example.com" size="large" /></el-form-item>
      <el-form-item label="密码" prop="password"><el-input v-model="form.password" type="password" show-password autocomplete="current-password" placeholder="请输入密码" size="large" @keyup.enter="submit" /></el-form-item>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="auth-alert" />
      <el-button type="primary" native-type="submit" class="auth-submit" :loading="loading">登录工作台 <span aria-hidden="true">→</span></el-button>
    </el-form>
    <div class="auth-bottom">还没有账户？ <RouterLink to="/register">使用邀请码注册</RouterLink></div>
    <div class="auth-warning">请勿在公共设备保存密码。登录状态由 HttpOnly 会话 Cookie 管理，不会保存在浏览器 LocalStorage。</div>
  </AuthFrame>
</template>

<style scoped>.auth-alert{margin:4px 0 10px}.auth-alert :deep(.el-alert__title){font-size:10px}</style>
