<template>
  <div class="graph-viz-card" :data-type="data.type">
    <div class="gv-header">
      <span class="gv-badge" :class="{ 'gv-badge-warn': isNeedsCompletion }">{{ typeLabel }}</span>
      <span v-if="data.center || data.found_in_kb?.summary" class="gv-center">
        <template v-if="isNeedsCompletion">数据不足：<b>{{ data.center || '该实体' }}</b></template>
        <template v-else>中心：<b>{{ data.center }}</b></template>
      </span>
      <span v-if="stats" class="gv-stats">{{ stats }}</span>
      <span v-if="data.meta?.source" class="gv-source">来源：{{ data.meta.source }}</span>
    </div>

    <!-- needs_completion 卡片：数据不足时显示 -->
    <div v-if="isNeedsCompletion" class="gv-completion">
      <div v-if="completionSummary" class="gv-comp-summary">
        <span class="gv-comp-icon">⚠</span>
        <span>{{ completionSummary }}</span>
      </div>
      <div v-if="existingDocs.length" class="gv-comp-docs">
        <div class="gv-comp-label">已找到 {{ existingDocs.length }} 项相关数据：</div>
        <ul>
          <li v-for="(d, i) in existingDocs.slice(0, 6)" :key="i">
            <span v-if="d.date || d.year" class="gv-doc-date">{{ d.date || d.year }}</span>
            <span class="gv-doc-title">{{ d.title || d.label || d.summary || '—' }}</span>
            <span v-if="d.source" class="gv-doc-source">— {{ d.source }}</span>
          </li>
        </ul>
      </div>
      <div v-if="completionActions.length" class="gv-comp-actions">
        <div class="gv-comp-label">补齐方式（请选一种）：</div>
        <button
          v-for="(s, i) in completionActions"
          :key="i"
          class="gv-comp-btn"
          :class="{ 'gv-comp-btn-primary': isWebAction(s), 'gv-comp-btn-busy': completing }"
          :disabled="completing"
          @click="onSuggestionClick(s)"
        >
          <div class="gv-comp-btn-title">{{ s.label || s.action || s.id }}</div>
          <div v-if="s.description || s.desc" class="gv-comp-btn-desc">{{ s.description || s.desc }}</div>
        </button>
      </div>
    </div>

    <!-- 图谱画布 -->
    <div v-if="!isNeedsCompletion" ref="chartEl" class="gv-canvas"></div>

    <div v-if="!isNeedsCompletion && legendItems.length" class="gv-legend">
      <span v-for="item in legendItems" :key="item.name" class="gv-leg">
        <i :style="{ background: item.color }"></i>{{ item.name }}
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onBeforeUnmount, ref, watch, nextTick } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  data: { type: Object, required: true },
})
const emit = defineEmits(['ask-followup'])

const chartEl = ref(null)
let chartInstance = null
const completing = ref(false)

const TYPE_LABEL = {
  overview: '全景图',
  person: '人物画像图',
  platform: '战略平台图',
  event: '事件因果图',
  topic_timeline: '主题脉络图',
  archive_heatmap: '历史档案图',
  needs_completion: '数据不足',
}

const typeLabel = computed(() => TYPE_LABEL[props.data?.type] || '图谱')

const isNeedsCompletion = computed(() => props.data?.type === 'needs_completion')

const completionSummary = computed(() => {
  const d = props.data || {}
  return (
    d.summary ||
    d.reason ||
    d.found_in_kb?.summary ||
    (d.found_count !== undefined
      ? `知识库中相关数据较少（找到 ${d.found_count} 项，建议至少 ${d.threshold || 10} 项），不足以画出有意义的图。`
      : '')
  )
})

const existingDocs = computed(() => {
  const d = props.data || {}
  return d.existing_data || d.found_in_kb?.documents || d.found_data_points || []
})

const completionActions = computed(() => {
  const d = props.data || {}
  return d.suggested_actions || d.suggestions || d.options || []
})

function isWebAction(s) {
  const key = (s?.id || s?.action || s?.action_type || '').toLowerCase()
  const lbl = (s?.label || s?.action || '').toLowerCase()
  return /web|firecrawl|网页|公开|抓取|爬/.test(key) || /web|firecrawl|网页|公开|抓取|爬/.test(lbl)
}

