from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from literature_intelligence.pipeline.codex_screening import (
    ScreeningValidationError,
    file_sha256,
    validate_screening_results,
)


ANALYSIS_FIELDS = {
    "schema_version", "study_type", "evidence_level", "background", "objective",
    "methods", "sample", "key_findings", "limitations", "rd_implications",
    "confidence", "warnings", "needs_human_review",
}
CLASSIFICATION_FIELDS = {
    "schema_version", "classifications", "unclassified", "confidence", "warnings",
    "needs_human_review",
}
REPORT_FIELDS = {
    "schema_version", "ai_title", "research_background", "research_objective",
    "study_design", "core_results", "limitations", "rd_implications", "source_links",
    "confidence", "warnings", "needs_human_review",
}
QUALITY_FIELDS = {
    "schema_version", "decision", "quality_score", "issues", "confidence", "warnings",
    "needs_human_review",
}
STUDY_TYPES = {
    "randomized_controlled_trial", "clinical_trial", "observational", "systematic_review",
    "meta_analysis", "animal", "in_vitro", "in_silico", "other", None,
}
EVIDENCE_LEVELS = {
    "human_interventional", "human_observational", "animal", "in_vitro",
    "computational", "mixed", None,
}
ISSUE_CODES = {
    "unsupported_fact", "numeric_mismatch", "causal_overclaim", "evidence_level_mismatch",
    "human_efficacy_implication", "unverified_url", "insufficient_evidence", "other",
}
BANNED_REPORT_TERMS = {"证实", "治愈", "突破", "革命性"}
NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?")


class MVPValidationError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MVPValidationError(message)


def _score(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and 0 <= value <= 1


def _text_or_none(value: object, max_length: int) -> bool:
    return value is None or isinstance(value, str) and len(value) <= max_length


def _string_list(value: object, *, max_items: int, max_length: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) <= max_items
        and all(isinstance(item, str) and len(item) <= max_length for item in value)
    )


