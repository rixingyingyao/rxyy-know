<script setup>
import { ref, onMounted } from 'vue'
import { message } from 'ant-design-vue'
import { taggingApi } from '@/apis/tagging_api'

const emit = defineEmits(['saved'])

const config = ref(null)
const loading = ref(false)
const saving = ref(false)
const testContent = ref('')
const testResult = ref(null)
const testing = ref(false)

const loadConfig = async () => {
  loading.value = true
  try {
    config.value = await taggingApi.getPromptConfig()
  } catch (e) {
    message.error('加载配置失败')
  } finally {
    loading.value = false
  }
}

const saveConfig = async () => {
  saving.value = true
  try {
    await taggingApi.updatePromptConfig(config.value)
    message.success('配置已保存')
    emit('saved')
  } catch (e) {
    message.error('保存失败')
  } finally {
    saving.value = false
  }
}

const testPrompt = async () => {
  if (!testContent.value.trim()) {
    message.warning('请输入测试内容')
    return
  }
  testing.value = true
  testResult.value = null
  try {
    const res = await taggingApi.testPrompt(testContent.value)
    testResult.value = res.tags
  } catch (e) {
    message.error('测试失败')
  } finally {
    testing.value = false
  }
}

onMounted(() => {
  loadConfig()
})
</script>

<template>
  <a-spin :spinning="loading">
    <div class="system-config" v-if="config">
      <a-collapse defaultActiveKey="['prompts', 'models', 'processing', 'review']">
        <!-- 提示词配置 -->
        <a-collapse-panel key="prompts" header="提示词配置">
          <a-form layout="vertical">
            <a-form-item label="文本打标 System Prompt">
              <a-textarea
                v-model:value="config.prompts.text_system_prompt"
                :autoSize="{ minRows: 4, maxRows: 12 }"
              />
            </a-form-item>
            <a-form-item label="文本打标 User Prompt">
              <a-textarea
                v-model:value="config.prompts.text_user_prompt"
                :autoSize="{ minRows: 2, maxRows: 6 }"
              />
              <div class="hint">可用变量：{content}、{taxonomy_tree}</div>
            </a-form-item>
            <a-form-item label="图片描述提示词">
              <a-textarea
                v-model:value="config.prompts.image_describe_prompt"
                :autoSize="{ minRows: 2, maxRows: 4 }"
              />
            </a-form-item>
            <a-form-item label="音频理解提示词">
              <a-textarea
                v-model:value="config.prompts.audio_transcribe_prompt"
                :autoSize="{ minRows: 2, maxRows: 4 }"
              />
            </a-form-item>
            <a-form-item label="视频总结提示词">
              <a-textarea
                v-model:value="config.prompts.video_summary_prompt"
                :autoSize="{ minRows: 2, maxRows: 4 }"
              />
            </a-form-item>
          </a-form>

          <!-- 测试区域 -->
          <a-divider>提示词测试</a-divider>
          <a-form-item label="测试内容">
            <a-textarea
              v-model:value="testContent"
              placeholder="输入一段文本测试打标效果..."
              :autoSize="{ minRows: 3, maxRows: 6 }"
            />
          </a-form-item>
          <a-button type="primary" @click="testPrompt" :loading="testing" ghost>
            测试打标
          </a-button>
          <div v-if="testResult" class="test-result">
            <a-tag v-for="(tag, i) in testResult" :key="i" color="blue">
              {{ tag.tag_name }} ({{ (tag.confidence * 100).toFixed(0) }}%)
            </a-tag>
          </div>
        </a-collapse-panel>

        <!-- 模型配置 -->
        <a-collapse-panel key="models" header="模型配置">
          <a-form layout="vertical">
            <a-form-item label="VL 模型（图片理解）">
              <a-input v-model:value="config.models.vl_model" placeholder="dashscope/qwen-vl-max-latest" />
            </a-form-item>
            <a-form-item label="音频模型（≤30min）">
              <a-input v-model:value="config.models.audio_model" placeholder="dashscope/qwen3.5-omni-plus" />
            </a-form-item>
            <a-form-item label="ASR 模型（>30min 长音频）">
              <a-input v-model:value="config.models.asr_model" placeholder="qwen3-asr-flash-filetrans" />
            </a-form-item>
            <a-form-item label="打标模型（为空则用 fast_model）">
              <a-input v-model:value="config.models.tag_model" placeholder="留空使用默认 fast_model" />
            </a-form-item>
          </a-form>
        </a-collapse-panel>

        <!-- 处理参数 -->
        <a-collapse-panel key="processing" header="处理参数">
          <a-form layout="vertical">
            <a-form-item label="最大并发任务数">
              <a-input-number
                v-model:value="config.processing.max_concurrent_tasks"
                :min="1"
                :max="10"
              />
            </a-form-item>
            <a-form-item label="音频策略切换阈值（分钟）">
              <a-input-number
                v-model:value="config.processing.audio_strategy_threshold_minutes"
                :min="5"
                :max="120"
              />
              <div class="hint">低于此值用 Omni 直接理解，高于用 ASR 转写</div>
            </a-form-item>
            <a-form-item label="视频抽帧间隔（秒）">
              <a-input-number
                v-model:value="config.processing.video_frame_interval_seconds"
                :min="5"
                :max="120"
              />
            </a-form-item>
            <a-form-item label="视频最大帧数">
              <a-input-number
                v-model:value="config.processing.video_max_frames"
                :min="1"
                :max="30"
              />
            </a-form-item>
            <a-form-item label="任务超时时间（秒）">
              <a-input-number
                v-model:value="config.processing.task_timeout_seconds"
                :min="60"
                :max="3600"
              />
            </a-form-item>
          </a-form>
        </a-collapse-panel>

        <!-- 审核参数 -->
        <a-collapse-panel key="review" header="审核参数">
          <a-form layout="vertical">
            <a-form-item label="每个文件最大标签数">
              <a-input-number
                v-model:value="config.review.max_tags"
                :min="1"
                :max="20"
              />
            </a-form-item>
            <a-form-item label="LLM 置信度阈值">
              <a-slider
                v-model:value="config.review.confidence_threshold"
                :min="0"
                :max="1"
                :step="0.05"
                :tooltipVisible="true"
              />
            </a-form-item>
            <a-form-item label="自动审批置信度阈值">
              <a-slider
                v-model:value="config.review.auto_approve_threshold"
                :min="0"
                :max="1"
                :step="0.05"
                :tooltipVisible="true"
              />
            </a-form-item>
            <a-form-item label="自动审批需要规则命中">
              <a-switch v-model:checked="config.review.auto_approve_require_rule_hit" />
            </a-form-item>
            <a-form-item label="解析时自动打标">
              <a-switch v-model:checked="config.review.auto_tag_on_parse" />
            </a-form-item>
          </a-form>
        </a-collapse-panel>
      </a-collapse>

      <div class="config-footer">
        <a-button type="primary" @click="saveConfig" :loading="saving">保存配置</a-button>
      </div>
    </div>
  </a-spin>
</template>

<style scoped lang="less">
.system-config {
  .hint {
    font-size: 12px;
    color: var(--gray-500);
    margin-top: 4px;
  }

  .test-result {
    margin-top: 12px;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .config-footer {
    margin-top: 24px;
    text-align: right;
  }
}
</style>
