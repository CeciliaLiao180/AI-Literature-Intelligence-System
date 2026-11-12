# AI Literature Intelligence System

当前 MVP 实现范围：

1. SQLite/SQLAlchemy 数据库初始化。
2. PubMed E-utilities 真实检索和 XML 元数据获取。
3. 论文标准化与 PMID/DOI/标题去重。
4. PMID、DOI 和展示 URL 验证。

正式发布流程固定为：AI 处理 → Quality Gate → 预览 → 批次人工确认 → 正式发布。本阶段尚不实现发布页面。

## 本地运行

V0.1 试运行配置由四个文件共同组成：

- `config/domains.v1.json`：四个领域画像、PubMed concept blocks、排除/降级规则和多领域策略；
- `config/update.v1.json`：45 天 EDAT 回看、四类候选入口、评分权重、时效分档及人工运行/发布规则；
- `config/journal_tiers.v0.1.json`：全局 S-tier 和各领域 A/B-tier starter list，明确标记为待领域同事确认，不作为硬筛选；
- `config/site.v2.json`：领域 code 到稳定站点 URL 的映射。

在运行新批次前可先校验四份配置的一致性：

~~~powershell
./.venv/Scripts/python.exe -m literature_intelligence.config_validation
~~~

当前仅完成配置落地，尚未将这些新路由接入下一批真实 PubMed 采集，也未修改 Screening / Analysis / Classification Prompt 契约。

只读生成四领域候选查询并执行 PubMed dry-run（不会写数据库或创建批次）：

~~~powershell
./.venv/Scripts/python.exe -m literature_intelligence.pipeline.pubmed_candidate_dry_run `
  --run-date 2026-09-10 `
  --history-database-url sqlite:///data/gut_health_mvp.db `
  --output data/candidate_runs/2026-09-10_v01_dry_run.json
~~~

报告保存每条实际查询、PubMed 总命中数、受配置上限约束的返回 PMID、领域内跨入口去重结果、历史 PMID 排除结果和跨领域命中关系。该阶段只用 PMID 做搜索结果和历史记录的精确去重；DOI 去重在后续 EFetch、标准化和正式采集阶段执行。

~~~powershell
./.venv/Scripts/python.exe -m literature_intelligence.db.init_db
./.venv/Scripts/python.exe -m literature_intelligence.db.sync_domains `
  --database-url sqlite:///data/app.db `
  --config config/domains.v1.json
./.venv/Scripts/python.exe -m unittest discover -s tests -v
./.venv/Scripts/python.exe -m literature_intelligence.pipeline.ingestion `
  --query "probiotics[Title]" `
  --domain-code "gut_health" `
  --domain-name "肠道健康" `
  --retmax 1
./.venv/Scripts/python.exe -m literature_intelligence.pipeline.content_readiness
~~~

将数据库中处于 `CONTENT_READY` 的论文导出为 Codex 人工分析任务包：

~~~powershell
./.venv/Scripts/python.exe -m literature_intelligence.pipeline.codex_task_export `
  --database-url sqlite:///data/app.db `
  --batch-id example_batch `
  --domain-code gut_health `
  --output data/codex_tasks/content-ready.json
~~~

导出过程只读数据库，不推进任务状态，也不调用任何 LLM API。

校验由 Codex 人工生成的 Screening 结果包：

~~~powershell
./.venv/Scripts/python.exe -m literature_intelligence.pipeline.codex_screening `
  --task-package data/codex_tasks/example_gut_health.json `
  --results data/codex_results/example_gut_health_screening.json
~~~

校验完整 Codex 结果并生成仅供人工确认的 HTML 预览：

~~~powershell
./.venv/Scripts/python.exe -m literature_intelligence.pipeline.codex_mvp_preview `
  --task-package data/codex_tasks/example_gut_health.json `
  --screening-results data/codex_results/example_gut_health_screening.json `
  --mvp-results data/codex_results/example_gut_health_mvp.json `
  --output data/previews/example_gut_health_preview.html
~~~

该命令只生成预览文件，不执行正式发布。

将已经通过上述契约校验的 Codex 结果幂等写入数据库：

