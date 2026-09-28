from field_cartography.ids import normalize_doi, canonical_id


def test_normalize_doi():
    assert normalize_doi("https://doi.org/10.1000/ABC") == "10.1000/abc"
    assert normalize_doi("DOI:10.1000/xyz") == "10.1000/xyz"


def test_canonical_id_prefers_doi():
    rec = {"doi": "10.1000/abc", "title": "X", "year": 2020, "authors": [{"name": "A"}]}
    assert canonical_id(rec) == "doi:10.1000/abc"


def test_canonical_id_hash_fallback():
    rec = {"title": "My Paper", "year": 2021, "authors": [{"name": "Smith"}]}
    cid = canonical_id(rec)
    assert cid.startswith("hash:")
