import io, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import fetch_pubmed_metadata as f

XML = b"""<PubmedArticleSet>
<PubmedArticle><MedlineCitation><PMID>11111111</PMID><Article>
<Journal><JournalIssue><PubDate><Year>2020</Year></PubDate></JournalIssue><Title>Fake J</Title></Journal>
<ArticleTitle>Fake title</ArticleTitle><AuthorList><Author><LastName>Doe</LastName><Initials>J</Initials></Author></AuthorList>
<PublicationTypeList><PublicationType>Journal Article</PublicationType></PublicationTypeList></Article></MedlineCitation>
<PubmedData><ArticleIdList><ArticleId IdType="doi">10.0/fake</ArticleId></ArticleIdList></PubmedData></PubmedArticle>
<PubmedArticle><MedlineCitation><PMID>22222222</PMID><Article>
<Journal><JournalIssue><PubDate><Month>Jan</Month></PubDate></JournalIssue><Title>J2</Title></Journal>
<ArticleTitle>No doi</ArticleTitle></Article></MedlineCitation><PubmedData/></PubmedArticle>
</PubmedArticleSet>"""


def test_build_with_mocked_http():
    urls = []
    def opener(url):
        urls.append(url)
        return io.BytesIO(XML)
    recs, failed = f.build(["11111111", "22222222", "33333333", "38757607"], opener)
    assert len(urls) == 1 and "tool=crn-pipeline" in urls[0] and "38757607" not in urls[0]
    assert failed == ["33333333"]
    a, b = recs
    assert a["doi"] == "10.0/fake" and a["year"] == 2020 and a["authors"] == ["Doe J"] and a["missing"] == {}
    assert b["doi"] is None and b["year"] is None and b["authors"] is None
    assert set(b["missing"]) == {"doi", "year", "authors", "publication_types"}
    assert a["provenance"]["source"] == "NCBI E-utilities" and a["provenance"]["retrieved_at"].endswith("Z")


def test_email_and_api_key_kept_out_of_provenance():
    import pathlib
    import pytest
    jsonschema = pytest.importorskip("jsonschema")
    urls = []
    def opener(url):
        urls.append(url)
        return io.BytesIO(XML)
    out = f.fetch(["11111111"], opener, email="someone@example.invalid", api_key="FAKEKEY123")
    assert "email=" in urls[0] and "api_key=FAKEKEY123" in urls[0]
    schema = json.loads((pathlib.Path(__file__).resolve().parents[1] / "schemas/methods.schema.json").read_text())
    validator = jsonschema.Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/pubmed_metadata_record"})
    for rec in out.values():
        params = rec["provenance"]["params"]
        assert "email" not in params and "api_key" not in params
        assert "FAKEKEY123" not in json.dumps(rec) and "example.invalid" not in json.dumps(rec)
        validator.validate(rec)
