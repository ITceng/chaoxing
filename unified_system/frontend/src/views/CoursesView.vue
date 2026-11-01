<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import api from '../services/api'
import type { Course, Platform } from '../types'
import { dateTime, errorMessage } from '../utils/format'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

const loading = ref(true)
const error = ref('')
const platforms = ref<Platform[]>([])
const courses = ref<Course[]>([])
const platformFilter = ref('')
const visibleCourses = computed(() => platformFilter.value
  ? courses.value.filter(course => course.platform_key === platformFilter.value)
  : courses.value)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [platformResponse, courseResponse] = await Promise.all([
      api.get<{ platforms: Platform[] }>('/platforms'),
      api.get<{ courses: Course[]; total: number }>('/courses'),
    ])
    platforms.value = platformResponse.data.platforms
    courses.value = courseResponse.data.courses
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page-wrap">
    <PageHeader eyebrow="COURSE CATALOG" title="课程数据" description="只展示真实 Worker 通过只读课程同步写入、且当前账号仍有权访问的数据。">
      <el-button :icon="Refresh" :loading="loading" @click="load">刷新课程</el-button>
    </PageHeader>
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
    <section v-loading="loading" class="surface surface-pad course-section">
      <div class="table-toolbar">
        <div class="toolbar-copy"><strong>{{ visibleCourses.length }} 门课程</strong><span>已按个人账号归属或当前有效共享授权隔离</span></div>
        <el-select v-model="platformFilter" clearable class="filter-select" placeholder="全部平台">
          <el-option v-for="platform in platforms" :key="platform.key" :label="platform.name" :value="platform.key" />
        </el-select>
      </div>
      <el-table v-if="visibleCourses.length" :data="visibleCourses" class="data-table" row-key="id">
        <el-table-column label="课程" prop="course_name" min-width="220" />
        <el-table-column label="平台" prop="platform_key" min-width="130" />
        <el-table-column label="教师" prop="teacher_name" min-width="140"><template #default="{row}">{{ row.teacher_name || '—' }}</template></el-table-column>
        <el-table-column label="学期" prop="semester" min-width="120"><template #default="{row}">{{ row.semester || '—' }}</template></el-table-column>
        <el-table-column label="资源状态" prop="resource_status" min-width="120" />
        <el-table-column label="数据来源" min-width="120"><template #default="{row}">{{ row.source_verified ? '平台验证数据' : '未验证' }}</template></el-table-column>
        <el-table-column label="最近同步" min-width="170"><template #default="{row}">{{ dateTime(row.last_synced_at) }}</template></el-table-column>
      </el-table>
      <EmptyState v-else title="暂无已同步课程" description="仅当真实平台 Worker 成功完成只读同步后，课程才会出现在这里。空列表不代表同步成功；当前没有模拟课程数据。" />
    </section>
  </div>
</template>

<style scoped>
.course-section{min-height:300px}.table-toolbar{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:14px}.toolbar-copy strong,.toolbar-copy span{display:block}.toolbar-copy strong{color:#29424c;font-size:14px}.toolbar-copy span{margin-top:4px;color:#89969c;font-size:11px}.filter-select{width:190px}@media(max-width:600px){.table-toolbar{align-items:stretch;flex-direction:column}.filter-select{width:100%}}
</style>
