<script setup>
import { ref, onMounted, computed } from 'vue'
import { message } from 'ant-design-vue'
import { taggingApi } from '@/apis/tagging_api'
import { Plus, Pencil, Trash2, GripVertical, Download, Upload } from 'lucide-vue-next'

const treeData = ref([])
const loading = ref(false)
const searchQuery = ref('')
const expandedKeys = ref([])
// 编辑状态
const editModalVisible = ref(false)
const editForm = ref({ id: '', name_zh: '', name_en: '', parent_id: null })
const editMode = ref('add') // 'add' | 'edit'

// 同义词编辑
const synonymsModalVisible = ref(false)
const synonymsForm = ref({ id: '', synonyms: [] })
const synonymInput = ref('')

const loadTree = async () => {
  loading.value = true
  try {
    const nodes = await taggingApi.getTaxonomyTree()
    treeData.value = buildTreeData(nodes)
  } catch (e) {
    message.error('加载标签体系失败')
  } finally {
    loading.value = false
  }
}

const buildTreeData = (flatNodes) => {
  const map = new Map()
  const roots = []

  flatNodes.forEach((n) => {
    map.set(n.id, {
      key: n.id,
      title: n.name_zh,
      nameEn: n.name_en,
      level: n.level,
      parentId: n.parent_id,
      path: n.path,
      source: n.source,
      dimension: n.dimension || 'topic',
      archived: n.archived || false,
      synonyms: n.synonyms || [],
      children: []
    })
  })

  map.forEach((node) => {
    if (node.parentId && map.has(node.parentId)) {
      map.get(node.parentId).children.push(node)
    } else if (!node.parentId) {
      roots.push(node)
    }
  })

  return roots
}

// 过滤树（按搜索词）
const filteredTree = computed(() => {
  const roots = treeData.value
  if (!searchQuery.value) return roots
  const q = searchQuery.value.toLowerCase()
  const filterNodes = (nodes) => {
    return nodes
      .map((n) => {
        const children = filterNodes(n.children)
        const match =
          n.title.toLowerCase().includes(q) || (n.nameEn || '').toLowerCase().includes(q)
        if (match || children.length > 0) {
          return { ...n, children }
        }
        return null
      })
      .filter(Boolean)
  }
  return filterNodes(roots)
})

const openAddNode = (parentId = null) => {
  editMode.value = 'add'
  editForm.value = { id: '', name_zh: '', name_en: '', parent_id: parentId }
  editModalVisible.value = true
}

const openEditNode = (node) => {
  editMode.value = 'edit'
  editForm.value = { id: node.key, name_zh: node.title, name_en: node.nameEn || '' }
  editModalVisible.value = true
}

const handleEditSubmit = async () => {
  try {
    if (editMode.value === 'add') {
      await taggingApi.addTaxonomyNode({
        name_zh: editForm.value.name_zh,
        name_en: editForm.value.name_en,
        parent_id: editForm.value.parent_id
      })
      message.success('添加成功')
    } else {
      await taggingApi.updateTaxonomyNode(editForm.value.id, {
        name_zh: editForm.value.name_zh,
        name_en: editForm.value.name_en
      })
      message.success('修改成功')
    }
    editModalVisible.value = false
    loadTree()
  } catch (e) {
    message.error('操作失败')
  }
}

const handleDelete = async (nodeId) => {
  try {
    const res = await taggingApi.deleteTaxonomyNode(nodeId)
    message.success(`已删除 ${res?.deleted || 1} 个节点`)
    loadTree()
  } catch (e) {
    message.error('删除失败')
  }
}

const openSynonyms = (node) => {
  synonymsForm.value = { id: node.key, synonyms: [...(node.synonyms || [])] }
  synonymsModalVisible.value = true
}

const addSynonym = () => {
  const val = synonymInput.value.trim()
  if (val && !synonymsForm.value.synonyms.includes(val)) {
    synonymsForm.value.synonyms.push(val)
  }
  synonymInput.value = ''
}

const removeSynonym = (index) => {
  synonymsForm.value.synonyms.splice(index, 1)
}

const handleSynonymsSave = async () => {
  try {
    await taggingApi.updateSynonyms(synonymsForm.value.id, synonymsForm.value.synonyms)
    message.success('同义词已更新')
    synonymsModalVisible.value = false
    loadTree()
  } catch (e) {
    message.error('保存失败')
  }
}

const handleExport = async () => {
  try {
    const data = await taggingApi.exportTaxonomy()
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'taxonomy_export.json'
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    message.error('导出失败')
  }
}

const handleImport = async (file) => {
  try {
    const text = await file.text()
    const data = JSON.parse(text)
    await taggingApi.importTaxonomy(data, true)
    message.success('导入成功')
    loadTree()
  } catch (e) {
    message.error('导入失败')
  }
  return false
}

onMounted(() => {
  loadTree()
})
</script>

