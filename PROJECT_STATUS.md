# AI 科研文献速递系统：开发交接快照

快照日期：2026-09-01（Asia/Shanghai）  
项目版本：0.1.0  
运行环境：Python 3.12.13、SQLAlchemy 2.0.52、SQLite  
快照依据：当前目录中的真实源码、两个 SQLite 文件、2026-09-01 重新执行的测试结果  

> 重要：当前目录不是 Git 仓库，没有可记录的 commit、branch 或 tag。本文件描述的是本机工作区现状，不代表某个已提交版本。

## 状态标记

本文使用三种状态，避免把设计规划误写成已完成：

| 标记 | 定义 |
|---|---|
| **A：已实现并测试通过** | 已有可执行源码，并有自动化测试或真实联网运行结果 |
| **B：只有代码结构/接口骨架** | 已有数据库表、配置模板、Protocol 或约束，但没有完整业务执行链或没有真实运行验证 |
| **C：仅存在于设计文档** | 只在 `docs/design` 中描述，当前 `src` 中没有对应实现 |

## 1. 当前完整目录树

以下树包含项目内全部人工维护文件和当前两个数据库。`.venv`、`__pycache__` 属于生成内容，只列目录不展开。

~~~text
AI-Literature-Intelligence-System/
├── .env.example
├── .gitignore
├── PROJECT_STATUS.md
├── README.md
├── pyproject.toml
├── .venv/                         # 本地虚拟环境，生成内容，未展开
├── config/
│   └── prompts.v1.yaml
├── data/
│   ├── app.db                     # 19 张表，当前无业务数据
│   └── smoke.db                   # 真实联网烟雾测试数据
├── docs/
│   └── design/
│       ├── README.md
│       ├── 01-database-er-design.md
│       ├── 02-agent-state-flow.md
│       └── 03-prompt-engineering-spec.md
├── src/
│   └── literature_intelligence/
│       ├── __init__.py
│       ├── config.py
│       ├── collectors/
│       │   ├── __init__.py
│       │   └── pubmed.py
│       ├── db/
│       │   ├── __init__.py
│       │   ├── base.py
│       │   ├── init_db.py
│       │   └── models.py
│       ├── pipeline/
│       │   ├── __init__.py
│       │   ├── content_readiness.py
│       │   └── ingestion.py
│       └── services/
│           ├── __init__.py
│           ├── normalization.py
│           ├── paper_ingest.py
│           └── verification.py
└── tests/
    ├── test_content_readiness.py
    ├── test_database.py
    ├── test_ingestion_pipeline.py
    ├── test_normalization.py
    ├── test_pubmed.py
    └── test_verification.py
~~~

当前安装了三个命令入口：

- `literature-db-init`
- `literature-ingest`
- `literature-content-ready`

## 2. 已实际实现的功能

### A：已实现并测试通过

#### 数据库

- SQLAlchemy 2.x Declarative 模型。
- SQLite 数据库目录自动创建。
- SQLite 外键约束自动启用。
- 19 张表一次性初始化，重复执行 `create_all` 不会重复建表。
- 正式发布数据库守卫：`publication_batches.status='published'` 时，必须存在确认人、确认时间、预览哈希和正式输出路径。

#### PubMed 数据采集

- 使用 NCBI E-utilities 的 ESearch 进行真实 PubMed 检索。
- ESearch JSON 响应解析。
- 使用 EFetch 获取 PubMed XML。
- PubMed XML 解析：
  - PMID
  - 标题
  - 结构化摘要
  - 期刊
  - 出版日期
  - DOI/PMCID
  - 语言
  - publication type
  - 作者、机构和 ORCID
- 无 API Key 时限制请求间隔约 0.34 秒。
- 支持 `tool`、`email`、`api_key` 参数。
- 对临时网络错误、429 和部分 5xx 状态执行有限重试。

#### 标准化

- Unicode NFKC、HTML entity 和空白标准化。
- 标题大小写与标点标准化。
- DOI URL、`doi:` 前缀、大小写和尾部标点标准化。
- PMID、PMCID、ORCID 标准化。
- PubMed 年/月/日及 MedlineDate 的有限解析。
- publication type 映射为内部 article type。
- 作者姓名、机构和 ORCID 标准化。

