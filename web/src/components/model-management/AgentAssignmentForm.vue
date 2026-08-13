<script setup>
import { computed } from 'vue'
import { LockKeyhole } from 'lucide-vue-next'

const props = defineProps({
  modelValue: {
    type: Object,
    required: true
  },
  options: {
    type: Object,
    default: () => ({ allowed_access_levels: [], departments: [], users: [] })
  },
  disabled: { type: Boolean, default: false },
  lockedDepartmentCount: { type: Number, default: 0 },
  lockedUserCount: { type: Number, default: 0 }
})

const emit = defineEmits(['update:modelValue'])

const canSetGlobal = computed(() =>
  (props.options.allowed_access_levels || []).includes('global')
)
const canSelectDepartments = computed(() =>
  (props.options.allowed_access_levels || []).includes('department')
)
const departmentOptions = computed(() =>
  (props.options.departments || []).map((item) => ({ label: item.name, value: Number(item.id) }))
)
const userOptions = computed(() =>
  (props.options.users || []).map((item) => ({
    label: item.department_name
      ? `${item.username}（${item.department_name}）`
      : item.username,
    value: item.uid
  }))
)
const hasLockedAssignment = computed(
  () => props.lockedDepartmentCount > 0 || props.lockedUserCount > 0
)

const updateValue = (updates) => {
  emit('update:modelValue', { ...props.modelValue, ...updates })
}
</script>

<template>
  <div class="agent-assignment-form">
    <div v-if="canSetGlobal" class="assignment-row global-row">
      <div>
        <div class="assignment-title">全公司可使用</div>
        <div class="assignment-description">开启后，不再按部门或个人限制使用范围。</div>
      </div>
      <a-switch
        :checked="modelValue.global_access"
        :disabled="disabled"
        @change="(checked) => updateValue({ global_access: checked })"
      />
    </div>

    <template v-if="!modelValue.global_access">
      <label v-if="canSelectDepartments" class="assignment-field">
        <span class="assignment-title">绑定部门</span>
        <span class="assignment-description">所选部门及其下级员工可以使用。</span>
        <a-select
          mode="multiple"
          show-search
          allow-clear
          :disabled="disabled"
          :value="modelValue.department_ids"
          :options="departmentOptions"
          placeholder="选择部门"
          @change="(value) => updateValue({ department_ids: value })"
        />
      </label>

      <label class="assignment-field">
        <span class="assignment-title">指定员工</span>
        <span class="assignment-description">可补充指定个人；普通员工仅能选择同部门成员。</span>
        <a-select
          mode="multiple"
          show-search
          allow-clear
          :disabled="disabled"
          :value="modelValue.user_uids"
          :options="userOptions"
          placeholder="选择员工"
          @change="(value) => updateValue({ user_uids: value })"
        />
      </label>
    </template>

    <div v-if="hasLockedAssignment" class="locked-assignment-notice">
      <LockKeyhole :size="15" />
      <span>
        另有 {{ lockedDepartmentCount }} 个部门、{{ lockedUserCount }} 个员工不在你的管理范围内，保存时会原样保留。
      </span>
    </div>
  </div>
</template>

<style scoped lang="less">
.agent-assignment-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.assignment-row,
.assignment-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.global-row {
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
  padding: 12px;
  border: 1px solid var(--gray-200);
  border-radius: 10px;
}

.assignment-title {
  color: var(--gray-900);
  font-size: 14px;
  font-weight: 500;
}

.assignment-description {
  color: var(--gray-500);
  font-size: 12px;
}

.locked-assignment-notice {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  padding: 10px 12px;
  color: var(--color-warning-900);
  font-size: 12px;
  background: var(--color-warning-50);
  border: 1px solid var(--color-warning-100);
  border-radius: 8px;
}
</style>