function isUploadAction(s) {
  const key = (s?.id || s?.action || s?.action_type || '').toLowerCase()
  const lbl = (s?.label || s?.action || '').toLowerCase()
  return /upload|手动|上传/.test(key) || /upload|手动|上传/.test(lbl)
}

function isListExistingAction(s) {
  const key = (s?.id || s?.action || s?.action_type || '').toLowerCase()
  const lbl = (s?.label || s?.action || '').toLowerCase()
  return /existing|list|view|已有|查看/.test(key) || /existing|list|view|已有|查看/.test(lbl)
}

function onSuggestionClick(s) {
  if (completing.value) return
  const entity = props.data?.center || props.data?.entity || '该实体'
  const intent = props.data?.intent || ''
  const kbHint = props.data?.kb_id ? `（kb_id=${props.data.kb_id}）` : ''
  let followup = ''
  if (isWebAction(s)) {
    const count = s?.limit || s?.count || 10
    followup = `好的，请调用 complete_kb_from_web 工具，参数 entity="${entity}"，count=${count}${props.data?.kb_id ? `，kb_id="${props.data.kb_id}"` : ''}。这会从深圳新闻网抓取相关稿件并完整执行入库（含 LightRAG 实体抽取，工具会同步等待全部完成，通常 2-10 分钟）。完成后请按返回的 status 字段告诉我真实处理结果（uploaded_count / indexed_count / failed_count / sample_titles），不要凭空猜测。`
    completing.value = true
  } else if (isListExistingAction(s)) {
    followup = `请列出知识库中所有关于 "${entity}" 的已有稿件，给我标题、日期和来源（不需要画图）。`
  } else if (isUploadAction(s)) {
    alert('请前往「知识库管理」页面手动上传文档，然后回来重新提问。')
    return
  } else if (s?.query) {
    followup = `请调用 complete_kb_from_web 工具，entity="${entity}"，count=${s?.limit || 10}。`
    completing.value = true
  } else {
    followup = `请帮我执行：${s?.label || s?.action || s?.id}`
  }
  if (followup) emit('ask-followup', followup)
  // 60 秒后允许再次点击（抓取通常 30-90s，避免重复触发）
  setTimeout(() => (completing.value = false), 60000)
}

const stats = computed(() => {
  const d = props.data || {}
  if (d.type === 'topic_timeline' && d.timeline_data) {
    const t = d.timeline_data
    return `时间段 ${(t.years || [])[0]}-${(t.years || []).slice(-1)[0]} · ${(t.milestones || []).length} 个关键节点`
  }
  if (d.type === 'archive_heatmap' && d.heatmap_data) {
    const h = d.heatmap_data
    return `${(h.years || []).length} 年 × ${(h.areas || []).length} 区域 · ${(h.data || []).length} 数据点`
  }
  const n = (d.nodes || []).length
  const e = (d.links || []).length
  if (n + e > 0) return `节点 ${n} · 边 ${e}`
  return ''
})

const legendItems = computed(() => {
  const cats = props.data?.categories || []
  return cats.map((c) => ({ name: c.name, color: c.color || '#999' }))
})

function buildForceGraphOption(d) {
  const categories = (d.categories || []).map((c) => ({
    name: c.name,
    itemStyle: { color: c.color || '#999' },
  }))
  return {
    tooltip: {
      formatter: (p) => {
        if (p.dataType === 'edge')
          return `${p.data.source} → ${p.data.target}<br/><b>${p.data.type || p.data.value || ''}</b>`
        const node = p.data
        const cat = categories[node.category]?.name || node.type || ''
        return `<b>${node.name}</b><br/>${cat}`
      },
    },
    legend: [{ show: false }],
    series: [
      {
        type: 'graph',
        layout: 'force',
        data: (d.nodes || []).map((n) => ({
          ...n,
          symbolSize: n.symbolSize || (n.id === 'n1' ? 60 : 40),
          label: { show: true, position: 'right', fontSize: 12 },
        })),
        links: (d.links || []).map((l) => ({
          source: l.source,
          target: l.target,
          value: l.type || l.value,
          type: l.type,
        })),
        categories,
        roam: true,
        draggable: true,
        label: { show: true, position: 'right', formatter: '{b}', fontSize: 12 },
        edgeLabel: {
          show: true,
          formatter: (p) => p.data.type || p.data.value || '',
          fontSize: 10,
          color: '#888',
        },
        lineStyle: { color: '#bbb', width: 1, curveness: 0.1 },
        emphasis: { focus: 'adjacency', lineStyle: { width: 3 } },
        force: { repulsion: 500, edgeLength: 100, gravity: 0.1 },
      },
    ],
  }
}

