<template>
  <a-modal
    :open="open"
    title="添加附件"
    ok-text="添加附件"
    cancel-text="取消"
    :confirm-loading="confirming"
    :ok-button-props="{ disabled: confirmDisabled }"
    @ok="handleConfirm"
    @cancel="handleCancel"
  >
    <a-upload-dragger
      :multiple="true"
      :show-upload-list="false"
      :before-upload="handleBeforeUpload"
      :disabled="confirming"
      class="attachment-dropzone"
    >
      <p class="dropzone-title">点击或拖拽文件到此处上传</p>
      <p class="dropzone-desc">支持任意文件格式 ≤ 5 MB；PDF 和图片可选解析为 Markdown。</p>
    </a-upload-dragger>

    <div v-if="fileItems.length" class="attachment-list">
      <div v-for="item in fileItems" :key="item.localId" class="attachment-item">
        <div class="attachment-file-icon">
          <FileTypeIcon :name="item.fileName" :size="20" />
        </div>

        <div class="attachment-item-content">
          <div class="attachment-name-row">
            <span class="attachment-name" :title="item.fileName">{{ item.fileName }}</span>
            <a-button
              size="small"
              type="text"
              class="lucide-icon-btn remove-btn"
              :disabled="confirming"
              @click="removeItem(item.localId)"
            >
              <X :size="16" />
            </a-button>
          </div>

          <div class="attachment-status-row">
            <div class="attachment-status-meta">
              <a-tag
                :color="getStatusColor(item.status)"
                :bordered="false"
                class="attachment-status-tag"
              >
                {{ getStatusLabel(item.status) }}
              </a-tag>
              <span>{{ formatFileSize(item.fileSize) }}</span>
              <span v-if="item.error" class="attachment-error">{{ item.error }}</span>
              <span v-else-if="item.parseError" class="attachment-error">{{
                item.parseError
              }}</span>
            </div>

            <a-popover
              v-if="item.parseSupported && item.status !== 'uploading' && item.status !== 'error'"
              v-model:open="item.parsePanelOpen"
              placement="bottomRight"
              trigger="click"
              overlayClassName="attachment-parse-popover"
              @openChange="(open) => handleParsePanelOpenChange(item.localId, open)"
            >
              <template #content>
                <div class="parse-panel">
                  <button
                    v-for="method in getAvailableParseMethods(item)"
                    :key="method"
                    type="button"
                    class="parse-method-option"
                    :class="{ selected: item.selectedParseMethod === method }"
                    :disabled="item.status === 'parsing' || confirming"
                    @click="handleParseMethodChange(item.localId, method)"
                  >
                    <span class="parse-method-option-header">
                      <span class="parse-method-name">{{ methodLabels[method] || method }}</span>
                      <span
                        class="parse-method-status"
                        :class="`status-${getMethodStatus(method)}`"
                      >
                        {{ getMethodStatusLabel(method) }}
                      </span>
                    </span>
                    <span class="parse-method-desc">{{ getMethodDescription(method) }}</span>
                  </button>

                  <div v-if="getUnavailableParseMethods(item).length" class="unavailable-methods">
                    <button
                      type="button"
                      class="unavailable-toggle"
                      @click="toggleUnavailableParseMethods(item.localId)"
                    >
                      <span>不可用选项（{{ getUnavailableParseMethods(item).length }}）</span>
                      <ChevronUp v-if="item.unavailableMethodsExpanded" :size="14" />
                      <ChevronDown v-else :size="14" />
                    </button>

                    <div v-if="item.unavailableMethodsExpanded" class="unavailable-method-list">
                      <button
                        v-for="method in getUnavailableParseMethods(item)"
                        :key="method"
                        type="button"
                        class="parse-method-option disabled"
                        disabled
                      >
                        <span class="parse-method-option-header">
                          <span class="parse-method-name">{{
                            methodLabels[method] || method
                          }}</span>
                          <span
                            class="parse-method-status"
                            :class="`status-${getMethodStatus(method)}`"
                          >
                            {{ getMethodStatusLabel(method) }}
                          </span>
                        </span>
                        <span class="parse-method-desc">{{ getMethodDescription(method) }}</span>
                      </button>
                    </div>
                  </div>

                  <a-button
                    type="primary"
                    size="small"
                    block
                    class="parse-start-btn"
                    :loading="item.status === 'parsing'"
                    :disabled="isParseDisabled(item)"
                    @click="handleStartParse(item.localId)"
                  >
                    开始解析
                  </a-button>
                </div>
              </template>

              <a-button
                size="small"
                class="parse-trigger-btn"
                :loading="item.status === 'parsing'"
                :disabled="confirming"
              >
                可解析
              </a-button>
            </a-popover>
          </div>
        </div>
      </div>
    </div>
  </a-modal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { ChevronDown, ChevronUp, X } from 'lucide-vue-next'
