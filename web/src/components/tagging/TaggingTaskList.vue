<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import {
  UploadOutlined,
  DeleteOutlined,
  ReloadOutlined,
  CheckOutlined
} from '@ant-design/icons-vue'
import { taggingApi } from '@/apis/tagging_api'
import { useDatabaseStore } from '@/stores/database'
import TagReviewDrawer from './TagReviewDrawer.vue'

const emit = defineEmits(['refresh'])
const databaseStore = useDatabaseStore()

const loading = ref(false)
const tasks = ref([])
const total = ref(0)
const pagination = ref({ current: 1, pageSize: 20 })
const filters = ref({ status: null, db_id: null, file_type: null })
const sortBy = ref('created_at')
const ascending = ref(false)

// 审核抽屉
const reviewDrawerVisible = ref(false)
const reviewTask = ref(null)

// 批量打标
const batchModalVisible = ref(false)
const batchForm = ref({ db_id: null, file_ids: [] })
const batchLoading = ref(false)
const batchFileList = ref([])

// 自动刷新
let pollTimer = null
const POLL_INTERVAL = 5000 // 5秒
const MAX_POLL_INTERVAL = 30000 // 最大退避 30秒
let currentPollInterval = POLL_INTERVAL
let pollFailCount = 0

// 知识库名称映射
const dbNameMap = computed(() => {
  const map = {}
  databaseStore.databases.forEach((d) => {
    map[d.kb_id] = d.name
  })
  return map
})
const getDbName = (dbId) => dbNameMap.value[dbId] || dbId

// 是否有进行中的任务（不含 tagged —— 已完成打标，仅待审核）
const hasRunningTasks = computed(() =>
  tasks.value.some((t) =>
    ['pending', 'preprocessing', 'preprocessed', 'tagging'].includes(t.status)
  )
)

const statusOptions = [
  { label: '全部', value: null },
  { label: '等待中', value: 'pending' },
  { label: '预处理中', value: 'preprocessing' },
  { label: '已预处理', value: 'preprocessed' },
  { label: '打标中', value: 'tagging' },
  { label: '待审核', value: 'review' },
  { label: '已通过', value: 'approved' },
  { label: '已拒绝', value: 'rejected' },
  { label: '预处理失败', value: 'error_preprocessing' },
  { label: '打标失败', value: 'error_tagging' }
]

const statusColorMap = {
  pending: 'default',
  preprocessing: 'processing',
  preprocessed: 'processing',
  tagging: 'processing',
  tagged: 'warning',
  review: 'warning',
  approved: 'success',
  rejected: 'error',
  error_preprocessing: 'error',
  error_tagging: 'error'
}

const statusTextMap = {
  pending: '等待中',
  preprocessing: '预处理中',
  preprocessed: '已预处理',
  tagging: '打标中',
  tagged: '已打标',
  review: '待审核',
  approved: '已通过',
  rejected: '已拒绝',
  error_preprocessing: '预处理失败',
  error_tagging: '打标失败'
}

const columns = [
  { title: '文件名', dataIndex: 'filename', width: 200, ellipsis: true },
  { title: '知识库', dataIndex: 'db_id', width: 120, ellipsis: true },
  { title: '类型', dataIndex: 'file_type', width: 80 },
  { title: '状态', dataIndex: 'status', width: 100 },
  { title: '置信度', dataIndex: 'avg_confidence', width: 80, sorter: true },
  { title: '标签', dataIndex: 'tags', width: 200 },
  { title: '创建时间', dataIndex: 'created_at', width: 150, sorter: true },
  { title: '操作', key: 'action', width: 120, fixed: 'right' }
]

const loadTasks = async () => {
  loading.value = true
  try {
    const res = await taggingApi.getTasks({
      status: filters.value.status,
      db_id: filters.value.db_id,
      file_type: filters.value.file_type,
      sort_by: sortBy.value,
      ascending: ascending.value,
      page: pagination.value.current,
      page_size: pagination.value.pageSize
    })
    tasks.value = res.tasks
    total.value = res.total
    // 成功时重置退避
    pollFailCount = 0
    currentPollInterval = POLL_INTERVAL
  } catch (e) {
    message.error('加载任务失败')
    // 指数退避：连续失败时增大轮询间隔
    pollFailCount++
    currentPollInterval = Math.min(POLL_INTERVAL * Math.pow(2, pollFailCount), MAX_POLL_INTERVAL)
  } finally {
    loading.value = false
  }
}

