# AI-Literature-Intelligence-System 项目迁移交接文档

> 文档版本：1.0
> 
> 编写日期：2026-11-24（Asia/Shanghai）
> 
> 适用对象：接替 Codex 的 WorkBuddy / CodeBuddy / 其他 AI 开发助手

## 0. 阅读说明与证据边界

这份文档基于当前仓库、仓库内设计文档、配置、测试、静态站点产物、Git 历史，以及工作区外的原始需求说明书 `D:\AAA gpt files\AI科研文献速递系统——开发需求说明书.md` 编写。

当前会话无法读取此前完整的 Codex 历史对话记录，也没有发现包含微信公众号完整名单或历史聊天导出的文件。因此，凡是仓库不能证明、且本文件未明确标为“已确定”的内容，均不得当作已实现事实。特别是公众号名单、此前逐条页面修改意见、某些未落盘的检索式和模型选择，仍需项目负责人补充。

状态约定：

- **已实现**：当前源码/构建产物存在，并有测试或真实产物支持。
- **配置/接口已存在**：已经落盘，但不等于已接入完整运行链。
- **设计中**：只在设计文档或需求说明中出现，不能当作已完成。
- **待确认**：现有材料不足以确认。

## 1. 项目定位、背景与目标

### 1.1 建设背景

项目原始定位是“AI科研文献速递助手”，面向保健食品、生物科技和生命科学研发人员。目标是把最新科研文献的发现、元数据核验、摘要/正文证据提取、研究质量控制和面向研发人员的速递展示串起来，降低研发人员筛读大量论文的时间成本，并形成可以持续更新的科研情报库。

首阶段的实际技术路线已经收敛为：

```text
PubMed 检索
  → 元数据和摘要获取
  → 标准化、PMID/DOI/标题去重
  → PMID/DOI/URL 验证
  → 内容快照权利/结构门禁
  → 导出结构化 AI 任务包
  → 外部 AI（此前由 Codex 人工执行）筛选与分析
  → 结果契约校验、证据逐字校验
  → 导入数据库
  → Quality Gate
  → 生成静态网站预览
  → 人工确认批次
  → 生成 published 制品并人工触发 GitHub Pages
```

这是一个“证据可追溯、人工可控发布”的科研文献情报系统，而不是医疗诊断工具、自动治疗建议工具或不经审核的内容生成器。

### 1.2 目标用户

- 保健食品、营养品和功能食品研发人员。
- 生物科技和生命科学企业的科研/产品团队。
- 需要跟踪机制、靶点、标志物、天然成分、营养干预和人体证据的研究人员。
- 后续可能扩展到高校/实验室动态、PI 追踪和产品研发辅助决策，但这些都尚未接入。

### 1.3 目标使用场景

1. 按四个研究领域发现近期开题价值较高的研究。
2. 从多条检索入口合并候选，保留论文命中的多个领域关系。
3. 优先显示新机制、新靶点、新标志物、新技术、新方法、新模型、首次人体证据、营养/天然成分和临床/转化价值。
4. 让研发人员从摘要级证据快速理解研究设计、核心发现、局限性和谨慎的研发启示。
5. 通过 PMID、DOI 和可打开的 PubMed 页面保证来源可追溯。
6. 在网页发布前保留批次级人工确认，避免 AI 草稿直接上线。

### 1.4 原始需求与当前实现的差异

原始需求说明书还提出 ScienceDirect、DOI 数据库、其他可信数据库、微信公众号、高校动态、PI 追踪、定时更新、知识库和科研问答等方向。当前仓库实际只实现/接入了 PubMed 主路径、Crossref DOI 核验、摘要内容门禁、Codex 结果导入和静态网站发布；其余均为后续方向或待确认事项。

## 2. 四大研究领域与检索规则

### 2.1 全局规则

权威配置文件为 `config/domains.v1.json`、`config/update.v1.json`、`config/journal_tiers.v0.1.json` 和 `config/site.v2.json`。当前配置状态是 `v0.1_trial_pending_domain_review`，说明领域同事审核仍未完成。

全局关注：近期研究、高水平综合期刊和领域重要期刊、新发现/机制/靶点/标志物/技术/方法/模型/评价指标、转化研究、人体研究、营养干预、动物机制、细胞机制、天然成分/植物来源/营养活性成分/功能食品干预。

- 营养和天然产物是强优先级，但不是硬筛选条件。
- 优先成年人，尤其约 30 岁以上、中年和中老年人；儿童、青少年和年轻健康运动员降优先级，但 PubMed 不做硬过滤。
- 允许 `human`、`animal`、`in_vitro`、`translational`，必须在分析和展示中明确证据层级。
- 一篇论文可属于多个领域；保存一个主领域 `primary_domain` 和零个或多个次领域 `secondary_domains`，并保存 `cross_domain_value`。
- 期刊等级和影响因子用于排序，不是硬排除条件。
- 边界不清时不得强行归类，应保留 `uncertain` 并进入人工审核候选。

#### 四类检索入口

每个领域默认生成四类入口，4 个领域共 16 条查询：

1. `journal_radar`：全局 S-tier 或领域 A-tier 且与领域高度相关，单领域最多 50。
2. `frontier_radar`：机制、靶点、通路、标志物、多组学、单细胞、空间转录组、类器官、新模型、新方法、新技术、首次人体，最多 60。
3. `clinical_radar`：RCT、临床试验、Meta-analysis、系统综述、随机/安慰剂、首次人体、队列、纵向、干预，最多 50。
4. `business_relevance_radar`：营养、饮食、补充剂、营养保健品、功能食品、益生菌/益生元/合生元/后生元、植物化学物、黄酮、植物提取物、草本、天然产物、中药，最多 60。

生产查询模板：