#### 去重与落库

- PMID 精确匹配自动复用已有论文。
- DOI 精确匹配自动复用已有论文。
- 同一记录重复导入保持论文和内容快照幂等。
- 标题+年份只作为疑似重复提示，不自动合并；新记录标为 `metadata_status='conflict'` 并进入人工复核路径。
- PMID/DOI 指向不同论文时抛出 `DeduplicationConflict`，不静默合并。
- 原始 PubMed 记录裁剪为 JSON 后写入 `source_records`，并保存 SHA-256。
- 新论文作者及论文作者顺序写入数据库。

#### 轻量内容快照

- PubMed 摘要自动生成 `content_snapshots`。
- 保存 source type、content scope、section JSON、来源 URL、rights basis、截断标记、获取时间和 SHA-256。
- 不抓取或保存付费论文全文。
- 内容门禁校验：
  - section 结构与必填字段
  - section id 唯一性
  - 总字符上限
  - SHA-256 一致性
  - 来源 URL 基本格式
  - source type/content scope/rights basis 合规组合
  - 截断内容必须填写原因
- 通过门禁的任务进入 `CONTENT_READY`。
- 缺少内容或权利组合不合规则进入 `REJECTED`。
- 哈希不一致、结构异常或截断原因缺失则进入 `REVIEW_PENDING`。

#### PMID/DOI/URL 验证

- PMID：通过 PubMed EFetch 返回的精确 PMID 验证。
- DOI：通过 Crossref `/works/{doi}` 返回元数据并核对返回 DOI。
- URL：支持 HEAD、有限重定向、必要时 GET fallback。
- URL 安全检查会拒绝：
  - 非 HTTP/HTTPS scheme
  - URL 用户名/密码
  - localhost
  - 私有、环回、链路本地、保留和未指定 IP
- 验证结果追加写入 `verification_records`，不覆盖历史。
- 结果状态支持 `valid`、`invalid`、`conflict`、`temporary_error`。

#### 确定性流水线

`literature-ingest` 已能串联：

~~~text
创建/复用研究领域
→ 创建/复用检索式
→ 创建 pipeline_run
→ PubMed ESearch
→ PubMed EFetch
→ source_record 落库
→ 论文标准化
→ PMID/DOI 去重
→ content_snapshot 落库
→ paper_task 创建
→ PMID/DOI/PubMed URL 验证
→ verification_records 落库
→ 任务进入 VERIFIED / RETRY_WAIT / REVIEW_PENDING / REJECTED
~~~

`literature-content-ready` 已能继续：

~~~text
VERIFIED
→ CONTENT_FETCHING
→ 校验 content_snapshot
→ CONTENT_READY / REVIEW_PENDING / REJECTED
~~~

#### 命令行

- 数据库初始化命令可运行。
- PubMed 检索到验证落库命令可运行。
- 内容快照门禁命令可运行。
- 命令行当前输出 JSON 摘要，没有 Web UI。

## 3. 尚未实现的功能

### B：只有代码结构/接口骨架

以下内容已有部分代码结构，但不能视为业务功能已完成：

| 内容 | 当前真实状态 |
|---|---|
| `PubMedSource`、`Verifier` Protocol | 接口已定义，且当前各自有真实实现；尚无可插拔 provider 注册机制 |
| Prompt 配置 | `config/prompts.v1.yaml` 含 6 个 draft 模板和 JSON Schema，但没有加载器、渲染器、版本注册器或模型调用 |
| `prompt_versions` | 表和模型存在，真实数据库 0 行 |
| `ai_runs` | 表和模型存在，没有 LLM gateway 或写入流程 |
| `paper_analyses` | 表和模型存在，没有 Analysis Agent |
| `analysis_evidence` | 表和模型存在，没有 evidence 提取或逐字匹配执行链 |
| `paper_categories` | 表和模型存在，没有分类服务 |
| `publication_batches` | 表、确认字段及 published 守卫存在；没有预览生成、确认命令或发布服务 |
| `report_items` | 表和模型存在，没有报告生成逻辑 |
| PMC/open-access/excerpt 内容类型 | 内容合规校验规则存在，但没有 PMC、出版社或人工片段采集器 |
| 通用 URL 校验 | 服务已实现；当前 ingestion 实际只验证 PubMed 展示 URL，未批量验证任意出版社页面 |

