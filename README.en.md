<div align="center">

<img src="web/public/logo.svg" width="96" height="96" alt="rxyy-know" />

# rxyy-know

**An LLM-powered knowledge base and knowledge-graph agent platform**

Make enterprise knowledge retrievable, reasonable and deliverable by agents

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Docker Compose](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](docker-compose.yml)
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](backend/pyproject.toml)
[![Vue 3](https://img.shields.io/badge/Vue-3-4FC08D?logo=vue.js&logoColor=white)](web/package.json)

[简体中文](README.md)

</div>

---

## Overview

**rxyy-know** is a self-hostable knowledge base and agent platform that combines **RAG retrieval**,
**knowledge graphs** and **LangGraph multi-agent orchestration** into one multi-tenant workspace:

- Admins configure knowledge bases, models and permissions
- Users chat with agents in a ChatGPT-like UI; agents can mount knowledge bases, Skills, MCP servers, sub-agents and sandbox tools
- Answers come with **citations**, **knowledge-graph reasoning** and downloadable **artifacts**

This project is built on top of [`xerrors/Yuxi`](https://github.com/xerrors/Yuxi) v0.7.1, adding a set of
capabilities aimed at **media-asset and document-heavy workloads**. Upstream is MIT licensed, and so is this fork.

## What this fork adds

| Area | Capability |
|---|---|
| **Auto tagging** | A 1811-node taxonomy (IPTC Media Topics + Chinese broadcast categories + tone/type/audience facets), hybrid rule + LLM tagging, a confidence-ascending human review drawer, tag CRUD and synonyms |
| **Multimodal ingestion** | Images go through OCR plus a vision model description that back each other up (image-only files stay searchable); audio/video get sentence-level ASR transcripts with timestamps plus scene-based keyframe descriptions; preprocessing results are cached in both directions so tagging and ingestion never pay the API cost twice |
| **OCR engines** | A Qwen-OCR parser with a **PDF text-layer fast path** — pages that already carry a text layer are read directly and only scanned pages hit the cloud OCR, so digital manuals cost almost nothing; multi-engine automatic fallback on failure |
| **Topic graph agent** | Plain-language questions produce six graph types (platform / persona / event causality / topic timeline / overview / archive heatmap), rendered automatically, with a "needs more data" card when coverage is thin |
| **Graph visualization** | 2D / 3D toggle, 3D powered by `3d-force-graph`, suitable for demoing large graphs |
| **Graph build performance** | Batched vector-layer writes plus Neo4j property indexes give roughly **10x** speedup on large collections; extraction timeout and concurrency are tunable to avoid provider-side queueing |
| **Retrieval quality** | Retrieved chunks carry the matched file's tags so the model understands classification; agents with an explicitly bound knowledge base are forced to retrieve before answering, preventing hallucinated shortcuts |
| **Robustness** | Interrupted `parsing`/`indexing` states are reset on startup (no more orphaned files after a restart), OOXML uploads are zip-validated up front, embedding batches split recursively on size errors, text decoding falls back across encodings, worker count and upload limits are configurable |
| **Operations** | A one-shot daily backup script (PostgreSQL / Neo4j / MinIO / saves, plus weekly Milvus) with configurable retention |

## Stack

| Layer | Technology |
| --- | --- |
| Frontend | Vue 3 · Vite · Ant Design Vue · Pinia |
| Backend | FastAPI · Python 3.12+ · LangGraph · ARQ |
| Storage | PostgreSQL · Redis · MinIO · Milvus · Neo4j |
| Parsing | Qwen-OCR · MinerU · PaddleX · RapidOCR |
| Models | Any OpenAI-compatible endpoint |
| Deployment | Docker Compose |

## Requirements

All models are accessed over API, so **no GPU is required**. The bundled vector and graph databases are what consume resources:

| Mode | Services | Recommended |
| --- | --- | --- |
| **Full** (knowledge base + graph) | api / worker / web / sandbox / postgres / redis / minio / etcd / **milvus** / **neo4j** | **4 cores, 16 GB RAM, 100 GB disk** and up |
| **Lite** (`make up-lite`) | api / worker / web / postgres / redis / minio | 2 cores, 4 GB RAM |

> Milvus standalone officially requires at least 8 GB of RAM (16 GB recommended) and Neo4j needs another 1–2 GB.
> A 4 GB cloud instance cannot run the full mode — use lite mode or resize.

## Quick start

**Prerequisites**: Docker with Docker Compose, and at least one OpenAI-compatible model API key.

```bash
git clone https://github.com/rixingyingyao/rxyy-know.git
cd rxyy-know

# Interactive .env setup (API keys, auto-generated JWT secret)
./scripts/init.sh          # Windows PowerShell: .\scripts\init.ps1

docker compose up -d --build

# Seed the admin account and demo departments.
# Passwords are randomly generated and printed once — save them immediately.
make seed
```

Open `http://localhost:5173` and sign in with the credentials printed above.

> To pin the admin password, set `YUXI_SUPER_ADMIN_PASSWORD` in `.env` before running `make seed`.
> When serving behind a reverse proxy or tunnel, set `VITE_ALLOWED_HOSTS=your.domain.com` in `.env`, otherwise Vite rejects the Host header.

## Layout

```
rxyy-know/
├─ backend/
│  ├─ package/yuxi/       core library: agents / knowledge / models / repositories / services / storage
│  ├─ server/             FastAPI app: routers / utils
│  ├─ scripts/            ops scripts: user seeding, legacy migration, preset agent prompt sync
│  └─ test/               unit / integration / e2e tests
├─ web/                   Vue 3 frontend
├─ packages/yuxi-cli/     command line client
├─ docker/                Dockerfiles and volume mount points
├─ docs/                  VitePress documentation site
└─ scripts/               init, image pull, backup
```

## Branding

Do not edit `info.template.yaml` directly; copy it instead:

```bash
cp backend/package/yuxi/config/static/info.template.yaml \
   backend/package/yuxi/config/static/info.local.yaml
# then in .env: YUXI_BRAND_FILE_PATH=backend/package/yuxi/config/static/info.local.yaml
```

`info.local.yaml` is gitignored. See [the branding guide](docs/advanced/branding.md) for details.

## Security

- No real credentials are committed. Everything lives in `.env` (gitignored); see `.env.template`
- `make seed` generates random admin passwords — there are no hardcoded defaults
- Before exposing this to the internet, set your own `JWT_SECRET_KEY`, database and MinIO credentials, and restrict published ports

## Acknowledgements

- [Yuxi](https://github.com/xerrors/Yuxi) — the upstream project this fork is based on
- [LangGraph](https://github.com/langchain-ai/langgraph) — multi-agent orchestration
- [DeepAgents](https://github.com/langchain-ai/deepagents) — deep agent framework
- [Milvus](https://github.com/milvus-io/milvus) / [Neo4j](https://neo4j.com/) — vector and graph storage
- [RAGFlow](https://github.com/infiniflow/ragflow) — document chunking strategy reference

## License

[MIT](LICENSE). The upstream `Yuxi Project Contributors` copyright notice is retained.
