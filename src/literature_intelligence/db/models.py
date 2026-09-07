from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from literature_intelligence.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid4())


class ResearchDomain(Base):
    __tablename__ = "research_domains"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name_zh: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SearchQuery(Base):
    __tablename__ = "search_queries"
    __table_args__ = (
        UniqueConstraint("domain_id", "source", "version", name="uq_search_query_version"),
        CheckConstraint("lookback_days > 0", name="ck_search_query_lookback_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("research_domains.id", ondelete="RESTRICT"))
    source: Mapped[str] = mapped_column(String(32), default="pubmed")
    query_text: Mapped[str] = mapped_column(Text)
    lookback_days: Mapped[int] = mapped_column(default=7)
    version: Mapped[int] = mapped_column(default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    __table_args__ = (Index("ix_pipeline_runs_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    trigger_type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), default="CREATED")
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_candidates: Mapped[int] = mapped_column(default=0)
    published_count: Mapped[int] = mapped_column(default=0)
    failed_count: Mapped[int] = mapped_column(default=0)
    error_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceRecord(Base):
    __tablename__ = "source_records"
    __table_args__ = (
        UniqueConstraint("pipeline_run_id", "source", "external_id", name="uq_source_record_external"),
        Index("ix_source_records_paper_id", "paper_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    pipeline_run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.id", ondelete="CASCADE"))
    search_query_id: Mapped[int] = mapped_column(ForeignKey("search_queries.id", ondelete="RESTRICT"))
    source: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(255))
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    paper_id: Mapped[int | None] = mapped_column(ForeignKey("papers.id", ondelete="SET NULL"))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Paper(Base):
    __tablename__ = "papers"
    __table_args__ = (
        Index("ix_papers_normalized_title", "normalized_title"),
        Index("ix_papers_publication_date", "publication_date"),
        Index("ix_papers_metadata_status", "metadata_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_title: Mapped[str] = mapped_column(Text)
    normalized_title: Mapped[str] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    journal_name: Mapped[str | None] = mapped_column(String(255))
    publication_date: Mapped[date | None] = mapped_column(Date)
    publication_date_precision: Mapped[str] = mapped_column(String(8), default="unknown")
    language: Mapped[str | None] = mapped_column(String(16))
    article_type: Mapped[str | None] = mapped_column(String(64))
    pubmed_url: Mapped[str | None] = mapped_column(Text)
    publisher_url: Mapped[str | None] = mapped_column(Text)
    metadata_status: Mapped[str] = mapped_column(String(24), default="unverified")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ContentSnapshot(Base):
    __tablename__ = "content_snapshots"
    __table_args__ = (
        UniqueConstraint("paper_id", "content_sha256", name="uq_content_snapshot_hash"),
        CheckConstraint(
            "source_type IN ('pubmed_abstract','pmc_open_access','publisher_excerpt','manual_excerpt')",
            name="ck_content_snapshot_source_type",
        ),
        CheckConstraint(
            "content_scope IN ('abstract','open_access_text','excerpt')",
            name="ck_content_snapshot_scope",
        ),
        CheckConstraint(
            "rights_basis IN ('public_metadata','open_access','licensed_excerpt','user_provided')",
            name="ck_content_snapshot_rights",
        ),
        Index("ix_content_snapshots_source_record", "source_record_id"),
        Index("ix_content_snapshots_retrieved_at", "retrieved_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"))
    source_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_records.id", ondelete="SET NULL")
    )
    source_type: Mapped[str] = mapped_column(String(32))
    content_scope: Mapped[str] = mapped_column(String(24))
    sections_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    content_sha256: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[str] = mapped_column(Text)
    rights_basis: Mapped[str] = mapped_column(String(32))
    is_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    truncation_reason: Mapped[str | None] = mapped_column(String(255))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaperIdentifier(Base):
    __tablename__ = "paper_identifiers"
    __table_args__ = (
        UniqueConstraint("scheme", "value", name="uq_paper_identifier"),
        Index("ix_paper_identifiers_paper_id", "paper_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"))
    scheme: Mapped[str] = mapped_column(String(16))
    value: Mapped[str] = mapped_column(String(255))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Author(Base):
    __tablename__ = "authors"

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255))
    normalized_name: Mapped[str] = mapped_column(String(255), index=True)
    orcid: Mapped[str | None] = mapped_column(String(32), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaperAuthor(Base):
    __tablename__ = "paper_authors"
    __table_args__ = (
        UniqueConstraint("paper_id", "author_order", name="uq_paper_author_order"),
        CheckConstraint("author_order > 0", name="ck_paper_author_order_positive"),
    )

    paper_id: Mapped[int] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    author_id: Mapped[int] = mapped_column(
        ForeignKey("authors.id", ondelete="RESTRICT"), primary_key=True
    )
    author_order: Mapped[int]
    is_corresponding: Mapped[bool | None] = mapped_column(Boolean)
    affiliation: Mapped[str | None] = mapped_column(Text)


class PaperTask(Base):
    __tablename__ = "paper_tasks"
    __table_args__ = (
        UniqueConstraint("pipeline_run_id", "paper_id", name="uq_paper_task_run_paper"),
        Index("ix_paper_tasks_state_retry", "state", "next_retry_at"),
        Index("ix_paper_tasks_run_state", "pipeline_run_id", "state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    pipeline_run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.id", ondelete="CASCADE"))
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="RESTRICT"))
    content_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("content_snapshots.id", ondelete="SET NULL")
    )
    state: Mapped[str] = mapped_column(String(32), default="DISCOVERED")
    resume_state: Mapped[str | None] = mapped_column(String(32))
    relevance_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    attempt_count: Mapped[int] = mapped_column(default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(128))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    state_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class VerificationRecord(Base):
    __tablename__ = "verification_records"
    __table_args__ = (
        Index("ix_verification_task_type_time", "paper_task_id", "check_type", "checked_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_task_id: Mapped[str] = mapped_column(ForeignKey("paper_tasks.id", ondelete="CASCADE"))
    check_type: Mapped[str] = mapped_column(String(32))
    target: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    http_status: Mapped[int | None]
    resolved_url: Mapped[str | None] = mapped_column(Text)
    response_fingerprint: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TaskStateEvent(Base):
    __tablename__ = "task_state_events"
    __table_args__ = (
        Index("ix_task_events_task_time", "paper_task_id", "created_at"),
        Index("ix_task_events_state_time", "to_state", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_task_id: Mapped[str] = mapped_column(ForeignKey("paper_tasks.id", ondelete="CASCADE"))
    from_state: Mapped[str | None] = mapped_column(String(32))
    to_state: Mapped[str] = mapped_column(String(32))
    event_type: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(128))
    reason_code: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint("prompt_key", "version", name="uq_prompt_version"),
        UniqueConstraint("template_sha256", name="uq_prompt_template_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    prompt_key: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(32))
    template_sha256: Mapped[str] = mapped_column(String(64))
    template_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    schema_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AIRun(Base):
    __tablename__ = "ai_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", "attempt_no", name="uq_ai_run_attempt"),
        Index("ix_ai_runs_key_status", "idempotency_key", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    paper_task_id: Mapped[str] = mapped_column(ForeignKey("paper_tasks.id", ondelete="CASCADE"))
    step: Mapped[str] = mapped_column(String(32))
    prompt_version_id: Mapped[int] = mapped_column(ForeignKey("prompt_versions.id", ondelete="RESTRICT"))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    idempotency_key: Mapped[str] = mapped_column(String(64))
    attempt_no: Mapped[int] = mapped_column(CheckConstraint("attempt_no > 0"))
    input_sha256: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="started")
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    latency_ms: Mapped[int | None]
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaperAnalysis(Base):
    __tablename__ = "paper_analyses"
    __table_args__ = (UniqueConstraint("paper_id", "version", name="uq_paper_analysis_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="RESTRICT"))
    ai_run_id: Mapped[str] = mapped_column(
        ForeignKey("ai_runs.id", ondelete="RESTRICT"), unique=True
    )
    version: Mapped[int]
    study_type: Mapped[str | None] = mapped_column(String(64))
    background: Mapped[str | None] = mapped_column(Text)
    objective: Mapped[str | None] = mapped_column(Text)
    methods_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    sample_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    key_findings_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    limitations_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    rd_implications_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    needs_human_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_reasons_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnalysisEvidence(Base):
    __tablename__ = "analysis_evidence"
    __table_args__ = (Index("ix_evidence_analysis_claim", "analysis_id", "claim_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("paper_analyses.id", ondelete="CASCADE"))
    claim_key: Mapped[str] = mapped_column(String(64))
    source_section: Mapped[str] = mapped_column(String(64))
    quote_text: Mapped[str] = mapped_column(Text)
    char_start: Mapped[int | None]
    char_end: Mapped[int | None]
    evidence_sha256: Mapped[str] = mapped_column(String(64))
    support_level: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaperCategory(Base):
    __tablename__ = "paper_categories"

    paper_id: Mapped[int] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    domain_id: Mapped[int] = mapped_column(
        ForeignKey("research_domains.id", ondelete="RESTRICT"), primary_key=True
    )
    score: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    reason: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PublicationBatch(Base):
    __tablename__ = "publication_batches"
    __table_args__ = (
        UniqueConstraint("edition_date", "edition_type", name="uq_publication_edition"),
        CheckConstraint(
            "status != 'published' OR "
            "(confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL "
            "AND preview_sha256 IS NOT NULL AND output_path IS NOT NULL)",
            name="ck_publication_requires_confirmation",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    pipeline_run_id: Mapped[str] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="RESTRICT"), unique=True
    )
    edition_date: Mapped[date] = mapped_column(Date)
    edition_type: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(24), default="draft")
    preview_path: Mapped[str | None] = mapped_column(Text)
    preview_sha256: Mapped[str | None] = mapped_column(String(64))
    output_path: Mapped[str | None] = mapped_column(Text)
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    confirmed_by: Mapped[str | None] = mapped_column(String(128))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmation_note: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReportItem(Base):
    __tablename__ = "report_items"
    __table_args__ = (
        UniqueConstraint("publication_batch_id", "paper_id", name="uq_report_batch_paper"),
        UniqueConstraint("publication_batch_id", "display_order", name="uq_report_batch_order"),
        Index("ix_report_items_review_quality", "review_status", "quality_score"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    publication_batch_id: Mapped[str] = mapped_column(
        ForeignKey("publication_batches.id", ondelete="CASCADE")
    )
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="RESTRICT"))
    analysis_id: Mapped[int] = mapped_column(ForeignKey("paper_analyses.id", ondelete="RESTRICT"))
    display_order: Mapped[int]
    ai_title: Mapped[str] = mapped_column(String(255))
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    quality_score: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    review_status: Mapped[str] = mapped_column(String(16))
    reviewed_by: Mapped[str | None] = mapped_column(String(128))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

