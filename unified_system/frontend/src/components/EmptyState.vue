<script setup lang="ts">
import { Box } from '@element-plus/icons-vue'
defineProps<{ title: string; description?: string }>()
</script>
<template>
  <div class="empty-state"><div class="empty-symbol"><el-icon><Box /></el-icon></div><strong>{{ title }}</strong><p v-if="description">{{ description }}</p><slot /></div>
</template>
