from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from literature_intelligence.db.base import make_engine
from literature_intelligence.db.models import (
    AIRun,
    Paper,
    PaperAnalysis,
    PaperCategory,
    PaperIdentifier,
    PaperTask,
    PipelineRun,
    PromptVersion,
    PublicationBatch,
    ReportItem,
    ResearchDomain,
)
from literature_intelligence.pipeline.static_site import (
    build_cumulative_site_data,
    build_site_data,
    generate_site,
)


BATCH_ID = "b031d6db-5d87-4506-96c6-5364ad63606a"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in {"a", "link", "script"}:
            return
        attributes = dict(attrs)
        value = attributes.get("href") or attributes.get("src")
        if value:
            self.links.append(value)


class StaticSiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_path = PROJECT_ROOT / "data" / "gut_health_mvp.db"
        cls.data = build_cumulative_site_data(
            database_url=f"sqlite:///{(PROJECT_ROOT / 'data' / 'gut_health_mvp.db').as_posix()}",
            site_config_path=PROJECT_ROOT / "config" / "site.v2.json",
            journal_metrics_path=PROJECT_ROOT / "config" / "journal_metrics.v1.json",
        )
        cls.legacy_data = build_site_data(
            database_url=f"sqlite:///{cls.database_path.as_posix()}",
            site_config_path=PROJECT_ROOT / "config" / "site.v2.json",
            journal_metrics_path=PROJECT_ROOT / "config" / "journal_metrics.v1.json",
            task_package_path=PROJECT_ROOT / "data" / "codex_tasks" / f"{BATCH_ID}_gut_health.json",
            screening_results_path=PROJECT_ROOT / "data" / "codex_results" / f"{BATCH_ID}_gut_health_screening.json",
            mvp_results_path=PROJECT_ROOT / "data" / "codex_results" / f"{BATCH_ID}_gut_health_mvp.json",
        )

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output = Path(self.temp_dir.name) / "site"
        self.result = generate_site(self.data, self.output)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_generates_expected_information_architecture(self) -> None:
        self.assertEqual(self.result["domain_pages"], 4)
        self.assertEqual(self.result["domain_alias_pages"], 3)
        self.assertEqual(self.result["paper_pages"], 4)
        self.assertEqual(
            set(self.result["generated_files"]),
            {
                ".nojekyll",
                "index.html",
                "assets/site.css",
                "assets/site.js",
                "assets/favicon.svg",
                "data/site-data.json",
                "data/site-manifest.json",
                "archive/index.html",
                "archive/2026-09-07/index.html",
                "domains/gut-health.html",
                "domains/immune.html",
                "domains/anti-aging.html",
                "domains/musculoskeletal.html",
                "domains/immune-modulation.html",
                "domains/healthy-aging.html",
                "domains/musculoskeletal-health.html",
                "papers/4.html",
                "papers/5.html",
                "papers/6.html",
                "papers/7.html",
            },
        )
        self.assertEqual(self.data["data_mode"], "cumulative_database")
        self.assertEqual(len(self.data["batches"]), 1)
        self.assertEqual(len(self.data["archives"]), 1)
        self.assertEqual(self.result["archive_pages"], 1)

    def test_single_batch_file_mode_remains_compatible(self) -> None:
        self.assertEqual(self.legacy_data["schema_version"], "1.0")
        self.assertEqual(self.legacy_data["summary"]["included"], 4)
        self.assertEqual(
            {paper["paper_id"] for paper in self.legacy_data["papers"]},
            {4, 5, 6, 7},
        )

    def test_manifest_describes_only_current_generated_output(self) -> None:
        manifest = json.loads(
            (self.output / "data" / "site-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["data_mode"], "cumulative_database")
        self.assertEqual(manifest["latest_batch_id"], BATCH_ID)
        self.assertEqual(
            manifest["counts"],
            {"domains": 4, "papers": 4, "batches": 1, "archives": 1},
        )
        self.assertEqual(set(manifest["paper_ids"]), {4, 5, 6, 7})
        self.assertEqual(
            manifest["archive_pages"], ["archive/2026-09-07/index.html"]
        )
        self.assertNotIn("data/site-manifest.json", manifest["files"])
        for relative_path, expected_hash in manifest["files"].items():
            actual_hash = hashlib.sha256(
                (self.output / relative_path).read_bytes()
            ).hexdigest()
            self.assertEqual(actual_hash, expected_hash)

    def test_generated_text_files_use_cross_platform_lf_newlines(self) -> None:
        for path in self.output.rglob("*"):
            if path.is_file():
                self.assertNotIn(b"\r\n", path.read_bytes(), path.as_posix())

    def test_home_and_domain_pages_show_data_and_empty_states(self) -> None:
        home = (self.output / "index.html").read_text(encoding="utf-8")
        self.assertIn("科研文献速递", home)
        self.assertIn("肠道/胃肠健康", home)
        self.assertIn("免疫调节", home)
        self.assertIn("延缓衰老/抗衰老", home)
        self.assertIn("肌骨/筋骨关节健康", home)
        self.assertIn("凝结芽孢杆菌干预功能性肠病的多组学观察", home)
        self.assertIn("本期更新 · 2026-09-07", home)
        self.assertIn('href="archive/2026-09-07/"', home)
        self.assertIn('href="archive/index.html"', home)
        self.assertIn('<span class="journal-name">Gut microbes</span>', home)
        self.assertIn('title="Journal Impact Factor · 2025 JIF · 来源：Taylor &amp; Francis">IF：15.3</a>', home)
        self.assertEqual(home.count('class="paper-row-head"'), 3)

        gut = (self.output / "domains" / "gut-health.html").read_text(encoding="utf-8")
        self.assertIn("本领域共 4 篇", gut)
        self.assertEqual(gut.count('class="paper-row"'), 4)
        for value in ("15.3", "8.8", "5.8", "3.7"):
            self.assertIn(f"IF：{value}", gut)
        self.assertEqual(gut.count('class="journal-name"'), 4)
        for slug in ("immune", "anti-aging", "musculoskeletal"):
            empty = (self.output / "domains" / f"{slug}.html").read_text(encoding="utf-8")
            self.assertIn("本领域共 0 篇", empty)
            self.assertIn("暂无文献", empty)

    def test_archive_index_and_edition_show_the_persisted_batch(self) -> None:
        archive_index = (self.output / "archive" / "index.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("历史速递", archive_index)
        self.assertIn("共 1 期", archive_index)
        self.assertIn('href="2026-09-07/"', archive_index)
        edition = (
            self.output / "archive" / "2026-09-07" / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn("本期 4 篇", edition)
        self.assertEqual(edition.count('class="paper-row"'), 4)
        self.assertIn('href="../../papers/4.html"', edition)
        self.assertIn('href="../index.html">历史速递</a>', edition)

    def test_legacy_domain_urls_redirect_to_canonical_routes(self) -> None:
        redirects = {
            "immune-modulation": "immune.html",
            "healthy-aging": "anti-aging.html",
            "musculoskeletal-health": "musculoskeletal.html",
        }
        for legacy_slug, destination in redirects.items():
            page = (self.output / "domains" / f"{legacy_slug}.html").read_text(
                encoding="utf-8"
            )
            self.assertIn(f'url={destination}', page)
            self.assertIn(f'rel="canonical" href="{destination}"', page)

    def test_detail_pages_contain_required_research_fields(self) -> None:
        detail = (self.output / "papers" / "7.html").read_text(encoding="utf-8")
        for label in (
            "合生元改善中老年慢性便秘的随机对照证据",
            "作者",
            "期刊",
            "发布时间",
            "PMID",
            "DOI",
            "研究背景",
            "研究目的",
            "研究类型",
            "实验设计与主要方法",
            "样本量 / 研究对象",
            "周期",
            "核心研究结果",
            "研究局限",
            "对研发的启示",
            "证据 / 原文支持",
            "Quality Gate",
        ):
            self.assertIn(label, detail)
        self.assertIn("https://pubmed.ncbi.nlm.nih.gov/42226670/", detail)
        self.assertIn("https://doi.org/10.1002/biot.70250", detail)

    def test_all_generated_internal_links_resolve(self) -> None:
        missing: list[str] = []
        for page in self.output.rglob("*.html"):
            parser = LinkParser()
            parser.feed(page.read_text(encoding="utf-8"))
            for link in parser.links:
                if link.startswith(("http://", "https://", "#", "mailto:")):
                    continue
                target = (page.parent / link.split("#", 1)[0]).resolve()
                if not target.exists():
                    missing.append(f"{page.relative_to(self.output)} -> {link}")
        self.assertEqual(missing, [])

    def test_second_batch_adds_new_paper_without_losing_old_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "two_batches.db"
            shutil.copy2(self.database_path, database_path)
            engine = make_engine(f"sqlite:///{database_path.as_posix()}")
            try:
                with Session(engine) as session, session.begin():
                    pipeline_run = PipelineRun(
                        trigger_type="manual",
                        status="PROCESSING",
                        config_snapshot={"test_fixture": "second_edition"},
                    )
                    session.add(pipeline_run)
                    session.flush()
                    paper = Paper(
                        canonical_title="Second edition cumulative test paper",
                        normalized_title="second edition cumulative test paper",
                        abstract="Second edition evidence.",
                        journal_name="Nutrients",
                        publication_date=date(2026, 9, 14),
                        publication_date_precision="day",
                        language="eng",
                        article_type="journal_article",
                        pubmed_url="https://pubmed.ncbi.nlm.nih.gov/99999999/",
                        metadata_status="verified",
                    )
                    session.add(paper)
                    session.flush()
                    new_paper_id = paper.id
                    session.add(
                        PaperIdentifier(
                            paper_id=paper.id,
                            scheme="pmid",
                            value="99999999",
                            is_primary=True,
                        )
                    )
                    task = PaperTask(
                        pipeline_run_id=pipeline_run.id,
                        paper_id=paper.id,
                        state="READY_FOR_PREVIEW",
                    )
                    session.add(task)
                    session.flush()
                    analysis_prompt = session.scalar(
                        select(PromptVersion).where(
                            PromptVersion.prompt_key == "paper_analysis"
                        )
                    )
                    quality_prompt = session.scalar(
                        select(PromptVersion).where(
                            PromptVersion.prompt_key == "report_quality_gate"
                        )
                    )
                    gut_health = session.scalar(
                        select(ResearchDomain).where(
                            ResearchDomain.code == "gut_health"
                        )
                    )
                    assert analysis_prompt and quality_prompt and gut_health
                    analysis_output = {
                        "schema_version": "1.0",
                        "study_type": "observational",
                        "evidence_level": "human_observational",
                        "background": "第二期测试背景。",
                        "objective": "验证累计建站。",
                        "methods": {
                            "design": "观察性研究",
                            "intervention": None,
                            "comparator": None,
                            "duration": None,
                        },
                        "sample": {
                            "size": None,
                            "population": "成人",
                            "species": "人类",
                        },
                        "key_findings": [
                            {
                                "finding_id": "finding_1",
                                "claim": "第二期测试结果。",
                                "evidence": [
                                    {
                                        "section_id": "abstract",
                                        "quote": "Second edition evidence.",
                                        "support_level": "direct",
                                    }
                                ],
                                "certainty": "reported",
                            }
                        ],
                        "limitations": [],
                        "rd_implications": [],
                        "confidence": 0.9,
                        "warnings": [],
                        "needs_human_review": False,
                    }
                    analysis_run = AIRun(
                        paper_task_id=task.id,
                        step="paper_analysis",
                        prompt_version_id=analysis_prompt.id,
                        provider="codex",
                        model="codex_manual",
                        idempotency_key="8" * 64,
                        attempt_no=1,
                        input_sha256="9" * 64,
                        status="succeeded",
                        output_json=analysis_output,
                    )
                    session.add(analysis_run)
                    session.flush()
                    analysis = PaperAnalysis(
                        paper_id=paper.id,
                        ai_run_id=analysis_run.id,
                        version=1,
                        study_type="observational",
                        background="第二期测试背景。",
                        objective="验证累计建站。",
                        methods_json=analysis_output["methods"],
                        sample_json=analysis_output["sample"],
                        key_findings_json=analysis_output["key_findings"],
                        limitations_json=[],
                        rd_implications_json=[],
                        confidence=Decimal("0.9000"),
                        needs_human_review=False,
                        review_reasons_json=[],
                    )
                    session.add(analysis)
                    session.flush()
                    session.add(
                        PaperCategory(
                            paper_id=paper.id,
                            domain_id=gut_health.id,
                            score=Decimal("0.9000"),
                            reason="累计建站回归测试",
                            source="codex",
                        )
                    )
                    quality_output = {
                        "schema_version": "1.0",
                        "decision": "pass",
                        "quality_score": 0.95,
                        "checks": [],
                        "required_actions": [],
                        "warnings": [],
                        "needs_human_review": False,
                    }
                    session.add(
                        AIRun(
                            paper_task_id=task.id,
                            step="report_quality_gate",
                            prompt_version_id=quality_prompt.id,
                            provider="codex",
                            model="codex_manual",
                            idempotency_key="a" * 64,
                            attempt_no=1,
                            input_sha256="b" * 64,
                            status="succeeded",
                            output_json=quality_output,
                        )
                    )
                    batch = PublicationBatch(
                        pipeline_run_id=pipeline_run.id,
                        edition_date=date(2026, 9, 14),
                        edition_type="weekly",
                        title="科研文献速递 2026-09-14",
                        status="draft",
                    )
                    session.add(batch)
                    session.flush()
                    session.add(
                        ReportItem(
                            publication_batch_id=batch.id,
                            paper_id=paper.id,
                            analysis_id=analysis.id,
                            display_order=1,
                            ai_title="第二期累计建站测试文献",
                            summary_json={
                                "schema_version": "1.0",
                                "ai_title": "第二期累计建站测试文献",
                                "research_background": "第二期测试背景。",
                                "research_objective": "验证累计建站。",
                                "study_design": "观察性研究",
                                "core_results": ["第二期测试结果。"],
                                "limitations": [],
                                "rd_implications": [],
                                "source_links": [
                                    "https://pubmed.ncbi.nlm.nih.gov/99999999/"
                                ],
                                "confidence": 0.9,
                                "warnings": [],
                                "needs_human_review": False,
                            },
                            quality_score=Decimal("0.9500"),
                            review_status="auto_approved",
                        )
                    )
                cumulative_data = build_cumulative_site_data(
                    database_url=f"sqlite:///{database_path.as_posix()}",
                    site_config_path=PROJECT_ROOT / "config" / "site.v2.json",
                    journal_metrics_path=PROJECT_ROOT
                    / "config"
                    / "journal_metrics.v1.json",
                )
            finally:
                engine.dispose()

            output = Path(temp_dir) / "site"
            generate_site(cumulative_data, output)
            self.assertEqual(cumulative_data["summary"]["included"], 5)
            self.assertEqual(len(cumulative_data["batches"]), 2)
            self.assertEqual(len(cumulative_data["archives"]), 2)
            self.assertEqual(cumulative_data["latest_edition_date"], "2026-09-14")
            self.assertTrue((output / "papers" / "4.html").exists())
            self.assertTrue((output / "papers" / f"{new_paper_id}.html").exists())
            self.assertTrue(
                (output / "archive" / "2026-09-07" / "index.html").exists()
            )
            self.assertTrue(
                (output / "archive" / "2026-09-14" / "index.html").exists()
            )
            first_edition = (
                output / "archive" / "2026-09-07" / "index.html"
            ).read_text(encoding="utf-8")
            second_edition = (
                output / "archive" / "2026-09-14" / "index.html"
            ).read_text(encoding="utf-8")
            self.assertEqual(first_edition.count('class="paper-row"'), 4)
            self.assertEqual(second_edition.count('class="paper-row"'), 1)
            self.assertNotIn("第二期累计建站测试文献", first_edition)
            self.assertIn("第二期累计建站测试文献", second_edition)
            gut_page = (output / "domains" / "gut-health.html").read_text(
                encoding="utf-8"
            )
            self.assertIn("本领域共 5 篇", gut_page)
            self.assertIn("第二期累计建站测试文献", gut_page)


if __name__ == "__main__":
    unittest.main()
