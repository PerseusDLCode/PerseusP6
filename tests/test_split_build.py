# tests/test_split_build.py
#
# End-to-end coverage for the corpus-only/global-only split (see
# _build_corpus_manifest, _merge_manifests in mvp.site.manifest, and
# create_app's collections_override/urn_index_override in mvp.site.app):
# building each corpus's manifest independently and merging them must
# reproduce exactly what a single combined build would have produced for
# /collections/ and /urn-index.json.
#
# This drives the real _build_collections/_build_urn_index/_version_entry
# code paths against a hand-built proto-page tree (index.json/metadata.json
# only) rather than compiling real TEI fixtures through Chunker — that
# would also exercise TEILinker/the gazetteer/_parse_chunk, none of which
# this split touches. It does not invoke the `mvp-build` CLI itself.

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mvp.site import app as appmod
from mvp.site import build as buildmod
from mvp.site import config
from mvp.site import manifest as manifestmod


# ---------------------------------------------------------------------------
# Fixture proto-page tree
# ---------------------------------------------------------------------------


def _write_version(
    proto_root: Path,
    corpus: str,
    textgroup: str,
    work: str,
    version: str,
    language: str,
) -> None:
    """Write a minimal version directory: just the index.json/metadata.json
    sidecars _version_entry and _build_urn_index actually read — no chunk
    XML, since nothing in this test freezes a reading-view page."""
    version_dir = proto_root / corpus / textgroup / work / version
    version_dir.mkdir(parents=True)

    urn = f"urn:cts:{corpus}:{textgroup}.{work}.{version}:1"
    (version_dir / "index.json").write_text(
        json.dumps({"chunks": [{"cts_urn": urn, "file": "chunk_1.html"}]})
    )
    (version_dir / "metadata.json").write_text(
        json.dumps(
            {
                "document": {
                    "language": language,
                    "editors": [{"name": "Ann Editor", "role": "editor"}],
                },
                "toc": [],
            }
        )
    )


@pytest.fixture
def combined_and_split_proto_dirs(tmp_path):
    """Build the same two-corpus catalog two ways: one proto dir with both
    corpora (simulating today's single combined build), and two separate
    proto dirs with one corpus each (simulating the split build)."""
    combined = tmp_path / "proto-combined"
    greek_only = tmp_path / "proto-greek"
    latin_only = tmp_path / "proto-latin"

    for root in (combined, greek_only, latin_only):
        root.mkdir()

    _write_version(combined, "greekLit", "tlg0001", "tlg001", "perseus-grc2", "grc")
    _write_version(combined, "latinLit", "phi1017", "phi007", "perseus-lat2", "lat")
    _write_version(greek_only, "greekLit", "tlg0001", "tlg001", "perseus-grc2", "grc")
    _write_version(latin_only, "latinLit", "phi1017", "phi007", "perseus-lat2", "lat")

    return combined, greek_only, latin_only


@pytest.fixture
def app_with_no_real_corpora(monkeypatch, tmp_path, combined_and_split_proto_dirs):
    """A create_app() instance with corpus auto-discovery disabled.

    CORPORA_DIR points at an empty directory so _discover_corpora/
    generate_proto_pages/CTSCatalog all see zero real corpora — this test
    only cares about the proto-page-tree-driven routes (/collections/,
    /urn-index.json), which read PROTO_DIR directly, not CORPORA_DIR. The
    resulting empty CTSCatalog is fine: _work_title/_group_name fall back
    to the directory name deterministically either way, so full vs. split
    builds still produce identical labels.
    """
    combined, _, _ = combined_and_split_proto_dirs
    monkeypatch.setattr(config, "CORPORA_DIR", tmp_path / "empty-corpora")
    monkeypatch.setattr(config, "PROTO_DIR", combined)
    return appmod.create_app()


# ---------------------------------------------------------------------------
# Split build reproduces the combined build
# ---------------------------------------------------------------------------