### C：只存在于设计文档，尚未实现

- LLM 相关性 Screening Agent。
- LLM 论文 Analysis Agent。
- 领域 Classification Agent。
- Report Composition Agent。
- 实际 Quality Gate 和自动质量评分。
- JSON repair 调用链。
- Prompt 版本加载、激活、回滚与评测记录。
- 5–10 篇 MVP 小型人工标注测试集。
- 50 篇正式 Golden Dataset。
- 预览网页生成。
- 批次级人工确认 UI/CLI/API。
- 正式静态网页发布。
- 自动正式发布配置开关。
- Scheduler/每日或每周定时任务。
- FastAPI 服务。
- HTML/CSS/JavaScript 前端。
- 人工审核界面。
- 失败任务自动重试 worker；当前只写入 `RETRY_WAIT` 和 `next_retry_at`。
- 租约 worker 的自动领取、续租和回收执行器。
- PostgreSQL 迁移与 Alembic migration。
- 全文搜索、向量数据库、知识图谱、科研问答。
- 用户、权限、订阅与多人协作。
- 微信公众号、高校动态和 PI 追踪。
- 日志文件、指标仪表盘和告警。
- CI/CD、容器镜像、部署与托管。

## 4. 数据库目前实际存在的表及作用

`data/app.db` 与 `data/smoke.db` 均实际存在以下 19 张表。`app.db` 所有表均为 0 行；下表的“烟雾库数据”来自 `data/smoke.db`。

| 表 | 实际作用 | 实现级别 | 烟雾库数据 |
|---|---|---|---:|
| `research_domains` | 研究领域配置 | A，ingestion 使用 | 1 |
| `search_queries` | PubMed 检索式及版本 | A，ingestion 使用 | 1 |
| `pipeline_runs` | 一次处理批次及配置快照 | A，ingestion 使用 | 1 |
| `source_records` | 外部原始记录裁剪快照与哈希 | A，ingestion 使用 | 1 |
| `papers` | 去重后的论文主数据 | A | 1 |
| `content_snapshots` | AI/筛选实际输入的合规内容快照 | A，已通过内容门禁 | 1 |
| `paper_identifiers` | PMID、DOI、PMCID 等标识符 | A | 2 |
| `authors` | 作者主数据 | A | 6 |
| `paper_authors` | 论文作者顺序和机构 | A | 6 |
| `paper_tasks` | 单篇论文当前状态、重试和快照引用 | A | 1 |
| `verification_records` | PMID/DOI/URL 的追加式验证历史 | A | 3 |
| `task_state_events` | 单篇任务追加式状态转换历史 | A | 6 |
| `prompt_versions` | Prompt 不可变版本快照 | B，仅表和模型 | 0 |
| `ai_runs` | 模型调用审计、token、成本与输出 | B，仅表和模型 | 0 |
| `paper_analyses` | 结构化论文分析 | B，仅表和模型 | 0 |
| `analysis_evidence` | AI 结论与原文证据映射 | B，仅表和模型 | 0 |
| `paper_categories` | 论文与研究领域的多标签关系 | B，仅表和模型 | 0 |
| `publication_batches` | 日报/周报批次、预览和人工确认信息 | B，表与约束已测试，服务未实现 | 0 |
| `report_items` | 冻结的发布条目快照 | B，仅表和模型 | 0 |

### 数据库文件说明

- `data/app.db`
  - 大小：270,336 bytes
  - 19 张表
  - 当前无领域、检索式、论文、任务或运行数据
- `data/smoke.db`
  - 大小：266,240 bytes
  - 19 张表
  - 保存一次真实 PubMed 单篇端到端烟雾测试

## 5. 当前 Agent/流水线实际执行到哪些状态

