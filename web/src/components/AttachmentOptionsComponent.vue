<template>
  <div class="attachment-options">
    <div class="option-item" :class="{ disabled: disabled }" @click="handleAttachmentClick">
      <a-tooltip title="支持任意文件格式 ≤ 5 MB" placement="right">
        <div class="option-content">
          <FileText :size="14" class="option-icon" />
          <span class="option-text">添加附件</span>
        </div>
      </a-tooltip>
    </div>

    <div class="option-item" @click="handleImageUpload">
      <a-tooltip title="支持 jpg/jpeg/png/gif， ≤ 5 MB" placement="right">
        <div class="option-content">
          <Image :size="14" class="option-icon" />
          <span class="option-text">上传图片</span>
        </div>
      </a-tooltip>
    </div>
  </div>
</template>

<script setup>
import { FileText, Image } from 'lucide-vue-next'
import { message } from 'ant-design-vue'
import { uploadMultimodalImage } from '@/utils/multimodal_image_upload'

const props = defineProps({
  disabled: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['upload', 'upload-image', 'upload-image-success'])

const handleAttachmentClick = () => {
  if (props.disabled) return
  emit('upload')
}

// 处理图片上传
const handleImageUpload = () => {
  if (props.disabled) return

  // 创建隐藏的文件输入
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = 'image/*'
  input.multiple = false
  input.style.display = 'none'

  input.onchange = async (event) => {
    const file = event.target.files[0]
    if (file) {
      await processImageUpload(file)
    }
    document.body.removeChild(input)
  }

  document.body.appendChild(input)
  input.click()

  emit('upload-image')
}

// 处理图片上传逻辑
const processImageUpload = async (file) => {
  try {
    const imageData = await uploadMultimodalImage(file)
    if (!imageData) return

    // 发出上传成功事件，包含处理后的图片数据
    emit('upload-image', imageData)

    // 发出上传成功通知事件，用于关闭选项面板
    emit('upload-image-success')
  } catch (error) {
    console.error('图片上传失败:', error)
    message.error({
      content: `图片上传失败: ${error.message || '未知错误'}`,
      key: 'image-upload'
    })
  }
}
</script>

<style lang="less" scoped>
.attachment-options {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 120px;
}

.option-item {
  cursor: pointer;
  transition: all 0.2s ease;

  &.disabled {
    cursor: not-allowed;
    opacity: 0.5;

    .option-content {
      color: var(--gray-400);
    }
  }
}

.option-content {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 10px;
  color: var(--gray-700);
  font-size: 12px;
  border-radius: 6px;
  transition: all 0.15s ease;

  .option-item:hover & {
    color: var(--main-color);
    background-color: var(--gray-50);
  }
}

.option-icon {
  flex-shrink: 0;
  color: inherit;
}

.option-text {
  font-weight: 500;
}
</style>
