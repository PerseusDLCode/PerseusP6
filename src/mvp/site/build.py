"""The `mvp-build` CLI: freezes the Flask app into a static site."""

import argparse
import json
import multiprocessing
import os
import zlib
from contextlib import suppress
from pathlib import Path
from typing import Any

from mvp.site import config
from mvp.site.app import create_app
from mvp.site.manifest import _build_corpus_manifest, _merge_manifests

# Set just before the freeze worker pool is created (see build()) and read
# by _freeze_one in each forked worker. A Freezer/Flask app can't be pickled
# (Jinja templates, compiled routing regexes, etc.), so it can't be passed
# through Pool.imap_unordered's task queue directly — instead we rely on
# fork's copy-on-write semantics: workers are forked *after* this global is
# set, so each one simply inherits its own copy already in memory.
_ACTIVE_FREEZER = None


def _freeze_one(url_and_last_modified: tuple[str, Any]) -> Path:
    """Render and write one frozen page. Runs in a worker process."""
    url, last_modified = url_and_last_modified
    return _ACTIVE_FREEZER._build_one(url, last_modified)  # ty: ignore[unresolved-attribute]


def _parse_build_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse mvp-build's CLI flags.

    argv defaults to sys.argv[1:] (via argparse), which is what the
    `mvp-build` console_script entry point invokes build() with — it calls
    build() with no arguments, so argv must come from process args, not a
    parameter, for that entry point to pick up flags at all.
    """
    parser = argparse.ArgumentParser(prog="mvp-build")
    parser.add_argument(
        "--mode",
        choices=["full", "corpus-only", "global-only"],
        default="full",
        help=(
            "full (default): build everything from CORPORA_DIR, unchanged "
            "from mvp-build's original behavior. corpus-only: freeze a "
            "shard of reading pages plus manifest.json, skipping "
            "/, /collections/, /urn-index.json, /research/ — CORPORA_DIR "
            "must contain every corpus (not just one), same as a full "
            "local checkout, so cross-corpus sibling/commentary lookups "
            "resolve correctly regardless of which shard a document lands "
            "in; --shard-index/--shard-count pick which slice of the full "
            "URL set this run actually writes to disk. global-only: skip "
            "corpus discovery entirely and freeze just those four pages, "
            "built from manifest.json files passed via --manifest."
        ),
    )
    parser.add_argument(
        "--manifest",
        action="append",
        default=[],
        metavar="PATH",
        help="Path to a corpus manifest.json (repeatable). Required, and "
        "only used, in --mode global-only.",
    )
    parser.add_argument(
        "--source-digest",
        default="",
        help="Opaque digest of this build's source tree, stamped into "
        "manifest.json for traceability. Only used in --mode corpus-only.",
    )
    parser.add_argument(
        "--shard-index",
        type=int,
        default=0,
        help="Which of --shard-count slices of the full URL set to freeze "
        "in this run. Only used in --mode corpus-only; every other mode "
        "freezes everything it generates. 0-indexed.",
    )
    parser.add_argument(
        "--shard-count",
        type=int,
        default=1,
        help="How many parallel corpus-only runs are splitting the full "
        "URL set between them (see --shard-index). 1 (default) freezes "
        "every URL in a single run.",
    )
    args = parser.parse_args(argv)
    if args.mode == "global-only" and not args.manifest:
        parser.error("--mode global-only requires at least one --manifest PATH")
    if not 0 <= args.shard_index < args.shard_count:
        parser.error("--shard-index must be in [0, --shard-count)")
    return args


def build():
    from flask_frozen import Freezer, walk_directory

    global _ACTIVE_FREEZER

    args = _parse_build_args()

    FREEZER_DESTINATION = config.ROOT_DIR / "build"

    collections_override = urn_index_override = None
    if args.mode == "global-only":
        collections_override, urn_index_override = _merge_manifests(
            [Path(p) for p in args.manifest]
        )

    app = create_app(
        collections_override=collections_override,
        urn_index_override=urn_index_override,
    )

    app.config.update(
        FREEZER_BASE_URL=os.getenv("FREEZER_BASE_URL", ""),
        FREEZER_DEFAULT_MIMETYPE="text/html",
        FREEZER_DESTINATION=FREEZER_DESTINATION,
        FREEZER_IGNORE_404_NOT_FOUND=True,
        FREEZER_REMOVE_EXTRA_FILES=True,
    )

    # corpus-only builds must not auto-freeze the global no-arg pages (/,
    # /collections/, /urn-index.json, /research/) — rendered from just this
    # corpus's data, they'd be wrong, and a later global-only build produces
    # the real ones anyway. global-only builds want exactly those four:
    # that falls out for free below, since PROTO_DIR has no corpus data
    # checked out, so the explicitly-registered per-page generators
    # (get_first_chunk et al.) yield no URLs.
    freezer = Freezer(
        app,
        with_no_argument_rules=(args.mode != "corpus-only"),
        log_url_for=False,
    )

    def _iter_version_metadata():
        """Yield (corpus, textgroup, work, version, scheme, metadata_path) tuples.

        scheme is None for a document's default citeStructure (metadata.json
        directly in the version directory) and the subdirectory name for any
        additional scheme (see _scheme_slug / SiteMap.chunk_dir)."""
        for metadata_path in config.PROTO_DIR.glob("**/metadata.json"):
            with metadata_path.open(encoding="utf-8") as f:
                meta = json.load(f)

            document = meta.get("document")
            if not document:
                continue

            base_urn = document.get("base_urn")
            if not base_urn:
                continue

            _urn, _cts, corpus, work_urn = base_urn.split(":")
            textgroup, work, version = work_urn.split(".")
            scheme = (
                None
                if meta.get("refsDecl_id", "CTS") == "CTS"
                else (metadata_path.parent.name)
            )

            yield corpus, textgroup, work, version, scheme, metadata_path

    @freezer.register_generator
    def get_collections_search_index():
        # Mirrors the with_no_argument_rules guard above: a corpus-only
        # build's search index would only cover this one corpus, and a
        # later global-only build produces the real one anyway.
        if args.mode != "corpus-only":
            yield "/collections/search-index.json"

    @freezer.register_generator
    def get_first_chunk():
        for corpus, textgroup, work, version, scheme, _ in _iter_version_metadata():
            if scheme is None:
                yield {
                    "corpus": corpus,
                    "textgroup": textgroup,
                    "work": work,
                    "version": version,
                }

    @freezer.register_generator
    def get_chunk_index():
        for corpus, textgroup, work, version, scheme, _ in _iter_version_metadata():
            if scheme is None:
                yield {
                    "corpus": corpus,
                    "textgroup": textgroup,
                    "work": work,
                    "version": version,
                }

    @freezer.register_generator
    def get_first_scheme_chunk():
        for corpus, textgroup, work, version, scheme, _ in _iter_version_metadata():
            if scheme is not None:
                yield {
                    "corpus": corpus,
                    "textgroup": textgroup,
                    "work": work,
                    "version": version,
                    "scheme": scheme,
                }

    @freezer.register_generator
    def get_nav_fragment():
        for corpus, textgroup, work, version, scheme, _ in _iter_version_metadata():
            if scheme is None:
                yield {
                    "corpus": corpus,
                    "textgroup": textgroup,
                    "work": work,
                    "version": version,
                }

    @freezer.register_generator
    def get_nav_fragment_scheme():
        for corpus, textgroup, work, version, scheme, _ in _iter_version_metadata():
            if scheme is not None:
                yield {
                    "corpus": corpus,
                    "textgroup": textgroup,
                    "work": work,
                    "version": version,
                    "scheme": scheme,
                }

    @freezer.register_generator
    def reading_view():
        for (
            corpus,
            textgroup,
            work,
            version,
            scheme,
            metadata_path,
        ) in _iter_version_metadata():
            if scheme is not None:
                continue
            with (metadata_path.parent / "index.json").open(encoding="utf-8") as f:
                chunks = json.load(f).get("chunks")
            for chunk in chunks:
                yield f"/{chunk.get('cts_urn')}/"

    @freezer.register_generator
    def reading_view_scheme():
        for (
            corpus,
            textgroup,
            work,
            version,
            scheme,
            metadata_path,
        ) in _iter_version_metadata():
            if scheme is None:
                continue
            with (metadata_path.parent / "index.json").open(encoding="utf-8") as f:
                chunks = json.load(f).get("chunks")
            for chunk in chunks:
                passage = chunk["cts_urn"].rsplit(":", 1)[-1]
                yield (
                    f"/urn:cts:{corpus}:{textgroup}.{work}.{version}"
                    f"/{scheme}:{passage}/"
                )

    from timeit import default_timer

    start = default_timer()

    # Mirrors flask_frozen.Freezer.freeze_yield() (frozen_flask==1.0.2), but
    # fans the expensive per-URL render+write step (_build_one) out across
    # BUILD_WORKERS processes instead of a single sequential loop. URL
    # enumeration stays single-threaded here — it's cheap (no rendering).
    seen_urls: set[str] = set()
    seen_endpoints: set[str] = set()
    work: list[tuple[str, Any]] = []
    for url, endpoint, last_modified in freezer._generate_all_urls():
        # Recorded before the shard skip below so _check_endpoints (every
        # endpoint got at least one URL somewhere) still passes per-shard —
        # it doesn't require every URL, just that each endpoint was seen.
        seen_endpoints.add(endpoint)
        if url in seen_urls:
            continue
        seen_urls.add(url)
        # CORPORA_DIR holds every corpus in every corpus-only run (see
        # --mode's help), so this URL set is already the *full* site's —
        # shard it here, by a stable hash of the URL itself, rather than by
        # corpus: the source repos don't partition cleanly by corpus (e.g.
        # First1KGreek declares both greekLit and hebrewlit URNs, grcnewxml
        # declares greekLit/itaLit/latinLit), so any corpus-keyed split
        # would either duplicate or drop pages. Hashing the URL spreads
        # pages evenly across shards regardless of which corpus/namespace
        # they belong to.
        if args.shard_count > 1:
            if zlib.crc32(url.encode()) % args.shard_count != args.shard_index:
                continue
        work.append((url, last_modified))

    total = len(work)
    built_paths: set[Path] = set()
    _ACTIVE_FREEZER = freezer
    ctx = multiprocessing.get_context("fork")
    with ctx.Pool(config.BUILD_WORKERS) as pool:
        for i, path in enumerate(pool.imap_unordered(_freeze_one, work), 1):
            built_paths.add(path)
            if i % 500 == 0 or i == total:
                print(f"  froze {i}/{total} pages")
    _ACTIVE_FREEZER = None

    freezer._check_endpoints(seen_endpoints)
    if app.config["FREEZER_REMOVE_EXTRA_FILES"]:
        ignore = app.config["FREEZER_DESTINATION_IGNORE"]
        previous_paths = {
            Path(freezer.root / name)
            for name in walk_directory(freezer.root, ignore=ignore)
        }
        for extra_path in previous_paths - built_paths:
            extra_path.unlink()
            with suppress(OSError):
                extra_path.parent.rmdir()

    end = default_timer()

    print(f"MVP took {end - start} seconds to build.")

    if args.mode == "corpus-only":
        manifest = _build_corpus_manifest(
            app, config.PROTO_DIR, app.catalog, args.source_digest
        )
        # Written inside FREEZER_DESTINATION, not ROOT_DIR: CI only bind-mounts
        # the frozen-pages directory out of the build container, and this way
        # that one mount also carries manifest.json out with it.
        manifest_path = FREEZER_DESTINATION / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f)
        print(f"Wrote {manifest_path}")
