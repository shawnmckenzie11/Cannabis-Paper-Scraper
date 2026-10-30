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