const handleTableChange = (pag, fil, sorter) => {
  pagination.value.current = pag.current
  pagination.value.pageSize = pag.pageSize
  if (sorter.field) {
    sortBy.value = sorter.field === 'avg_confidence' ? 'confidence' : sorter.field
    ascending.value = sorter.order === 'ascend'
  }
  loadTasks()
}

const openReview = (task) => {
  reviewTask.value = task
  reviewDrawerVisible.value = true
}

const handleApprove = async (taskId) => {
  try {
    await taggingApi.approveTask(taskId)
    message.success('已通过')
    loadTasks()
    emit('refresh')
  } catch (e) {
    message.error('操作失败')
  }
}

const handleReject = async (taskId) => {
  try {
    await taggingApi.rejectTask(taskId)
    message.success('已拒绝')
    loadTasks()
    emit('refresh')
  } catch (e) {
    message.error('操作失败')
  }
}

const handleRetry = async (taskId) => {
  try {
    await taggingApi.retryTask(taskId)
    message.success('已重新加入队列')
    loadTasks()
    emit('refresh')
  } catch (e) {
    message.error('重试失败')
  }
}

// 行选择
const selectedRowKeys = ref([])
const rowSelection = computed(() => ({
  selectedRowKeys: selectedRowKeys.value,
  onChange: (keys) => {
    selectedRowKeys.value = keys
  }
}))

const handleBatchApprove = async () => {
  const ids = selectedRowKeys.value.filter((id) => {
    const t = tasks.value.find((t) => t.task_id === id)
    return t && t.status === 'review'
  })
  if (!ids.length) {
    message.warning('没有可通过的任务')
    return
  }
  try {
    const res = await taggingApi.batchApproveTasks(ids)
    message.success(`已通过 ${res.approved} 个任务`)
    selectedRowKeys.value = []
    loadTasks()
    emit('refresh')
  } catch (e) {
    message.error('批量通过失败')
  }
}

const handleBatchRetry = async () => {
  const ids = selectedRowKeys.value.filter((id) => {
    const t = tasks.value.find((t) => t.task_id === id)
    return t && ['error_preprocessing', 'error_tagging', 'rejected'].includes(t.status)
  })
  if (!ids.length) {
    message.warning('没有可重试的任务')
    return
  }
  try {
    const res = await taggingApi.batchRetryTasks(ids)
    message.success(`已重试 ${res.retried} 个任务`)
    selectedRowKeys.value = []
    loadTasks()
    emit('refresh')
  } catch (e) {
    message.error('批量重试失败')
  }
}

const handleBatchDelete = () => {
  if (!selectedRowKeys.value.length) {
    message.warning('请先选择任务')
    return
  }
  Modal.confirm({
    title: '确认删除',
    content: `确定删除选中的 ${selectedRowKeys.value.length} 个任务？此操作不可恢复。`,
    okType: 'danger',
    async onOk() {
      try {
        const res = await taggingApi.batchDeleteTasks(selectedRowKeys.value)
        message.success(`已删除 ${res.deleted} 个任务`)
        selectedRowKeys.value = []
        loadTasks()
        emit('refresh')
      } catch (e) {
        message.error('删除失败')
      }
    }
  })
}

const handleDeleteOne = (taskId) => {
  Modal.confirm({
    title: '确认删除',
    content: '确定删除此任务？',
    okType: 'danger',
    async onOk() {
      try {
        await taggingApi.deleteTask(taskId)
        message.success('已删除')
        loadTasks()
        emit('refresh')
      } catch (e) {
        message.error('删除失败')
      }
    }
  })
}

const handleReviewDone = () => {
  reviewDrawerVisible.value = false
  loadTasks()
  emit('refresh')
}

// 批量打标
const loadKbFiles = async () => {
  if (!batchForm.value.db_id) {
    batchFileList.value = []
    return
  }
  try {
    const { documentApi } = await import('@/apis/knowledge_api')
    const data = await documentApi.listDocuments(batchForm.value.db_id, { page: 1, page_size: 500 })
    const files = data.items || []
    batchFileList.value = files
      .filter((f) => !f.is_folder)
      .map((f) => ({
        label: f.filename,
        value: f.file_id
      }))
  } catch (e) {
    batchFileList.value = []
  }
}

