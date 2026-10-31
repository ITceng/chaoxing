<script setup lang="ts">
defineProps<{ eyebrow: string; heading: string; description: string }>()
</script>

<template>
  <div class="auth-page">
    <section class="auth-visual">
      <div class="auth-brand"><span class="auth-brand-mark">学</span><span><strong>学习空间</strong><small>统一学习管理系统</small></span></div>
      <div class="auth-pitch">
        <p class="eyebrow">A CLEARER WAY TO LEARN</p>
        <h1>把学习管理<br />变得简单而清晰。</h1>
        <p>在一个安全、清楚的工作台中管理个人资料、账号授权与平台状态。平台能力只有经过真实验证后才会开放。</p>
      </div>
      <div class="auth-trust">隐私优先 · 服务端授权 · 平台状态透明</div>
    </section>
    <section class="auth-form-area">
      <div class="auth-card">
        <p class="eyebrow">{{ eyebrow }}</p>
        <h2>{{ heading }}</h2>
        <p>{{ description }}</p>
        <slot />
      </div>
    </section>
  </div>
</template>