```text
({DOMAIN_BLOCK}) AND ({ROUTE_BLOCK}) AND
hasabstract NOT (editorial[pt] OR comment[pt] OR letter[pt] OR news[pt] OR retracted-publication[pt])
{DOMAIN_ADDITIONAL_FILTER}
```

检索窗口为最近 45 天，日期字段 `edat`，排序 `relevance`，单领域硬上限 200。45 天是为了覆盖 PubMed 收录延迟，不意味着每次都重复分析所有论文；历史 PMID、DOI 和处理状态必须去重。

选题排序权重（总分 100）：领域相关性 25、前沿价值 25、期刊/来源质量 15、证据强度 15、营养/天然产物相关性 10、时效性 10。跨领域最多加 2 分，仅用于平分或小幅排序，不得压过严重的相关性/证据问题。

候选目标：AI shortlist 20–30 篇，人审池 5–10 篇，最终速递建议 3–6 篇；均不强制凑数，不为达到固定篇数降低质量。

### 2.2 肠胃/肠道健康 `gut_health`

关注胃肠道功能、肠道菌群、消化吸收、肠屏障、排便功能、胃肠症状以及宿主健康效应；包括益生菌、益生元、合生元、后生元、膳食成分和植物活性成分。

重点主题：

- gut microbiome / microbiota；intestinal barrier / permeability。
- gut microbial metabolites；host–microbiome interaction。
- gut–brain axis、gut–muscle axis、gut–immune axis。
- probiotics、prebiotics、synbiotics、postbiotics。
- microbiome-related nutrition intervention、microbiome multi-omics。
- 新型肠道微生态干预、新机制、新技术、新评价方法。

当前实际 PubMed concept block：

```text
("Gastrointestinal Microbiome"[mesh] OR gut-microbiom*[tiab] OR gut-microbiota[tiab]
 OR intestinal-microbiom*[tiab] OR intestinal-microbiota[tiab]
 OR intestinal-barrier[tiab] OR gut-barrier[tiab]
 OR intestinal-permeability[tiab] OR microbial-metabolite*[tiab]
 OR host-microbiome[tiab] OR gut-brain-axis[tiab] OR gut-muscle-axis[tiab]
 OR gut-immune-axis[tiab]
 OR ((probiotic*[tiab] OR prebiotic*[tiab] OR synbiotic*[tiab] OR postbiotic*[tiab])
     AND (gut[tiab] OR intestin*[tiab] OR gastrointestinal[tiab] OR host-health[tiab])))
```

排除：只有食品发酵、生产工艺或菌株工业性能、没有肠道/宿主健康结果；或者只是出现 `probiotics`/`microbiota`，但主题与肠道健康没有实质关系。关键词命中不等于相关。

### 2.3 延衰/健康衰老 `healthy_aging`

关注衰老机制、生物年龄和衰老标志物、细胞衰老、健康寿命以及器官衰老，优先营养、天然成分、新机制和转化价值。

重点主题：aging mechanisms、biomarkers of aging / biological age、cellular senescence、longevity、healthspan、mitochondrial dysfunction、inflammaging、epigenetic aging、autophagy、proteostasis、nutrient sensing、reproductive/ovarian aging、brain/cognitive aging、ocular/visual aging、muscle aging/sarcopenia、skin aging，以及具有明确衰老机制或标志物意义的器官衰老。

当前实际 PubMed concept block：

```text
("Aging"[mesh] OR aging[ti] OR ageing[ti] OR geroscience[tiab]
 OR longevity[tiab] OR healthspan[tiab] OR cellular-senescence[tiab]
 OR senolytic*[tiab] OR senomorphic*[tiab] OR biological-age[tiab]
 OR epigenetic-clock*[tiab] OR inflammaging[tiab] OR immunosenescence[tiab]
 OR ovarian-aging[tiab] OR reproductive-aging[tiab]
 OR cognitive-aging[tiab] OR brain-aging[tiab] OR ocular-aging[tiab]
 OR muscle-aging[tiab] OR skin-aging[tiab])
```

当前没有额外排除规则；仍需按证据层级区分人体、动物、细胞和机制研究，不能把“延长寿命”或“抗衰老”从标题直接推断为人体功效。

### 2.4 肌骨关节 `musculoskeletal_health`

关注骨、关节、软骨和肌肉功能，尤其是骨质疏松、骨关节炎、肌少症，以及营养或天然成分干预；纯手术、关节置换、假体和植入器械研究默认排除或降级。

重点主题：bone health、joint health、cartilage、muscle、sarcopenia、osteoporosis、osteoarthritis、skeletal muscle、muscle aging，以及中草药、植物成分、天然提取物、营养成分、功能食品、作用机制和人体干预。

当前实际 PubMed concept block：

```text
(bone-health[tiab] OR joint-health[tiab] OR cartilage[tiab]
 OR sarcopenia[tiab] OR skeletal-muscle[tiab] OR muscle-aging[tiab]
 OR osteoporosis[tiab] OR osteoarthritis[tiab] OR musculoskeletal[tiab])
```

当前额外过滤：

```text
NOT (((arthroplasty[ti] OR joint-replacement[ti] OR prosthes*[ti]
 OR orthopedic-implant*[ti] OR surgical-technique*[ti])
 NOT (nutrition*[tiab] OR diet*[tiab] OR supplement*[tiab]
 OR natural-product*[tiab] OR biomarker*[tiab] OR mechanis*[tiab])))
```

含营养、天然产物、标志物或机制价值的手术/器械相关论文不一定排除，但应人工复核其是否真正服务于领域目标。

### 2.5 免疫 `immune_health`

关注健康免疫功能、免疫稳态、炎症调节、免疫衰老、黏膜免疫、免疫代谢，以及营养/天然成分/功能食品与免疫的关系。