const handleBatchTag = async () => {
  if (!batchForm.value.db_id || batchForm.value.file_ids.length === 0) {
    message.warning('请选择知识库和文件')
    return
  }
  batchLoading.value = true
  try {
    const res = await taggingApi.batchTag(batchForm.value.db_id, batchForm.value.file_ids)
    message.success(`已创建 ${res.count} 个打标任务`)
    batchModalVisible.value = false
    batchForm.value = { db_id: null, file_ids: [] }
    loadTasks()
  } catch (e) {
    message.error('批量打标失败')
  } finally {
    batchLoading.value = false
  }
}

// 上传打标
const uploadLoading = ref(false)
const handleUploadFile = async (file) => {
  uploadLoading.value = true
  try {
    const res = await taggingApi.uploadAndTag(file)
    message.success(`已创建打标任务: ${res.filename}`)
    loadTasks()
  } catch (e) {
    message.error('上传打标失败')
  } finally {
    uploadLoading.value = false
  }
  return false // prevent default upload
}

watch(() => batchForm.value.db_id, loadKbFiles)

// 自动轮询：有进行中任务时启动（支持退避）
const startPolling = () => {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(() => {
    loadTasks()
    // 动态调整间隔（如果退避了，重建 timer）
    if (currentPollInterval !== POLL_INTERVAL && pollFailCount > 0) {
      clearInterval(pollTimer)
      pollTimer = setInterval(loadTasks, currentPollInterval)
    }
  }, currentPollInterval)
}

watch(hasRunningTasks, (running) => {
  if (running && !pollTimer) {
    startPolling()
  } else if (!running && pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
})

onMounted(() => {
  loadTasks()
  if (databaseStore.databases.length === 0) {
    databaseStore.loadDatabases()
  }
})

onUnmounted(() => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
})
</script>

