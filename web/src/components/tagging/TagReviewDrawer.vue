<script setup>
import { ref, watch, computed } from 'vue'
import { message } from 'ant-design-vue'
import { taggingApi } from '@/apis/tagging_api'
import { X, Check, Plus } from 'lucide-vue-next'

const props = defineProps({
  visible: Boolean,
  task: Object,
})
const emit = defineEmits(['close', 'done'])

const tags = ref([])
const loading = ref(false)
const addTagVisible = ref(false)
const newTagSearch = ref('')
const searchResults = ref([])

const tagColor = (tag) => {
  return tag.source === 'rules' ? 'green' : undefined
}

watch(
  () => props.task,
  (val) => {
    if (val && val.tags) {
      tags.value = JSON.parse(JSON.stringify(val.tags))
    }
  },
  { immediate: true }
)

const removeTag = (index) => {
  tags.value.splice(index, 1)
}

const searchTaxonomy = async () => {
  if (!newTagSearch.value.trim()) {
    searchResults.value = []
    return
  }
  try {
    searchResults.value = await taggingApi.searchTaxonomy(newTagSearch.value)
  } catch (e) {
    searchResults.value = []
  }
}

const addTag = (node) => {
  if (tags.value.some((t) => t.tag_id === node.id)) {
    message.warning('该标签已存在')
    return
  }
  tags.value.push({
    tag_id: node.id,
    tag_name: node.name_zh,
    path: node.path,
    level: node.level,
    confidence: 1.0,
    source: 'manual',
    reasoning: '人工添加',
  })
  addTagVisible.value = false
  newTagSearch.value = ''
}

const quickCreateAndAdd = async () => {
  if (!newTagSearch.value.trim()) return
  try {
    const node = await taggingApi.addTaxonomyNode({ name_zh: newTagSearch.value })
    addTag(node)
    message.success('标签已创建并添加')
  } catch (e) {
    message.error('创建标签失败')
  }
}

const handleApprove = async () => {
  loading.value = true
  try {
    await taggingApi.updateTaskTags(props.task.task_id, tags.value)
    message.success('审核通过')
    emit('done')
  } catch (e) {
    message.error('操作失败')
  } finally {
    loading.value = false
  }
}

const handleReject = async () => {
  loading.value = true
  try {
    await taggingApi.rejectTask(props.task.task_id)
    message.success('已拒绝')
    emit('done')
  } catch (e) {
    message.error('操作失败')
  } finally {
    loading.value = false
  }
}

const handleKeydown = (e) => {
  if (!props.visible) return
  if (e.key === 'Enter' && !addTagVisible.value) {
    handleApprove()
  } else if (e.key === 'Escape' && !addTagVisible.value) {
    handleReject()
  }
}

// 键盘快捷键
if (typeof window !== 'undefined') {
  window.addEventListener('keydown', handleKeydown)
}
</script>

<template>
  <a-drawer
    :open="visible"
    title="标签审核"
    :width="560"
    @close="emit('close')"
    :destroyOnClose="true"
  >
    <template v-if="task">
      <div class="review-info">
        <div class="info-row">
          <span class="label">文件名</span>
          <span class="value">{{ task.filename }}</span>
        </div>
        <div class="info-row">
          <span class="label">类型</span>
          <span class="value">{{ task.file_type }}</span>
        </div>
        <div class="info-row">
          <span class="label">平均置信度</span>
          <span class="value">{{ (task.avg_confidence * 100).toFixed(0) }}%</span>
        </div>
      </div>

      <a-divider>推荐标签</a-divider>

      <div class="tags-section">
        <div v-for="(tag, i) in tags" :key="i" class="tag-item">
          <div class="tag-info">
            <a-tag :color="tagColor(tag)">
              <span v-if="tag.ancestor_path" style="opacity: 0.5; font-size: 11px">
                {{ tag.ancestor_path.split(' > ').slice(0, -1).join(' > ') }}
                <template v-if="tag.ancestor_path.split(' > ').slice(0, -1).length > 0">
                  &gt;
                </template>
              </span>
              {{ tag.tag_name }}
            </a-tag>
            <a-progress
              :percent="Math.round(tag.confidence * 100)"
              :strokeWidth="4"
              size="small"
              style="width: 80px"
            />
          </div>
          <div class="tag-meta">
            <span class="tag-path">{{ tag.path }}</span>
            <span class="tag-reason">{{ tag.reasoning }}</span>
          </div>
          <a-button type="text" size="small" danger @click="removeTag(i)">
            <template #icon><X :size="14" /></template>
          </a-button>
        </div>

        <a-button type="dashed" block @click="addTagVisible = true" style="margin-top: 8px">
          <template #icon><Plus :size="14" /></template>
          添加标签
        </a-button>
      </div>

      <!-- 添加标签弹窗 -->
      <a-modal v-model:open="addTagVisible" title="添加标签" :footer="null" width="400px">
        <a-input-search
          v-model:value="newTagSearch"
          placeholder="搜索标签..."
          @search="searchTaxonomy"
          @input="searchTaxonomy"
          style="margin-bottom: 12px"
        />
        <div class="search-results" v-if="searchResults.length">
          <div
            v-for="node in searchResults.slice(0, 10)"
            :key="node.id"
            class="search-item"
            @click="addTag(node)"
          >
            <span>{{ node.name_zh }}</span>
            <span class="search-path">{{ node.path }}</span>
          </div>
        </div>
        <div v-else-if="newTagSearch" class="no-results">
          <span>未找到匹配标签</span>
          <a-button type="link" size="small" @click="quickCreateAndAdd">
            创建「{{ newTagSearch }}」并添加
          </a-button>
        </div>
      </a-modal>
    </template>

    <template #footer>
      <div class="review-footer">
        <span class="shortcut-hint">Enter 通过 · Esc 拒绝</span>
        <a-space>
          <a-button @click="handleReject" :loading="loading" danger>拒绝</a-button>
          <a-button type="primary" @click="handleApprove" :loading="loading">
            <template #icon><Check :size="14" /></template>
            通过
          </a-button>
        </a-space>
      </div>
    </template>
  </a-drawer>
</template>

<style scoped lang="less">
.review-info {
  .info-row {
    display: flex;
    padding: 6px 0;
    .label {
      width: 80px;
      color: var(--gray-600);
      flex-shrink: 0;
    }
    .value {
      color: var(--gray-1000);
    }
  }
}

.tags-section {
  .tag-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px;
    border-radius: 6px;
    margin-bottom: 6px;
    background: var(--gray-25);

    .tag-info {
      display: flex;
      align-items: center;
      gap: 8px;
      flex: 1;
    }

    .tag-meta {
      display: flex;
      flex-direction: column;
      flex: 1;
      .tag-path {
        font-size: 11px;
        color: var(--gray-500);
      }
      .tag-reason {
        font-size: 11px;
        color: var(--gray-400);
      }
    }
  }
}

.search-results {
  max-height: 300px;
  overflow-y: auto;

  .search-item {
    display: flex;
    justify-content: space-between;
    padding: 8px 12px;
    cursor: pointer;
    border-radius: 4px;

    &:hover {
      background: var(--gray-50);
    }

    .search-path {
      font-size: 12px;
      color: var(--gray-400);
    }
  }
}

.no-results {
  text-align: center;
  padding: 16px;
  color: var(--gray-500);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.review-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;

  .shortcut-hint {
    font-size: 12px;
    color: var(--gray-400);
  }
}
</style>
