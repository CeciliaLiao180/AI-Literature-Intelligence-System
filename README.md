# AI Literature Intelligence System

当前 MVP 实现范围：

1. SQLite/SQLAlchemy 数据库初始化。
2. PubMed E-utilities 真实检索和 XML 元数据获取。
3. 论文标准化与 PMID/DOI/标题去重。
4. PMID、DOI 和展示 URL 验证。

正式发布流程固定为：AI 处理 → Quality Gate → 预览 → 批次人工确认 → 正式发布。本阶段尚不实现发布页面。

## 本地运行

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

`.github/workflows/deploy-pages.yml` 已准备 GitHub Pages 的人工触发部署流程，但只接受 `publication_status=published` 的站点。当前真实批次仍为 `draft`、站点仍为 `preview`，因此工作流不会绕过批次级人工确认发布。首次部署所需的 GitHub owner、repository、仓库可见性和 Pages 权限仍需需求方明确，详见 `docs/deployment/github-pages.md`。

`config/update.v1.json` 已预留最近检索天数、PubMed 日期字段、时区、星期和运行时间配置。当前 `schedule.enabled=false`，只允许人工触发；该配置尚未接入调度器。

联网采集遵循 NCBI E-utilities 的请求频率限制；生产运行建议在 `.env` 配置 `NCBI_EMAIL`，高频调用时再配置个人 `NCBI_API_KEY`。采集后运行内容门禁，将结构、哈希和来源权利检查通过的论文推进到 `CONTENT_READY`，随后等待相关性筛选/AI 分析阶段。

设计基线见 [docs/design/README.md](docs/design/README.md)。
