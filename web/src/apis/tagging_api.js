import { apiAdminGet, apiAdminPost, apiAdminPut, apiAdminDelete } from './base'

/**
 * 标签服务API模块
 */

export const taggingApi = {
  // ============ 标签体系 ============

  getTaxonomy: async () => {
    return apiAdminGet('/api/tagging/taxonomy')
  },

  getTaxonomyTree: async () => {
    return apiAdminGet('/api/tagging/taxonomy/tree')
  },

  addTaxonomyNode: async (data) => {
    return apiAdminPost('/api/tagging/taxonomy/nodes', data)
  },

  updateTaxonomyNode: async (nodeId, data) => {
    return apiAdminPut(`/api/tagging/taxonomy/nodes/${nodeId}`, data)
  },

  deleteTaxonomyNode: async (nodeId) => {
    return apiAdminDelete(`/api/tagging/taxonomy/nodes/${nodeId}`)
  },

  moveTaxonomyNode: async (nodeId, newParentId) => {
    return apiAdminPut(`/api/tagging/taxonomy/nodes/${nodeId}/move`, {
      new_parent_id: newParentId
    })
  },

  updateSynonyms: async (nodeId, synonyms) => {
    return apiAdminPut(`/api/tagging/taxonomy/nodes/${nodeId}/synonyms`, { synonyms })
  },

  searchTaxonomy: async (query, includeArchived = false) => {
    return apiAdminPost('/api/tagging/taxonomy/search', {
      query,
      include_archived: includeArchived
    })
  },

  importTaxonomy: async (data, merge = true) => {
    return apiAdminPost('/api/tagging/taxonomy/import', { ...data, merge })
  },

  exportTaxonomy: async () => {
    return apiAdminGet('/api/tagging/taxonomy/export')
  },

  // ============ 打标操作 ============

  autoTag: async (content, modelSpec = null, maxTags = 5) => {
    return apiAdminPost('/api/tagging/auto-tag', {
      content,
      model_spec: modelSpec,
      max_tags: maxTags
    })
  },

  autoTagFile: async (fileId, dbId, modelSpec = null) => {
    return apiAdminPost(`/api/tagging/auto-tag-file/${fileId}`, {
      db_id: dbId,
      model_spec: modelSpec
    })
  },

  batchTag: async (dbId, fileIds) => {
    return apiAdminPost('/api/tagging/batch-tag', {
      file_ids: fileIds,
      db_id: dbId
    })
  },

  // ============ 任务管理 ============

  getTasks: async (params = {}) => {
    const query = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => {
      if (v !== null && v !== undefined) query.set(k, v)
    })
    const qs = query.toString()
    return apiAdminGet(`/api/tagging/tasks${qs ? '?' + qs : ''}`)
  },

  approveTask: async (taskId) => {
    return apiAdminPost(`/api/tagging/tasks/${taskId}/approve`)
  },

  rejectTask: async (taskId) => {
    return apiAdminPost(`/api/tagging/tasks/${taskId}/reject`)
  },

  updateTaskTags: async (taskId, tags) => {
    return apiAdminPut(`/api/tagging/tasks/${taskId}/tags`, { tags })
  },

  retryTask: async (taskId) => {
    return apiAdminPost(`/api/tagging/tasks/${taskId}/retry`)
  },

  batchRetryTasks: async (taskIds) => {
    return apiAdminPost('/api/tagging/tasks/batch-retry', { task_ids: taskIds })
  },

  batchApproveTasks: async (taskIds) => {
    return apiAdminPost('/api/tagging/tasks/batch-approve', { task_ids: taskIds })
  },

  batchDeleteTasks: async (taskIds) => {
    return apiAdminPost('/api/tagging/tasks/batch-delete', { task_ids: taskIds })
  },

  deleteTask: async (taskId) => {
    return apiAdminDelete(`/api/tagging/tasks/${taskId}`)
  },

  // ============ 配置 ============

  getPromptConfig: async () => {
    return apiAdminGet('/api/tagging/prompt-config')
  },

  updatePromptConfig: async (config) => {
    return apiAdminPut('/api/tagging/prompt-config', config)
  },

  testPrompt: async (content) => {
    return apiAdminPost('/api/tagging/test-prompt', { content })
  },

  // ============ 统计 ============

  getStats: async () => {
    return apiAdminGet('/api/tagging/stats')
  },

  getConcurrency: async () => {
    return apiAdminGet('/api/tagging/stats/concurrency')
  },

  // ============ 上传打标 ============

  uploadAndTag: async (file, dbId = null) => {
    const formData = new FormData()
    formData.append('file', file)
    const qs = dbId ? `?db_id=${encodeURIComponent(dbId)}` : ''
    return apiAdminPost(`/api/tagging/upload-and-tag${qs}`, formData)
  }
}