重点主题：healthy immune function、immune homeostasis、immune regulation、inflammatory regulation/resolution、immunosenescence、mucosal immunity、immunometabolism、metabolic–immune interaction、nutrition and immunity、天然/植物成分、功能食品、新机制和人体研究。

当前实际 PubMed concept block：

```text
(immune-homeostasis[tiab] OR immune-regulat*[tiab]
 OR immunoregulat*[tiab] OR healthy-immune[tiab]
 OR inflammat*-regulat*[tiab] OR inflammat*-resolut*[tiab]
 OR immunosenescence[tiab] OR mucosal-immun*[tiab]
 OR immunometabol*[tiab] OR metabolic-immune[tiab]
 OR nutrition*-immun*[tiab] OR diet*-immun*[tiab])
```

纯严重疾病药物治疗研究降优先级；如果揭示重要且可转化的免疫机制，可保留。不能因为“免疫”一词出现就纳入健康免疫方向。

### 2.6 文献类型、质量与排除规则

基础过滤：必须尽量有摘要；排除 Editorial、Comment、Letter、News、Retracted Publication。临床入口优先 RCT、clinical trial、systematic review、meta-analysis、重要队列和纵向研究；机制入口允许高质量动物、细胞和转化研究，但在结果中显式标注。

质量要求：

- 研究质量：优先设计清楚、样本/对照/干预可判断、结论与数据相符的研究。
- 时效性：优先最近 0–14 天，其次 15–30 天，再次 31–45 天，分别得 10/7/4 分。
- 创新性：新机制、新靶点、新标志物、新技术/方法/模型、首次人体或新干预优先。
- 临床价值：人体干预、临床证据、可转化机制和研发启示优先，但不能以“有应用想象”替代证据。
- 期刊等级/IF：作为质量分和排序信号，不是硬过滤。
- 不相关、仅背景提及、关键词陷阱、来源撤回、链接无法验证、关键数据无法由输入证据支持的候选应排除或进入人工审核。

### 2.7 交叉检索规则

跨领域关系保留而不重复分析同一论文。典型交叉包括：

- 肠道 × 免疫：gut–immune axis、黏膜免疫、炎症调节、微生物代谢物与免疫稳态。
- 肠道 × 肌骨：gut–muscle axis、微生态与肌少症/骨代谢。
- 肠道 × 延衰：菌群、代谢物、屏障和炎症与健康衰老/生物年龄。
- 免疫 × 延衰：immunosenescence、inflammaging、炎症消退、免疫代谢。
- 延衰 × 肌骨：肌肉衰老、肌少症、骨质疏松、骨骼肌线粒体和健康寿命。
- 肌骨 × 免疫：骨免疫、炎症性骨关节疾病、免疫调节与组织修复。

系统应以一个主领域生成一条报告条目，同时显示次领域和跨领域价值；不要为每个领域复制同一篇论文造成重复推荐。

## 3. 微信公众号及其他信息源

### 3.1 当前可确认的信息

仓库没有公众号名单数据库、抓取脚本、公众号文章快照或官网映射表。当前唯一明确落盘的外部来源配置是 `config/update.v1.json` 中的：

```json
{
  "name": "Aging 微信公众号等外部来源",
  "role": "benchmark_and_supplementary_discovery_reference",
  "automatic_ingestion": false
}
```

因此目前不能诚实地提供“此前收集的各领域微信公众号完整名单”。现阶段不得把“公众号等外部来源”误写成已接入来源，也不得自行补写公众号名称。

### 3.2 原始需求中的信息源范围

原始需求说明书提出：

- 微信公众号：科研解读、企业研发动态、行业趋势；输出标题、来源、发布时间、核心观点、原文链接。
- 高校科研动态：高校官网新闻、实验室成果、最新发表成果。
- PI 追踪：用户输入 PI 姓名、研究方向、机构，系统追踪最新论文、科研动态和研究成果。
- 原始优先数据库还提到 PubMed、ScienceDirect、DOI 数据库和其他可信科研数据库。

上述公众号、高校和 PI 模块当前均未接入仓库。没有发现微信公众号官方 API 配置、爬虫、RSS、搜索结果缓存或内容表。

### 3.3 内容获取限制与此前可确认的结论

从项目设计和需求背景可以确认，公众号内容获取存在以下风险：微信内容通常需要登录/客户端上下文，文章页面反爬和访问限制较强；搜索引擎对公众号文章索引不完整，搜索结果可能只有标题或摘要，且同名账号、转载和失效链接难以可靠区分。仓库没有保留具体尝试日志，因此“曾经用过哪些具体抓取方式以及每种方式的效果”属于待确认，不能伪造。

建议后续把公众号作为“线索/趋势来源”，而不是直接当作学术证据：

1. 记录公众号名称、主体、文章标题、发布日期、原文链接、获取时间和获取方式。
2. 对公众号观点中的论文、DOI、PMID、研究团队和关键词反查 PubMed/Crossref。
3. 最终文献结论只引用可验证的原始论文证据；公众号文章只作为 discovery/reference 标签。
4. 不能稳定获取正文时，保存公开搜索片段或人工提供的链接，并标记 `content_unverified`。
5. 加入人工审核、合规、版权和频率限制，不绕过微信登录或反爬保护。

待项目负责人补充：四领域公众号完整名单、账号主体/官网、官网 URL、关注理由、已经尝试的获取方案、是否允许人工粘贴文章内容，以及公众号条目是否进入同一站点或独立页面。

## 4. 当前技术架构

### 4.1 技术栈与依赖