~~~powershell
./.venv/Scripts/python.exe -m literature_intelligence.pipeline.codex_results_import `
  --database-url sqlite:///data/gut_health_mvp.db `
  --task-package data/codex_tasks/example_gut_health.json `
  --screening-results data/codex_results/example_gut_health_screening.json `
  --mvp-results data/codex_results/example_gut_health_mvp.json `
  --prompt-spec config/prompts.v1.yaml `
  --edition-date 2026-09-07
~~~

导入会记录 Prompt 快照、Codex 各步骤、结构化分析、原文证据、领域分类、报告条目和任务状态历史。重复导入相同文件不会产生重复数据；批次保持 `draft`，不会绕过人工确认执行正式发布。

从数据库中累计读取已持久化、已通过 Quality Gate 的各批次内容，并重新生成完整静态网站：

~~~powershell
./.venv/Scripts/python.exe -m literature_intelligence.pipeline.static_site `
  --database-url sqlite:///data/gut_health_mvp.db `
  --site-config config/site.v2.json `
  --journal-metrics config/journal_metrics.v1.json `
  --output-dir site
~~~

固定站点目录为 `site/`。站点包含首页、领域页、稳定的论文详情页、`archive/index.html` 历史入口、`archive/YYYY-MM-DD/index.html` 日期归档、共享样式、独立的 `site-data.json` 和本次输出文件校验清单 `site-manifest.json`；生成状态固定为预览。默认累计读取 `site.v2.json` 中 `preview_batch_statuses` 允许的数据库批次，不会因生成新一期而丢失旧批次论文或旧日期归档。若同时传入 `--task-package`、`--screening-results` 和 `--mvp-results`，仍可使用原来的单批次文件兼容模式；该模式会生成空的历史入口，但不会把文件输入虚构成数据库历史批次。

首页与领域文献列表中的期刊影响因子来自独立的 `journal_metrics.v1.json`，配置保留指标年份、核对日期和出版社来源链接；未匹配的期刊显示“IF：暂无”。领域 canonical URL 由 `site.v2.json` 固定，旧领域路径会生成兼容跳转页。

校验本地站点制品及 `site-manifest.json` 中的文件哈希：

~~~powershell
./.venv/Scripts/python.exe -m literature_intelligence.pipeline.site_artifact `
  --site-dir site `
  --require-publication-status preview
~~~

`.github/workflows/deploy-pages.yml` 提供 GitHub Pages 的人工触发部署流程，并且只接受 `publication_status=published` 的站点，因此不会绕过批次级人工确认发布。仓库、Pages 权限和首次部署操作说明见 `docs/deployment/github-pages.md`。

人工确认批次后生成正式制品；只有 Pages 实际部署成功后才执行 `finalize` 回写 `published`：

~~~powershell
./.venv/Scripts/python.exe -m literature_intelligence.pipeline.publication_release `
  --database-url sqlite:///data/gut_health_mvp.db `
  confirm `
  --batch-id <batch-id> `
  --preview-site-dir site `
  --confirmed-by <reviewer> `
  --confirmation-note "批准正式发布"

./.venv/Scripts/python.exe -m literature_intelligence.pipeline.static_site `
  --database-url sqlite:///data/gut_health_mvp.db `
  --publication-status published `
  --output-dir site

# GitHub Pages 部署成功并实际验证网址后再执行：
./.venv/Scripts/python.exe -m literature_intelligence.pipeline.publication_release `
  --database-url sqlite:///data/gut_health_mvp.db `
  finalize `
  --batch-id <batch-id> `
  --published-site-dir site `
  --site-url https://<owner>.github.io/<repository>/
~~~

`config/update.v1.json` 已配置 45 天 PubMed EDAT 滚动回看，并预留时区、星期和运行时间。当前 `schedule.enabled=false`，只允许人工触发；该配置尚未接入调度器。45 天用于处理 PubMed 收录延迟，历史 PMID / DOI / 已处理状态仍须去重，不代表每次重新分析窗口内全部论文。

联网采集遵循 NCBI E-utilities 的请求频率限制；生产运行建议在 `.env` 配置 `NCBI_EMAIL`，高频调用时再配置个人 `NCBI_API_KEY`。采集后运行内容门禁，将结构、哈希和来源权利检查通过的论文推进到 `CONTENT_READY`，随后等待相关性筛选/AI 分析阶段。

设计基线见 [docs/design/README.md](docs/design/README.md)。