### 代码中已实现的状态

ingestion 已实现并测试以下路径：

~~~text
DISCOVERED
→ NORMALIZED
→ VERIFYING
→ VERIFIED
~~~

验证分支代码还实现以下转换；底层验证状态映射有单元测试，但这些分支尚未全部进行流水线级端到端测试：

- `VERIFYING → RETRY_WAIT`
- `VERIFYING → REVIEW_PENDING`
- `VERIFYING → REJECTED`

内容门禁已实现并测试：

~~~text
VERIFIED
→ CONTENT_FETCHING
→ CONTENT_READY
~~~

内容异常分支已测试：

- `CONTENT_FETCHING → REVIEW_PENDING`
- `CONTENT_FETCHING → REJECTED`

### 真实烟雾数据当前状态

真实任务的实际状态历史为：

~~~text
DISCOVERED
→ NORMALIZED
→ VERIFYING
→ VERIFIED
→ CONTENT_FETCHING
→ CONTENT_READY
~~~

实际状态：

- `pipeline_run.id`：`661c7a09-6cc2-44ac-9177-6a602e337f7d`
- 批次状态：`PROCESSING`
- 论文任务状态：`CONTENT_READY`
- `relevance_score`：NULL
- 发布数量：0
- 失败数量：0

批次保持 `PROCESSING` 是当前代码的有意行为：后续 Screening、Analysis、Quality Gate 和发布均未实现，不能把该批次标成完整业务流程已完成。

### 只在设计文档中存在、尚未进入真实执行的状态

- `SCREENING`
- `IRRELEVANT`
- `ANALYZING`
- `ANALYZED`
- `CLASSIFYING`
- `CLASSIFIED`
- `COMPOSING`
- `QUALITY_CHECK`
- `READY_FOR_PREVIEW`
- `PREVIEWED`
- `PUBLISHED`
- 批次级 `PREVIEWING`
- 批次级 `AWAITING_CONFIRMATION`
- 批次级 `PUBLISHING`
- 批次级 `PARTIAL`

当前没有一个集中式状态机引擎去验证设计文档中的全部允许转换；两个 pipeline 模块各自显式写入其负责的状态和审计事件。

## 6. 已接入的真实外部接口

### A：已真实调用成功

| 外部接口 | 用途 | 实际结果 |
|---|---|---|
| NCBI E-utilities ESearch | PubMed 关键词检索 | 查询 `probiotics[Title]` 成功 |
| NCBI E-utilities EFetch | 获取 PubMed XML、验证 PMID | 成功取得并解析真实论文 |
| Crossref REST `/works/{doi}` | DOI 存在性和 DOI/标题一致性检查 | 实际 DOI 返回 HTTP 200 并匹配 |
| PubMed 论文页面 | 展示 URL 可达性检查 | 实际页面返回 HTTP 203，判定有效 |

真实烟雾论文：

- PMID：`42668201`
- DOI：`10.1016/j.fm.2026.105264`
- PubMed URL：`https://pubmed.ncbi.nlm.nih.gov/42668201/`
- 检索当时 ESearch 总命中数：10,427
- 本次 `retmax`：1

### B/C：未接入

- OpenAI、Anthropic/Claude 或其他 LLM API：未接入。
- PMC Open Access API/全文：未接入。
- ScienceDirect/Elsevier API：未接入。
- DataCite DOI API：未接入。
- 微信公众号、高校网站或 PI 数据源：未接入。
- 邮件、消息、对象存储或网站托管接口：未接入。

## 7. 当前测试数量、结果及实际端到端结果

### 2026-09-01 重新执行结果

执行命令：

~~~powershell
./.venv/Scripts/python.exe -m compileall -q src tests
./.venv/Scripts/python.exe -m unittest discover -s tests -v
./.venv/Scripts/python.exe -m pip check
~~~

结果：

- 自动化测试：23 项
- 通过：23
- 失败：0
- 错误：0
- 跳过：0
- `compileall`：通过
- `pip check`：`No broken requirements found`

测试分布：

