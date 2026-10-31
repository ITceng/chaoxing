<script setup lang="ts">
import { reactive, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import AuthFrame from '../components/AuthFrame.vue'

const auth = useAuthStore()
const router = useRouter()
const formRef = ref<FormInstance>()
const loading = ref(false)
const error = ref('')
const form = reactive({ display_name: '', email: '', invitation_code: '', password: '', confirm_password: '' })
const validateConfirm = (_rule: unknown, value: string, callback: (error?: Error) => void) => {
  if (value !== form.password) callback(new Error('两次输入的密码不一致'))
  else callback()
}
const rules: FormRules = {
  display_name: [{ required: true, message: '请输入显示名称', trigger: 'blur' }, { min: 1, max: 120, message: '名称长度为 1–120 个字符', trigger: 'blur' }],
  email: [{ required: true, message: '请输入邮箱', trigger: 'blur' }, { type: 'email', message: '请输入有效邮箱地址', trigger: 'blur' }],
  invitation_code: [{ required: true, message: '请输入管理员发放的邀请码', trigger: 'blur' }, { min: 16, message: '邀请码格式不正确', trigger: 'blur' }],
  password: [{ required: true, message: '请设置密码', trigger: 'blur' }, { min: 12, max: 256, message: '密码长度需为 12–256 位', trigger: 'blur' }],
  confirm_password: [{ required: true, message: '请再次输入密码', trigger: 'blur' }, { validator: validateConfirm, trigger: 'blur' }],
}
async function submit() {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  loading.value = true
  error.value = ''
  try {
    await auth.register({ email: form.email.trim(), display_name: form.display_name.trim(), password: form.password, invitation_code: form.invitation_code.trim() })
    ElMessage.success('注册成功，已自动登录')
    await router.replace('/app/dashboard')
  } catch (err: any) {
    error.value = err.message || '注册失败，请检查邀请码和网络后重试'
  } finally { loading.value = false }
}
</script>

<template>
  <AuthFrame eyebrow="INVITED ACCESS" heading="创建账户" description="注册需要管理员发放的有效邀请码。服务端会验证邀请码状态、有效期和可用次数。">
    <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="submit">
      <el-form-item label="显示名称" prop="display_name"><el-input v-model="form.display_name" autocomplete="name" placeholder="例如：林同学" /></el-form-item>
      <el-form-item label="邮箱" prop="email"><el-input v-model="form.email" type="email" autocomplete="email" placeholder="name@example.com" /></el-form-item>
      <el-form-item label="邀请码" prop="invitation_code"><el-input v-model="form.invitation_code" autocomplete="one-time-code" placeholder="粘贴管理员发放的邀请码" /></el-form-item>
      <el-form-item label="密码" prop="password"><el-input v-model="form.password" type="password" show-password autocomplete="new-password" placeholder="至少 12 位" /></el-form-item>
      <el-form-item label="确认密码" prop="confirm_password"><el-input v-model="form.confirm_password" type="password" show-password autocomplete="new-password" placeholder="再次输入密码" @keyup.enter="submit" /></el-form-item>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="auth-alert" />
      <el-button type="primary" native-type="submit" class="auth-submit" :loading="loading">验证邀请码并注册 <span aria-hidden="true">→</span></el-button>
    </el-form>
    <div class="auth-bottom">已经有账户？ <RouterLink to="/login">返回登录</RouterLink></div>
    <div class="auth-warning">邀请码在服务端一次性验证；失效、过期、次数耗尽或账户重复时，会显示真实接口返回结果。</div>
  </AuthFrame>
</template>

<style scoped>.auth-alert{margin:4px 0 10px}.auth-alert :deep(.el-alert__title){font-size:10px}</style>