<template>
  <div class="task-list">
    <div class="task-toolbar">
      <div class="filters">
        <a-select
          v-model:value="filters.status"
          placeholder="状态筛选"
          :options="statusOptions"
          style="width: 140px"
          allowClear
          @change="loadTasks"
        />
        <a-select
          v-model:value="filters.db_id"
          placeholder="知识库"
          :options="databaseStore.databases.map((d) => ({ label: d.name, value: d.kb_id }))"
          style="width: 160px"
          allowClear
          @change="loadTasks"
        />
      </div>
      <a-space>
        <a-upload
          :showUploadList="false"
          :beforeUpload="handleUploadFile"
          :multiple="false"
          accept=".txt,.md,.pdf,.docx,.mp3,.mp4,.wav,.png,.jpg,.jpeg"
        >
          <a-button :loading="uploadLoading">
            <template #icon><UploadOutlined /></template>
            上传打标
          </a-button>
        </a-upload>
        <a-button type="primary" @click="batchModalVisible = true">新建打标任务</a-button>
      </a-space>
    </div>

    <!-- 批量操作栏 -->
    <div v-if="selectedRowKeys.length" class="batch-action-bar">
      <span>已选 {{ selectedRowKeys.length }} 项</span>
      <a-space>
        <a-button size="small" @click="handleBatchApprove">
          <template #icon><CheckOutlined /></template>
          批量通过
        </a-button>
        <a-button size="small" @click="handleBatchRetry">
          <template #icon><ReloadOutlined /></template>
          批量重试
        </a-button>
        <a-button size="small" danger @click="handleBatchDelete">
          <template #icon><DeleteOutlined /></template>
          批量删除
        </a-button>
        <a-button size="small" type="link" @click="selectedRowKeys = []">取消选择</a-button>
      </a-space>
    </div>

    <a-table
      :columns="columns"
      :dataSource="tasks"
      :loading="loading"
      :row-selection="rowSelection"
      :pagination="{
        current: pagination.current,
        pageSize: pagination.pageSize,
        total: total,
        showSizeChanger: true,
        showTotal: (t) => `共 ${t} 条`
      }"
      :scroll="{ x: 1100 }"
      rowKey="task_id"
      size="small"
      @change="handleTableChange"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.dataIndex === 'db_id'">
          {{ getDbName(record.db_id) }}
        </template>
        <template v-else-if="column.dataIndex === 'status'">
          <a-tooltip v-if="record.error && record.status.startsWith('error')" :title="record.error">
            <a-tag :color="statusColorMap[record.status]" style="cursor: help">
              {{ statusTextMap[record.status] || record.status }}
            </a-tag>
          </a-tooltip>
          <a-tag v-else :color="statusColorMap[record.status]">
            {{ statusTextMap[record.status] || record.status }}
          </a-tag>
        </template>
        <template v-else-if="column.dataIndex === 'avg_confidence'">
          <span v-if="record.avg_confidence > 0">
            {{ (record.avg_confidence * 100).toFixed(0) }}%
          </span>
          <span v-else class="text-muted">-</span>
        </template>
        <template v-else-if="column.dataIndex === 'tags'">
          <template v-if="record.tags && record.tags.length">
            <a-popover placement="topLeft" trigger="hover">
              <template #content>
                <div style="max-width: 500px">
                  <a-tag
                    v-for="(tag, i) in record.tags"
                    :key="i"
                    :color="
                      tag.source === 'rules' ? 'green' : tag.is_suggested ? 'orange' : undefined
                    "
                    style="margin: 2px"
                  >
                    <a-tooltip v-if="tag.ancestor_path" :title="tag.ancestor_path">
                      <span style="opacity: 0.5; font-size: 11px">
                        {{ tag.ancestor_path.split(' > ').slice(0, -1).join(' > ') }}
                        <template
                          v-if="tag.ancestor_path.split(' > ').slice(0, -1).length > 0"
                        >
                          &gt;
                        </template>
                      </span>
                      {{ tag.tag_name }}
                    </a-tooltip>
                    <template v-else>{{ tag.tag_name }}</template>
                    <span style="opacity: 0.6; font-size: 11px">
                      {{ (tag.confidence * 100).toFixed(0) }}%
                    </span>
                  </a-tag>
                </div>
              </template>
              <span style="cursor: pointer">
                <a-tag v-for="(tag, i) in record.tags.slice(0, 3)" :key="i" size="small">
                  {{ tag.tag_name }}
                </a-tag>
                <a-tag v-if="record.tags.length > 3" size="small" color="default">
                  +{{ record.tags.length - 3 }}
                </a-tag>
              </span>
            </a-popover>
          </template>
          <span v-else class="text-muted">-</span>
        </template>
        <template v-else-if="column.key === 'action'">
          <a-space>
            <a-button
              v-if="record.status === 'review'"
              type="link"
              size="small"
              @click="openReview(record)"
            >
              审核
            </a-button>
            <a-button
              v-if="record.status === 'review'"
              type="link"
              size="small"
              @click="handleApprove(record.task_id)"
            >
              通过
            </a-button>
            <a-button
              v-if="record.status === 'review'"
              type="link"
              size="small"
              danger
              @click="handleReject(record.task_id)"
            >
              拒绝
            </a-button>
            <a-button
              v-if="['error_preprocessing', 'error_tagging', 'rejected'].includes(record.status)"
              type="link"
              size="small"
              @click="handleRetry(record.task_id)"
            >
              重试
            </a-button>
            <a-button
              v-if="record.status === 'approved'"
              type="link"
              size="small"
              @click="openReview(record)"
            >
              查看
            </a-button>
            <a-button type="link" size="small" danger @click="handleDeleteOne(record.task_id)">
              <template #icon><DeleteOutlined /></template>
            </a-button>
          </a-space>
        </template>
      </template>
    </a-table>

    <!-- 批量打标弹窗 -->
    <a-modal
      v-model:open="batchModalVisible"
      title="新建打标任务"
      @ok="handleBatchTag"
      :confirmLoading="batchLoading"
    >
      <a-form layout="vertical">
        <a-form-item label="选择知识库">
          <a-select
            v-model:value="batchForm.db_id"
            placeholder="选择知识库"
            :options="databaseStore.databases.map((d) => ({ label: d.name, value: d.kb_id }))"
          />
        </a-form-item>
        <a-form-item label="选择文件">
          <div style="display: flex; justify-content: flex-end; margin-bottom: 4px">
            <a-button
              type="link"
              size="small"
              @click="batchForm.file_ids = batchFileList.map((f) => f.value)"
              :disabled="batchFileList.length === 0"
            >
              全选
            </a-button>
          </div>
          <a-select
            v-model:value="batchForm.file_ids"
            mode="multiple"
            placeholder="选择要打标的文件"
            :options="batchFileList"
            :maxTagCount="5"
          />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 审核抽屉 -->
    <TagReviewDrawer
      :visible="reviewDrawerVisible"
      :task="reviewTask"
      @close="reviewDrawerVisible = false"
      @done="handleReviewDone"
    />
  </div>
</template>

<style scoped lang="less">
.task-list {
  .task-toolbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;
  }

  .filters {
    display: flex;
    gap: 8px;
  }

  .batch-action-bar {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 8px 12px;
    margin-bottom: 12px;
    background: var(--gray-50);
    border-radius: 6px;
  }

  .text-muted {
    color: var(--gray-400);
  }
}
</style>
