<template>
  <div class="graph-canvas-3d-container" ref="rootEl">
    <div v-show="graphData.nodes.length > 0" class="graph-canvas-3d" ref="container"></div>
    <div class="slots">
      <div v-if="$slots.top" class="overlay top">
        <slot name="top" />
      </div>
      <div class="canvas-content">
        <slot name="content" />
      </div>
      <div class="graph-stats-panel" v-if="graphData.nodes.length > 0">
        <div class="stat-item">
          <span class="stat-label">节点</span>
          <span class="stat-value">{{ graphData.nodes.length }}</span>
          <span v-if="graphInfo?.node_count" class="stat-total">/ {{ graphInfo.node_count }}</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">边</span>
          <span class="stat-value">{{ graphData.edges.length }}</span>
          <span v-if="graphInfo?.edge_count" class="stat-total">/ {{ graphInfo.edge_count }}</span>
        </div>
      </div>
      <div v-if="$slots.bottom" class="overlay bottom">
        <slot name="bottom" />
      </div>
    </div>
  </div>
</template>

<script setup>
import ForceGraph3D from '3d-force-graph'
import SpriteText from 'three-spritetext'
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js'
import { onMounted, onUnmounted, ref, watch, nextTick } from 'vue'

const PALETTE = [
  '#60a5fa', '#34d399', '#f59e0b', '#f472b6', '#22d3ee',
  '#a78bfa', '#f97316', '#4ade80', '#f43f5e', '#2dd4bf'
]

const props = defineProps({
  graphData: { type: Object, required: true, default: () => ({ nodes: [], edges: [] }) },
  graphInfo: { type: Object, default: () => ({}) },
  showEdgeLabels: { type: Boolean, default: false },
  minDegreeForLabel: { type: Number, default: 2 },
  minDegreeFilter: { type: Number, default: 0 },
  hiddenEntityTypes: { type: Array, default: () => [] },
  nodeSpacing: { type: Number, default: 180 },
  highlightKeywords: { type: Array, default: () => [] }
})

const emit = defineEmits(['ready', 'data-rendered', 'node-click', 'edge-click', 'canvas-click', 'types-updated'])

const container = ref(null)
const rootEl = ref(null)
let graphInstance = null
let resizeObserver = null

const entityTypeSet = ref(new Map())

// Hover 高亮状态
const highlightNodes = new Set()
const highlightLinks = new Set()
let hoverNode = null

// 类型 → 颜色映射
const typeColorMap = new Map()
function getTypeColor(typeName) {
  if (typeColorMap.has(typeName)) return typeColorMap.get(typeName)
  const idx = typeColorMap.size % PALETTE.length
  typeColorMap.set(typeName, PALETTE[idx])
  return PALETTE[idx]
}

// 邻居索引
let neighborsMap = new Map() // nodeId -> Set<nodeId>
let linksOf = new Map() // nodeId -> Set<link>

function buildNeighborIndex(nodes, links) {
  neighborsMap = new Map()
  linksOf = new Map()
  for (const n of nodes) {
    neighborsMap.set(n.id, new Set())
    linksOf.set(n.id, new Set())
  }
  for (const l of links) {
    const sid = typeof l.source === 'object' ? l.source.id : l.source
    const tid = typeof l.target === 'object' ? l.target.id : l.target
    neighborsMap.get(sid)?.add(tid)
    neighborsMap.get(tid)?.add(sid)
    linksOf.get(sid)?.add(l)
    linksOf.get(tid)?.add(l)
  }
}

// 前端数据格式转换 + 过滤
function formatData() {
  const data = props.graphData || { nodes: [], edges: [] }

  // 计算度数
  const degrees = new Map()
  for (const n of data.nodes) degrees.set(String(n.id), 0)
  for (const e of data.edges) {
    const s = String(e.source_id)
    const t = String(e.target_id)
    degrees.set(s, (degrees.get(s) || 0) + 1)
    degrees.set(t, (degrees.get(t) || 0) + 1)
  }

  // 统计所有实体类型（过滤前）
  const allTypeCounter = new Map()
  for (const n of data.nodes) {
    const eType = n.type || n.properties?.entity_type || 'Entity'
    allTypeCounter.set(eType, (allTypeCounter.get(eType) || 0) + 1)
  }

  // 过滤隐藏的实体类型
  const hiddenTypes = new Set(props.hiddenEntityTypes)
  const filteredNodes = data.nodes.filter((n) => {
    const eType = n.type || n.properties?.entity_type || 'Entity'
    return !hiddenTypes.has(eType)
  })

  // 过滤最小度数
  const minDeg = props.minDegreeFilter || 0
  const visibleNodeIds = new Set()

  const nodes = filteredNodes
    .filter((n) => {
      const deg = degrees.get(String(n.id)) || 0
      return deg >= minDeg
    })
    .map((n) => {
      const id = String(n.id)
      const deg = degrees.get(id) || 0
      const entityType = n.type || n.properties?.entity_type || 'Entity'
      visibleNodeIds.add(id)
      return {
        id,
        name: n.name || id,
        val: Math.max(1, deg),
        entityType,
        color: getTypeColor(entityType),
        degree: deg,
        data: {
          label: n.name || id,
          degree: deg,
          entityType,
          original: n
        }
      }
    })

  const links = data.edges
    .filter((e) => visibleNodeIds.has(String(e.source_id)) && visibleNodeIds.has(String(e.target_id)))
    .map((e, idx) => ({
      source: String(e.source_id),
      target: String(e.target_id),
      type: e.type || '',
      data: {
        label: e.type || '',
        original: e
      }
    }))

  // 重新计算过滤后度数
  const filteredDeg = new Map()
  for (const n of nodes) filteredDeg.set(n.id, 0)
  for (const l of links) {
    filteredDeg.set(l.source, (filteredDeg.get(l.source) || 0) + 1)
    filteredDeg.set(l.target, (filteredDeg.get(l.target) || 0) + 1)
  }
  for (const n of nodes) {
    n.degree = filteredDeg.get(n.id) || 0
    n.val = Math.max(1, n.degree)
    n.data.degree = n.degree
  }

  return { nodes, links, typeCounter: allTypeCounter }
}

