<script setup lang="ts">
defineProps<{ title: string | null }>()
</script>

<template>
  <section class="report-section">
    <h2 v-if="title" class="report-section-title">{{ title }}</h2>
    <div class="report-section-body">
      <slot />
    </div>
  </section>
</template>

<style scoped>
.report-section {
  display: flex;
  width: 100%;
  min-width: 0;
  flex-direction: column;
  gap: 12px;
}

.report-section-title {
  margin: 0;
  padding-bottom: 6px;
  font-size: var(--report-section-title-size, 18px);
  font-weight: 400;
  line-height: 1.4;
  border-bottom: 1px solid var(--report-border, #e2e5e9);
}

.report-section-body {
  display: flex;
  width: 100%;
  min-width: 0;
  flex-direction: column;
  align-items: stretch;
  gap: 16px;
}

@media (max-width: 640px) {
  .report-section-title {
    font-size: 16px;
  }
}
</style>