- Python `>=3.11`；当前工作区记录为 Python 3.12.13。
- SQLAlchemy `>=2.0,<3.0`；当前状态记录为 2.0.52。
- SQLite；数据库目录自动创建，外键约束启用。
- Python 标准库：`urllib`、`xml.etree.ElementTree`、`json`、`hashlib`、`argparse`、`html` 等。
- PubMed NCBI E-utilities：ESearch JSON、EFetch XML。
- Crossref `/works/{doi}`： DOI 元数据验证。
- 静态 HTML/CSS/JavaScript：站点由 Python 生成，不是 FastAPI/React 应用。
- GitHub Actions + GitHub Pages：只部署经过 manifest 和 publication status 校验的 `site/`。
- 没有发现 FastAPI、Alembic、LLM SDK、向量数据库或全文搜索引擎的已接入依赖。

### 4.2 目录树

```text
AI-Literature-Intelligence-System/
├── .env.example
├── .gitignore
├── .github/workflows/deploy-pages.yml
├── PROJECT_STATUS.md
├── PROJECT_HANDOVER.md
├── README.md
├── pyproject.toml
├── config/
│   ├── domains.v1.json
│   ├── journal_metrics.v1.json
│   ├── journal_tiers.v0.1.json
│   ├── prompts.v1.yaml
│   ├── site.v1.json
│   ├── site.v2.json
│   └── update.v1.json
├── data/
│   ├── candidate_runs/
│   ├── codex_results/
│   ├── codex_tasks/
│   ├── previews/
│   ├── site_preview/
│   └── site_previews/                 # 当前本地未跟踪生成物
├── docs/
│   ├── deployment/github-pages.md
│   └── design/
│       ├── README.md
│       ├── 01-database-er-design.md
│       ├── 02-agent-state-flow.md
│       └── 03-prompt-engineering-spec.md
├── site/                              # 当前发布静态站点制品
│   ├── index.html
│   ├── archive/
│   ├── assets/
│   ├── data/
│   ├── domains/
│   └── papers/
├── src/literature_intelligence/
│   ├── config.py
│   ├── config_validation.py
│   ├── collectors/pubmed.py
│   ├── db/{base.py,init_db.py,models.py,sync_domains.py}
│   ├── pipeline/
│   │   ├── ingestion.py
│   │   ├── content_readiness.py
│   │   ├── pubmed_candidate_dry_run.py
│   │   ├── pubmed_candidate_metadata.py
│   │   ├── selected_candidate_ingestion.py
│   │   ├── codex_task_export.py
│   │   ├── codex_screening.py
│   │   ├── codex_mvp_preview.py
│   │   ├── codex_results_import.py
│   │   ├── static_site.py
│   │   ├── site_artifact.py
│   │   └── publication_release.py
│   └── services/{normalization.py,paper_ingest.py,verification.py}
└── tests/                             # 20 个测试模块
```

### 4.3 核心模块职责

| 模块 | 作用 |
|---|---|
| `collectors/pubmed.py` | ESearch/EFetch、限频、429/部分 5xx 重试、XML 解析、标题/摘要/期刊/日期/作者/PMID/DOI/PMCID 获取 |
| `services/normalization.py` | NFKC、HTML entity、空白、标题、DOI、PMID、PMCID、ORCID、日期、作者和 publication type 标准化；生成摘要 snapshot |
| `services/paper_ingest.py` | PMID/DOI 去重、标题+年份疑似冲突标记、原始记录哈希、论文/作者/内容快照落库 |
| `services/verification.py` | PMID 精确验证、Crossref DOI 验证、安全 URL HEAD/GET 验证和追加式验证记录 |
| `db/models.py` | 19 张表的 SQLAlchemy 模型、状态和发布守卫 |
| `pipeline/ingestion.py` | 查询→ESearch→EFetch→标准化→去重→摘要快照→任务→验证的主路径 |
| `pipeline/content_readiness.py` | 内容结构、长度、哈希、来源和权利组合门禁，推进 `CONTENT_READY` 等状态 |
| `pipeline/pubmed_candidate_dry_run.py` | 根据四领域×四入口生成实际查询、统计命中、跨入口去重和历史排除；只读 |
| `pipeline/pubmed_candidate_metadata.py` | 候选元数据包和路由排名 |
| `pipeline/selected_candidate_ingestion.py` | 把选定候选批量交给真实 ingestion |
| `pipeline/codex_task_export.py` | 从 `CONTENT_READY` 数据导出带哈希和 prompt snapshot 的人工 AI 任务包 |
| `pipeline/codex_screening.py` | 校验人工生成的筛选结果契约、输入哈希和领域 code |
| `pipeline/codex_mvp_preview.py` | 校验 screening/analysis/classification/report/quality gate，并渲染单批次 HTML 预览 |
| `pipeline/codex_results_import.py` | 幂等导入已校验 Codex 结果、记录 prompt/AI run/分析/证据/分类/报告/状态事件 |
| `pipeline/static_site.py` | 从数据库累计生成首页、领域页、论文页、归档、JSON 数据和 SHA-256 manifest |
| `pipeline/site_artifact.py` | 校验 manifest 文件集合、哈希和 preview/published 状态 |
| `pipeline/publication_release.py` | 人工确认批次和 Pages 成功后的 finalize，维护发布审计和状态守卫 |
| `config/prompts.v1.yaml` | draft 状态的 Screening、Analysis、Classification、Report、Quality Gate、JSON repair 模板和 schema |
| `config_validation.py` | 校验四领域、更新、期刊 tiers、站点配置的一致性 |

### 4.4 数据流与状态

数据库主表包括 `research_domains`、`search_queries`、`pipeline_runs`、`source_records`、`papers`、`content_snapshots`、`paper_identifiers`、`authors`、`paper_authors`、`paper_tasks`、`verification_records`、`task_state_events`、`prompt_versions`、`ai_runs`、`paper_analyses`、`analysis_evidence`、`paper_categories`、`publication_batches`、`report_items`。