function initGraph() {
  if (!container.value) return
  const width = container.value.offsetWidth
  const height = container.value.offsetHeight
  if (width === 0 || height === 0) return

  container.value.innerHTML = ''
  if (graphInstance) {
    graphInstance._destructor?.()
    graphInstance = null
  }

  const spacing = props.nodeSpacing || 180
  const nodeCount = props.graphData?.nodes?.length || 0

  graphInstance = ForceGraph3D()(container.value)
    .width(width)
    .height(height)
    .backgroundColor('#05080d')
    // ---- 节点 ----
    .nodeColor((n) => {
      if (!hoverNode) return n.color
      return highlightNodes.has(n.id) ? n.color : 'rgba(80,80,80,0.25)'
    })
    .nodeVal((n) => n.val)
    .nodeLabel((n) => {
      const p = n.data?.original?.properties || {}
      const desc = p.description || p.desc || ''
      return `<div style="text-align:center;font-size:13px;max-width:280px;line-height:1.5;padding:4px 8px">
        <div style="font-weight:600;margin-bottom:2px">${n.name}</div>
        <div style="color:#aaa;font-size:11px">${n.entityType}${n.degree ? ` · ${n.degree}条关系` : ''}</div>
        ${desc ? `<div style="color:#ccc;font-size:11px;margin-top:4px">${desc.slice(0, 100)}</div>` : ''}
      </div>`
    })
    .nodeOpacity(0.7)
    .nodeResolution(nodeCount > 300 ? 8 : 12)
    // SpriteText 标签悬浮在节点上方
    .nodeThreeObject((n) => {
      // 性能优化：节点过多时只为高度数节点显示标签
      if (nodeCount > 200 && n.degree < 2) return undefined
      const sprite = new SpriteText(n.name)
      sprite.material.depthWrite = false
      sprite.color = '#cfd6dd'
      sprite.textHeight = 3.5
      sprite.fontWeight = '500'
      sprite.backgroundColor = 'rgba(0,0,0,0.55)'
      sprite.borderRadius = 3
      sprite.padding = [1.5, 4]
      sprite.center.set(0.5, 2.0) // 偏移到节点上方
      return sprite
    })
    .nodeThreeObjectExtend(true)
    // ---- 边 ----
    .linkColor((l) => {
      if (!hoverNode) return 'rgba(255,255,255,0.18)'
      return highlightLinks.has(l) ? 'rgba(255,255,255,0.6)' : 'rgba(255,255,255,0.04)'
    })
    .linkWidth((l) => (highlightLinks.has(l) ? 1.2 : 0.4))
    .linkOpacity(0.7)
    .linkDirectionalArrowLength(3.5)
    .linkDirectionalArrowRelPos(1)
    .linkDirectionalParticles((l) => {
      if (nodeCount > 300) return highlightLinks.has(l) ? 2 : 0
      return highlightLinks.has(l) ? 3 : 1
    })
    .linkDirectionalParticleSpeed(0.004)
    .linkDirectionalParticleWidth(nodeCount > 300 ? 0.8 : 1.2)
    .linkDirectionalParticleColor((l) => {
      const srcId = typeof l.source === 'object' ? l.source.id : l.source
      return getTypeColor(nodeTypeById.get(srcId) || 'Entity')
    })
    .linkLabel((l) => (props.showEdgeLabels ? l.type : ''))
    // ---- 力模型 ----
    .d3AlphaDecay(0.02)
    .d3VelocityDecay(0.3)
    .warmupTicks(50)
    .cooldownTime(3000)
    // ---- 事件 ----
    .onNodeHover((node) => {
      // 清除旧状态
      highlightNodes.clear()
      highlightLinks.clear()
      hoverNode = node || null

      if (node) {
        highlightNodes.add(node.id)
        const nb = neighborsMap.get(node.id)
        if (nb) nb.forEach((nid) => highlightNodes.add(nid))
        const lk = linksOf.get(node.id)
        if (lk) lk.forEach((l) => highlightLinks.add(l))
      }

      // 更新 DOM cursor
      if (container.value) {
        container.value.style.cursor = node ? 'pointer' : 'default'
      }
    })
    .onNodeClick((node) => {
      emit('node-click', node)
      const distance = 80
      const distRatio = 1 + distance / Math.hypot(node.x, node.y, node.z)
      graphInstance.cameraPosition(
        { x: node.x * distRatio, y: node.y * distRatio, z: node.z * distRatio },
        node,
        1500
      )
    })
    .onBackgroundClick(() => {
      emit('canvas-click')
    })

  // 力参数
  graphInstance.d3Force('charge').strength(-spacing * 2)
  graphInstance.d3Force('link').distance(spacing * 0.8)

  // Bloom 辉光后处理（克制，避免节点过亮）
  try {
    const bloomPass = new UnrealBloomPass()
    bloomPass.strength = 0.18
    bloomPass.radius = 0.3
    bloomPass.threshold = 0.85
    graphInstance.postProcessingComposer().addPass(bloomPass)
  } catch (e) {
    console.warn('Bloom effect not available:', e)
  }

  emit('ready', graphInstance)
}