<template>
  <div class="taxonomy-tree">
    <div class="tree-toolbar">
      <a-input-search
        v-model:value="searchQuery"
        placeholder="搜索标签..."
        style="width: 240px"
        allowClear
      />
      <a-space>
        <a-button @click="openAddNode(null)">
          <template #icon><Plus :size="14" /></template>
          添加一级标签
        </a-button>
        <a-button @click="handleExport">
          <template #icon><Download :size="14" /></template>
          导出
        </a-button>
        <a-upload :beforeUpload="handleImport" :showUploadList="false" accept=".json">
          <a-button>
            <template #icon><Upload :size="14" /></template>
            导入
          </a-button>
        </a-upload>
      </a-space>
    </div>

    <div class="dim-legend">
      <span class="dim-legend-item"><span class="dim-dot dim-topic" /> 主题</span>
      <span class="dim-legend-item"><span class="dim-dot dim-tone" /> 基调</span>
      <span class="dim-legend-item"><span class="dim-dot dim-type" /> 类型</span>
      <span class="dim-legend-item"><span class="dim-dot dim-audience" /> 受众</span>
    </div>

    <a-spin :spinning="loading">
      <a-tree
        :treeData="filteredTree"
        :expandedKeys="expandedKeys"
        @expand="(keys) => (expandedKeys = keys)"
        blockNode
        showLine
      >
        <template #title="{ key, title, nameEn, level, source, dimension, archived, synonyms }">
          <div class="tree-node" :class="{ archived }">
            <span class="node-title">
              <span v-if="level <= 2 && dimension" class="dim-dot" :class="'dim-' + dimension" />
              {{ title }}
              <span v-if="nameEn" class="node-en">{{ nameEn }}</span>
              <a-tag v-if="source !== 'IPTC'" size="small" color="purple" style="margin-left: 4px">
                {{ source }}
              </a-tag>
              <a-tag v-if="archived" size="small" color="default">已归档</a-tag>
              <a-tag v-for="s in (synonyms || []).slice(0, 2)" :key="s" size="small">
                {{ s }}
              </a-tag>
            </span>
            <span class="node-actions">
              <a-button type="text" size="small" @click.stop="openAddNode(key)" title="添加子标签">
                <template #icon><Plus :size="12" /></template>
              </a-button>
              <a-button
                type="text"
                size="small"
                @click.stop="openEditNode({ key, title, nameEn })"
                title="编辑"
              >
                <template #icon><Pencil :size="12" /></template>
              </a-button>
              <a-button
                type="text"
                size="small"
                @click.stop="openSynonyms({ key, synonyms })"
                title="同义词"
              >
                同
              </a-button>
              <a-popconfirm title="确认删除此标签及其子节点？" @confirm="handleDelete(key)">
                <a-button type="text" size="small" danger @click.stop title="删除">
                  <template #icon><Trash2 :size="12" /></template>
                </a-button>
              </a-popconfirm>
            </span>
          </div>
        </template>
      </a-tree>
    </a-spin>

    <!-- 编辑/添加弹窗 -->
    <a-modal
      v-model:open="editModalVisible"
      :title="editMode === 'add' ? '添加标签' : '编辑标签'"
      @ok="handleEditSubmit"
    >
      <a-form layout="vertical">
        <a-form-item label="中文名称" required>
          <a-input v-model:value="editForm.name_zh" placeholder="标签中文名" />
        </a-form-item>
        <a-form-item label="英文名称">
          <a-input v-model:value="editForm.name_en" placeholder="标签英文名（可选）" />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 同义词弹窗 -->
    <a-modal v-model:open="synonymsModalVisible" title="管理同义词" @ok="handleSynonymsSave">
      <div class="synonyms-list">
        <a-tag v-for="(s, i) in synonymsForm.synonyms" :key="i" closable @close="removeSynonym(i)">
          {{ s }}
        </a-tag>
      </div>
      <a-input-search
        v-model:value="synonymInput"
        placeholder="输入同义词后回车"
        enterButton="添加"
        @search="addSynonym"
        style="margin-top: 12px"
      />
    </a-modal>
  </div>
</template>

<style scoped lang="less">
.taxonomy-tree {
  .tree-toolbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
  }

  .dim-legend {
    display: flex;
    gap: 16px;
    margin-bottom: 12px;
    font-size: 12px;
    color: var(--gray-500);

    .dim-legend-item {
      display: flex;
      align-items: center;
      gap: 4px;
    }
  }

  .tree-node {
    display: flex;
    justify-content: space-between;
    align-items: center;
    width: 100%;

    &.archived {
      opacity: 0.5;
    }

    .node-title {
      display: flex;
      align-items: center;
      gap: 4px;

      .dim-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        flex-shrink: 0;

        &.dim-topic {
          background: #1677ff;
        }
        &.dim-tone {
          background: #faad14;
        }
        &.dim-type {
          background: #52c41a;
        }
        &.dim-audience {
          background: #eb2f96;
        }
      }

      .node-en {
        font-size: 12px;
        color: var(--gray-400);
        margin-left: 4px;
      }
    }

    .node-actions {
      display: none;
    }

    &:hover .node-actions {
      display: flex;
      gap: 2px;
    }
  }

  .synonyms-list {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    min-height: 32px;
  }
}
</style>