已实现状态主线：

```text
DISCOVERED → NORMALIZED → VERIFYING → VERIFIED
VERIFIED → CONTENT_FETCHING → CONTENT_READY
```

异常路径包括 `RETRY_WAIT`、`REVIEW_PENDING`、`REJECTED`。AI 结果导入和发布阶段有更细的任务/批次守卫；外部 AI 调用本身没有在仓库内自动执行器。

去重规则：PMID 或 DOI 精确匹配复用论文；标题+年份只提示疑似重复，不自动合并；不同论文共享 PMID/DOI 时抛出冲突；重复导入相同 Codex 文件幂等。

内容快照只保存摘要/合规片段，不抓取或保存付费全文。快照包含来源类型、范围、sections、URL、rights basis、截断标记、获取时间和 SHA-256。截断必须有原因，来源/权利组合不合规则拒绝。

### 4.5 API、外部服务与配置

`.env.example` 只提供变量名，不要提交真实值：

```text
DATABASE_URL=sqlite:///data/app.db
NCBI_API_KEY=
NCBI_EMAIL=
NCBI_TOOL=ai_literature_intelligence
CROSSREF_EMAIL=
```

NCBI 无 API Key 时约 0.34 秒限频；生产建议设置 `NCBI_EMAIL`，高频再设置个人 `NCBI_API_KEY`。Crossref email 用于礼貌标识。当前没有 LLM API key 配置，也不应把任何 key/token/password 写入本文件或 Git。

### 4.6 本地运行、调试和测试

在项目根目录 PowerShell 执行：

```powershell
./.venv/Scripts/python.exe -m literature_intelligence.config_validation
./.venv/Scripts/python.exe -m unittest discover -s tests -v
./.venv/Scripts/python.exe -m literature_intelligence.db.init_db
./.venv/Scripts/python.exe -m literature_intelligence.db.sync_domains `
  --database-url sqlite:///data/app.db `
  --config config/domains.v1.json
```

单条真实 PubMed 烟雾采集：

```powershell
./.venv/Scripts/python.exe -m literature_intelligence.pipeline.ingestion `
  --query 'probiotics[Title]' `
  --domain-code gut_health `
  --domain-name 肠道健康 `
  --retmax 1 `
  --database-url sqlite:///data/smoke.db
```

四领域只读 dry-run：

```powershell
./.venv/Scripts/python.exe -m literature_intelligence.pipeline.pubmed_candidate_dry_run `
  --run-date 2026-11-12 `
  --history-database-url sqlite:///data/gut_health_mvp.db `
  --output data/candidate_runs/2026-11-12_v01_dry_run.json
```

Codex/外部 AI 接力：

```powershell
./.venv/Scripts/python.exe -m literature_intelligence.pipeline.codex_task_export `
  --database-url sqlite:///data/app.db `
  --batch-id <batch-id> `
  --domain-code gut_health `
  --output data/codex_tasks/<name>.json

./.venv/Scripts/python.exe -m literature_intelligence.pipeline.codex_screening `
  --task-package data/codex_tasks/<name>.json `
  --results data/codex_results/<name>_screening.json

./.venv/Scripts/python.exe -m literature_intelligence.pipeline.codex_mvp_preview `
  --task-package data/codex_tasks/<name>.json `
  --screening-results data/codex_results/<name>_screening.json `
  --mvp-results data/codex_results/<name>_mvp.json `
  --output data/previews/<name>.html
```

导入、生成站点、校验：

```powershell
./.venv/Scripts/python.exe -m literature_intelligence.pipeline.codex_results_import `
  --database-url sqlite:///data/app.db `
  --task-package data/codex_tasks/<name>.json `
  --screening-results data/codex_results/<name>_screening.json `
  --mvp-results data/codex_results/<name>_mvp.json `
  --prompt-spec config/prompts.v1.yaml `
  --edition-date 2026-11-12

./.venv/Scripts/python.exe -m literature_intelligence.pipeline.static_site `
  --database-url sqlite:///data/app.db `
  --site-config config/site.v2.json `
  --journal-metrics config/journal_metrics.v1.json `
  --output-dir site

./.venv/Scripts/python.exe -m literature_intelligence.pipeline.site_artifact `
  --site-dir site `
  --require-publication-status preview
```

发布必须先人工确认并在 Pages 成功后 finalize：

```powershell
./.venv/Scripts/python.exe -m literature_intelligence.pipeline.publication_release `
  --database-url sqlite:///data/app.db confirm `
  --batch-id <batch-id> --preview-site-dir site `
  --confirmed-by <reviewer> --confirmation-note '批准正式发布'

./.venv/Scripts/python.exe -m literature_intelligence.pipeline.static_site `
  --database-url sqlite:///data/app.db --publication-status published --output-dir site

./.venv/Scripts/python.exe -m literature_intelligence.pipeline.publication_release `
  --database-url sqlite:///data/app.db finalize `
  --batch-id <batch-id> --published-site-dir site `
  --site-url https://<owner>.github.io/<repository>/
```

### 4.7 GitHub 与 GitHub Pages

当前 remote 目标是 `CeciliaLiao180/AI-Literature-Intelligence-System`，本地 `main` 跟踪 `origin/main`。当前 HEAD 为 `f4cb341 Publish 2026-11-12 four-domain literature digest`。工作流 `.github/workflows/deploy-pages.yml` 仅 `workflow_dispatch`，权限是 contents:read、pages:write、id-token:write；部署前要求 `site/data/site-manifest.json` 的 `publication_status=published`，因此不能直接把 preview 部署上线。

更新流程：