def _validate_analysis(output: object, task: dict[str, Any]) -> None:
    task_id = task["paper_task_id"]
    _require(isinstance(output, dict), f"{task_id}: analysis must be an object")
    assert isinstance(output, dict)
    _require(set(output) == ANALYSIS_FIELDS, f"{task_id}: analysis fields mismatch")
    _require(output["schema_version"] == "1.0", f"{task_id}: analysis schema version")
    _require(output["study_type"] in STUDY_TYPES, f"{task_id}: invalid study_type")
    _require(output["evidence_level"] in EVIDENCE_LEVELS, f"{task_id}: invalid evidence_level")
    _require(_text_or_none(output["background"], 1200), f"{task_id}: invalid background")
    _require(_text_or_none(output["objective"], 800), f"{task_id}: invalid objective")

    methods = output["methods"]
    _require(isinstance(methods, dict), f"{task_id}: methods must be an object")
    _require(set(methods) == {"design", "intervention", "comparator", "duration"}, f"{task_id}: methods fields mismatch")
    for key, maximum in (("design", 500), ("intervention", 500), ("comparator", 500), ("duration", 200)):
        _require(_text_or_none(methods[key], maximum), f"{task_id}: invalid methods.{key}")

    sample = output["sample"]
    _require(isinstance(sample, dict), f"{task_id}: sample must be an object")
    _require(set(sample) == {"size", "population", "species"}, f"{task_id}: sample fields mismatch")
    _require(sample["size"] is None or isinstance(sample["size"], int) and not isinstance(sample["size"], bool) and sample["size"] >= 1, f"{task_id}: invalid sample size")
    _require(_text_or_none(sample["population"], 500), f"{task_id}: invalid population")
    _require(_text_or_none(sample["species"], 200), f"{task_id}: invalid species")

    sections = {
        section["section_id"]: section["text"]
        for section in task["input_data"]["content_snapshot"]["sections"]
    }
    findings = output["key_findings"]
    _require(isinstance(findings, list) and len(findings) <= 5, f"{task_id}: invalid key_findings")
    finding_ids: set[str] = set()
    for finding in findings:
        _require(isinstance(finding, dict) and set(finding) == {"finding_id", "claim", "evidence", "certainty"}, f"{task_id}: finding fields mismatch")
        finding_id = finding["finding_id"]
        _require(isinstance(finding_id, str) and re.fullmatch(r"finding_[1-5]", finding_id) is not None, f"{task_id}: invalid finding_id")
        _require(finding_id not in finding_ids, f"{task_id}: duplicate finding_id")
        finding_ids.add(finding_id)
        _require(isinstance(finding["claim"], str) and len(finding["claim"]) <= 800, f"{task_id}: invalid finding claim")
        _require(finding["certainty"] in {"reported", "inferred"}, f"{task_id}: invalid finding certainty")
        evidence = finding["evidence"]
        _require(isinstance(evidence, list) and bool(evidence), f"{task_id}: finding requires evidence")
        for item in evidence:
            _require(isinstance(item, dict) and set(item) == {"section_id", "quote", "support_level"}, f"{task_id}: evidence fields mismatch")
            section_id = item["section_id"]
            quote = item["quote"]
            _require(section_id in sections, f"{task_id}: evidence section not found")
            _require(isinstance(quote, str) and 1 <= len(quote) <= 1200 and quote in sections[section_id], f"{task_id}: evidence quote is not an exact source substring")
            _require(item["support_level"] in {"direct", "indirect", "insufficient"}, f"{task_id}: invalid support_level")

    limitations = output["limitations"]
    _require(isinstance(limitations, list) and len(limitations) <= 8, f"{task_id}: invalid limitations")
    for item in limitations:
        _require(isinstance(item, dict) and set(item) == {"text", "basis"}, f"{task_id}: limitation fields mismatch")
        _require(isinstance(item["text"], str) and len(item["text"]) <= 600, f"{task_id}: invalid limitation")
        _require(item["basis"] in {"reported", "inferred"}, f"{task_id}: invalid limitation basis")

    implications = output["rd_implications"]
    _require(isinstance(implications, list) and len(implications) <= 5, f"{task_id}: invalid implications")
    for item in implications:
        _require(isinstance(item, dict) and set(item) == {"text", "evidence_boundary"}, f"{task_id}: implication fields mismatch")
        _require(isinstance(item["text"], str) and len(item["text"]) <= 600, f"{task_id}: invalid implication")
        _require(isinstance(item["evidence_boundary"], str) and len(item["evidence_boundary"]) <= 400, f"{task_id}: invalid evidence boundary")

    _require(_score(output["confidence"]), f"{task_id}: invalid analysis confidence")
    _require(_string_list(output["warnings"], max_items=100, max_length=300), f"{task_id}: invalid analysis warnings")
    _require(isinstance(output["needs_human_review"], bool), f"{task_id}: invalid analysis review flag")

    if sample["size"] is not None:
        source_text = " ".join(sections.values())
        _require(str(sample["size"]) in source_text, f"{task_id}: sample size is absent from source")


def _validate_classification(output: object, task: dict[str, Any]) -> None:
    task_id = task["paper_task_id"]
    _require(isinstance(output, dict) and set(output) == CLASSIFICATION_FIELDS, f"{task_id}: classification fields mismatch")
    assert isinstance(output, dict)
    _require(output["schema_version"] == "1.0", f"{task_id}: classification schema version")
    active = {item["code"] for item in task["input_data"]["domain_context"]["active_domains"]}
    rows = output["classifications"]
    _require(isinstance(rows, list), f"{task_id}: classifications must be a list")
    codes: list[str] = []
    for row in rows:
        _require(isinstance(row, dict) and set(row) == {"domain_code", "score", "reason"}, f"{task_id}: classification row fields mismatch")
        _require(row["domain_code"] in active, f"{task_id}: classification domain is not active")
        _require(_score(row["score"]), f"{task_id}: invalid classification score")
        _require(isinstance(row["reason"], str) and len(row["reason"]) <= 400, f"{task_id}: invalid classification reason")
        codes.append(row["domain_code"])
    _require(len(codes) == len(set(codes)), f"{task_id}: duplicate classification")
    _require(isinstance(output["unclassified"], bool) and output["unclassified"] == (not rows), f"{task_id}: inconsistent unclassified flag")
    _require(_score(output["confidence"]), f"{task_id}: invalid classification confidence")
    _require(_string_list(output["warnings"], max_items=100, max_length=300), f"{task_id}: invalid classification warnings")
    _require(isinstance(output["needs_human_review"], bool), f"{task_id}: invalid classification review flag")


