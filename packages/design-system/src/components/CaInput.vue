<script setup lang="ts">
import { computed, useId } from 'vue'

const props = withDefaults(
  defineProps<{
    modelValue?: string | number
    label?: string
    hint?: string
    error?: string
    placeholder?: string
    type?: string
    multiline?: boolean
    rows?: number
    name?: string
    autocomplete?: string
    disabled?: boolean
    readonly?: boolean
    required?: boolean
  }>(),
  {
    modelValue: '',
    label: undefined,
    hint: undefined,
    error: undefined,
    placeholder: undefined,
    type: 'text',
    multiline: false,
    rows: 4,
    name: undefined,
    autocomplete: undefined,
    disabled: false,
    readonly: false,
    required: false,
  },
)

const emit = defineEmits<{
  (event: 'update:modelValue', value: string): void
  (event: 'focus', payload: FocusEvent): void
  (event: 'blur', payload: FocusEvent): void
}>()

const uid = useId()
const controlId = `${uid}-control`
const hintId = `${uid}-hint`
const errorId = `${uid}-error`

const describedBy = computed(() => {
  const ids: string[] = []
  if (props.hint) ids.push(hintId)
  if (props.error) ids.push(errorId)
  return ids.length > 0 ? ids.join(' ') : undefined
})

function onInput(event: Event) {
  const target = event.target
  if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) {
    emit('update:modelValue', target.value)
  }
}
</script>

<template>
  <div class="ca-field">
    <label v-if="props.label" class="ca-field__label" :for="controlId">{{ props.label }}</label>
    <textarea
      v-if="props.multiline"
      :id="controlId"
      class="ca-field__control"
      :value="props.modelValue"
      :name="props.name"
      :rows="props.rows"
      :placeholder="props.placeholder"
      :disabled="props.disabled"
      :readonly="props.readonly"
      :required="props.required"
      :aria-invalid="props.error ? 'true' : undefined"
      :aria-describedby="describedBy"
      @input="onInput"
      @focus="emit('focus', $event)"
      @blur="emit('blur', $event)"
    />
    <input
      v-else
      :id="controlId"
      class="ca-field__control"
      :value="props.modelValue"
      :type="props.type"
      :name="props.name"
      :placeholder="props.placeholder"
      :autocomplete="props.autocomplete"
      :disabled="props.disabled"
      :readonly="props.readonly"
      :required="props.required"
      :aria-invalid="props.error ? 'true' : undefined"
      :aria-describedby="describedBy"
      @input="onInput"
      @focus="emit('focus', $event)"
      @blur="emit('blur', $event)"
    >
    <p v-if="props.hint" :id="hintId" class="ca-field__hint">{{ props.hint }}</p>
    <p v-if="props.error" :id="errorId" class="ca-field__error">{{ props.error }}</p>
  </div>
</template>
