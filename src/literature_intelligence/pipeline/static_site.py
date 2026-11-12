from __future__ import annotations

import argparse
import hashlib
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from literature_intelligence.config import Settings
from literature_intelligence.db.base import make_engine
from literature_intelligence.db.models import (
    AIRun,
    Author,
    Paper,
    PaperAnalysis,
    PaperAuthor,
    PaperCategory,
    PaperIdentifier,
    PaperTask,
    PublicationBatch,
    ReportItem,
    ResearchDomain,
    SourceRecord,
)
from literature_intelligence.pipeline.codex_mvp_preview import validate_mvp_results


SITE_CSS = r"""
:root{--blue:#0969e8;--blue-dark:#0754ba;--ink:#111827;--muted:#667085;--line:#d9dee7;--soft:#f5f8fc;--green:#15966a;--amber:#9a6700;--max:1180px}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;color:var(--ink);background:#fff;font-family:"Microsoft YaHei","PingFang SC",Arial,sans-serif;line-height:1.68}
a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}.site-header{position:sticky;top:0;z-index:20;background:#fffffff2;border-bottom:1px solid var(--line);backdrop-filter:blur(10px)}
.header-inner{max-width:var(--max);margin:auto;min-height:70px;padding:0 24px;display:flex;align-items:center;gap:28px}.brand{font-size:22px;font-weight:800;color:var(--ink);white-space:nowrap}.brand:hover{text-decoration:none;color:var(--blue)}
.nav-toggle{display:none;border:1px solid var(--line);background:white;border-radius:8px;padding:7px 11px}.site-nav{display:flex;align-items:center;gap:20px;margin-left:auto}.site-nav a{color:#344054;font-size:14px}.site-nav a[aria-current="page"]{color:var(--blue);font-weight:700}
.shell{max-width:var(--max);margin:auto;padding:34px 24px 80px}.breadcrumb{font-size:13px;color:var(--muted);margin-bottom:22px}.breadcrumb span{margin:0 7px}.hero{display:grid;grid-template-columns:1.5fr .8fr;gap:30px;align-items:end;padding:34px 0 38px;border-bottom:2px solid var(--ink)}
.kicker{color:var(--blue);font-weight:800;letter-spacing:.08em;text-transform:uppercase;font-size:13px}.hero h1,.page-head h1{font-size:clamp(34px,5vw,58px);line-height:1.12;margin:8px 0 15px;letter-spacing:-.03em}.hero p,.page-head p{color:var(--muted);font-size:17px;max-width:760px}.hero-stat{border-left:3px solid var(--blue);padding-left:18px}.hero-stat strong{display:block;font-size:42px;line-height:1;color:var(--blue)}
.section-head{display:flex;justify-content:space-between;align-items:end;gap:20px;margin:44px 0 18px}.section-head h2{font-size:27px;margin:0}.section-head p{margin:0;color:var(--muted)}
.domain-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.domain-card{border:1px solid var(--line);border-radius:12px;padding:20px;min-height:170px;display:flex;flex-direction:column;transition:.18s}.domain-card:hover{border-color:#8ab8f5;box-shadow:0 8px 24px #0969e814;transform:translateY(-2px);text-decoration:none}.domain-card .number{font-size:13px;color:var(--blue);font-weight:800}.domain-card h3{color:var(--ink);margin:9px 0 5px;font-size:20px}.domain-card p{color:var(--muted);font-size:13px;margin:0}.domain-card .count{margin-top:auto;padding-top:15px;color:var(--blue);font-weight:700}
.domain-section{padding-top:22px}.domain-title-row{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line);padding-bottom:10px}.domain-title-row h2{margin:0;font-size:24px}.view-all{font-size:14px;font-weight:700}.paper-list{counter-reset:item;list-style:none;margin:0;padding:0}.paper-row{counter-increment:item;display:grid;grid-template-columns:54px 1fr;gap:18px;padding:28px 0;border-bottom:1px solid var(--line)}.paper-row:before{content:counter(item,decimal-leading-zero);color:var(--blue);font-size:22px;font-weight:800;padding-top:27px}.paper-row-head{display:flex;align-items:center;justify-content:space-between;gap:18px;margin:0 0 8px}.journal-name{color:var(--blue);font-size:15px;font-weight:800;letter-spacing:.01em}.impact-factor{flex:0 0 auto;border:1px solid #9ed9c5;border-radius:999px;color:#147a59;font-size:12px;font-weight:800;line-height:1.3;padding:4px 9px}.impact-factor:hover{background:#edf9f4;text-decoration:none}.impact-factor.unavailable{border-color:var(--line);color:var(--muted)}.paper-row h3{font-size:22px;line-height:1.36;margin:0 0 7px}.paper-row h3 a{color:var(--ink)}.paper-row h3 a:hover{color:var(--blue)}.original{font-size:15px;color:var(--blue);margin:0 0 9px}.paper-meta{display:flex;gap:13px;flex-wrap:wrap;color:var(--muted);font-size:13px}.paper-conclusion{margin:12px 0 0;color:#344054}.tag-row{display:flex;gap:8px;flex-wrap:wrap;margin-top:13px}.tag{border:1px solid #9bc2f5;color:var(--blue);border-radius:999px;padding:3px 10px;font-size:12px}.evidence-tag{border-color:#9ed9c5;color:#147a59}.empty-state{border:1px dashed #b8c1ce;background:var(--soft);border-radius:12px;padding:30px;color:var(--muted);text-align:center;margin:18px 0 35px}
.page-head{padding:26px 0 28px;border-bottom:2px solid var(--ink)}.page-count{display:inline-flex;background:#eaf3ff;color:var(--blue);border-radius:999px;padding:5px 11px;font-size:13px;font-weight:700}.detail-layout{display:grid;grid-template-columns:minmax(0,1fr) 260px;gap:44px;margin-top:34px}.detail-main h2{font-size:23px;margin:38px 0 10px;border-bottom:1px solid var(--line);padding-bottom:8px}.detail-main h3{font-size:17px;margin:24px 0 7px}.detail-title{font-size:clamp(30px,4vw,48px);line-height:1.22;margin:8px 0 12px}.detail-original{font-size:19px;color:var(--blue);line-height:1.5}.detail-meta{display:grid;grid-template-columns:120px 1fr;margin:25px 0;border-top:1px solid var(--line)}.detail-meta dt,.detail-meta dd{margin:0;padding:9px 0;border-bottom:1px solid var(--line)}.detail-meta dt{font-weight:700}.detail-meta dd{color:#475467}.detail-sidebar{position:sticky;top:96px;align-self:start;border-left:1px solid var(--line);padding-left:22px}.detail-sidebar h2{font-size:15px;margin:0 0 8px}.detail-sidebar a{display:block;padding:5px 0;font-size:13px}.notice{background:#fff8e7;border-left:4px solid #e5a50a;padding:12px 15px;color:#694c08;margin:18px 0}.quality-box{background:#edf9f4;border:1px solid #b9e4d5;border-radius:10px;padding:15px;margin-top:22px}.quality-box strong{color:#147a59}.result-list{padding-left:22px}.result-list li{margin:10px 0}.evidence{border-left:3px solid #a7c9f4;padding:12px 16px;margin:13px 0;background:#f8fbff}.evidence cite{display:block;color:var(--muted);font-size:12px;margin-bottom:4px;font-style:normal}.source-actions{display:flex;gap:10px;flex-wrap:wrap;margin:24px 0}.button{display:inline-block;background:var(--blue);color:white;padding:9px 15px;border-radius:8px;font-weight:700}.button:hover{background:var(--blue-dark);text-decoration:none}.button.secondary{background:white;color:var(--blue);border:1px solid var(--blue)}
.site-footer{border-top:1px solid var(--line);color:var(--muted);font-size:13px}.footer-inner{max-width:var(--max);margin:auto;padding:24px;display:flex;justify-content:space-between;gap:20px}.preview-badge{color:var(--amber);font-weight:700}
.edition-strip{display:flex;align-items:center;justify-content:space-between;gap:24px;margin:24px 0 0;padding:18px 20px;border:1px solid #b8d4f7;border-radius:12px;background:#f7fbff}.edition-strip strong{display:block;font-size:18px}.edition-strip p{margin:2px 0 0;color:var(--muted);font-size:13px}.edition-actions{display:flex;gap:16px;flex:0 0 auto;font-size:14px;font-weight:700}.archive-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:22px}.archive-card{display:block;border:1px solid var(--line);border-radius:12px;padding:20px;color:var(--ink)}.archive-card:hover{border-color:#8ab8f5;box-shadow:0 8px 24px #0969e814;text-decoration:none}.archive-card time{color:var(--blue);font-size:13px;font-weight:800}.archive-card h2{font-size:19px;line-height:1.4;margin:7px 0}.archive-card p{color:var(--muted);font-size:13px;margin:0}.archive-card .count{display:block;color:var(--blue);font-weight:700;margin-top:12px}.archive-domain-counts{display:flex;gap:7px;flex-wrap:wrap;margin-top:12px}.archive-domain-counts span{border:1px solid var(--line);border-radius:999px;padding:2px 8px;color:var(--muted);font-size:11px}
@media(max-width:900px){.domain-grid{grid-template-columns:repeat(2,1fr)}.detail-layout{grid-template-columns:1fr}.detail-sidebar{position:static;border-left:0;border-top:1px solid var(--line);padding:18px 0}.hero{grid-template-columns:1fr}.site-nav{display:none;position:absolute;top:70px;left:0;right:0;background:white;border-bottom:1px solid var(--line);padding:18px 24px;flex-direction:column;align-items:flex-start}.site-nav.open{display:flex}.nav-toggle{display:block;margin-left:auto}}
@media(max-width:900px){.archive-grid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:580px){.shell{padding-left:17px;padding-right:17px}.domain-grid,.archive-grid{grid-template-columns:1fr}.edition-strip{align-items:flex-start;flex-direction:column}.paper-row{grid-template-columns:38px 1fr;gap:9px}.paper-row-head{align-items:flex-start}.journal-name{font-size:13px}.paper-row h3{font-size:19px}.detail-meta{grid-template-columns:92px 1fr}.footer-inner{display:block}}
""".strip()