| 测试文件 | 数量 | 覆盖内容 |
|---|---:|---|
| `test_database.py` | 2 | 19 表初始化、无人工确认禁止 published |
| `test_pubmed.py` | 4 | ESearch JSON、EFetch XML、非法 PMID、残缺文章 |
| `test_normalization.py` | 4 | DOI/标题标准化、PMID/DOI 幂等去重、弱匹配人工复核 |
| `test_verification.py` | 6 | PMID、Crossref DOI、URL 状态、安全 URL、验证落库 |
| `test_ingestion_pipeline.py` | 2 | 确定性端到端落库、空检索批次 |
| `test_content_readiness.py` | 5 | 内容通过、缺失、哈希错误、权利阻断、截断原因 |

### 真实联网端到端结果

真实运行不是 mock，执行了：

~~~text
PubMed ESearch
→ EFetch
→ 标准化
→ 去重
→ source_record
→ paper/author/identifier
→ content_snapshot
→ PMID 验证
→ Crossref DOI 验证
→ PubMed URL 验证
→ 内容快照门禁
→ CONTENT_READY
~~~

实际落库结果：

| 项目 | 数量/结果 |
|---|---:|
| pipeline run | 1 |
| source record | 1 |
| paper | 1 |
| author / paper_author | 6 / 6 |
| identifier | 2 |
| content snapshot | 1 |
| paper task | 1 |
| verification record | 3 |
| state event | 6 |
| 最终任务状态 | `CONTENT_READY` |
| 内容 section 数 | 1 |
| 内容字符数 | 1,060 |
| 内容是否截断 | 否 |
| 内容 SHA-256 | `4a10f9818d54bbb1570b28c0c9dc3f4f57a892cb0f6b6f1a18aa323bd97e70db` |

说明：自动化测试全部使用临时数据库；`data/smoke.db` 是单独保留的真实联网烟雾数据，不是生产数据库。

## 8. 与最初架构设计相比发生过的调整

### 已确认并反映到设计与部分代码

1. 正式发布从“Quality Gate 后可能自动发布”调整为：

   `AI 处理 → Quality Gate → 生成预览 → 批次人工确认 → 正式发布`

   当前只实现了数据库字段和禁止无确认直接 published 的约束；预览、确认和发布服务尚未实现。

2. 新增轻量 `content_snapshots`：

   - 记录 AI/筛选实际使用的内容。
   - 保存 section、来源、哈希、获取时间、截断和权利基础。
   - 明确不扩展为付费全文仓库。
   - 已有真实代码、落库和内容门禁测试。

3. Golden Dataset 规划调整：

   - 正式目标仍为 50 篇。
   - 当前 MVP 启动只要求先准备 5–10 篇小型测试集。
   - 小型测试集尚未实际建立。

4. Agent 编排实现方式收缩：

   - 设计曾提及 LangChain/LangGraph 思路。
   - 当前没有依赖 LangChain 或 LangGraph。
   - 当前使用普通 Python 类、SQLAlchemy 和显式状态写入，降低 MVP 复杂度。

5. Web/API/调度暂缓：

   - FastAPI、Scheduler 和静态网站仍在设计中。
   - 当前只提供 CLI。

6. 依赖收缩：

   - `pyproject.toml` 当前唯一运行依赖是 SQLAlchemy。
   - HTTP、JSON、XML、重试基础逻辑使用 Python 标准库。

7. 流水线当前边界明确收缩到 `CONTENT_READY`：

   - 已有真实采集、验证和内容门禁。
   - 没有把未接入的 LLM、Quality Gate 或发布流程伪装成已完成。

## 9. 当前已确认的产品规则

以下规则已由需求方确认，部分已经代码化：