def _validate_report(output: object, task: dict[str, Any]) -> None:
    task_id = task["paper_task_id"]
    _require(isinstance(output, dict) and set(output) == REPORT_FIELDS, f"{task_id}: report fields mismatch")
    assert isinstance(output, dict)
    _require(output["schema_version"] == "1.0", f"{task_id}: report schema version")
    _require(isinstance(output["ai_title"], str) and 1 <= len(output["ai_title"]) <= 40, f"{task_id}: invalid AI title")
    _require(_text_or_none(output["research_background"], 1000), f"{task_id}: invalid report background")
    _require(_text_or_none(output["research_objective"], 600), f"{task_id}: invalid report objective")
    _require(_text_or_none(output["study_design"], 1000), f"{task_id}: invalid study design")
    _require(_string_list(output["core_results"], max_items=5, max_length=700), f"{task_id}: invalid core results")
    _require(_string_list(output["limitations"], max_items=8, max_length=500), f"{task_id}: invalid report limitations")
    _require(_string_list(output["rd_implications"], max_items=5, max_length=600), f"{task_id}: invalid report implications")
    links = output["source_links"]
    verified = task["input_data"]["verified_source_links"]
    _require(isinstance(links, list) and len(links) == len(set(links)), f"{task_id}: invalid source links")
    _require(set(links) == set(verified), f"{task_id}: report links must exactly match verified links")
    _require(_score(output["confidence"]), f"{task_id}: invalid report confidence")
    _require(_string_list(output["warnings"], max_items=100, max_length=300), f"{task_id}: invalid report warnings")
    _require(isinstance(output["needs_human_review"], bool), f"{task_id}: invalid report review flag")

    report_text = json.dumps(output, ensure_ascii=False)
    for term in BANNED_REPORT_TERMS:
        _require(term not in report_text, f"{task_id}: report contains banned exaggeration term {term}")
    source_text = " ".join(
        section["text"] for section in task["input_data"]["content_snapshot"]["sections"]
    )
    source_numbers = set(NUMBER_PATTERN.findall(source_text))
    report_numbers = set(NUMBER_PATTERN.findall(" ".join(
        [output["study_design"] or ""] + output["core_results"]
    )))
    _require(report_numbers.issubset(source_numbers), f"{task_id}: report contains a number absent from source")


def _validate_quality(output: object, task_id: str) -> None:
    _require(isinstance(output, dict) and set(output) == QUALITY_FIELDS, f"{task_id}: quality fields mismatch")
    assert isinstance(output, dict)
    _require(output["schema_version"] == "1.0", f"{task_id}: quality schema version")
    _require(output["decision"] in {"pass", "review", "reject"}, f"{task_id}: invalid quality decision")
    _require(_score(output["quality_score"]), f"{task_id}: invalid quality score")
    _require(isinstance(output["issues"], list), f"{task_id}: issues must be a list")
    for issue in output["issues"]:
        _require(isinstance(issue, dict) and set(issue) == {"code", "severity", "location", "explanation"}, f"{task_id}: quality issue fields mismatch")
        _require(issue["code"] in ISSUE_CODES, f"{task_id}: invalid quality issue code")
        _require(issue["severity"] in {"low", "medium", "high"}, f"{task_id}: invalid issue severity")
        _require(isinstance(issue["location"], str) and len(issue["location"]) <= 200, f"{task_id}: invalid issue location")
        _require(isinstance(issue["explanation"], str) and len(issue["explanation"]) <= 600, f"{task_id}: invalid issue explanation")
    _require(_score(output["confidence"]), f"{task_id}: invalid quality confidence")
    _require(_string_list(output["warnings"], max_items=100, max_length=300), f"{task_id}: invalid quality warnings")
    _require(isinstance(output["needs_human_review"], bool), f"{task_id}: invalid quality review flag")
    if output["decision"] == "pass":
        _require(not any(issue["severity"] == "high" for issue in output["issues"]), f"{task_id}: pass cannot contain a high-severity issue")


