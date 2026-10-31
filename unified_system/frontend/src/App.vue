<script setup lang="ts">
import { onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'

const router = useRouter()
const onExpired = () => {
  ElMessage.warning('登录已失效，请重新登录。')
  router.push({ name: 'login', query: { redirect: router.currentRoute.value.fullPath } })
}
onMounted(() => window.addEventListener('session-expired', onExpired))
onBeforeUnmount(() => window.removeEventListener('session-expired', onExpired))
</script>

<template>
  <el-config-provider :locale="zhCn">
    <router-view />
  </el-config-provider>
</template>
