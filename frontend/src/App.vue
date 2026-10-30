<script setup lang="ts">
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import ToastHost from '@/components/common/ToastHost.vue'
</script>

<template>
  <RouterView />
  <ConfirmDialog />
  <ToastHost />
</template>