import { threadApi } from '@/apis'
import { ocrApi } from '@/apis/system_api'
import { useConfigStore } from '@/stores/config'
import FileTypeIcon from '@/components/common/FileTypeIcon.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  threadId: { type: String, default: '' },
  ensureThread: { type: Function, default: null },
  initialFiles: { type: Array, default: () => [] },
  initialFilesKey: { type: Number, default: 0 }
})

const emit = defineEmits(['update:open', 'added'])

const configStore = useConfigStore()
const DEFAULT_OCR_ENGINE = 'rapid_ocr'
const fileItems = ref([])
const confirming = ref(false)
let localIdSeed = 0
let consumedInitialFilesKey = 0

const methodLabels = {
  disable: 'PDF 文本提取',
  rapid_ocr: 'RapidOCR',
  qwen_ocr: 'Qwen-OCR (百炼)',
  mineru_ocr: 'MinerU OCR',
  mineru_official: 'MinerU Official',
  pp_structure_v3_ocr: 'PP-Structure V3',
  deepseek_ocr: 'DeepSeek OCR',
  paddleocr_vl_1_6: 'PaddleOCR-VL-1.6',
  paddleocr_pp_ocrv6: 'PP-OCRv6'
}

const ocrMethodKeys = [
  'rapid_ocr',
  'mineru_ocr',
  'mineru_official',
  'pp_structure_v3_ocr',
  'deepseek_ocr',
  'paddleocr_vl_1_6',
  'paddleocr_pp_ocrv6'
]

const defaultOcrHealthStatus = () =>
  Object.fromEntries(ocrMethodKeys.map((method) => [method, { status: 'unknown', message: '' }]))

const ocrHealthStatus = ref(defaultOcrHealthStatus())
const ocrHealthChecking = ref(false)

const methodStatusLabels = {
  local: '无需 OCR',
  healthy: '可用',
  configured: '已配置',
  unavailable: '不可用',
  unhealthy: '异常',
  timeout: '超时',
  error: '异常',
  checking: '检查中',
  unknown: '状态未知'
}

const busy = computed(() =>
  fileItems.value.some((item) => ['uploading', 'parsing'].includes(item.status))
)
const confirmableItems = computed(() =>
  fileItems.value.filter((item) => ['uploaded', 'parsed'].includes(item.status))
)
const confirmDisabled = computed(() => busy.value || confirmableItems.value.length === 0)

watch(
  () => props.open,
  (open) => {
    if (!open) {
      fileItems.value = []
      confirming.value = false
    }
  }
)

const getErrorMessage = (error, fallback = '操作失败') => {
  return error?.response?.data?.detail || error?.message || fallback
}

const getDefaultParseMethod = (parseMethods) => {
  if (!Array.isArray(parseMethods) || parseMethods.length === 0) {
    return null
  }
  const configuredEngine = String(
    configStore.config?.default_ocr_engine || DEFAULT_OCR_ENGINE
  ).trim()
  if (parseMethods.includes(configuredEngine)) {
    return configuredEngine
  }
  if (parseMethods.includes(DEFAULT_OCR_ENGINE)) {
    return DEFAULT_OCR_ENGINE
  }
  return parseMethods[0]
}

const normalizeTmpUpload = (response) => ({
  tmpFileId: response.tmp_file_id,
  fileName: response.file_name,
  fileType: response.file_type,
  fileSize: response.file_size,
  bucketName: response.bucket_name,
  objectName: response.object_name,
  minioUrl: response.minio_url,
  parseSupported: response.parse_supported,
  parseMethods: response.parse_methods || [],
  selectedParseMethod: getDefaultParseMethod(response.parse_methods || []),
  parseMethodTouched: false
})

watch(
  () => configStore.config?.default_ocr_engine,
  () => {
    fileItems.value = fileItems.value.map((item) => {
      if (!item.parseSupported || item.parseMethodTouched || item.status === 'parsed') {
        return item
      }
      return { ...item, selectedParseMethod: getDefaultParseMethod(item.parseMethods || []) }
    })
  }
)

const updateItem = (localId, patch) => {
  fileItems.value = fileItems.value.map((item) =>
    item.localId === localId ? { ...item, ...patch } : item
  )
}

const checkOcrHealth = async () => {
  if (ocrHealthChecking.value) return

  ocrHealthChecking.value = true
  try {
    const healthData = await ocrApi.getHealth()
    ocrHealthStatus.value = {
      ...defaultOcrHealthStatus(),
      ...(healthData?.services || {})
    }
  } catch (error) {
    console.error('OCR健康检查失败:', error)
  } finally {
    ocrHealthChecking.value = false
  }
}