function buildTimelineOption(d) {
  const t = d.timeline_data || { years: [], counts: [], milestones: [] }
  return {
    tooltip: {
      trigger: 'axis',
      formatter: (p) => {
        const i = p[0].dataIndex
        const m = (t.milestones || [])[i] || {}
        return `<b>${m.label || ''}（${m.year || ''}）</b><br/>${m.desc || ''}<br/>报道量：<span style="color:#c8102e;font-weight:700">${(t.counts || [])[i]}</span> 篇`
      },
    },
    grid: { left: 60, right: 40, top: 50, bottom: 50 },
    xAxis: {
      type: 'category',
      data: t.years || [],
      axisLine: { lineStyle: { color: '#888' } },
      axisLabel: { fontSize: 12, color: '#1c2230', fontWeight: 600 },
    },
    yAxis: {
      type: 'value',
      name: '报道量（篇）',
      axisLine: { show: true },
      splitLine: { lineStyle: { color: '#eee', type: 'dashed' } },
    },
    series: [
      {
        type: 'line',
        data: t.counts || [],
        smooth: true,
        symbol: 'circle',
        symbolSize: 16,
        itemStyle: { color: '#c8102e', borderColor: '#fff', borderWidth: 2 },
        lineStyle: { color: '#c8102e', width: 3 },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(200,16,46,0.3)' },
              { offset: 1, color: 'rgba(200,16,46,0.02)' },
            ],
          },
        },
        label: {
          show: true,
          position: 'top',
          formatter: (p) => ((t.milestones || [])[p.dataIndex] || {}).label || '',
          fontSize: 11,
          color: '#003366',
          fontWeight: 600,
          backgroundColor: 'rgba(255,255,255,0.92)',
          padding: [2, 5],
          borderRadius: 3,
          borderColor: '#c8a96a',
          borderWidth: 1,
        },
      },
    ],
  }
}

function buildHeatmapOption(d) {
  const h = d.heatmap_data || { years: [], areas: [], data: [] }
  const max = Math.max(1, ...((h.data || []).map((x) => x[2] || 0)))
  return {
    tooltip: {
      position: 'top',
      formatter: (p) =>
        `${(h.areas || [])[p.value[1]]} · ${(h.years || [])[p.value[0]]}<br/>报道量：<b>${p.value[2]}</b> 篇`,
    },
    grid: { left: 80, right: 30, top: 50, bottom: 50 },
    xAxis: {
      type: 'category',
      data: h.years || [],
      splitArea: { show: true },
      axisLabel: { fontSize: 12, color: '#1c2230' },
    },
    yAxis: {
      type: 'category',
      data: h.areas || [],
      splitArea: { show: true },
      axisLabel: { fontSize: 12, color: '#1c2230', fontWeight: 600 },
    },
    visualMap: {
      min: 0,
      max,
      orient: 'horizontal',
      left: 'center',
      bottom: 5,
      inRange: { color: ['#fef2f2', '#fecaca', '#fca5a5', '#dc2626', '#7f1d1d'] },
      text: ['多', '少'],
      itemWidth: 16,
      itemHeight: 180,
    },
    series: [
      {
        type: 'heatmap',
        data: (h.data || []).map((row) => [row[1], row[0], row[2]]),
        label: { show: true, fontSize: 10, color: '#fff' },
        emphasis: { itemStyle: { shadowBlur: 8, shadowColor: 'rgba(0,0,0,0.4)' } },
      },
    ],
  }
}

function render() {
  if (!chartInstance || !props.data) return
  const t = props.data.type
  let option
  if (t === 'topic_timeline') option = buildTimelineOption(props.data)
  else if (t === 'archive_heatmap') option = buildHeatmapOption(props.data)
  else option = buildForceGraphOption(props.data)
  chartInstance.clear()
  chartInstance.setOption(option, true)
}

