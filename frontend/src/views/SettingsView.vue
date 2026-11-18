<script setup lang="ts">
/** 设置：资料、对话用的大模型、脱敏（总开关、规则和脱敏专用模型，放在大模型与结构协议之间；两份模型配置同构，各自保存） */
import { settingsApi } from '@/api'
import LLMConfigPanel from '@/components/settings/LLMConfigPanel.vue'
import PrivacyCard from '@/components/settings/PrivacyCard.vue'
import ProfileCard from '@/components/settings/ProfileCard.vue'

const chatModelApi = { load: settingsApi.getLLM, save: settingsApi.saveLLM, test: settingsApi.testLLM }
const privacyModelApi = { load: settingsApi.getPrivacyLLM, save: settingsApi.savePrivacyLLM, test: settingsApi.testPrivacyLLM }
</script>

<template>
  <div class="h-full overflow-y-auto">
    <div class="mx-auto max-w-[760px] px-5 py-8 pb-44 sm:px-8 sm:py-10 sm:pb-44">
      <h1 class="text-[28px] font-semibold tracking-tight">设置</h1>

      <section class="mt-8" aria-labelledby="profile-heading">
        <h2 id="profile-heading" class="mb-3 text-lg font-semibold tracking-tight">资料</h2>
        <ProfileCard />
      </section>

      <LLMConfigPanel title="大模型" :api="chatModelApi">
        <section class="mt-8" aria-labelledby="privacy-heading">
          <h2 id="privacy-heading" class="mb-3 text-lg font-semibold tracking-tight">脱敏</h2>
          <PrivacyCard>
            <LLMConfigPanel
              embedded
              title="脱敏模型"
              id-prefix="privacy-"
              default-provider="custom"
              :providers="['custom']"
              :show-protocol="false"
              :api="privacyModelApi"
            />
          </PrivacyCard>
        </section>
      </LLMConfigPanel>
    </div>
  </div>
</template>