const uploadFile = async (file) => {
  const localId = `${Date.now()}-${localIdSeed++}`
  const item = {
    localId,
    fileName: file.name,
    fileSize: file.size,
    status: 'uploading',
    error: null,
    parseError: null,
    parseSupported: false,
    parseMethods: []
  }
  fileItems.value.push(item)

  try {
    const response = await threadApi.uploadTmpAttachment(file)
    const normalized = normalizeTmpUpload(response)
    updateItem(localId, { ...normalized, status: 'uploaded' })
    if (normalized.parseSupported) {
      void checkOcrHealth()
    }
  } catch (error) {
    updateItem(localId, {
      status: 'error',
      error: getErrorMessage(error, '上传失败')
    })
  }
}

const handleBeforeUpload = (file) => {
  void uploadFile(file)
  return false
}

const uploadInitialFiles = () => {
  if (!props.open || !props.initialFilesKey) return
  if (props.initialFilesKey === consumedInitialFilesKey) return

  consumedInitialFilesKey = props.initialFilesKey
  Array.from(props.initialFiles || [])
    .filter((file) => file instanceof File)
    .forEach((file) => {
      void uploadFile(file)
    })
}

watch(
  () => [props.open, props.initialFilesKey],
  () => {
    uploadInitialFiles()
  },
  { flush: 'post' }
)

const getMethodStatus = (method) => {
  if (method === 'disable') return 'local'
  const current = ocrHealthStatus.value?.[method]
  if (ocrHealthChecking.value && (!current || current.status === 'unknown')) return 'checking'
  return current?.status || 'unknown'
}

const getMethodStatusLabel = (method) => methodStatusLabels[getMethodStatus(method)] || '状态未知'

const getMethodDescription = (method) => {
  if (method === 'disable') return '使用文件内置文本层，不调用 OCR 服务'

  const messageText = ocrHealthStatus.value?.[method]?.message
  if (messageText) return messageText

  const status = getMethodStatus(method)
  const fallbackMap = {
    healthy: '服务正常',
    configured: 'Token 已配置，将在解析时验证',
    unavailable: '服务不可用',
    unhealthy: '服务异常',
    timeout: '服务检查超时',
    error: '服务异常',
    checking: '正在检查服务状态',
    unknown: '服务状态未知'
  }
  return fallbackMap[status] || '服务状态未知'
}

const isUnavailableParseMethod = (method) =>
  ['unavailable', 'error'].includes(getMethodStatus(method))

const getAvailableParseMethods = (item) =>
  (item.parseMethods || []).filter((method) => !isUnavailableParseMethod(method))

const getUnavailableParseMethods = (item) =>
  (item.parseMethods || []).filter((method) => isUnavailableParseMethod(method))

const isParseDisabled = (item) =>
  item.status === 'parsing' ||
  !item.selectedParseMethod ||
  confirming.value ||
  isUnavailableParseMethod(item.selectedParseMethod)

const clearParsedState = {
  parsedObjectName: null,
  parsedMinioUrl: null,
  truncated: false,
  parseMethod: null
}

const handleParseMethodChange = (localId, selectedParseMethod) => {
  const item = fileItems.value.find((entry) => entry.localId === localId)
  updateItem(localId, {
    ...clearParsedState,
    selectedParseMethod,
    parseMethodTouched: true,
    parseError: null,
    status: item?.status === 'parsed' ? 'uploaded' : item?.status
  })
}

const handleStartParse = (localId) => {
  const item = fileItems.value.find((entry) => entry.localId === localId)
  if (!item || isParseDisabled(item)) return
  updateItem(localId, { parsePanelOpen: false })
  void handleParse(item)
}

const handleParse = async (item) => {
  if (!item.objectName || !item.selectedParseMethod) return

  updateItem(item.localId, {
    ...clearParsedState,
    status: 'parsing',
    parseError: null
  })
  try {
    const response = await threadApi.parseTmpAttachment({
      object_name: item.objectName,
      file_name: item.fileName,
      bucket_name: item.bucketName,
      parse_method: item.selectedParseMethod
    })
    updateItem(item.localId, {
      status: 'parsed',
      parsedObjectName: response.parsed_object_name,
      parsedMinioUrl: response.parsed_minio_url,
      truncated: response.truncated,
      parseMethod: response.parse_method
    })
    message.success('附件解析完成')
  } catch (error) {
    updateItem(item.localId, {
      ...clearParsedState,
      status: 'uploaded',
      parseError: getErrorMessage(error, '解析失败')
    })
  }
}

const removeItem = (localId) => {
  fileItems.value = fileItems.value.filter((item) => item.localId !== localId)
}

const toggleUnavailableParseMethods = (localId) => {
  const item = fileItems.value.find((entry) => entry.localId === localId)
  updateItem(localId, { unavailableMethodsExpanded: !item?.unavailableMethodsExpanded })
}