def validate_mvp_results(task_path: Path, screening_path: Path, results_path: Path) -> dict[str, Any]:
    try:
        validate_screening_results(task_path, screening_path)
    except ScreeningValidationError as exc:
        raise MVPValidationError(f"screening package is invalid: {exc}") from exc
    tasks_package = json.loads(task_path.read_text(encoding="utf-8"))
    screening_package = json.loads(screening_path.read_text(encoding="utf-8"))
    results_package = json.loads(results_path.read_text(encoding="utf-8"))

    _require(results_package.get("result_package_version") == "1.0", "invalid MVP result package version")
    source = results_package.get("source")
    _require(isinstance(source, dict), "source must be an object")
    _require(source.get("task_package_sha256") == file_sha256(task_path), "task package SHA-256 mismatch")
    _require(source.get("screening_results_sha256") == file_sha256(screening_path), "screening SHA-256 mismatch")
    _require(source.get("batch_id") == tasks_package["batch"]["batch_id"], "batch_id mismatch")

    tasks = {item["paper_task_id"]: item for item in tasks_package["tasks"]}
    screening = {item["paper_task_id"]: item for item in screening_package["results"]}
    relevant_ids = {task_id for task_id, row in screening.items() if row["output"]["decision"] == "relevant"}
    items = results_package.get("items")
    _require(isinstance(items, list), "items must be a list")
    item_ids = [item.get("paper_task_id") for item in items if isinstance(item, dict)]
    _require(len(item_ids) == len(items) and len(item_ids) == len(set(item_ids)), "invalid or duplicate result items")
    _require(set(item_ids) == relevant_ids, "MVP items must exactly match relevant Screening tasks")

    quality_decisions: Counter[str] = Counter()
    for item in items:
        task_id = item["paper_task_id"]
        task = tasks[task_id]
        _require(item.get("paper_id") == task["input_data"]["paper"]["paper_id"], f"{task_id}: paper_id mismatch")
        _require(item.get("pmid") == task["input_data"]["paper"]["identifiers"].get("pmid"), f"{task_id}: PMID mismatch")
        _require(item.get("input_sha256") == task["input_sha256"], f"{task_id}: input SHA-256 mismatch")
        _validate_analysis(item.get("analysis"), task)
        _validate_classification(item.get("classification"), task)
        _validate_report(item.get("report"), task)
        _validate_quality(item.get("quality_gate"), task_id)
        quality_decisions[item["quality_gate"]["decision"]] += 1

    expected_summary = {
        "retrieved": len(tasks),
        "included": len(relevant_ids),
        "excluded": sum(row["output"]["decision"] == "irrelevant" for row in screening.values()),
        "manual_review": sum(row["output"]["decision"] == "uncertain" for row in screening.values()),
        "quality_pass": quality_decisions["pass"],
        "quality_review": quality_decisions["review"],
        "quality_reject": quality_decisions["reject"],
    }
    _require(results_package.get("summary") == expected_summary, "MVP summary mismatch")
    return {"valid": True, "batch_id": source["batch_id"], "summary": expected_summary}


def _e(value: object) -> str:
    return html.escape("未报告" if value is None else str(value), quote=True)


def _list_html(items: list[str], empty: str = "未报告") -> str:
    if not items:
        return f'<p class="muted">{_e(empty)}</p>'
    return "<ul>" + "".join(f"<li>{_e(item)}</li>" for item in items) + "</ul>"


def _display_publication_date(value: str | None, precision: str | None) -> str:
    if not value:
        return "未报告"
    if precision == "year":
        return value[:4]
    if precision == "month":
        return value[:7]
    return value


