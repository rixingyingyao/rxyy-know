<template>
  <div class="user-config-settings">
    <div class="header-section">
      <div class="header-content">
        <div class="section-title">用户配置(Beta)</div>
        <p class="section-description">
          配置当前用户的专属设置。Memory 开启后会写入智能体系统提示，跨对话记住你填的偏好和事实。
        </p>
      </div>
      <div class="header-actions">
        <a-button class="lucide-icon-btn" :loading="loading" @click="loadUserConfig">
          <template #icon><RefreshCw :size="16" :class="{ spin: loading }" /></template>
          刷新
        </a-button>
        <a-button type="primary" :loading="saving" @click="saveUserConfig">
          {{ saveButtonText }}
        </a-button>
      </div>
    </div>

    <a-spin :spinning="loading">
      <div class="config-panel">
        <div class="config-row">
          <div class="config-meta">
            <div class="config-title-line">
              <span class="config-title">是否启用 Memory</span>
            </div>
            <p class="config-description">
              开启后，下方记忆会进入每次对话的系统提示。关闭则不注入，已保存的记忆仍保留。
            </p>
          </div>
          <a-switch :checked="draftEnableMemory" @change="draftEnableMemory = Boolean($event)" />
        </div>
        <div v-if="draftEnableMemory" class="config-row config-row-block">
          <div class="config-meta">
            <div class="config-title-line">
              <span class="config-title">记忆内容</span>
            </div>
            <p class="config-description">
              写你希望智能体跨对话记住的偏好、身份和长期事实。不要放密码或密钥。
            </p>
          </div>
          <a-textarea
            v-model:value="draftMemoryText"
            :rows="6"
            :maxlength="4000"
            show-count
            placeholder="例如：回复用简体中文；我做知识库产品，默认先给可执行步骤。"
          />
        </div>
      </div>
    </a-spin>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { RefreshCw } from 'lucide-vue-next'
import { userConfigApi } from '@/apis/user_config_api'

const loading = ref(false)
const saving = ref(false)
const draftEnableMemory = ref(false)
const savedEnableMemory = ref(false)
const draftMemoryText = ref('')
const savedMemoryText = ref('')

const hasUnsavedChanges = computed(
  () =>
    draftEnableMemory.value !== savedEnableMemory.value ||
    draftMemoryText.value !== savedMemoryText.value
)
const saveButtonText = computed(() => (hasUnsavedChanges.value ? '保存（有修改）' : '保存'))

const applyResponse = (res) => {
  draftEnableMemory.value = res.enable_memory
  savedEnableMemory.value = res.enable_memory
  draftMemoryText.value = res.memory_text || ''
  savedMemoryText.value = res.memory_text || ''
}

const loadUserConfig = async () => {
  loading.value = true
  try {
    const res = await userConfigApi.get()
    applyResponse(res)
  } catch (error) {
    message.error(error.message || '加载用户配置失败')
  } finally {
    loading.value = false
  }
}

const saveUserConfig = async () => {
  if (!hasUnsavedChanges.value) {
    message.info('用户配置未变化')
    return
  }

  saving.value = true
  try {
    const res = await userConfigApi.update({
      enable_memory: draftEnableMemory.value,
      memory_text: draftMemoryText.value
    })
    applyResponse(res)
    message.success('用户配置已保存')
  } catch (error) {
    message.error(error.message || '保存用户配置失败')
  } finally {
    saving.value = false
  }
}

onMounted(loadUserConfig)
</script>

<style lang="less" scoped>
.user-config-settings {
  .header-section {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: 16px;
    margin-bottom: 12px;

    @media (max-width: 760px) {
      align-items: stretch;
      flex-direction: column;
    }
  }

  .header-content {
    flex: 1;
    min-width: 0;
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
  }

  .config-panel {
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--gray-0);
    overflow: hidden;
  }

  .config-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
    padding: 14px 16px;

    @media (max-width: 560px) {
      align-items: flex-start;
      flex-direction: column;
    }
  }

  .config-row-block {
    align-items: stretch;
    flex-direction: column;
  }

  .config-meta {
    min-width: 0;
  }

  .config-title-line {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .config-title {
    color: var(--gray-900);
    font-size: 14px;
    font-weight: 500;
    line-height: 1.4;
  }

  .reserved-badge {
    display: inline-flex;
    align-items: center;
    height: 22px;
    padding: 0 8px;
    border-radius: 999px;
    border: 1px solid var(--color-warning-100);
    background: var(--color-warning-10);
    color: var(--color-warning-700);
    font-size: 12px;
    line-height: 1;
    white-space: nowrap;
  }

  .config-description {
    margin: 6px 0 0;
    color: var(--gray-600);
    font-size: 13px;
    line-height: 1.5;
  }
}

:deep(.spin) {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }

  to {
    transform: rotate(360deg);
  }
}
</style>