const handleParsePanelOpenChange = (localId, open) => {
  updateItem(localId, { parsePanelOpen: open })
  if (open) {
    void checkOcrHealth()
  }
}

const handleConfirm = async () => {
  if (confirmDisabled.value) return

  const attachments = confirmableItems.value.map((item) => ({
    file_name: item.fileName,
    file_type: item.fileType,
    bucket_name: item.bucketName,
    object_name: item.objectName,
    parsed_object_name: item.parsedObjectName || null,
    truncated: Boolean(item.truncated)
  }))

  confirming.value = true
  try {
    const threadId = props.threadId || (props.ensureThread ? await props.ensureThread() : '')
    if (!threadId) {
      message.error('创建对话失败，无法添加附件')
      return
    }

    const response = await threadApi.confirmTmpThreadAttachments(threadId, attachments)
    message.success('附件已添加')
    emit('added', response)
    emit('update:open', false)
  } catch (error) {
    message.error(getErrorMessage(error, '添加附件失败'))
  } finally {
    confirming.value = false
  }
}

const handleCancel = () => {
  emit('update:open', false)
}

const getStatusColor = (status) => {
  const colorMap = {
    uploading: 'processing',
    uploaded: 'blue',
    parsing: 'processing',
    parsed: 'green',
    error: 'red'
  }
  return colorMap[status] || 'default'
}

const getStatusLabel = (status) => {
  const labelMap = {
    uploading: '上传中',
    uploaded: '已上传',
    parsing: '解析中',
    parsed: '已解析',
    error: '失败'
  }
  return labelMap[status] || status
}

const formatFileSize = (size) => {
  if (!Number.isFinite(size)) return '未知大小'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}
</script>

<style lang="less" scoped>
.attachment-dropzone {
  margin-bottom: 0;
}

.dropzone-title {
  margin: 0 0 4px;
  color: var(--gray-800);
  font-size: 14px;
  font-weight: 600;
}

.dropzone-desc {
  margin: 0;
  color: var(--gray-500);
  font-size: 12px;
}

.attachment-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 360px;
  margin-top: 16px;
  overflow: auto;
}

.attachment-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--gray-100);
  border-radius: 10px;
  background: var(--gray-50);
}

.attachment-file-icon {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: var(--gray-0);
  font-size: 18px;
}

.attachment-item-content {
  min-width: 0;
  flex: 1;
}

.attachment-name-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.attachment-name {
  flex: 1;
  overflow: hidden;
  color: var(--gray-900);
  font-size: 13px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.remove-btn {
  color: var(--gray-500);
}

.remove-btn:hover {
  color: var(--color-error-500);
  background: var(--color-error-50);
}

.attachment-status-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: var(--gray-500);
  font-size: 11px;
}

.attachment-status-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.attachment-status-tag {
  min-height: 18px;
  margin-inline-end: 0;
  padding: 0 5px;
  border: none;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  line-height: 18px;
}

.attachment-error {
  color: var(--color-error-700);
}

.parse-trigger-btn {
  flex: none;
  margin-left: auto;
  font-size: 12px;
}

.parse-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 260px;
}

.parse-method-option {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--gray-100);
  border-radius: 8px;
  background: var(--gray-0);
  color: inherit;
  cursor: pointer;
  text-align: left;
}

.parse-method-option:hover:not(:disabled) {
  border-color: var(--main-color);
  background: color-mix(in srgb, var(--main-color) 6%, var(--gray-0));
}

.parse-method-option.selected {
  border-color: var(--main-color);
  background: color-mix(in srgb, var(--main-color) 8%, var(--gray-0));
}

.parse-method-option.disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.unavailable-methods,
.unavailable-method-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.unavailable-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 4px 2px;
  border: none;
  background: transparent;
  color: var(--gray-500);
  cursor: pointer;
  font-size: 12px;
}

.unavailable-toggle:hover {
  color: var(--gray-800);
}

.parse-method-option-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.parse-method-name {
  color: var(--gray-900);
  font-size: 13px;
  font-weight: 500;
}

.parse-method-status {
  flex: none;
  font-size: 12px;
}

.parse-method-status.status-local,
.parse-method-status.status-healthy,
.parse-method-status.status-configured {
  color: var(--color-success-700);
}

.parse-method-status.status-unavailable,
.parse-method-status.status-error {
  color: var(--color-error-700);
}

.parse-method-status.status-unhealthy,
.parse-method-status.status-timeout,
.parse-method-status.status-unknown,
.parse-method-status.status-checking {
  color: var(--color-warning-700);
}

.parse-method-desc {
  color: var(--gray-500);
  font-size: 12px;
  line-height: 1.4;
}

.parse-start-btn {
  margin-top: 2px;
}

:global(.attachment-parse-popover .ant-popover-inner-content) {
  padding: 10px;
}
</style>
