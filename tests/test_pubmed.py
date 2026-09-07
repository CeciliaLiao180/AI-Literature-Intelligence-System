from __future__ import annotations

import json
import unittest

from literature_intelligence.collectors.pubmed import PubMedClient, parse_pubmed_xml


SAMPLE_XML = """<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>12345678</PMID>
      <Article PubModel="Print-Electronic">
        <Journal>
          <JournalIssue><PubDate><Year>2026</Year><Month>Aug</Month><Day>12</Day></PubDate></JournalIssue>
          <Title>Journal of Tests</Title>
        </Journal>
        <ArticleTitle>Effects of <i>Example</i> on gut health.</ArticleTitle>
        <Abstract>
          <AbstractText Label="BACKGROUND">Background text.</AbstractText>
          <AbstractText Label="RESULTS">Results text.</AbstractText>
        </Abstract>
        <AuthorList>
          <Author>
            <LastName>Smith</LastName><ForeName>Jane</ForeName><Initials>J</Initials>
            <AffiliationInfo><Affiliation>Example University</Affiliation></AffiliationInfo>
            <Identifier Source="ORCID">0000-0001-2345-6789</Identifier>
          </Author>
        </AuthorList>
        <Language>eng</Language>
        <PublicationTypeList><PublicationType>Randomized Controlled Trial</PublicationType></PublicationTypeList>
        <ELocationID EIdType="doi">10.1000/EXAMPLE</ELocationID>
        <ArticleDate DateType="Electronic"><Year>2026</Year><Month>07</Month><Day>30</Day></ArticleDate>
      </Article>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="pubmed">12345678</ArticleId>
        <ArticleId IdType="doi">10.1000/EXAMPLE</ArticleId>
        <ArticleId IdType="pmc">PMC123456</ArticleId>
      </ArticleIdList>
      <History>
        <PubMedPubDate PubStatus="aheadofprint"><Year>2026</Year><Month>07</Month><Day>30</Day></PubMedPubDate>
      </History>
    </PubmedData>
  </PubmedArticle>
</PubmedArticleSet>
"""


class PubMedClientTests(unittest.TestCase):
    def test_search_parses_json_and_adds_identity_parameters(self) -> None:
        calls: list[tuple[str, dict[str, str | int]]] = []

        def transport(endpoint: str, params: dict[str, str | int], _: float) -> bytes:
            calls.append((endpoint, params))
            return json.dumps(
                {"esearchresult": {"count": "2", "idlist": ["123", "456"]}}
            ).encode()

        client = PubMedClient(
            email="owner@example.com",
            tool="test_tool",
            transport=transport,
        )
        result = client.search("probiotics", retmax=2)

        self.assertEqual(result.count, 2)
        self.assertEqual(result.pmids, ("123", "456"))
        self.assertEqual(calls[0][0], "esearch.fcgi")
        self.assertEqual(calls[0][1]["db"], "pubmed")
        self.assertEqual(calls[0][1]["email"], "owner@example.com")
        self.assertEqual(calls[0][1]["tool"], "test_tool")

    def test_fetch_parses_pubmed_xml(self) -> None:
        def transport(endpoint: str, _: dict[str, str | int], __: float) -> bytes:
            self.assertEqual(endpoint, "efetch.fcgi")
            return SAMPLE_XML.encode()

        record = PubMedClient(transport=transport).fetch(["12345678"])[0]
        self.assertEqual(record.pmid, "12345678")
        self.assertEqual(record.title, "Effects of Example on gut health.")
        self.assertEqual(record.doi, "10.1000/EXAMPLE")
        self.assertEqual(record.pmcid, "PMC123456")
        self.assertIn("RESULTS: Results text.", record.abstract or "")
        self.assertEqual(record.authors[0].display_name, "Jane Smith")
        self.assertEqual(record.publication_date_source, "Article/Journal/JournalIssue/PubDate")
        self.assertEqual(record.publication_model, "Print-Electronic")
        self.assertEqual(record.article_dates[0]["date_type"], "Electronic")
        self.assertEqual(record.history_dates[0]["pub_status"], "aheadofprint")

    def test_invalid_fetch_id_is_rejected_before_network(self) -> None:
        client = PubMedClient(transport=lambda *_: b"")
        with self.assertRaises(ValueError):
            client.fetch(["not-a-pmid"])

    def test_parser_skips_incomplete_article(self) -> None:
        records = parse_pubmed_xml(
            "<PubmedArticleSet><PubmedArticle><MedlineCitation /></PubmedArticle></PubmedArticleSet>"
        )
        self.assertEqual(records, [])


if __name__ == "__main__":
    unittest.main()