1. MVP 继续沿用现有整体架构，不推翻重做。
2. 第一阶段聚焦 PubMed 科研文献，不扩展微信公众号、PI 追踪、用户系统或知识图谱。
3. 不保存受版权限制的付费论文全文。
4. `content_snapshots` 只保存实际分析所需的公开摘要、开放内容或合规有限片段。
5. PMID、DOI 属于强标识符，可以自动去重。
6. 标题+年份属于弱匹配，只提示人工复核，不能自动合并。
7. PMID、DOI、展示 URL 必须验证并保留验证历史。
8. 外部网络临时错误与确定性无效结果必须区分。
9. 空检索结果是成功空批次，不是系统失败。
10. Quality Gate 和自动质量评分保留，但不能直接授权 MVP 正式发布。
11. MVP 正式发布前必须执行批次级人工确认。
12. 暂不启用完全无人值守的自动正式发布。
13. 自动正式发布只能在系统稳定后通过明确配置开启。
14. 当前先准备 5–10 篇测试论文验证端到端；50 篇保留为正式评测目标，不阻塞基础 MVP 启动。
15. 每个开发阶段必须实际运行测试通过后，再进入下一阶段。
16. API Key 不写入代码、数据库或仓库；使用环境变量。

已代码化但仍需后续端到端验证的规则：

- 无批次确认记录时数据库拒绝 `published`。
- 私有/本地 URL 不允许进入 URL 验证请求。
- 截断内容必须记录截断原因。
- 不合规的 source type/content scope/rights basis 组合被阻断。

## 10. 当前仍需要需求方决定的问题

以下选择会实质影响下一阶段，不应由开发方擅自假设：

1. **LLM 供应商和模型**
   - OpenAI、Claude、国内模型或多供应商路由。
   - 需要指定首选模型、备用模型和允许的成本范围。

2. **LLM 凭据和费用归属**
   - API Key 由谁提供和维护。
   - 日/月 token 或费用上限。
   - Key 只应放在本地/部署环境变量，不应粘贴到聊天或提交仓库。

3. **首批 5–10 篇测试论文**
   - 由需求方指定，还是由系统从 PubMed 选取后交人工确认。
   - 应覆盖哪些产品领域和研究类型。

4. **MVP 内容范围**
   - 是否只使用 PubMed 摘要。
   - 是否下一阶段就接入 PMC Open Access。
   - 暂不建议接入需要付费或登录的出版社全文。

5. **研究领域和检索式**
   - 首批正式领域 code、中文名、PubMed query。
   - 查询时间窗、每日最大获取量和去重回看周期。

6. **相关性阈值**
   - 设计文档给出默认阈值，但尚未用真实标注样本确认。
   - 需要明确人工审核灰区是否接受默认值。

7. **人工审核与批次确认载体**
   - CLI、简单本地 Web 页面，还是后续正式后台。
   - 确认人身份如何记录和认证。

8. **正式发布目标**
   - 本地静态目录、内网服务器、对象存储或公开网站。
   - 发布 URL、权限和回滚策略。

9. **调度频率**
   - 每日还是每周。
   - 运行时间、时区、失败重试和通知方式。

10. **数据保留期限**
    - `source_records`、`content_snapshots`、验证响应指纹和日志分别保留多久。

11. **生产数据库时点**
    - 继续使用 SQLite 到何种规模。
    - 何时迁移 PostgreSQL 并引入 Alembic。

12. **Git 与交付流程**
    - 当前目录不是 Git 仓库。
    - 是否现在初始化 Git、建立首次基线提交以及确定远程仓库。

## 11. 下一步建议，但暂时不要执行

建议下一阶段只完成“相关性筛选最小闭环”，不要同时实现 Analysis、Report、Web 和 Scheduler：

1. 需求方确认 LLM 供应商、模型、预算和本地环境变量名。
2. 确认首批 5–10 篇人工测试论文及预期 relevant/irrelevant/uncertain 标签。
3. 实现 Prompt YAML 加载、版本哈希注册和 JSON Schema 校验。
4. 只实现 `CONTENT_READY → SCREENING → ANALYZING/IRRELEVANT/REVIEW_PENDING`。
5. 保存 `ai_runs`、Prompt 版本、输入哈希和相关性结果。
6. 先运行离线 mock/contract 测试，再使用真实 LLM 对 5–10 篇测试集运行。
7. 输出错误样本和人工复核结果；通过后再决定是否进入 Analysis Agent。

本快照生成后，应继续保持暂停状态，直到需求方对第 10 节中的关键问题给出决定。