onMounted(async () => {
  await nextTick()
  if (chartEl.value) {
    chartInstance = echarts.init(chartEl.value)
    render()
  }
})

onBeforeUnmount(() => {
  if (chartInstance) {
    chartInstance.dispose()
    chartInstance = null
  }
})

watch(
  () => props.data,
  () => {
    nextTick(render)
  },
  { deep: true },
)

const resizeChart = () => chartInstance && chartInstance.resize()
window.addEventListener('resize', resizeChart)
onBeforeUnmount(() => window.removeEventListener('resize', resizeChart))
</script>

<style lang="less" scoped>
.graph-viz-card {
  margin: 12px 0;
  border: 1px solid var(--gray-200, #e5e7eb);
  border-radius: 10px;
  background: var(--gray-0, #fff);
  overflow: hidden;
}

.gv-header {
  padding: 8px 14px;
  background: linear-gradient(180deg, #fff, #f7fbfc);
  border-bottom: 1px solid var(--gray-200, #e5e7eb);
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 12.5px;
  color: #4b5563;

  .gv-badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 4px;
    background: #e6f6f7;
    color: #0a4a52;
    font-weight: 700;
    font-size: 12px;
    border: 1px solid #c2e7ea;

    &.gv-badge-warn {
      background: #fff8e6;
      color: #8a6a1f;
      border-color: #ecdcb0;
    }
  }

  .gv-center b {
    color: #003366;
  }

  .gv-stats {
    color: #6b7280;
  }

  .gv-source {
    margin-left: auto;
    color: #137045;
    font-size: 11.5px;
  }
}

.gv-canvas {
  width: 100%;
  height: 460px;
}

.gv-completion {
  padding: 14px 18px;
  background: #fafbfd;

  .gv-comp-summary {
    background: #fff8e6;
    border-left: 3px solid #c8a96a;
    border-radius: 4px;
    padding: 10px 14px;
    margin-bottom: 14px;
    font-size: 13px;
    color: #5b4a1e;
    display: flex;
    gap: 8px;
    align-items: flex-start;

    .gv-comp-icon {
      font-size: 16px;
      color: #c8a96a;
      flex: 0 0 18px;
    }
  }

  .gv-comp-label {
    font-size: 12.5px;
    color: #6b7280;
    font-weight: 600;
    margin: 8px 0 6px;
  }

  .gv-comp-docs ul {
    margin: 4px 0 14px;
    padding-left: 18px;
    font-size: 12.5px;
    line-height: 1.7;
    color: #374151;

    li {
      .gv-doc-date {
        display: inline-block;
        min-width: 90px;
        color: #6b7280;
        font-variant-numeric: tabular-nums;
      }

      .gv-doc-title {
        color: #1c2230;
      }

      .gv-doc-source {
        color: #6b7280;
        font-size: 11.5px;
        margin-left: 6px;
      }
    }
  }

  .gv-comp-actions {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-top: 4px;
  }

  .gv-comp-btn {
    text-align: left;
    background: #fff;
    border: 1px solid #d4d4d8;
    border-radius: 6px;
    padding: 10px 14px;
    cursor: pointer;
    transition: all 0.15s ease;

    &:hover:not(:disabled) {
      border-color: #0e7490;
      background: #f0fafa;
    }

    &.gv-comp-btn-primary {
      border-color: #c8102e;

      &:hover:not(:disabled) {
        background: #fff5f0;
      }
    }

    &.gv-comp-btn-busy,
    &:disabled {
      opacity: 0.55;
      cursor: not-allowed;
    }

    .gv-comp-btn-title {
      font-weight: 600;
      font-size: 13.5px;
      color: #1c2230;
      margin-bottom: 2px;
    }

    .gv-comp-btn-desc {
      font-size: 11.5px;
      color: #6b7280;
      line-height: 1.5;
    }
  }
}

.gv-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  padding: 8px 14px;
  border-top: 1px solid var(--gray-200, #e5e7eb);
  background: #fafbfd;
  font-size: 11.5px;
  color: #4b5563;

  .gv-leg {
    display: inline-flex;
    align-items: center;
    gap: 5px;

    i {
      display: inline-block;
      width: 10px;
      height: 10px;
      border-radius: 50%;
    }
  }
}
</style>