def render_preview(task_path: Path, screening_path: Path, results_path: Path, output_path: Path) -> dict[str, Any]:
    validation = validate_mvp_results(task_path, screening_path, results_path)
    tasks_package = json.loads(task_path.read_text(encoding="utf-8"))
    screening_package = json.loads(screening_path.read_text(encoding="utf-8"))
    results_package = json.loads(results_path.read_text(encoding="utf-8"))
    tasks = {item["paper_task_id"]: item for item in tasks_package["tasks"]}
    domain_names = {item["code"]: item["name_zh"] for item in tasks_package["tasks"][0]["input_data"]["domain_context"]["active_domains"]}

    cards: list[str] = []
    for item in results_package["items"]:
        task = tasks[item["paper_task_id"]]
        paper = task["input_data"]["paper"]
        meta = paper["study_metadata"]
        analysis = item["analysis"]
        classification = item["classification"]
        report = item["report"]
        quality = item["quality_gate"]
        authors = "、".join(author["name"] for author in meta["authors"])
        domains = "".join(f'<span class="tag">{_e(domain_names.get(row["domain_code"], row["domain_code"]))}</span>' for row in classification["classifications"])
        links = "".join(f'<a href="{_e(link)}" target="_blank" rel="noopener noreferrer">{"PubMed" if "pubmed" in link else "DOI"}</a>' for link in report["source_links"])
        evidence = "".join(
            '<div class="evidence-item"><strong>' + _e(finding["finding_id"]) + '</strong>'
            + "".join(f'<blockquote>{_e(ev["quote"])}</blockquote>' for ev in finding["evidence"])
            + '</div>' for finding in analysis["key_findings"]
        )
        date_warning = meta["publication_date_metadata"]["warnings"]
        warning_html = "".join(f'<p class="warning">{_e(w["message"])}</p>' for w in date_warning)
        warning_line = f"  {warning_html}" if warning_html else ""
        publication_date = _display_publication_date(
            paper["publication_date"], meta["publication_date_precision"]
        )
        cards.append(f'''
<article class="paper-card">
  <div class="card-top"><div><span class="field-label">领域分类</span>{domains}</div><span class="quality pass">Quality Gate：{_e(quality["decision"]) } · {_e(quality["quality_score"])}</span></div>
  <h2>{_e(report["ai_title"])}</h2>
  <p class="original-title">{_e(paper["title"])}</p>
  <dl class="metadata">
    <div><dt>作者</dt><dd>{_e(authors)}</dd></div><div><dt>期刊</dt><dd>{_e(paper["journal"])}</dd></div>
    <div><dt>发布日期</dt><dd>{_e(publication_date)}（精度：{_e(meta["publication_date_precision"]) }）</dd></div>
    <div><dt>研究类型</dt><dd>{_e(analysis["study_type"])} / {_e(analysis["evidence_level"])}</dd></div>
  </dl>
{warning_line}
  <section><h3>核心摘要</h3><p>{_e(report["research_background"])}</p><p>{_e(report["research_objective"])}</p></section>
  <section><h3>实验设计</h3><p>{_e(report["study_design"])}</p></section>
  <section><h3>核心结果</h3>{_list_html(report["core_results"])}</section>
  <section><h3>研究局限</h3>{_list_html(report["limitations"])}</section>
  <section><h3>研发启示</h3>{_list_html(report["rd_implications"])}</section>
  <details><summary>查看原文证据</summary>{evidence}</details>
  <div class="source-links">{links}</div>
</article>''')

    excluded: list[str] = []
    review: list[str] = []
    for row in screening_package["results"]:
        decision = row["output"]["decision"]
        if decision == "relevant":
            continue
        paper = tasks[row["paper_task_id"]]["input_data"]["paper"]
        line = f'<li><strong>{_e(paper["title"])}</strong><br><span>{_e(row["output"]["reason"])}</span></li>'
        (excluded if decision == "irrelevant" else review).append(line)

    summary = validation["summary"]
    document = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>肠道健康科研速递 · 预览版</title>