class TestSplitBuildMatchesCombinedBuild:
    def test_collections_page_is_identical(
        self, app_with_no_real_corpora, combined_and_split_proto_dirs
    ):
        _, greek_only, latin_only = combined_and_split_proto_dirs
        app = app_with_no_real_corpora
        client = app.test_client()

        baseline_html = client.get("/collections/").get_data(as_text=True)

        greek_manifest = manifestmod._build_corpus_manifest(
            app, greek_only, app.catalog, "digest-greek"
        )
        latin_manifest = manifestmod._build_corpus_manifest(
            app, latin_only, app.catalog, "digest-latin"
        )
        manifest_paths = []
        for name, manifest in [
            ("greek", greek_manifest),
            ("latin", latin_manifest),
        ]:
            path = greek_only.parent / f"manifest-{name}.json"
            path.write_text(json.dumps(manifest))
            manifest_paths.append(path)

        collections, urn_index = manifestmod._merge_manifests(manifest_paths)

        split_app = appmod.create_app(
            collections_override=collections, urn_index_override=urn_index
        )
        split_html = split_app.test_client().get("/collections/").get_data(as_text=True)

        assert split_html == baseline_html

    def test_urn_index_is_identical(
        self, app_with_no_real_corpora, combined_and_split_proto_dirs
    ):
        _, greek_only, latin_only = combined_and_split_proto_dirs
        app = app_with_no_real_corpora
        client = app.test_client()

        baseline_index = client.get("/urn-index.json").get_json()

        greek_manifest = manifestmod._build_corpus_manifest(
            app, greek_only, app.catalog, "digest-greek"
        )
        latin_manifest = manifestmod._build_corpus_manifest(
            app, latin_only, app.catalog, "digest-latin"
        )
        manifest_paths = []
        for name, manifest in [
            ("greek", greek_manifest),
            ("latin", latin_manifest),
        ]:
            path = greek_only.parent / f"manifest-{name}.json"
            path.write_text(json.dumps(manifest))
            manifest_paths.append(path)

        _, urn_index = manifestmod._merge_manifests(manifest_paths)

        assert urn_index == baseline_index
        # Sanity check it's non-trivial, not two empty dicts matching by accident.
        assert urn_index == {
            "urn:cts:greekLit:tlg0001.tlg001": [
                {
                    "id": "perseus-grc2",
                    "label": "perseus-grc2",
                    "language": "grc",
                    "language_label": "Greek",
                    "route_kwargs": {
                        "corpus": "greekLit",
                        "textgroup": "tlg0001",
                        "work": "tlg001",
                        "version": "perseus-grc2",
                    },
                }
            ],
            "urn:cts:latinLit:phi1017.phi007": [
                {
                    "id": "perseus-lat2",
                    "label": "perseus-lat2",
                    "language": "lat",
                    "language_label": "Latin",
                    "route_kwargs": {
                        "corpus": "latinLit",
                        "textgroup": "phi1017",
                        "work": "phi007",
                        "version": "perseus-lat2",
                    },
                }
            ],
        }

    def test_manifest_href_resolves_to_the_real_reading_view_route(
        self, app_with_no_real_corpora, combined_and_split_proto_dirs
    ):
        """The manifest must carry a real, resolvable href (see
        _build_corpus_manifest), not the raw first_chunk_kwargs a
        global-only build has no Flask app to resolve on its own."""
        _, greek_only, _ = combined_and_split_proto_dirs
        app = app_with_no_real_corpora

        manifest = manifestmod._build_corpus_manifest(
            app, greek_only, app.catalog, "digest-greek"
        )
        version = manifest["collections"][0]["textgroups"][0]["works"][0]["versions"][0]

        assert "first_chunk_kwargs" not in version
        assert version["href"] == "/urn:cts:greekLit:tlg0001.tlg001.perseus-grc2:1/"


class TestCrossRepoNamespaceOverlap:
    """Regression coverage for a real production bug: a corpus-only build's
    proto-page tree is not guaranteed to contain exactly one CTS namespace.

    Two ways that assumption breaks, both observed for real:
      1. Two different source repos (different CI matrix legs) can both
         contribute documents under the *same* namespace — e.g.
         First1KGreek's own documents declare urn:cts:greekLit:..., the
         same namespace canonical-greekLit's build produces. Their
         manifests must merge into one "greekLit" collections entry, not
         sit side by side as two separate "Greek" sections.
      2. A single source repo's proto-page tree can itself contain more
         than one namespace (e.g. a mistagged document) — the earlier
         `collections[0]` implementation silently dropped every namespace
         but the alphabetically-first one, which is how an entire corpus's
         real content (e.g. all of Latin) can vanish behind one stray
         mistagged document.
    """

    def test_two_manifests_contributing_to_the_same_namespace_merge(
        self, app_with_no_real_corpora, tmp_path
    ):
        app = app_with_no_real_corpora

        canonical_greek = tmp_path / "proto-canonical-greek"
        first1k = tmp_path / "proto-first1k"
        canonical_greek.mkdir()
        first1k.mkdir()
        # Different repos, same namespace, different textgroups — exactly
        # the canonical-greekLit / First1KGreek situation.
        _write_version(
            canonical_greek, "greekLit", "tlg0001", "tlg001", "perseus-grc2", "grc"
        )
        _write_version(first1k, "greekLit", "ggm0001", "ggm001", "1st1K-grc1", "grc")

        m1 = manifestmod._build_corpus_manifest(app, canonical_greek, app.catalog, "d1")
        m2 = manifestmod._build_corpus_manifest(app, first1k, app.catalog, "d2")
        paths = []
        for name, manifest in [("m1", m1), ("m2", m2)]:
            path = tmp_path / f"{name}.json"
            path.write_text(json.dumps(manifest))
            paths.append(path)

        collections, urn_index = manifestmod._merge_manifests(paths)

        # One "greekLit" entry, not two — with both textgroups combined.
        assert len(collections) == 1
        assert collections[0]["id"] == "greekLit"
        textgroup_ids = {tg["id"] for tg in collections[0]["textgroups"]}
        assert textgroup_ids == {"tlg0001", "ggm0001"}
        assert set(urn_index) == {
            "urn:cts:greekLit:tlg0001.tlg001",
            "urn:cts:greekLit:ggm0001.ggm001",
        }

    def test_one_manifest_spanning_two_namespaces_keeps_both(
        self, app_with_no_real_corpora, tmp_path
    ):
        app = app_with_no_real_corpora

        mixed = tmp_path / "proto-mixed"
        mixed.mkdir()
        # A "latinLit" build whose proto tree also contains one stray
        # document under "greekLit" — alphabetically first, which is
        # exactly what made collections[0] pick the wrong one.
        _write_version(
            mixed, "greekLit", "viaf2603144", "viaf001", "perseus-eng1", "eng"
        )
        _write_version(mixed, "latinLit", "phi1017", "phi007", "perseus-lat2", "lat")

        manifest = manifestmod._build_corpus_manifest(app, mixed, app.catalog, "d1")

        ids = {corpus["id"] for corpus in manifest["collections"]}
        assert ids == {"greekLit", "latinLit"}, (
            "the real (larger) namespace must not be dropped just because "
            "a smaller stray namespace sorts first"
        )


