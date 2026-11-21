<script setup lang="ts">
/** 个人设置：头像、昵称、密码（全局模型、脱敏、公式库与统计均已迁入管理后台）。 */
import ProfileCard from '@/components/settings/ProfileCard.vue'
</script>

<template>
  <div class="h-full overflow-y-auto">
    <div class="mx-auto max-w-[680px] px-5 py-8 sm:px-8 sm:py-10">
      <h1 class="text-2xl font-semibold tracking-tight text-ink">个人设置</h1>

      <section class="mt-7" aria-labelledby="profile-heading">
        <h2 id="profile-heading" class="mb-3 text-sm font-semibold text-ink">个人资料</h2>
        <ProfileCard />
      </section>
    </div>
  </div>
</template>