// 节点类型索引
const nodeTypeById = new Map()

function setGraphData() {
  if (!graphInstance) initGraph()
  if (!graphInstance) return

  const { nodes, links, typeCounter } = formatData()

  // 建立索引
  nodeTypeById.clear()
  for (const n of nodes) nodeTypeById.set(n.id, n.entityType)
  buildNeighborIndex(nodes, links)

  // 更新实体类型集合
  if (typeCounter) {
    entityTypeSet.value = typeCounter
    emit('types-updated', typeCounter)
  }

  graphInstance.graphData({ nodes, links })
  emit('data-rendered')
}

function refreshGraph() {
  setGraphData()
}

function clearFocus() {
  if (graphInstance) {
    graphInstance.cameraPosition({ x: 0, y: 0, z: 500 }, { x: 0, y: 0, z: 0 }, 1000)
  }
}

defineExpose({ entityTypeSet, refreshGraph, clearFocus })

// 监听数据变化
watch(
  () => [props.graphData.nodes.length, props.graphData.edges.length],
  () => {
    nextTick(() => setGraphData())
  }
)

// 监听过滤条件变化
watch(
  () => [props.hiddenEntityTypes, props.minDegreeFilter, props.showEdgeLabels],
  () => {
    nextTick(() => setGraphData())
  },
  { deep: true }
)

// 监听节点间距变化 → 更新力参数
watch(
  () => props.nodeSpacing,
  (spacing) => {
    if (!graphInstance) return
    graphInstance.d3Force('charge').strength(-spacing * 2)
    graphInstance.d3Force('link').distance(spacing * 0.8)
    graphInstance.d3ReheatSimulation()
  }
)

onMounted(() => {
  nextTick(() => {
    initGraph()
    if (props.graphData.nodes.length > 0) setGraphData()
  })

  resizeObserver = new ResizeObserver(() => {
    if (!graphInstance || !container.value) return
    graphInstance.width(container.value.offsetWidth)
    graphInstance.height(container.value.offsetHeight)
  })
  if (rootEl.value) resizeObserver.observe(rootEl.value)
})

onUnmounted(() => {
  resizeObserver?.disconnect()
  if (graphInstance) {
    graphInstance._destructor?.()
    graphInstance = null
  }
})
</script>

<style lang="less" scoped>
.graph-canvas-3d-container {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
}

.graph-canvas-3d {
  width: 100%;
  height: 100%;
}

.slots {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  display: flex;
  flex-direction: column;

  .overlay {
    pointer-events: auto;
    &.top {
      z-index: 10;
    }
    &.bottom {
      z-index: 10;
    }
  }

  .canvas-content {
    flex: 1;
    pointer-events: none;
  }
}

.graph-stats-panel {
  position: absolute;
  bottom: 12px;
  left: 12px;
  display: flex;
  gap: 12px;
  padding: 6px 14px;
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(4px);
  border-radius: 8px;
  pointer-events: auto;
  z-index: 10;

  .stat-item {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 12px;
    color: rgba(255, 255, 255, 0.75);
  }

  .stat-label {
    color: rgba(255, 255, 255, 0.5);
  }

  .stat-value {
    font-weight: 600;
    color: #fff;
  }

  .stat-total {
    color: rgba(255, 255, 255, 0.4);
    font-size: 11px;
  }
}
</style>
