from __future__ import annotations

import unittest

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from literature_intelligence.collectors.pubmed import PubMedAuthor, PubMedRecord
from literature_intelligence.db.base import create_database
from literature_intelligence.db.models import ContentSnapshot, Paper, PaperIdentifier
from literature_intelligence.services.normalization import normalize_doi, normalize_title
from literature_intelligence.services.paper_ingest import ingest_pubmed_record


def make_record(
    *,
    pmid: str = "12345678",
    doi: str | None = "https://doi.org/10.1000/EXAMPLE.",
    title: str = "Effects of Example: On Gut Health",
    year: int = 2026,
) -> PubMedRecord:
    return PubMedRecord(
        pmid=pmid,
        title=title,
        abstract="A structured abstract with a reported result.",
        journal="Journal of Tests",
        publication_year=year,
        publication_month="Sep",
        publication_day=1,
        medline_date=None,
        doi=doi,
        pmcid=None,
        language="eng",
        publication_types=("Randomized Controlled Trial",),
        authors=(PubMedAuthor(display_name="Jane Smith", orcid="0000-0001-2345-6789"),),
    )


class NormalizationTests(unittest.TestCase):
    def test_normalizes_doi_url_and_title_punctuation(self) -> None:
        self.assertEqual(normalize_doi(" DOI: https://doi.org/10.1000/EXAMPLE. "), "10.1000/example")
        self.assertEqual(
            normalize_title(" Effects—of Example: ON gut health! "),
            "effects of example on gut health",
        )


class PaperIngestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_database("sqlite:///:memory:")

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_same_pmid_is_idempotent_and_snapshot_is_reused(self) -> None:
        with Session(self.engine) as session:
            first = ingest_pubmed_record(session, make_record())
            second = ingest_pubmed_record(session, make_record())
            session.commit()

            self.assertEqual(first.paper_id, second.paper_id)
            self.assertEqual(first.content_snapshot_id, second.content_snapshot_id)
            self.assertEqual(session.scalar(select(func.count()).select_from(Paper)), 1)
            self.assertEqual(
                session.scalar(select(func.count()).select_from(ContentSnapshot)), 1
            )
            identifiers = session.scalars(
                select(PaperIdentifier).where(PaperIdentifier.paper_id == first.paper_id)
            ).all()
            self.assertEqual({item.scheme for item in identifiers}, {"pmid", "doi"})

    def test_normalized_doi_matches_existing_paper(self) -> None:
        with Session(self.engine) as session:
            first = ingest_pubmed_record(session, make_record())
            second = ingest_pubmed_record(
                session,
                make_record(pmid="87654321", doi="doi:10.1000/example"),
            )
            session.commit()

            self.assertEqual(first.paper_id, second.paper_id)
            self.assertEqual(second.matched_by, "doi")

    def test_title_year_match_requires_review_instead_of_auto_merge(self) -> None:
        with Session(self.engine) as session:
            first = ingest_pubmed_record(session, make_record(doi=None))
            second = ingest_pubmed_record(
                session,
                make_record(
                    pmid="99999999",
                    doi=None,
                    title="Effects of Example — On Gut Health",
                ),
            )
            session.commit()

            self.assertNotEqual(first.paper_id, second.paper_id)
            self.assertTrue(second.needs_human_review)
            self.assertEqual(second.possible_duplicate_id, first.paper_id)
            self.assertEqual(session.get(Paper, second.paper_id).metadata_status, "conflict")


if __name__ == "__main__":
    unittest.main()
