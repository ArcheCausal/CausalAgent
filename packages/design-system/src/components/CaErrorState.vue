<script setup lang="ts">
const props = withDefaults(
  defineProps<{
    title: string
    description?: string
    code?: string
  }>(),
  {
    description: undefined,
    code: undefined,
  },
)
</script>

<template>
  <div class="ca-error" role="alert">
    <p class="ca-error__title">{{ props.title }}</p>
    <p v-if="props.description" class="ca-error__description">{{ props.description }}</p>
    <p v-if="props.code" class="ca-error__code">{{ props.code }}</p>
    <slot />
    <div v-if="$slots.actions" class="ca-error__actions"><slot name="actions" /></div>
  </div>
</template>
