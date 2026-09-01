<div align="center">

<img src="web/public/logo.svg" width="96" height="96" alt="rxyy-know" />

# rxyy-know

**大模型驱动的知识库与知识图谱智能体平台**

让企业知识可被智能体检索、推理与交付

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Docker Compose](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](docker-compose.yml)
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](backend/pyproject.toml)
[![Vue 3](https://img.shields.io/badge/Vue-3-4FC08D?logo=vue.js&logoColor=white)](web/package.json)

[English](README.en.md)

</div>

---

## 简介

**rxyy-know** 是一个可私有化部署的知识库与智能体平台，把 **RAG 检索**、**知识图谱** 和
**LangGraph 多智能体编排** 整合到一个多租户工作台里：

- 管理员配置知识库、模型与权限
- 用户在类 ChatGPT 的界面里跟智能体对话，智能体可挂载知识库、Skills、MCP、子智能体与沙盒工具
- 回答带**引用来源**、**知识图谱推理**与可下载的**交付产物**

本项目基于开源项目 [`xerrors/Yuxi`](https://github.com/xerrors/Yuxi) v0.7.1 二次开发，
在其之上补齐了一整套面向**媒资与文档密集型场景**的能力（见下）。上游采用 MIT 协议，本项目同样以 MIT 开源。

## 在上游基础上做了什么

| 方向 | 能力 |
|---|---|
| **自动打标** | 1811 节点的标签体系（IPTC Media Topics + 国内广电分类 + 情绪/类型/受众四维）、规则与 LLM 混合打标、置信度升序的人工审核抽屉、标签 CRUD 与同义词 |
| **多模态入库** | 图片走 OCR + 视觉模型描述互为兜底（无字图也可检索）；音视频走 ASR 逐句转写（带时间戳）+ 场景抽帧画面描述；预处理结果双向缓存，打标与入库不重复烧 API |
| **OCR 引擎** | 新增 Qwen-OCR 解析器，并做了 **PDF 文本层 fast path**——页面自带文本层时直读，只有扫描页才走云端 OCR，电子版手册几乎零 OCR 成本；多引擎失败自动降级 |
| **主题图谱助手** | 纯自然语言提问即可产出 6 类图谱（战略平台图 / 人物画像图 / 事件因果图 / 主题脉络图 / 全景图 / 档案热力图），前端自动渲染，数据不足时给出补齐建议卡 |
| **图谱可视化** | 2D / 3D 双视图切换，3D 用 `3d-force-graph` 渲染，适合大图演示 |
| **图谱构建性能** | 向量层合批写入 + Neo4j 属性索引，大库构建实测提速约 **10 倍**；抽取超时与并发可调，避免供应商侧排队打爆 |
| **检索质量** | 检索结果注入命中文件的标签供模型理解分类；智能体显式绑定知识库时强制先检索再作答，杜绝跳过 RAG 直接幻觉 |
| **健壮性** | 启动时自动复位 `parsing`/`indexing` 中间态（重启不再留下孤儿文件）、OOXML 上传 zip 预检、embedding 超限二分重试、文本编码多级兜底、worker 数与上传上限可配 |
| **运维** | 一键每日备份脚本（PG / Neo4j / MinIO / saves + 每周 Milvus），滚动保留策略可配 |

## 技术栈

| 层 | 技术 |
| --- | --- |
| 前端 | Vue 3 · Vite · Ant Design Vue · Pinia |
| 后端 | FastAPI · Python 3.12+ · LangGraph · ARQ（异步 worker） |
| 存储 | PostgreSQL · Redis · MinIO · Milvus（向量）· Neo4j（图谱） |
| 文档解析 | Qwen-OCR · MinerU · PaddleX · RapidOCR |
| 模型接入 | 任意 OpenAI 兼容接口（阿里百炼 / SiliconFlow / 本地 vLLM 等） |
| 部署 | Docker Compose |

## 资源要求

模型全部走 API，**不需要 GPU**。真正吃资源的是自带的向量库与图数据库：

| 模式 | 启动的服务 | 建议配置 |
| --- | --- | --- |
| **完整模式**（知识库 + 知识图谱） | api / worker / web / sandbox / postgres / redis / minio / etcd / **milvus** / **neo4j** | **4 核 16G 内存 / 100G 磁盘**起 |
| **LITE 模式**（`make up-lite`） | api / worker / web / postgres / redis / minio | 2 核 4G 内存 |

> Milvus standalone 官方要求最低 8G 内存（推荐 16G），Neo4j 另需 1~2G。
> **4G 内存的轻量云主机跑不起完整模式**，请用 LITE 模式或升配。

## 快速开始

**前置**：Docker + Docker Compose，以及至少一个 OpenAI 兼容的大模型 API Key。

```bash
git clone https://github.com/rixingyingyao/rxyy-know.git
cd rxyy-know

# 初始化 .env（交互式填 API Key，自动生成 JWT 密钥）
./scripts/init.sh          # Windows PowerShell: .\scripts\init.ps1

# 启动全部服务
docker compose up -d --build

# 初始化管理员与演示部门（口令随机生成，只打印一次，请立刻保存）
make seed
```

启动完成后打开 `http://localhost:5173`，用上一步打印的管理员账号登录。

> 想固定管理员口令，在 `.env` 里预设 `YUXI_SUPER_ADMIN_PASSWORD` 再执行 `make seed`。
> 通过反向代理或隧道域名访问时，需在 `.env` 设置 `VITE_ALLOWED_HOSTS=your.domain.com`，否则 Vite 会拒绝该 Host。

常用命令：

```bash
make up          # 启动
make up-lite     # 轻量模式（不启 milvus / neo4j / etcd）
make down        # 停止
make lint        # 代码检查
make format      # 格式化
```

## 目录结构

```
rxyy-know/
├─ backend/
│  ├─ package/yuxi/       核心库：agents / knowledge / models / repositories / services / storage
│  ├─ server/             FastAPI 应用：routers / utils
│  ├─ scripts/            运维脚本：种子用户、历史用户迁移、预置智能体提示词同步
│  └─ test/               unit / integration / e2e 分层测试
├─ web/                   Vue 3 前端
├─ packages/yuxi-cli/     命令行客户端
├─ docker/                Dockerfile 与数据卷挂载点
├─ docs/                  VitePress 文档站
└─ scripts/               初始化、镜像拉取、备份
```

## 品牌自定义

不要直接改 `info.template.yaml`，复制一份本地配置即可：

```bash
cp backend/package/yuxi/config/static/info.template.yaml \
   backend/package/yuxi/config/static/info.local.yaml
# 然后在 .env 里：YUXI_BRAND_FILE_PATH=backend/package/yuxi/config/static/info.local.yaml
```

`info.local.yaml` 已在 `.gitignore` 中。可配置组织名、Logo、登录背景、页脚版权、用户协议链接等，
主题色在 `web/src/assets/css/base.css`。详见 [品牌自定义文档](docs/advanced/branding.md)。

## 安全

- 仓库内**不含任何真实密钥**。所有凭据走 `.env`（已 gitignore），模板见 `.env.template`
- `make seed` 的管理员口令默认**随机生成**，不存在写死的默认口令
- 对公网暴露前，请务必自定义 `JWT_SECRET_KEY`、数据库与 MinIO 口令，并收敛端口暴露范围

## 致谢

本项目站在这些开源项目的肩膀上：

- [Yuxi](https://github.com/xerrors/Yuxi) —— 本项目的上游，提供了整体架构与绝大部分基础能力
- [LangGraph](https://github.com/langchain-ai/langgraph) —— 多智能体编排框架
- [DeepAgents](https://github.com/langchain-ai/deepagents) —— 深度智能体框架
- [Milvus](https://github.com/milvus-io/milvus) / [Neo4j](https://neo4j.com/) —— 向量与图谱存储
- [RAGFlow](https://github.com/infiniflow/ragflow) —— 文档分块策略参考

## 许可证

[MIT](LICENSE)。上游 `Yuxi Project Contributors` 的版权声明已保留。