1. 修改前检查 `git status`，不要覆盖他人未提交改动。
2. 在本地完成配置校验和全量测试。
3. 运行候选、AI 任务包、结果校验/导入和静态站点生成。
4. 检查 `site/` 页面、链接、manifest、发布日期、批次状态和累计归档。
5. 由负责人完成批次确认；确认后重新生成 published 制品。
6. 提交并推送代码/站点变更，人工触发 GitHub Actions。
7. 验证 GitHub Pages 实际 URL 后才运行 `publication_release finalize`。

当前没有自定义域名/CNAME。站点目录是生成物，修改页面优先修改 `src/.../static_site.py`、配置或数据，不要只手改 `site/*.html`，否则下一次构建会覆盖。

## 5. 已完成、进行中和未实现

### 5.1 已完成并有仓库证据

- PubMed ESearch/EFetch 真实采集、XML 解析和限频/有限重试。
- 论文、作者、标识符、摘要快照和任务状态落库。
- PMID/DOI/标题+年份去重策略及冲突保护。
- PMID、DOI、PubMed URL 的验证记录；安全 URL 校验。
- 摘要内容门禁、哈希和权利检查。
- 四领域配置、16 条候选路由生成、45 天窗口、候选去重与历史排除代码。
- Codex 任务导出、screening 校验、MVP 结果校验、证据逐字匹配、已验证 URL 限制、幂等导入。
- 数据库累计生成静态站点：首页、四个 canonical 领域页、兼容 alias 页、论文详情页、归档页、共享 CSS/JS、site-data 和 manifest。
- 批次人工确认、preview/published 守卫和 GitHub Pages 手工部署工作流。
- 自动化测试覆盖数据库、PubMed、标准化、验证、ingestion、内容门禁、Codex 契约、静态站点、发布和 GitHub Actions。

### 5.2 当前真实网站状态

`site/data/site-manifest.json` 显示：

- 生成时间：2026-11-12。
- `publication_status=published`。
- 4 个领域、12 篇论文、2 个批次、2 个日期归档。
- 批次 `b031d6db-5d87-4506-96c6-5364ad63606a`：2026-09-07，4 篇。
- 批次 `b9d2568e-3d17-4f65-b006-8b691907f453`：2026-11-12，8 篇，站点最新批次。
- 论文详情页为 `site/papers/4.html` 至 `site/papers/15.html` 的非连续实际编号集合。
- 每篇展示英文原题、AI 标题、作者/期刊/日期、分析结构、证据绑定的核心发现、局限、研发启示、PubMed/DOI 已验证链接；网页会区分动物/机制等证据边界。

注意：`site/data/site-data.json` 的领域对象当前不应直接用来推断每领域文章数量；以 manifest 的总数、batch paper_ids 和实际 HTML 为准。部分 PubMed JournalIssue 日期可能晚于站点生成日，代码会保留原值并写入 `FUTURE_PUBLICATION_DATE` 警告。

### 5.3 正在开发/部分落地

- V0.1 四领域检索配置已经落盘，但 README 明确提示新路由尚未接入下一批真实 PubMed 采集；接入前必须做 dry-run 和人工检查。
- `config/prompts.v1.yaml` 有 6 个 draft 模板和 schema；Codex 文件契约校验已实现，但通用 prompt loader、模型路由、评测注册和自动调用未实现。
- 发布流程已有代码和测试，实际线上发布仍需人工触发和人工确认。
- `data/site_previews/` 是工作区当前存在但未跟踪的本地产物，不应在本次交接中擅自清理或纳入提交。

### 5.4 尚未实现

- LLM gateway、Screening/Analysis/Classification/Report/Quality Gate 自动 worker。
- Prompt 版本注册、激活、回滚、Golden Dataset 评测流水线。
- PMC/开放获取/出版社/人工片段采集器和全文处理。
- 定时调度器（当前 `schedule.enabled=false`）、失败重试 worker、租约 worker。
- FastAPI、数据库迁移/Alembic、前端审核界面、用户权限、反馈系统。
- ScienceDirect 等其他学术源、微信公众号、高校动态、PI 追踪。
- 全文搜索、向量库、知识图谱、科研问答、产品决策辅助。
- CI/CD、日志、指标面板、告警和容器化。

## 6. 重要设计决策与历史需求

### 6.1 已确定的原则

- 先保证事实忠实、证据可追溯，再追求语言流畅和覆盖量。
- 论文/API/网页文本全部是“不可信数据”，不能把论文中的指令注入到 system prompt。
- 未报告字段返回 `null`，不能猜样本量、剂量、统计值、机制或因果关系。
- 动物/体外/观察性研究不能改写成人体功效；相关性不能升级为因果性。
- 每条关键发现必须带来自输入快照的最短充分原文证据；自动发布只接受 direct evidence。
- 报告链接只能复制验证服务提供的 URL，模型不能生成并直接展示新链接。
- AI 预览不等于正式发布；必须有批次级人工确认和 manifest 哈希守卫。
- 旧批次和旧日期归档不能因生成新一期被删除。
- 领域等级和 IF 是排序信号，不是硬门槛；不为凑固定篇数降低质量。
- 网站面向研发决策支持，不能生成诊断、治疗建议或功效承诺。

### 6.2 Prompt 设计约束

设计文档 `docs/design/03-prompt-engineering-spec.md` 将每个 Prompt 分为 System policy、Task instruction、Output contract、Runtime input 四层。Prompt key 为 `paper_relevance`、`paper_analysis`、`paper_classification`、`report_composition`、`report_quality_gate`、`json_repair`；核心温度 0–0.1，报告不高于 0.2。结构化结果要经过 JSON、schema、枚举、长度、证据匹配和业务规则五层校验；JSON repair 最多一次，只修格式不改事实。

