<script setup>
import { ref, onMounted, computed } from 'vue'
import { taggingApi } from '@/apis/tagging_api'
import TaggingTaskList from '@/components/tagging/TaggingTaskList.vue'
import TaxonomyTree from '@/components/tagging/TaxonomyTree.vue'
import SystemConfig from '@/components/tagging/SystemConfig.vue'

const activeTab = ref('tasks')
const stats = ref({
  pending_review: 0,
  approved_today: 0,
  total: 0,
  by_status: {}
})
const concurrency = ref({ current: 0, limit: 3 })

const loadStats = async () => {
  try {
    const [s, c] = await Promise.all([taggingApi.getStats(), taggingApi.getConcurrency()])
    stats.value = s
    concurrency.value = c
  } catch (e) {
    console.error('Failed to load stats:', e)
  }
}

onMounted(() => {
  loadStats()
})
</script>

<template>
  <div class="tagging-container">
    <div class="tagging-header">
      <h2 class="page-title">标签中心</h2>
      <div class="stat-cards">
        <div class="stat-card">
          <span class="stat-value">{{ stats.pending_review }}</span>
          <span class="stat-label">待审核</span>
        </div>
        <div class="stat-card">
          <span class="stat-value">{{ stats.approved_today }}</span>
          <span class="stat-label">今日通过</span>
        </div>
        <div class="stat-card">
          <span class="stat-value">{{ concurrency.current }} / {{ concurrency.limit }}</span>
          <span class="stat-label">当前并发</span>
        </div>
      </div>
    </div>

    <a-tabs v-model:activeKey="activeTab" class="tagging-tabs">
      <a-tab-pane key="tasks" tab="打标任务">
        <TaggingTaskList @refresh="loadStats" />
      </a-tab-pane>
      <a-tab-pane key="taxonomy" tab="标签体系">
        <TaxonomyTree />
      </a-tab-pane>
      <a-tab-pane key="config" tab="系统配置">
        <SystemConfig @saved="loadStats" />
      </a-tab-pane>
    </a-tabs>
  </div>
</template>

<style scoped lang="less">
.tagging-container {
  padding: 20px 24px;
  height: 100%;
  overflow-y: auto;
}

.tagging-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.page-title {
  font-size: 20px;
  font-weight: 600;
  color: var(--gray-1000);
  margin: 0;
}

.stat-cards {
  display: flex;
  gap: 16px;
}

.stat-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 8px 20px;
  background: var(--gray-25);
  border-radius: 8px;
  min-width: 80px;
}

.stat-value {
  font-size: 18px;
  font-weight: 600;
  color: var(--main-700);
}

.stat-label {
  font-size: 12px;
  color: var(--gray-600);
  margin-top: 2px;
}

.tagging-tabs {
  :deep(.ant-tabs-nav) {
    margin-bottom: 16px;
  }
}
</style>