SITE_JS = """document.querySelector('.nav-toggle')?.addEventListener('click',()=>{document.querySelector('.site-nav')?.classList.toggle('open')});"""

SITE_FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#0969e8"/><path d="M17 16h30v7H25v8h19v7H25v10h22v7H17z" fill="#fff"/></svg>"""

STUDY_TYPE_ZH = {
    "randomized_controlled_trial": "随机对照试验",
    "clinical_trial": "临床试验",
    "observational": "观察性研究",
    "systematic_review": "系统综述",
    "meta_analysis": "Meta分析",
    "animal": "动物研究",
    "in_vitro": "体外研究",
    "in_silico": "计算研究",
    "other": "其他/混合研究",
}


def _e(value: object) -> str:
    return html.escape("未报告" if value is None else str(value), quote=True)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write deterministic UTF-8 bytes instead of using the platform text mode.
    # On Windows, text-mode newline translation previously produced CRLF files;
    # Git normalized them to LF on the Linux Pages runner, invalidating the
    # byte-level SHA-256 values recorded in site-manifest.json.
    normalized_content = content.replace("\r\n", "\n").replace("\r", "\n")
    path.write_bytes(normalized_content.encode("utf-8"))


def _normalized_journal_name(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def _display_publication_date(
    value: str | None,
    precision: str | None,
) -> str:
    if not value:
        return "未报告"
    if precision == "year":
        return value[:4]
    if precision == "month":
        return value[:7]
    return value


def _journal_metric_index(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for journal in config.get("journals", []):
        for name in [journal.get("canonical_name"), *journal.get("aliases", [])]:
            key = _normalized_journal_name(name)
            if key:
                index[key] = journal
    return index


def _publication_date_metadata(
    paper: Paper,
    source_record: SourceRecord | None,
) -> dict[str, Any]:
    payload = source_record.raw_payload if source_record else {}
    is_future = bool(
        paper.publication_date
        and paper.publication_date > datetime.now(timezone.utc).date()
    )
    warnings: list[dict[str, str]] = []
    if is_future:
        warnings.append(
            {
                "code": "FUTURE_PUBLICATION_DATE",
                "message": (
                    "归一化日期晚于站点生成日期；该值来自 PubMed "
                    "JournalIssue/PubDate，已原样保留。应结合 PubModel、"
                    "ArticleDate 和 History 判断是否为在线优先或未来期次元数据。"
                ),
            }
        )
    return {
        "normalized_date": paper.publication_date.isoformat()
        if paper.publication_date
        else None,
        "precision": paper.publication_date_precision,
        "source_path": payload.get(
            "publication_date_source",
            "Article/Journal/JournalIssue/PubDate",
        ),
        "raw_issue_date": {
            "year": payload.get("publication_year"),
            "month": payload.get("publication_month"),
            "day": payload.get("publication_day"),
            "medline_date": payload.get("medline_date"),
        },
        "publication_model": payload.get("publication_model"),
        "citation_status": payload.get("citation_status"),
        "article_dates": payload.get("article_dates", []),
        "history_dates": payload.get("history_dates", []),
        "is_future_date": is_future,
        "warnings": warnings,
    }


def _screening_audit(session: Session) -> dict[str, list[dict[str, Any]]]:
    audit: dict[str, list[dict[str, Any]]] = {
        "excluded": [],
        "manual_review": [],
    }
    seen: set[int] = set()
    tasks = session.scalars(
        select(PaperTask)
        .where(PaperTask.state.in_(("IRRELEVANT", "REVIEW_PENDING")))
        .order_by(PaperTask.state_changed_at.desc(), PaperTask.id.desc())
    )
    for task in tasks:
        if task.paper_id in seen:
            continue
        seen.add(task.paper_id)
        screening_run = session.scalar(
            select(AIRun)
            .where(
                AIRun.paper_task_id == task.id,
                AIRun.step == "paper_relevance",
                AIRun.status == "succeeded",
            )
            .order_by(AIRun.finished_at.desc(), AIRun.created_at.desc())
            .limit(1)
        )
        if screening_run is None or not screening_run.output_json:
            continue
        output = screening_run.output_json
        pmid = session.scalar(
            select(PaperIdentifier.value).where(
                PaperIdentifier.paper_id == task.paper_id,
                PaperIdentifier.scheme == "pmid",
            )
        )
        row = {
            "paper_id": task.paper_id,
            "paper_task_id": task.id,
            "pmid": pmid,
            "decision": output["decision"],
            "relevance_score": output["relevance_score"],
            "reason": output["reason"],
        }
        audit["excluded" if task.state == "IRRELEVANT" else "manual_review"].append(
            row
        )
    return audit


def _build_archives(
    batches: list[dict[str, Any]],
    papers: list[dict[str, Any]],
    domains: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    papers_by_id = {paper["paper_id"]: paper for paper in papers}
    archives_by_date: dict[str, dict[str, Any]] = {}
    for batch in batches:
        edition_date = batch["edition_date"]
        archive = archives_by_date.setdefault(
            edition_date,
            {
                "edition_date": edition_date,
                "slug": edition_date,
                "title": batch["title"],
                "batch_ids": [],
                "batch_statuses": [],
                "paper_ids": [],
            },
        )
        archive["batch_ids"].append(batch["batch_id"])
        if batch["status"] not in archive["batch_statuses"]:
            archive["batch_statuses"].append(batch["status"])
        for paper_id in batch["paper_ids"]:
            if paper_id not in archive["paper_ids"]:
                archive["paper_ids"].append(paper_id)

    archives = sorted(
        archives_by_date.values(),
        key=lambda item: item["edition_date"],
        reverse=True,
    )
    for archive in archives:
        archive["paper_count"] = len(archive["paper_ids"])
        archive["domain_counts"] = {
            domain["code"]: sum(
                1
                for paper_id in archive["paper_ids"]
                if paper_id in papers_by_id
                and domain["code"] in papers_by_id[paper_id]["domain_codes"]
            )
            for domain in domains
        }
    return archives


def build_cumulative_site_data(
    *,
    database_url: str,
    site_config_path: Path,
    journal_metrics_path: Path,
    publication_status: str = "preview",
) -> dict[str, Any]:
    if publication_status not in {"preview", "published"}:
        raise ValueError(f"unsupported publication_status: {publication_status}")
    site_config = _load_json(site_config_path)
    journal_metrics = _load_json(journal_metrics_path)
    metric_by_journal = _journal_metric_index(journal_metrics)
    domains = [dict(item) for item in site_config["domains"]]
    domain_by_code = {item["code"]: item for item in domains}
    included_batch_statuses = (
        site_config.get(
            "preview_batch_statuses",
            [
                "draft",
                "preview_ready",
                "awaiting_confirmation",
                "approved",
                "published",
            ],
        )
        if publication_status == "preview"
        else ["approved", "published"]
    )

    papers_by_id: dict[int, dict[str, Any]] = {}
    batch_paper_ids: dict[str, list[int]] = {}
    engine = make_engine(database_url)
    try:
        with Session(engine) as session:
            batches = list(
                session.scalars(
                    select(PublicationBatch)
                    .where(PublicationBatch.status.in_(included_batch_statuses))
                    .order_by(
                        PublicationBatch.edition_date.desc(),
                        PublicationBatch.created_at.desc(),
                    )
                )
            )
            if publication_status == "published":
                if not batches:
                    raise ValueError(
                        "published site requires at least one approved publication batch"
                    )
                unconfirmed = [
                    batch.id
                    for batch in batches
                    if not batch.confirmed_by
                    or batch.confirmed_at is None
                    or not batch.preview_sha256
                ]
                if unconfirmed:
                    raise ValueError(
                        f"published site has unconfirmed batches: {unconfirmed}"
                    )
            batch_rows = session.execute(
                select(ReportItem, PublicationBatch)
                .join(
                    PublicationBatch,
                    PublicationBatch.id == ReportItem.publication_batch_id,
                )
                .where(
                    PublicationBatch.status.in_(included_batch_statuses),
                    ReportItem.review_status.in_(("auto_approved", "approved")),
                )
                .order_by(
                    PublicationBatch.edition_date.desc(),
                    PublicationBatch.created_at.desc(),
                    ReportItem.display_order,
                )
            ).all()
            for report_item, batch in batch_rows:
                batch_paper_ids.setdefault(batch.id, []).append(report_item.paper_id)
                existing = papers_by_id.get(report_item.paper_id)
                if existing is not None:
                    existing["publication_batch_ids"].append(batch.id)
                    continue

                paper = session.get(Paper, report_item.paper_id)
                analysis_row = session.get(PaperAnalysis, report_item.analysis_id)
                if paper is None or analysis_row is None:
                    raise ValueError(
                        f"report item {report_item.id} has missing paper or analysis"
                    )
                analysis_run = session.get(AIRun, analysis_row.ai_run_id)
                if analysis_run is None or not analysis_run.output_json:
                    raise ValueError(
                        f"analysis {analysis_row.id} has no persisted Codex output"
                    )
                task = session.get(PaperTask, analysis_run.paper_task_id)
                if task is None:
                    raise ValueError(f"analysis {analysis_row.id} has no paper task")
                quality_run = session.scalar(
                    select(AIRun)
                    .where(
                        AIRun.paper_task_id == task.id,
                        AIRun.step == "report_quality_gate",
                        AIRun.status == "succeeded",
                    )
                    .order_by(AIRun.finished_at.desc(), AIRun.created_at.desc())
                    .limit(1)
                )
                if quality_run is None or not quality_run.output_json:
                    raise ValueError(f"paper {paper.id} has no persisted Quality Gate")

                classification_codes = list(
                    session.scalars(
                        select(ResearchDomain.code)
                        .join(
                            PaperCategory,
                            PaperCategory.domain_id == ResearchDomain.id,
                        )
                        .where(PaperCategory.paper_id == paper.id)
                        .order_by(PaperCategory.score.desc(), ResearchDomain.code)
                    )
                )
                unknown = set(classification_codes) - set(domain_by_code)
                if unknown:
                    raise ValueError(
                        f"site config has no presentation entry for {sorted(unknown)}"
                    )
                if not classification_codes:
                    raise ValueError(f"paper {paper.id} has no persisted classification")

                authors = [
                    {
                        "name": author.display_name,
                        "orcid": author.orcid,
                        "affiliation": paper_author.affiliation,
                    }
                    for paper_author, author in session.execute(
                        select(PaperAuthor, Author)
                        .join(Author, Author.id == PaperAuthor.author_id)
                        .where(PaperAuthor.paper_id == paper.id)
                        .order_by(PaperAuthor.author_order)
                    ).all()
                ]
                identifiers = dict(
                    session.execute(
                        select(PaperIdentifier.scheme, PaperIdentifier.value)
                        .where(PaperIdentifier.paper_id == paper.id)
                        .order_by(PaperIdentifier.id)
                    ).all()
                )
                source_record = session.scalar(
                    select(SourceRecord)
                    .where(
                        SourceRecord.paper_id == paper.id,
                        SourceRecord.pipeline_run_id == task.pipeline_run_id,
                        SourceRecord.source == "pubmed",
                    )
                    .order_by(SourceRecord.fetched_at.desc(), SourceRecord.id.desc())
                    .limit(1)
                )
                report = report_item.summary_json
                papers_by_id[paper.id] = {
                    "paper_id": paper.id,
                    "paper_task_id": task.id,
                    "title": paper.canonical_title,
                    "ai_title": report_item.ai_title,
                    "authors": authors,
                    "journal": paper.journal_name,
                    "journal_metric": metric_by_journal.get(
                        _normalized_journal_name(paper.journal_name),
                        {
                            "canonical_name": paper.journal_name,
                            "impact_factor": None,
                            "metric_year": None,
                            "source_name": None,
                            "source_url": None,
                        },
                    ),
                    "publication_date": paper.publication_date.isoformat()
                    if paper.publication_date
                    else None,
                    "publication_date_precision": paper.publication_date_precision,
                    "publication_date_metadata": _publication_date_metadata(
                        paper, source_record
                    ),
                    "identifiers": identifiers,
                    "verified_source_links": report["source_links"],
                    "domain_codes": classification_codes,
                    "analysis": analysis_run.output_json,
                    "report": report,
                    "quality_gate": quality_run.output_json,
                    "publication_batch_ids": [batch.id],
                    "latest_edition_date": batch.edition_date.isoformat(),
                }

            papers = sorted(
                papers_by_id.values(),
                key=lambda item: (item["publication_date"] or "", item["paper_id"]),
                reverse=True,
            )
            for domain in domains:
                domain["paper_ids"] = [
                    paper["paper_id"]
                    for paper in papers
                    if domain["code"] in paper["domain_codes"]
                ]
                domain["paper_count"] = len(domain["paper_ids"])

            state_counts = dict(
                session.execute(
                    select(PaperTask.state, func.count(func.distinct(PaperTask.paper_id)))
                    .group_by(PaperTask.state)
                ).all()
            )
            retrieved_count = session.scalar(
                select(func.count(func.distinct(PaperTask.paper_id)))
            ) or 0
            latest_batch = batches[0] if batches else None
            batch_data = [
                {
                    "batch_id": batch.id,
                    "edition_date": batch.edition_date.isoformat(),
                    "edition_type": batch.edition_type,
                    "title": batch.title,
                    "status": batch.status,
                    "paper_ids": batch_paper_ids.get(batch.id, []),
                    "paper_count": len(batch_paper_ids.get(batch.id, [])),
                }
                for batch in batches
            ]
            screening_audit = _screening_audit(session)
            included_paper_ids = set(papers_by_id)
            screening_audit = {
                key: [
                    row
                    for row in rows
                    if row["paper_id"] not in included_paper_ids
                ]
                for key, rows in screening_audit.items()
            }
            archives = _build_archives(batch_data, papers, domains)
    finally:
        engine.dispose()

    return {
        "schema_version": "2.0",
        "generated_at": _iso_now(),
        "publication_status": publication_status,
        "data_mode": "cumulative_database",
        "batch_id": latest_batch.id if latest_batch else "none",
        "latest_edition_date": latest_batch.edition_date.isoformat()
        if latest_batch
        else None,
        "site": {
            "title": site_config["site_title"],
            "subtitle": site_config["site_subtitle"],
            "config_version": site_config["version"],
            "output_directory": site_config.get("output_directory", "site"),
            "home_latest_per_domain": site_config.get("home_latest_per_domain", 3),
            "archive": site_config.get(
                "archive",
                {"directory": "archive", "date_format": "%Y-%m-%d"},
            ),
        },
        "summary": {
            "retrieved": retrieved_count,
            "included": len(papers),
            "excluded": len(screening_audit["excluded"]),
            "manual_review": len(screening_audit["manual_review"]),
            "quality_pass": len(papers),
            "quality_review": 0,
            "quality_reject": state_counts.get("REJECTED", 0),
        },
        "domains": domains,
        "papers": papers,
        "batches": batch_data,
        "archives": archives,
        "screening_audit": screening_audit,
        "sources": {
            "database_mode": "cumulative",
            "publication_batch_count": len(batch_data),
            "journal_metrics": {
                "version": journal_metrics.get("version"),
                "metric_name": journal_metrics.get("metric_name"),
                "retrieved_at": journal_metrics.get("retrieved_at"),
                "sha256": hashlib.sha256(journal_metrics_path.read_bytes()).hexdigest(),
            },
        },
    }


def build_site_data(
    *,
    database_url: str,
    site_config_path: Path,
    journal_metrics_path: Path,
    task_package_path: Path,
    screening_results_path: Path,
    mvp_results_path: Path,
) -> dict[str, Any]:
    validation = validate_mvp_results(
        task_package_path,
        screening_results_path,
        mvp_results_path,
    )
    site_config = _load_json(site_config_path)
    journal_metrics = _load_json(journal_metrics_path)
    metric_by_journal = _journal_metric_index(journal_metrics)
    task_package = _load_json(task_package_path)
    screening_package = _load_json(screening_results_path)
    mvp_package = _load_json(mvp_results_path)
    tasks = {item["paper_task_id"]: item for item in task_package["tasks"]}
    domains = [dict(item) for item in site_config["domains"]]
    domain_by_code = {item["code"]: item for item in domains}

    papers: list[dict[str, Any]] = []
    engine = make_engine(database_url)
    try:
        with Session(engine) as session:
            for result in mvp_package["items"]:
                task = tasks[result["paper_task_id"]]
                paper = session.get(Paper, result["paper_id"])
                if paper is None:
                    raise ValueError(f"paper {result['paper_id']} is missing from database")
                authors = [
                    {
                        "name": author.display_name,
                        "orcid": author.orcid,
                        "affiliation": paper_author.affiliation,
                    }
                    for paper_author, author in session.execute(
                        select(PaperAuthor, Author)
                        .join(Author, Author.id == PaperAuthor.author_id)
                        .where(PaperAuthor.paper_id == paper.id)
                        .order_by(PaperAuthor.author_order)
                    ).all()
                ]
                identifiers = dict(
                    session.execute(
                        select(PaperIdentifier.scheme, PaperIdentifier.value)
                        .where(PaperIdentifier.paper_id == paper.id)
                        .order_by(PaperIdentifier.id)
                    ).all()
                )
                classification_codes = [
                    row["domain_code"]
                    for row in result["classification"]["classifications"]
                ]
                unknown = set(classification_codes) - set(domain_by_code)
                if unknown:
                    raise ValueError(f"site config has no presentation entry for {sorted(unknown)}")
                papers.append(
                    {
                        "paper_id": paper.id,
                        "paper_task_id": result["paper_task_id"],
                        "title": paper.canonical_title,
                        "ai_title": result["report"]["ai_title"],
                        "authors": authors,
                        "journal": paper.journal_name,
                        "journal_metric": metric_by_journal.get(
                            _normalized_journal_name(paper.journal_name),
                            {
                                "canonical_name": paper.journal_name,
                                "impact_factor": None,
                                "metric_year": None,
                                "source_name": None,
                                "source_url": None,
                            },
                        ),
                        "publication_date": paper.publication_date.isoformat() if paper.publication_date else None,
                        "publication_date_precision": paper.publication_date_precision,
                        "publication_date_metadata": task["input_data"]["paper"]["study_metadata"]["publication_date_metadata"],
                        "identifiers": identifiers,
                        "verified_source_links": result["report"]["source_links"],
                        "domain_codes": classification_codes,
                        "analysis": result["analysis"],
                        "report": result["report"],
                        "quality_gate": result["quality_gate"],
                    }
                )
    finally:
        engine.dispose()

    papers.sort(key=lambda item: (item["publication_date"] or "", item["paper_id"]), reverse=True)
    for domain in domains:
        domain["paper_ids"] = [paper["paper_id"] for paper in papers if domain["code"] in paper["domain_codes"]]
        domain["paper_count"] = len(domain["paper_ids"])

    screening_audit = {"excluded": [], "manual_review": []}
    for row in screening_package["results"]:
        decision = row["output"]["decision"]
        if decision == "relevant":
            continue
        audit_row = {
            "paper_id": row["paper_id"],
            "paper_task_id": row["paper_task_id"],
            "pmid": row["pmid"],
            "decision": decision,
            "relevance_score": row["output"]["relevance_score"],
            "reason": row["output"]["reason"],
        }
        screening_audit["excluded" if decision == "irrelevant" else "manual_review"].append(audit_row)

    return {
        "schema_version": "1.0",
        "generated_at": _iso_now(),
        "publication_status": "preview",
        "data_mode": "single_batch_files",
        "batch_id": validation["batch_id"],
        "latest_edition_date": None,
        "site": {
            "title": site_config["site_title"],
            "subtitle": site_config["site_subtitle"],
            "config_version": site_config["version"],
            "output_directory": site_config.get("output_directory", "site"),
            "home_latest_per_domain": site_config.get("home_latest_per_domain", 3),
            "archive": site_config.get(
                "archive",
                {"directory": "archive", "date_format": "%Y-%m-%d"},
            ),
        },
        "summary": validation["summary"],
        "domains": domains,
        "papers": papers,
        "batches": [],
        "archives": [],
        "screening_audit": screening_audit,
        "sources": {
            "database_url": database_url,
            "task_package_sha256": hashlib.sha256(task_package_path.read_bytes()).hexdigest(),
            "screening_results_sha256": hashlib.sha256(screening_results_path.read_bytes()).hexdigest(),
            "mvp_results_sha256": hashlib.sha256(mvp_results_path.read_bytes()).hexdigest(),
            "journal_metrics": {
                "version": journal_metrics.get("version"),
                "metric_name": journal_metrics.get("metric_name"),
                "retrieved_at": journal_metrics.get("retrieved_at"),
                "sha256": hashlib.sha256(journal_metrics_path.read_bytes()).hexdigest(),
            },
        },
    }


def _nav(domains: list[dict[str, Any]], *, root: str, current: str | None) -> str:
    links = [f'<a href="{root}index.html"{(" aria-current=\"page\"" if current == "home" else "")}>首页</a>']
    links.extend(
        f'<a href="{root}domains/{_e(domain["slug"])}.html"{(" aria-current=\"page\"" if current == domain["code"] else "")}>{_e(domain["short_name"])}</a>'
        for domain in domains
    )
    links.append(
        f'<a href="{root}archive/index.html"{(" aria-current=\"page\"" if current == "archive" else "")}>历史速递</a>'
    )
    return f'''<header class="site-header"><div class="header-inner"><a class="brand" href="{root}index.html">科研文献速递</a><button class="nav-toggle" type="button" aria-label="打开目录">目录</button><nav class="site-nav" aria-label="主导航">{''.join(links)}</nav></div></header>'''


def _footer(batch_id: str, publication_status: str) -> str:
    status_text = (
        "正式发布 · 已完成批次级人工确认"
        if publication_status == "published"
        else "本地预览 · 人工确认前不得正式发布"
    )
    return f'''<footer class="site-footer"><div class="footer-inner"><span>批次 {_e(batch_id)}</span><span class="preview-badge">{status_text}</span></div></footer>'''


def _redirect_page(*, destination: str, title: str) -> str:
    safe_destination = _e(destination)
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,follow"><meta http-equiv="refresh" content="0; url={safe_destination}"><link rel="canonical" href="{safe_destination}"><title>{_e(title)} · 页面地址已更新</title></head><body><p>页面地址已更新，正在跳转。若未自动跳转，请访问 <a href="{safe_destination}">{_e(title)}</a>。</p></body></html>'''


def _paper_list(papers: list[dict[str, Any]], domains: dict[str, dict[str, Any]], *, root: str, limit: int | None = None) -> str:
    selected = papers[:limit] if limit else papers
    if not selected:
        return '<div class="empty-state"><strong>暂无文献</strong><br>该领域页面结构已准备完成，等待后续批次数据。</div>'
    rows = []
    for paper in selected:
        report = paper["report"]
        analysis = paper["analysis"]
        tags = "".join(f'<span class="tag">{_e(domains[code]["short_name"])}</span>' for code in paper["domain_codes"])
        tags += f'<span class="tag evidence-tag">{_e(STUDY_TYPE_ZH.get(analysis["study_type"], analysis["study_type"]))}</span>'
        conclusion = report["core_results"][0] if report["core_results"] else "摘要未提供可展示的核心结果。"
        metric = paper["journal_metric"]
        metric_year = f'{metric["metric_year"]} JIF' if metric.get("metric_year") else "当前出版社页面值"
        if metric.get("impact_factor") is None:
            impact_factor = '<span class="impact-factor unavailable">IF：暂无</span>'
        else:
            metric_title = f'Journal Impact Factor · {metric_year} · 来源：{metric.get("source_name") or "未标注"}'
            impact_factor = f'<a class="impact-factor" href="{_e(metric.get("source_url"))}" target="_blank" rel="noopener noreferrer" title="{_e(metric_title)}">IF：{_e(metric["impact_factor"])}</a>'
        publication_date = _display_publication_date(
            paper["publication_date"], paper["publication_date_precision"]
        )
        rows.append(f'''<li class="paper-row"><div><div class="paper-row-head"><span class="journal-name">{_e(paper["journal"])}</span>{impact_factor}</div><h3><a href="{root}papers/{paper["paper_id"]}.html">{_e(paper["ai_title"])}</a></h3><p class="original">{_e(paper["title"])}</p><div class="paper-meta"><span>{_e(publication_date)}</span><span>PMID {_e(paper["identifiers"].get("pmid"))}</span></div><p class="paper-conclusion">{_e(conclusion)}</p><div class="tag-row">{tags}</div></div></li>''')
    return '<ol class="paper-list">' + "".join(rows) + '</ol>'


def _page(
    title: str,
    body: str,
    *,
    domains: list[dict[str, Any]],
    root: str,
    current: str | None,
    batch_id: str,
    publication_status: str,
) -> str:
    robots = "index,follow" if publication_status == "published" else "noindex,nofollow"
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="{robots}"><title>{_e(title)} · 科研文献速递</title><link rel="icon" href="{root}assets/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="{root}assets/site.css"></head><body>{_nav(domains,root=root,current=current)}{body}{_footer(batch_id, publication_status)}<script src="{root}assets/site.js"></script></body></html>'''


def _render_index(data: dict[str, Any]) -> str:
    domains = data["domains"]
    papers = data["papers"]
    domain_by_code = {domain["code"]: domain for domain in domains}
    cards = "".join(
        f'''<a class="domain-card" href="domains/{_e(domain["slug"])}.html"><span class="number">{index:02d}</span><h3>{_e(domain["name"])}</h3><p>{_e(domain["description"])}</p><span class="count">{domain["paper_count"]} 篇文献 →</span></a>'''
        for index, domain in enumerate(domains, start=1)
    )
    sections = []
    for domain in domains:
        domain_papers = [paper for paper in papers if domain["code"] in paper["domain_codes"]]
        sections.append(f'''<section class="domain-section"><div class="domain-title-row"><h2>{_e(domain["name"])}</h2><a class="view-all" href="domains/{_e(domain["slug"])}.html">查看领域全部文献 →</a></div>{_paper_list(domain_papers,domain_by_code,root="",limit=data["site"]["home_latest_per_domain"])}</section>''')
    count_label = (
        "篇累计收录并通过 Quality Gate 的文献"
        if data.get("data_mode") == "cumulative_database"
        else "篇已通过 Quality Gate 的本期文献"
    )
    latest_archive = data.get("archives", [None])[0] if data.get("archives") else None
    if latest_archive:
        edition_strip = f'''<section class="edition-strip"><div><strong>本期更新 · {_e(latest_archive["edition_date"])}</strong><p>新增 {latest_archive["paper_count"]} 篇文献，已进入固定站点累计展示。</p></div><div class="edition-actions"><a href="archive/{_e(latest_archive["slug"])}/">查看本期 →</a><a href="archive/index.html">历史速递 →</a></div></section>'''
    else:
        edition_strip = '''<section class="edition-strip"><div><strong>尚无历史速递</strong><p>完成首个数据库批次后将在这里生成固定日期归档。</p></div><div class="edition-actions"><a href="archive/index.html">历史速递 →</a></div></section>'''
    kicker = (
        "AI Research Digest"
        if data["publication_status"] == "published"
        else "AI Research Digest · Preview"
    )
    body = f'''<main class="shell"><section class="hero"><div><span class="kicker">{kicker}</span><h1>{_e(data["site"]["title"])}</h1><p>{_e(data["site"]["subtitle"])}</p></div><div class="hero-stat"><strong>{data["summary"]["included"]}</strong><span>{count_label}</span></div></section>{edition_strip}<div class="section-head"><div><h2>关注领域</h2><p>从领域进入最新文献列表，再查看结构化研究详情。</p></div></div><section class="domain-grid">{cards}</section>{''.join(sections)}</main>'''
    return _page(
        "首页",
        body,
        domains=domains,
        root="",
        current="home",
        batch_id=data["batch_id"],
        publication_status=data["publication_status"],
    )


def _render_domain(data: dict[str, Any], domain: dict[str, Any]) -> str:
    domains = data["domains"]
    domain_by_code = {item["code"]: item for item in domains}
    papers = [paper for paper in data["papers"] if domain["code"] in paper["domain_codes"]]
    body = f'''<main class="shell"><div class="breadcrumb"><a href="../index.html">首页</a><span>/</span>{_e(domain["name"])}</div><section class="page-head"><span class="kicker">Research Domain</span><h1>{_e(domain["name"])}</h1><p>{_e(domain["description"])}</p><span class="page-count">本领域共 {len(papers)} 篇</span></section><div class="section-head"><div><h2>最新文献</h2><p>按 PubMed 期刊日期由新到旧排列。</p></div></div>{_paper_list(papers,domain_by_code,root="../")}</main>'''
    return _page(
        domain["name"],
        body,
        domains=domains,
        root="../",
        current=domain["code"],
        batch_id=data["batch_id"],
        publication_status=data["publication_status"],
    )


def _archive_domain_counts(
    archive: dict[str, Any], domains: list[dict[str, Any]]
) -> str:
    domain_by_code = {domain["code"]: domain for domain in domains}
    return "".join(
        f'<span>{_e(domain_by_code[code]["short_name"])} {count} 篇</span>'
        for code, count in archive["domain_counts"].items()
        if count and code in domain_by_code
    )


def _render_archive_index(data: dict[str, Any]) -> str:
    domains = data["domains"]
    archives = data.get("archives", [])
    if archives:
        cards = "".join(
            f'''<a class="archive-card" href="{_e(archive["slug"])}/"><time datetime="{_e(archive["edition_date"])}">{_e(archive["edition_date"])}</time><h2>{_e(archive["title"])}</h2><p>独立保存的历史速递，可继续访问其中的稳定论文详情页。</p><div class="archive-domain-counts">{_archive_domain_counts(archive, domains)}</div><span class="count">本期 {archive["paper_count"]} 篇 →</span></a>'''
            for archive in archives
        )
        content = f'<section class="archive-grid">{cards}</section>'
    else:
        content = '<div class="empty-state"><strong>暂无历史速递</strong><br>首个批次完成后将在这里显示按日期保存的归档。</div>'
    body = f'''<main class="shell"><div class="breadcrumb"><a href="../index.html">首页</a><span>/</span>历史速递</div><section class="page-head"><span class="kicker">Archive</span><h1>历史速递</h1><p>每期内容使用固定日期目录保存，新一期不会替代旧一期。</p><span class="page-count">共 {len(archives)} 期</span></section>{content}</main>'''
    return _page(
        "历史速递",
        body,
        domains=domains,
        root="../",
        current="archive",
        batch_id=data["batch_id"],
        publication_status=data["publication_status"],
    )


def _render_archive(data: dict[str, Any], archive: dict[str, Any]) -> str:
    domains = data["domains"]
    domain_by_code = {domain["code"]: domain for domain in domains}
    papers_by_id = {paper["paper_id"]: paper for paper in data["papers"]}
    papers = [
        papers_by_id[paper_id]
        for paper_id in archive["paper_ids"]
        if paper_id in papers_by_id
    ]
    domain_counts = _archive_domain_counts(archive, domains)
    body = f'''<main class="shell"><div class="breadcrumb"><a href="../../index.html">首页</a><span>/</span><a href="../index.html">历史速递</a><span>/</span>{_e(archive["edition_date"])}</div><section class="page-head"><span class="kicker">Weekly Edition</span><h1>{_e(archive["title"])}</h1><p>本页固定记录 {_e(archive["edition_date"])} 期收录的文献；领域页与首页继续展示累计内容。</p><span class="page-count">本期 {archive["paper_count"]} 篇</span><div class="archive-domain-counts">{domain_counts}</div></section><div class="section-head"><div><h2>本期文献</h2><p>点击文献进入稳定的独立详情页。</p></div></div>{_paper_list(papers, domain_by_code, root="../../")}</main>'''
    return _page(
        archive["title"],
        body,
        domains=domains,
        root="../../",
        current="archive",
        batch_id=data["batch_id"],
        publication_status=data["publication_status"],
    )


def _ul(values: list[str]) -> str:
    if not values:
        return '<p class="muted">未报告</p>'
    return '<ul class="result-list">' + "".join(f'<li>{_e(value)}</li>' for value in values) + '</ul>'


def _render_paper(data: dict[str, Any], paper: dict[str, Any]) -> str:
    domains = data["domains"]
    domain_by_code = {item["code"]: item for item in domains}
    primary_domain = domain_by_code[paper["domain_codes"][0]]
    analysis = paper["analysis"]
    report = paper["report"]
    methods = analysis["methods"]
    sample = analysis["sample"]
    authors = "、".join(author["name"] for author in paper["authors"])
    date_warnings = paper["publication_date_metadata"]["warnings"]
    warning_html = "".join(f'<div class="notice">{_e(item["message"])}</div>' for item in date_warnings)
    evidence_html = "".join(
        f'''<h3>{_e(finding["claim"])}</h3>''' + "".join(
            f'''<blockquote class="evidence"><cite>section: {_e(ev["section_id"])} · {_e(ev["support_level"])}</cite>{_e(ev["quote"])}</blockquote>'''
            for ev in finding["evidence"]
        ) for finding in analysis["key_findings"]
    )
    links = "".join(
        f'<a class="button{(" secondary" if "doi.org" in link else "")}" href="{_e(link)}" target="_blank" rel="noopener noreferrer">{("查看 DOI" if "doi.org" in link else "查看 PubMed")}</a>'
        for link in paper["verified_source_links"]
    )
    sidebar_links = "".join(f'<a href="../domains/{_e(domain_by_code[code]["slug"])}.html">← 返回{_e(domain_by_code[code]["short_name"])}文献列表</a>' for code in paper["domain_codes"])
    quality_notice = (
        "本批次已完成人工确认并正式发布。"
        if data["publication_status"] == "published"
        else "当前内容仅用于内部预览，正式发布前仍需人工确认。"
    )
    publication_date = _display_publication_date(
        paper["publication_date"], paper["publication_date_precision"]
    )
    body = f'''<main class="shell"><div class="breadcrumb"><a href="../index.html">首页</a><span>/</span><a href="../domains/{_e(primary_domain["slug"])}.html">{_e(primary_domain["name"])}</a><span>/</span>文献详情</div><div class="detail-layout"><article class="detail-main"><span class="kicker">Paper Detail · PMID {_e(paper["identifiers"].get("pmid"))}</span><h1 class="detail-title">{_e(paper["ai_title"])}</h1><p class="detail-original">{_e(paper["title"])}</p><dl class="detail-meta"><dt>作者</dt><dd>{_e(authors)}</dd><dt>期刊</dt><dd>{_e(paper["journal"])}</dd><dt>发布时间</dt><dd>{_e(publication_date)}（精度：{_e(paper["publication_date_precision"]) }）</dd><dt>PMID</dt><dd>{_e(paper["identifiers"].get("pmid"))}</dd><dt>DOI</dt><dd>{_e(paper["identifiers"].get("doi"))}</dd><dt>研究类型</dt><dd>{_e(STUDY_TYPE_ZH.get(analysis["study_type"], analysis["study_type"]))} / {_e(analysis["evidence_level"])}</dd></dl>{warning_html}<h2 id="background">研究背景</h2><p>{_e(analysis["background"])}</p><h2 id="objective">研究目的</h2><p>{_e(analysis["objective"])}</p><h2 id="design">实验设计与主要方法</h2><h3>设计</h3><p>{_e(methods["design"])}</p><h3>样本量 / 研究对象</h3><p>样本量：{_e(sample["size"])}；研究对象：{_e(sample["population"])}；物种：{_e(sample["species"])}</p><h3>干预与对照</h3><p>干预：{_e(methods["intervention"])}<br>对照：{_e(methods["comparator"])}</p><h3>周期</h3><p>{_e(methods["duration"])}</p><h2 id="results">核心研究结果</h2>{_ul(report["core_results"])}<h2 id="limitations">研究局限</h2>{_ul(report["limitations"])}<h2 id="implications">对研发的启示</h2>{_ul(report["rd_implications"])}<h2 id="evidence">证据 / 原文支持</h2>{evidence_html}<div class="quality-box"><strong>Quality Gate：{_e(paper["quality_gate"]["decision"]) } · {_e(paper["quality_gate"]["quality_score"])}</strong><br>{quality_notice}</div><div class="source-actions">{links}</div></article><aside class="detail-sidebar"><h2>页面目录</h2><a href="#background">研究背景</a><a href="#objective">研究目的</a><a href="#design">实验设计</a><a href="#results">核心结果</a><a href="#limitations">研究局限</a><a href="#implications">研发启示</a><a href="#evidence">原文证据</a><hr>{sidebar_links}<a href="../index.html">← 返回首页</a></aside></div></main>'''
    return _page(
        paper["ai_title"],
        body,
        domains=domains,
        root="../",
        current=primary_domain["code"],
        batch_id=data["batch_id"],
        publication_status=data["publication_status"],
    )


def generate_site(data: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    assets = output_dir / "assets"
    domains_dir = output_dir / "domains"
    papers_dir = output_dir / "papers"
    data_dir = output_dir / "data"
    archive_dir = output_dir / data["site"]["archive"]["directory"]
    for directory in (assets, domains_dir, papers_dir, data_dir, archive_dir):
        directory.mkdir(parents=True, exist_ok=True)

    _write(assets / "site.css", SITE_CSS + "\n")
    _write(assets / "site.js", SITE_JS + "\n")
    _write(assets / "favicon.svg", SITE_FAVICON_SVG + "\n")
    _write(output_dir / ".nojekyll", "")
    _write(data_dir / "site-data.json", json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    _write(output_dir / "index.html", _render_index(data))
    _write(archive_dir / "index.html", _render_archive_index(data))
    canonical_slugs = {domain["slug"] for domain in data["domains"]}
    alias_slugs: set[str] = set()
    for domain in data["domains"]:
        _write(domains_dir / f'{domain["slug"]}.html', _render_domain(data, domain))
        for alias in domain.get("aliases", []):
            if alias in canonical_slugs or alias in alias_slugs:
                raise ValueError(f"duplicate domain route: {alias}")
            alias_slugs.add(alias)
            _write(
                domains_dir / f"{alias}.html",
                _redirect_page(
                    destination=f'{domain["slug"]}.html',
                    title=domain["name"],
                ),
            )
    for paper in data["papers"]:
        _write(papers_dir / f'{paper["paper_id"]}.html', _render_paper(data, paper))
    for archive in data.get("archives", []):
        _write(
            archive_dir / archive["slug"] / "index.html",
            _render_archive(data, archive),
        )

    content_files = [
        ".nojekyll",
        "assets/site.css",
        "assets/site.js",
        "assets/favicon.svg",
        "data/site-data.json",
        "index.html",
        f'{data["site"]["archive"]["directory"]}/index.html',
        *[f'domains/{domain["slug"]}.html' for domain in data["domains"]],
        *[
            f"domains/{alias}.html"
            for domain in data["domains"]
            for alias in domain.get("aliases", [])
        ],
        *[f'papers/{paper["paper_id"]}.html' for paper in data["papers"]],
        *[
            f'{data["site"]["archive"]["directory"]}/{archive["slug"]}/index.html'
            for archive in data.get("archives", [])
        ],
    ]
    file_hashes = {
        relative_path: hashlib.sha256(
            (output_dir / relative_path).read_bytes()
        ).hexdigest()
        for relative_path in sorted(content_files)
    }
    manifest = {
        "schema_version": "1.0",
        "generated_at": data["generated_at"],
        "publication_status": data["publication_status"],
        "data_mode": data.get("data_mode", "single_batch_files"),
        "latest_batch_id": data["batch_id"],
        "latest_edition_date": data.get("latest_edition_date"),
        "counts": {
            "domains": len(data["domains"]),
            "papers": len(data["papers"]),
            "batches": len(data.get("batches", [])),
            "archives": len(data.get("archives", [])),
        },
        "paper_ids": [paper["paper_id"] for paper in data["papers"]],
        "batch_ids": [batch["batch_id"] for batch in data.get("batches", [])],
        "archive_pages": [
            f'{data["site"]["archive"]["directory"]}/{archive["slug"]}/index.html'
            for archive in data.get("archives", [])
        ],
        "canonical_domain_pages": [
            f'domains/{domain["slug"]}.html' for domain in data["domains"]
        ],
        "alias_domain_pages": sorted(
            f"domains/{alias}.html"
            for domain in data["domains"]
            for alias in domain.get("aliases", [])
        ),
        "files": file_hashes,
        "sources": data["sources"],
    }
    manifest_relative_path = "data/site-manifest.json"
    _write(
        output_dir / manifest_relative_path,
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    generated_files = sorted([*content_files, manifest_relative_path])
    return {
        "output_dir": output_dir.resolve().as_posix(),
        "publication_status": data["publication_status"],
        "domain_pages": len(data["domains"]),
        "domain_alias_pages": len(alias_slugs),
        "paper_pages": len(data["papers"]),
        "archive_pages": len(data.get("archives", [])),
        "manifest": manifest_relative_path,
        "generated_files": generated_files,
    }


def main() -> None:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(
        description="Generate the cumulative static research digest preview site."
    )
    parser.add_argument("--database-url", default=settings.database_url)
    parser.add_argument("--site-config", type=Path, default=Path("config/site.v2.json"))
    parser.add_argument("--journal-metrics", type=Path, default=Path("config/journal_metrics.v1.json"))
    parser.add_argument("--task-package", type=Path)
    parser.add_argument("--screening-results", type=Path)
    parser.add_argument("--mvp-results", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("site"))
    parser.add_argument(
        "--publication-status",
        choices=("preview", "published"),
        default="preview",
    )
    args = parser.parse_args()
    legacy_inputs = (
        args.task_package,
        args.screening_results,
        args.mvp_results,
    )
    if any(legacy_inputs) and not all(legacy_inputs):
        parser.error(
            "--task-package, --screening-results and --mvp-results must be "
            "provided together for single-batch compatibility mode"
        )
    if all(legacy_inputs):
        if args.publication_status != "preview":
            parser.error("single-batch compatibility mode only supports preview")
        data = build_site_data(
            database_url=args.database_url,
            site_config_path=args.site_config,
            journal_metrics_path=args.journal_metrics,
            task_package_path=args.task_package,
            screening_results_path=args.screening_results,
            mvp_results_path=args.mvp_results,
        )
    else:
        data = build_cumulative_site_data(
            database_url=args.database_url,
            site_config_path=args.site_config,
            journal_metrics_path=args.journal_metrics,
            publication_status=args.publication_status,
        )
    result = generate_site(data, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