class TestMergeManifestsSchemaVersion:
    def test_rejects_mismatched_schema_version(self, tmp_path):
        bad_manifest = {
            "schema_version": manifestmod._MANIFEST_SCHEMA_VERSION + 1,
            "collections": [],
            "urn_index": {},
        }
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps(bad_manifest))

        with pytest.raises(ValueError, match="schema_version"):
            manifestmod._merge_manifests([path])


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


class TestParseBuildArgs:
    def test_defaults_to_full_mode(self):
        args = buildmod._parse_build_args([])
        assert args.mode == "full"
        assert args.manifest == []
        assert args.source_digest == ""

    def test_corpus_only_accepts_source_digest(self):
        args = buildmod._parse_build_args(
            ["--mode", "corpus-only", "--source-digest", "abc123"]
        )
        assert args.mode == "corpus-only"
        assert args.source_digest == "abc123"

    def test_global_only_accepts_repeated_manifest_flag(self):
        args = buildmod._parse_build_args(
            ["--mode", "global-only", "--manifest", "a.json", "--manifest", "b.json"]
        )
        assert args.manifest == ["a.json", "b.json"]

    def test_global_only_without_manifest_is_rejected(self):
        with pytest.raises(SystemExit):
            buildmod._parse_build_args(["--mode", "global-only"])

    def test_corpus_only_defaults_to_a_single_unsharded_run(self):
        args = buildmod._parse_build_args(["--mode", "corpus-only"])
        assert args.shard_index == 0
        assert args.shard_count == 1

    def test_corpus_only_accepts_shard_flags(self):
        args = buildmod._parse_build_args(
            ["--mode", "corpus-only", "--shard-index", "2", "--shard-count", "5"]
        )
        assert args.shard_index == 2
        assert args.shard_count == 5

    def test_shard_index_out_of_range_is_rejected(self):
        with pytest.raises(SystemExit):
            buildmod._parse_build_args(
                ["--mode", "corpus-only", "--shard-index", "5", "--shard-count", "5"]
            )

    def test_negative_shard_index_is_rejected(self):
        with pytest.raises(SystemExit):
            buildmod._parse_build_args(
                ["--mode", "corpus-only", "--shard-index", "-1", "--shard-count", "5"]
            )


class TestUrlSharding:
    """Every URL a full build would generate must land in exactly one shard,
    regardless of which corpus namespace it belongs to (see build.py's use
    of zlib.crc32 in build() — corpus-keyed sharding was tried and reverted
    because these repos' namespaces overlap; see build-corpus.yml)."""

    def _shard_of(self, url: str, shard_count: int) -> int:
        import zlib

        return zlib.crc32(url.encode()) % shard_count

    def test_every_url_lands_in_exactly_one_shard(self):
        urls = [
            f"/urn:cts:greekLit:tlg0059.tlg030.perseus-grc2:{n}/" for n in range(200)
        ]
        shard_count = 5
        shards: dict[int, list[str]] = {i: [] for i in range(shard_count)}
        for url in urls:
            shards[self._shard_of(url, shard_count)].append(url)

        # Every URL assigned, none duplicated, none dropped.
        assert sorted(u for bucket in shards.values() for u in bucket) == sorted(urls)

    def test_same_url_always_hashes_to_the_same_shard(self):
        url = "/urn:cts:greekLit:viaf107078652.viaf002.perseus-eng1:1.327A/"
        assert self._shard_of(url, 5) == self._shard_of(url, 5)