<style>
:root{{--ink:#17202a;--muted:#667085;--line:#dfe5e2;--green:#176b54;--pale:#eef7f3;--amber:#9a6700;--bg:#f5f7f6}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:"Microsoft YaHei","PingFang SC",Arial,sans-serif;line-height:1.72}}
main{{max-width:1040px;margin:auto;padding:42px 20px 80px}}header{{background:linear-gradient(135deg,#123d35,#1f725d);color:white;padding:36px;border-radius:18px;margin-bottom:28px}}
.eyebrow{{letter-spacing:.12em;font-size:13px;opacity:.82}}h1{{margin:.25em 0;font-size:clamp(28px,5vw,44px)}}.lede{{max-width:760px;margin:0;opacity:.9}}
.stats{{display:flex;gap:12px;flex-wrap:wrap;margin-top:22px}}.stat{{background:#ffffff18;border:1px solid #ffffff30;border-radius:10px;padding:8px 14px}}
.paper-card,.screening-note{{background:white;border:1px solid var(--line);border-radius:16px;padding:28px;margin:22px 0;box-shadow:0 8px 28px #19352d0c}}
.card-top{{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}}.field-label{{font-size:13px;color:var(--muted);margin-right:8px}}.tag{{display:inline-block;background:var(--pale);color:var(--green);padding:4px 10px;border-radius:999px;font-size:13px;font-weight:700;margin-right:6px}}
.quality{{font-size:13px;padding:4px 9px;border-radius:8px}}.quality.pass{{background:#eaf7ef;color:#176b45}}h2{{font-size:26px;line-height:1.35;margin:18px 0 5px}}h3{{font-size:17px;margin:24px 0 8px;color:#24483e}}.original-title{{color:var(--muted);margin-top:0}}
.metadata{{display:grid;grid-template-columns:1fr 1fr;gap:8px 24px;background:#f8faf9;padding:16px;border-radius:10px}}.metadata div{{display:grid;grid-template-columns:74px 1fr}}dt{{font-weight:700}}dd{{margin:0;color:#344054}}ul{{padding-left:22px}}li{{margin:7px 0}}
.warning{{background:#fff8e6;border-left:4px solid #e4a11b;padding:9px 12px;color:#6e4b00}}details{{border-top:1px solid var(--line);padding-top:16px;margin-top:24px}}summary{{cursor:pointer;font-weight:700;color:var(--green)}}blockquote{{margin:9px 0;padding:8px 13px;border-left:3px solid #9ac7b8;background:#f7faf9;color:#3f514b}}
.source-links{{display:flex;gap:10px;margin-top:20px}}.source-links a{{color:white;background:var(--green);text-decoration:none;padding:8px 14px;border-radius:8px}}.screening-note h2{{font-size:22px}}.screening-note span,.muted{{color:var(--muted)}}footer{{text-align:center;color:var(--muted);margin-top:34px;font-size:13px}}
@media(max-width:700px){{.metadata{{grid-template-columns:1fr}}.paper-card{{padding:20px}}header{{padding:26px}}}}
</style></head><body><main>
<header><div class="eyebrow">内部评审 · 预览版 · 尚未正式发布</div><h1>肠道健康科研速递</h1><p class="lede">基于经 PMID、DOI 和来源链接验证的 PubMed 摘要，由 Codex 按项目 Prompt 规范完成结构化整理。动物及体外结果不代表人体功效。</p>
<div class="stats"><span class="stat">本期文献 {summary["included"]} 篇</span><span class="stat">初始检索 {summary["retrieved"]} 篇</span><span class="stat">待人工审核 {summary["manual_review"]} 篇</span></div></header>
{''.join(cards)}
<section class="screening-note"><h2>未纳入本期</h2><h3>明确不相关（{summary["excluded"]}）</h3><ul>{''.join(excluded)}</ul><h3>待人工审核（{summary["manual_review"]}）</h3><ul>{''.join(review)}</ul></section>
<footer>批次 {_e(validation["batch_id"])} · 本页面仅为预览，正式发布前必须完成人工确认。</footer>
</main></body></html>'''
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")
    return {"output": output_path.resolve().as_posix(), "sha256": hashlib.sha256(document.encode("utf-8")).hexdigest(), "summary": summary}


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Codex MVP results and render an HTML preview.")
    parser.add_argument("--task-package", type=Path, required=True)
    parser.add_argument("--screening-results", type=Path, required=True)
    parser.add_argument("--mvp-results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = render_preview(args.task_package, args.screening_results, args.mvp_results, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
