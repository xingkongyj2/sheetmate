<script setup lang="ts">
import { provide, shallowReactive, watch } from 'vue'
import { routeLocationKey, type RouteLocationNormalizedLoaded } from 'vue-router'

const props = defineProps<{ route: RouteLocationNormalizedLoaded }>()

// 底层页面的 useRoute() 也保持在自己的路由，不跟随设置覆盖层变化。
const pageRoute = shallowReactive({ ...props.route })
watch(() => props.route, (route) => Object.assign(pageRoute, route), { flush: 'sync' })
provide(routeLocationKey, pageRoute)
</script>

<template>
  <div class="relative z-0 flex min-h-0 flex-1 flex-col">
    <RouterView :route="route" v-slot="{ Component, route: displayedRoute }">
      <component :is="Component" :key="displayedRoute.path" />
    </RouterView>
  </div>
</template>