质量门槛设计包括：schema 通过率 ≥99%、关键数字原文支持率 100%、direct evidence 覆盖率 100%、非人体误写人体功效 0 次、人工审核后重大事实错误 0 次。正式生产还需要 5–10 篇小测试集和至少 50 篇 Golden Dataset；当前没有证据表明这些评测已经完成。

### 6.3 尚未最终确定的技术方案

- 采用哪家 LLM/provider、是否由 WorkBuddy/CodeBuddy 直接调用模型，还是继续生成任务包由人工/外部系统执行。
- 微信公众号的合法、稳定、可审计获取方式。
- 是否接入 PMC/出版社全文，版权/rights basis 如何落地。
- PostgreSQL/Alembic 和生产部署环境。
- 调度频率、自动分析和自动发布的开启条件。
- 站点是否加入公众号/高校/PI 独立栏目。

## 7. 当前问题清单

1. **文献质量和相关性**：关键词命中可能造成相关性偏差；各领域配置仍标记待领域审核；摘要级输入无法判断完整方法、效应量和偏倚。
2. **覆盖和准确性**：当前主要是 PubMed；ScienceDirect、其他数据库、PMC/出版社全文未接入；45 天窗口和各路由上限可能漏检；检索式仍需真实批次评估。
3. **微信公众号**：没有名单、抓取、缓存、来源表或合规策略；微信反爬、登录和搜索引擎索引不完整导致不可自动稳定接入。
4. **摘要/信息提取**：只保存合规摘要快照；缺少样本量、剂量、对照、周期时必须留空；部分日期有未来期次警告；没有全文级证据校验。
5. **网页可读性**：当前已有静态站点，但历史对话中逐条页面修改意见无法从本次可见材料恢复；需要负责人重新确认字号、排序、中文标题风格、证据/研发启示层次和移动端要求。
6. **自动化稳定性**：调度关闭，AI/重试/租约 worker 未实现；发布依赖人工操作；外部 API 限频、429、5xx、Crossref/URL 临时错误仍需运营监控。
7. **数据与版本**：SQLite 适合 MVP，不是多人生产数据库；数据库文件被 `.gitignore` 忽略；本地未跟踪 `data/site_previews/` 可能造成工作区混淆；Prompt 仍为 draft。
8. **历史资料缺失**：公众号完整名单、Codex 对话中的未落盘需求、曾尝试的抓取方法及页面意见需补充。
9. **当前测试基线有一个已知失败**：2026-11-24 执行 70 个 unittest 时 69 个通过、1 个失败，失败为 `tests/test_publication_release.py::test_confirm_generate_publish_and_finalize_are_audited`，断言 `state_counts["PUBLISHED"] == 4`，实际为 12。该测试会复制 `data/gut_health_mvp.db` 并假定其中只含 4 篇批次数据，而当前数据库/站点数据已经累计到 12 篇；接手者应先确认测试 fixture 是否应更新为独立的 4 篇基准库，再修复测试或数据，不要为了让测试通过删除现有发布数据。

## 8. WorkBuddy / CodeBuddy 迁移指南

### 8.1 新 AI 的首读顺序

1. 本文件 `PROJECT_HANDOVER.md`。
2. `README.md`：当前运行命令和发布安全边界。
3. `PROJECT_STATUS.md`：早期实现级别、数据库表和已知未实现范围；若与当前仓库冲突，以当前源码/manifest/Git HEAD 为准。
4. `docs/design/README.md`、`01-database-er-design.md`、`02-agent-state-flow.md`、`03-prompt-engineering-spec.md`。
5. `config/domains.v1.json`、`config/update.v1.json`、`config/prompts.v1.yaml`、`config/site.v2.json`。
6. `src/literature_intelligence/db/models.py`、`collectors/pubmed.py`、`services/normalization.py`、`services/paper_ingest.py`、`services/verification.py`。
7. `src/literature_intelligence/pipeline/` 中 ingestion→content readiness→Codex 契约→import→static site→release 的顺序。
8. 相关 `tests/`，尤其 `test_codex_*`、`test_publication_release.py`、`test_static_site.py`、`test_pubmed_candidate_*`。
9. `docs/deployment/github-pages.md` 和 `.github/workflows/deploy-pages.yml`。

### 8.2 快速确认环境

```powershell
Set-Location 'D:\AAA gpt files\AI-Literature-Intelligence-System'
git status --short
git log --oneline -5
./.venv/Scripts/python.exe --version
./.venv/Scripts/python.exe -m unittest discover -s tests -v
./.venv/Scripts/python.exe -m literature_intelligence.config_validation
```

若 `.venv` 不可用，使用 Python 3.11+ 创建新虚拟环境并安装项目依赖；不要修改 `pyproject.toml` 只是为了绕过环境问题。联网测试前才配置 NCBI/Crossref 环境变量，敏感值只存在本地 `.env`。

### 8.3 WorkBuddy 与独立 CodeBuddy 的分工建议

- WorkBuddy：负责理解交接上下文、拆解需求、维护任务记录、审阅现有实现、运行检查、协助人工确认发布；不能把 Codex 历史中不存在的内容当事实。
- 独立 CodeBuddy IDE：负责在工作区中导航源码、编辑 Python/JSON/YAML/HTML/CSS、运行测试、查看 Git diff 和本地静态站点。
- 两者都应复用现有 CLI 和测试，不要另起一套数据格式或手写站点 HTML 作为长期方案。

### 8.4 继续开发的安全流程

遵循项目负责人要求的原则：**先理解现有项目，再修改；先评估影响，再实施；保留已有可用功能，不未经确认推翻现有架构。**

具体流程：

