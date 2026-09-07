from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from literature_intelligence.collectors.pubmed import PubMedRecord
from literature_intelligence.db.models import (
    Author,
    ContentSnapshot,
    Paper,
    PaperAuthor,
    PaperIdentifier,
    SourceRecord,
    utcnow,
)
from literature_intelligence.services.normalization import (
    NormalizedPaper,
    build_abstract_snapshot,
    normalize_pubmed_record,
)


class DeduplicationConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class IngestResult:
    paper_id: int
    action: str
    matched_by: str | None
    possible_duplicate_id: int | None
    content_snapshot_id: str | None

    @property
    def needs_human_review(self) -> bool:
        return self.possible_duplicate_id is not None


def ingest_pubmed_record(
    session: Session,
    record: PubMedRecord,
    *,
    source_record: SourceRecord | None = None,
) -> IngestResult:
    normalized = normalize_pubmed_record(record)
    matched = _find_by_identifiers(session, normalized.identifiers)

    possible_duplicate_id: int | None = None
    if matched is None:
        possible_duplicate_id = _find_title_year_candidate(session, normalized)
        paper = _create_paper(session, normalized, conflict=possible_duplicate_id is not None)
        action = "created"
        matched_by = None
    else:
        paper, matched_by = matched
        _fill_missing_metadata(paper, normalized)
        action = "matched"

    session.flush()
    _attach_identifiers(session, paper.id, normalized.identifiers)
    if action == "created":
        _attach_authors(session, paper.id, normalized)
    snapshot_id = _attach_abstract_snapshot(session, paper.id, normalized, source_record)
    if source_record is not None:
        source_record.paper_id = paper.id
    paper.last_seen_at = utcnow()
    session.flush()

    return IngestResult(
        paper_id=paper.id,
        action=action,
        matched_by=matched_by,
        possible_duplicate_id=possible_duplicate_id,
        content_snapshot_id=snapshot_id,
    )


def _find_by_identifiers(
    session: Session, identifiers: dict[str, str]
) -> tuple[Paper, str] | None:
    predicates = [
        and_(PaperIdentifier.scheme == scheme, PaperIdentifier.value == value)
        for scheme, value in identifiers.items()
    ]
    rows = session.execute(
        select(PaperIdentifier.paper_id, PaperIdentifier.scheme).where(or_(*predicates))
    ).all()
    paper_ids = {row.paper_id for row in rows}
    if len(paper_ids) > 1:
        raise DeduplicationConflict(
            f"identifiers resolve to multiple papers: {sorted(paper_ids)}"
        )
    if not rows:
        return None
    paper_id = rows[0].paper_id
    paper = session.get(Paper, paper_id)
    if paper is None:
        raise DeduplicationConflict(f"identifier points to missing paper {paper_id}")
    schemes = {row.scheme for row in rows}
    matched_by = "pmid" if "pmid" in schemes else "doi" if "doi" in schemes else sorted(schemes)[0]
    return paper, matched_by


def _find_title_year_candidate(session: Session, normalized: NormalizedPaper) -> int | None:
    statement = select(Paper).where(Paper.normalized_title == normalized.normalized_title)
    candidates = list(session.scalars(statement))
    if normalized.publication_date:
        candidates = [
            paper
            for paper in candidates
            if paper.publication_date
            and paper.publication_date.year == normalized.publication_date.year
        ]
    return candidates[0].id if len(candidates) == 1 else None


def _create_paper(session: Session, normalized: NormalizedPaper, *, conflict: bool) -> Paper:
    paper = Paper(
        canonical_title=normalized.canonical_title,
        normalized_title=normalized.normalized_title,
        abstract=normalized.abstract,
        journal_name=normalized.journal_name,
        publication_date=normalized.publication_date,
        publication_date_precision=normalized.publication_date_precision,
        language=normalized.language,
        article_type=normalized.article_type,
        pubmed_url=normalized.pubmed_url,
        publisher_url=normalized.publisher_url,
        metadata_status="conflict" if conflict else "unverified",
    )
    session.add(paper)
    session.flush()
    return paper


def _fill_missing_metadata(paper: Paper, normalized: NormalizedPaper) -> None:
    for name in (
        "abstract",
        "journal_name",
        "publication_date",
        "language",
        "article_type",
        "pubmed_url",
        "publisher_url",
    ):
        if getattr(paper, name) is None and getattr(normalized, name) is not None:
            setattr(paper, name, getattr(normalized, name))


def _attach_identifiers(
    session: Session, paper_id: int, identifiers: dict[str, str]
) -> None:
    for scheme, value in identifiers.items():
        existing = session.scalar(
            select(PaperIdentifier).where(
                PaperIdentifier.scheme == scheme,
                PaperIdentifier.value == value,
            )
        )
        if existing:
            if existing.paper_id != paper_id:
                raise DeduplicationConflict(
                    f"{scheme}:{value} already belongs to paper {existing.paper_id}"
                )
            continue
        session.add(
            PaperIdentifier(
                paper_id=paper_id,
                scheme=scheme,
                value=value,
                is_primary=scheme == "pmid",
            )
        )


def _attach_authors(session: Session, paper_id: int, normalized: NormalizedPaper) -> None:
    for order, normalized_author in enumerate(normalized.authors, start=1):
        author = None
        if normalized_author.orcid:
            author = session.scalar(
                select(Author).where(Author.orcid == normalized_author.orcid)
            )
        if author is None:
            author = Author(
                display_name=normalized_author.display_name,
                normalized_name=normalized_author.normalized_name,
                orcid=normalized_author.orcid,
            )
            session.add(author)
            session.flush()
        session.add(
            PaperAuthor(
                paper_id=paper_id,
                author_id=author.id,
                author_order=order,
                is_corresponding=None,
                affiliation=normalized_author.affiliation,
            )
        )


def _attach_abstract_snapshot(
    session: Session,
    paper_id: int,
    normalized: NormalizedPaper,
    source_record: SourceRecord | None,
) -> str | None:
    built = build_abstract_snapshot(normalized)
    if built is None:
        return None
    sections, digest = built
    existing = session.scalar(
        select(ContentSnapshot).where(
            ContentSnapshot.paper_id == paper_id,
            ContentSnapshot.content_sha256 == digest,
        )
    )
    if existing:
        return existing.id
    snapshot = ContentSnapshot(
        paper_id=paper_id,
        source_record_id=source_record.id if source_record else None,
        source_type="pubmed_abstract",
        content_scope="abstract",
        sections_json=sections,
        content_sha256=digest,
        source_url=normalized.pubmed_url,
        rights_basis="public_metadata",
        is_truncated=False,
    )
    session.add(snapshot)
    session.flush()
    return snapshot.id

