from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from literature_intelligence.db.base import create_database
from literature_intelligence.db.models import (
    Author,
    ContentSnapshot,
    Paper,
    PaperAuthor,
    PaperIdentifier,
    PaperTask,
    PipelineRun,
    ResearchDomain,
    SearchQuery,
    SourceRecord,
    TaskStateEvent,
    VerificationRecord,
)
from literature_intelligence.pipeline.codex_task_export import CodexTaskPackageExporter
from literature_intelligence.services.normalization import compute_sections_sha256


class CodexTaskPackageExporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.engine = create_database(f"sqlite:///{(root / 'tasks.db').as_posix()}")
        self.prompt_spec = root / "prompts.v1.yaml"
        self.prompt_spec.write_text("spec_version: '1.0'\n", encoding="utf-8")
        self.output_path = root / "exports" / "tasks.json"

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _seed(self, *, state: str = "CONTENT_READY", hash_override: str | None = None) -> str:
        with Session(self.engine) as session:
            domain = ResearchDomain(
                code="gut_health",
                name_zh="肠道健康",
                description="肠道微生态、益生菌与相关研发研究",
            )
            run = PipelineRun(trigger_type="manual", status="PROCESSING", config_snapshot={})
            paper = Paper(
                canonical_title="A verified probiotic study",
                normalized_title="a verified probiotic study",
                abstract="A randomized study reported a microbiome outcome.",
                journal_name="Gut Research",
                publication_date=date(2099, 1, 1),
                publication_date_precision="day",
                language="eng",
                article_type="randomized_controlled_trial",
                pubmed_url="https://pubmed.ncbi.nlm.nih.gov/12345678/",
                publisher_url="https://doi.org/10.1000/example",
                metadata_status="verified",
            )
            session.add_all([domain, run, paper])
            session.flush()
            query = SearchQuery(
                domain_id=domain.id,
                source="pubmed",
                query_text="probiotics[Title]",
                lookback_days=7,
                version=1,
            )
            session.add(query)
            session.flush()
            source = SourceRecord(
                pipeline_run_id=run.id,
                search_query_id=query.id,
                source="pubmed",
                external_id="12345678",
                raw_payload={
                    "publication_year": 2099,
                    "publication_month": "Jan",
                    "publication_day": 1,
                    "medline_date": None,
                    "publication_date_source": "Article/Journal/JournalIssue/PubDate",
                    "publication_model": "Print-Electronic",
                    "citation_status": "MEDLINE",
                    "article_dates": [
                        {
                            "date_type": "Electronic",
                            "year": "2098",
                            "month": "12",
                            "day": "20",
                        }
                    ],
                    "history_dates": [],
                },
                payload_sha256="1" * 64,
                paper_id=paper.id,
            )
            session.add(source)
            session.flush()
            sections = [
                {
                    "section_id": "abstract",
                    "name": "Abstract",
                    "text": paper.abstract,
                }
            ]
            snapshot = ContentSnapshot(
                paper_id=paper.id,
                source_record_id=source.id,
                source_type="pubmed_abstract",
                content_scope="abstract",
                sections_json=sections,
                content_sha256=hash_override or compute_sections_sha256(sections),
                source_url=paper.pubmed_url,
                rights_basis="public_metadata",
                is_truncated=False,
            )
            author = Author(display_name="Jane Doe", normalized_name="jane doe")
            session.add_all([snapshot, author])
            session.flush()
            task = PaperTask(
                pipeline_run_id=run.id,
                paper_id=paper.id,
                content_snapshot_id=snapshot.id,
                state=state,
            )
            session.add_all(
                [
                    task,
                    PaperIdentifier(
                        paper_id=paper.id,
                        scheme="pmid",
                        value="12345678",
                        is_primary=True,
                    ),
                    PaperIdentifier(
                        paper_id=paper.id,
                        scheme="doi",
                        value="10.1000/example",
                    ),
                    PaperAuthor(
                        paper_id=paper.id,
                        author_id=author.id,
                        author_order=1,
                        affiliation="Example Institute",
                    ),
                ]
            )
            session.flush()
            session.add_all(
                [
                    VerificationRecord(
                        paper_task_id=task.id,
                        check_type="pmid",
                        target="12345678",
                        status="valid",
                    ),
                    VerificationRecord(
                        paper_task_id=task.id,
                        check_type="doi",
                        target="10.1000/example",
                        status="valid",
                        http_status=200,
                    ),
                    VerificationRecord(
                        paper_task_id=task.id,
                        check_type="url",
                        target=paper.pubmed_url,
                        status="valid",
                        http_status=200,
                        resolved_url=paper.pubmed_url,
                    ),
                ]
            )
            session.commit()
            return task.id

    def test_exports_complete_prompt_input_without_mutating_database(self) -> None:
        task_id = self._seed()
        exporter = CodexTaskPackageExporter(
            engine=self.engine,
            prompt_spec_path=self.prompt_spec,
        )
        package = exporter.write(
            self.output_path,
            batch_id="gut_health_test_batch",
            domain_code="gut_health",
        )

        self.assertEqual(package["task_count"], 1)
        self.assertEqual(package["batch"]["batch_id"], "gut_health_test_batch")
        self.assertEqual(package["domain"]["code"], "gut_health")
        exported = package["tasks"][0]
        self.assertEqual(exported["paper_task_id"], task_id)
        self.assertEqual(len(exported["input_sha256"]), 64)
        input_data = exported["input_data"]
        self.assertEqual(input_data["contract_version"], "1.0")
        self.assertEqual(input_data["task"], "paper_relevance")
        self.assertEqual(input_data["paper"]["identifiers"]["pmid"], "12345678")
        self.assertEqual(
            input_data["paper"]["study_metadata"]["authors"][0]["name"],
            "Jane Doe",
        )
        self.assertEqual(
            input_data["content_snapshot"]["sections"][0]["section_id"],
            "abstract",
        )
        self.assertEqual(
            input_data["domain_context"]["active_domains"][0]["code"],
            "gut_health",
        )
        date_metadata = input_data["paper"]["study_metadata"]["publication_date_metadata"]
        self.assertTrue(date_metadata["is_future_date"])
        self.assertEqual(date_metadata["source_path"], "Article/Journal/JournalIssue/PubDate")
        self.assertEqual(date_metadata["warnings"][0]["code"], "FUTURE_PUBLICATION_DATE")
        self.assertEqual(
            input_data["verified_source_links"],
            [
                "https://pubmed.ncbi.nlm.nih.gov/12345678/",
                "https://doi.org/10.1000/example",
            ],
        )
        self.assertEqual(
            [step["prompt_key"] for step in exported["codex_workflow"]],
            [
                "paper_relevance",
                "paper_analysis",
                "paper_classification",
                "report_composition",
                "report_quality_gate",
            ],
        )
        self.assertEqual(json.loads(self.output_path.read_text(encoding="utf-8")), package)

        with Session(self.engine) as session:
            self.assertEqual(session.get(PaperTask, task_id).state, "CONTENT_READY")
            self.assertEqual(
                session.scalar(select(func.count()).select_from(TaskStateEvent)),
                0,
            )

    def test_only_exports_content_ready_tasks(self) -> None:
        self._seed(state="VERIFIED")
        package = CodexTaskPackageExporter(
            engine=self.engine,
            prompt_spec_path=self.prompt_spec,
        ).build()
        self.assertEqual(package["task_count"], 0)

    def test_revalidates_snapshot_before_export(self) -> None:
        self._seed(hash_override="0" * 64)
        exporter = CodexTaskPackageExporter(
            engine=self.engine,
            prompt_spec_path=self.prompt_spec,
        )
        with self.assertRaisesRegex(ValueError, "CONTENT_HASH_MISMATCH"):
            exporter.build()


if __name__ == "__main__":
    unittest.main()