1. 先运行 `git status --short`，保护已有未提交改动。
2. 找到目标入口、数据模型、配置和测试；写出影响范围。
3. 先增加/修改测试或契约，再实施最小改动。
4. 保持 JSON/YAML 版本和 backward compatibility；不要原地改变 active/delivered 数据结构。
5. 运行配置校验、相关测试、全量测试和必要的真实 dry-run。
6. 检查站点页面、链接、manifest、批次状态和数据是否累计保留。
7. 输出 `git diff --stat`、关键 diff、测试结果和未解决风险，等待确认后再推送/发布。

### 8.5 Codex 依赖的替代方案

当前 `codex_*` 文件名代表“外部 AI 任务包协议”，不是必须调用 Codex 的技术限制。WorkBuddy/CodeBuddy 可以：

- 直接按 `config/prompts.v1.yaml` 生成相同 JSON 结果；或
- 继续读取 `data/codex_tasks/*.json`，输出与现有 screening/MVP result schema 兼容的 JSON。

无论使用哪种助手，都必须保留任务输入哈希、prompt/schema 版本、证据原文、已验证链接、`needs_human_review` 和幂等导入规则。未经确认不要把 `codex_*` 模块重命名或重构成新协议，以免破坏现有测试和批次数据。

## 9. 后续路线图与验收标准

### P0：先恢复可重复、可审计运行

1. 用当前仓库执行完整测试和配置校验；核对 `site/` 与 Git HEAD；确认当前 12 篇站点内容和两个批次可复现。
2. 将 V0.1 四领域候选 dry-run 与真实 PubMed 选择性采集跑通；验收：16 条查询、窗口、去重、跨领域关系、历史排除和输出 JSON 均可审查，不能改变既有发布站点。
3. 补充检索评估样本和领域专家审核；验收：每个领域给出误纳/漏纳案例和调整理由，更新配置版本而非静默改旧版本。
4. 明确公众号名单与合规获取方案；验收：有来源表、官网 URL、获取方式和 `automatic_ingestion` 决策，不能以搜索片段替代原文证据。

### P1：完成可控的 AI 接力

1. 为 WorkBuddy/CodeBuddy 建立兼容现有任务包的执行说明和结果模板。
2. 实现 prompt loader、版本 hash、模型/请求审计和一次 JSON repair。
3. 准备 5–10 篇小测试集；验收：结构化、证据、数字、研究类型边界、注入样本均通过。
4. 加入至少一个真实领域批次的人工复核；验收：screening、analysis、classification、report、quality gate 全部可导入且可回溯。

### P2：提升来源和内容质量

1. 评估 PMC/开放获取/人工摘录内容管线；验收：rights basis、section_id、哈希、截断原因均可审计，付费全文不被抓取保存。
2. 设计 50 篇 Golden Dataset；验收：达到设计文档中的相关率、证据和非人体表述门槛后，才讨论自动化分析。
3. 修复日期/期刊/排序显示的已知边界案例；验收：未来期次警告可解释，站点不误显示为当前最新研究。

### P3：自动化与生产化

1. 调度器、重试 worker、租约、指标和告警；验收：失败可恢复、幂等、不重复分析、不越过人工发布门禁。
2. PostgreSQL/Alembic 和部署方案；验收：迁移可回滚、历史审计保留、并发任务不覆盖迟到结果。
3. 高校/PI/公众号模块、搜索/知识库/问答；验收：每种新来源有独立证据等级和版权/合规策略，不降低原始论文证据标准。

## 10. 模拟交接检查

假设新 AI 只阅读本文件和当前仓库，它应能回答：项目做什么、四领域是什么、检索式在哪里、如何初始化/测试/生成站点、AI 任务如何接力、发布为何需要人工确认、当前站点有多少批次和论文、哪些功能未实现。

本次检查已从仓库确认：

- 根目录和 Python 包结构可定位。
- 运行命令和环境变量可定位。
- 16 路候选查询的配置来源和四领域具体 concept block 可定位。
- GitHub Pages 工作流、发布状态和 manifest 守卫可定位。
- 当前站点为 2026-11-12、2 批次、12 篇、4 领域的 published 制品。
- 测试入口为 `python -m unittest discover -s tests -v`。
- Codex 依赖可以被替换成兼容 JSON 任务包的 WorkBuddy/CodeBuddy 执行流程。

本次实际运行结果：配置校验通过；70 个测试中 69 个通过、1 个失败，具体失败及原因已列在第 7 节第 9 项。该失败应作为接手后的第一项测试基线核查，不应被文档掩盖为“全绿”。

仍不能仅凭仓库回答、需要负责人补充：

- 完整微信公众号名单、每个账号的领域归属、官网 URL 和官方主体。
- 历史 Codex 对话中的逐条页面修改意见、检索式讨论、公众号抓取尝试结果。
- 当前真实生产数据库位置/备份、GitHub Pages 实际公开 URL（仓库文档只确认 owner/repository，不应猜测部署是否仍在线）。
- 领域专家最终确认的 A/B/S-tier 期刊清单和质量阈值。
- 未来 LLM provider、模型、调用预算、是否允许 WorkBuddy/CodeBuddy 自动执行外部模型调用。

这部分缺失不应由新 AI 猜测补齐；收到资料后，应先更新本文件再开始相关模块开发。

## 11. 迁移交接结论

项目已经从“单纯的 PubMed MVP”发展到“候选发现配置 + 外部 AI 结果契约 + 累计静态站点 + 人工确认发布”的可运行原型，但不是已经完成的全自动科研情报平台。接手者最重要的工作顺序是：先复现当前状态，再验证 V0.1 检索配置，再稳定 AI 任务包和质量门禁，最后才扩展公众号、其他数据库和自动化。

不要删除或绕过现有去重、证据、哈希、人工确认和发布守卫；不要把设计文档里的功能写成已实现；不要把公众号线索写成原始论文证据。
