"""index_root: ingest -> extract (process pool) -> store -> resolve -> leak scan."""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from pydantic import BaseModel, Field

from citegraph.config import load_config
from citegraph.extract import EXTRACTORS, module_name_for
from citegraph.home import index_path
from citegraph.ingest import MAX_FILE_BYTES, IngestError, RepoInfo, discover_repos, scan_repo
from citegraph.models import ExtractResult
from citegraph.redact import configure_extra_patterns, sanitize
from citegraph.redact.leakscan import scan_paths
from citegraph.resolve import resolve_all
from citegraph.resolve.rules import resolver_fingerprint
from citegraph.store import Store

PARALLEL_THRESHOLD = 50
RESOLVER_FINGERPRINT_KEY = "resolver_fingerprint"


class IndexStats(BaseModel):
    db_path: str
    repos: int = 0
    files_total: int = 0
    files_changed: int = 0
    files_deleted: int = 0
    skipped_large: int = 0
    parse_errors: int = 0
    symbols: int = 0
    edges: int = 0
    resolved: bool = False
    leak_scan_clean: bool = True
    duration_s: float = 0.0
    warnings: list[str] = Field(default_factory=list[str])


def _extract_one(item: tuple[str, str, bytes]) -> ExtractResult:
    rel_path, lang, data = item
    try:
        return EXTRACTORS[lang].extract(rel_path, data)
    except Exception:  # one bad file never aborts a run; it is counted as a parse error
        return ExtractResult(parse_error=True)


def _extract_all(items: list[tuple[str, str, bytes]], jobs: int | None) -> list[ExtractResult]:
    if jobs == 1 or len(items) < PARALLEL_THRESHOLD or not items:
        return [_extract_one(item) for item in items]
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(_extract_one, items, chunksize=16))


def _index_repo(store: Store, info: RepoInfo, jobs: int | None, stats: IndexStats) -> bool:
    repo_id = store.upsert_repo(info.name, str(info.path), info.head_sha)
    existing = store.file_hashes(repo_id)
    current: set[str] = set()
    todo: list[tuple[str, str, str, bytes]] = []
    for file in info.files:
        if file.size > MAX_FILE_BYTES:
            stats.skipped_large += 1
            continue
        key = sanitize(file.rel_path)
        current.add(key)
        data = (info.path / file.rel_path).read_bytes()
        digest = hashlib.blake2b(data, digest_size=16).hexdigest()
        if existing.get(key) != digest:
            todo.append((file.rel_path, file.lang, digest, data))
    deleted = [path for path in existing if path not in current]
    for path in deleted:
        store.delete_file(repo_id, path)
    results = _extract_all([(rel, lang, data) for rel, lang, _, data in todo], jobs)
    for (rel, lang, digest, data), result in zip(todo, results, strict=True):
        module = module_name_for(rel) if lang == "python" else None
        store.upsert_file(repo_id, rel, lang, digest, data.count(b"\n") + 1, result, module)
    store.commit()
    stats.files_total += len(info.files)
    stats.files_changed += len(todo)
    stats.files_deleted += len(deleted)
    return bool(todo or deleted)


def index_root(root: Path, name: str | None = None, jobs: int | None = None) -> IndexStats:
    started = time.perf_counter()
    config = load_config(root)
    configure_extra_patterns(config.extra_redaction_patterns)
    repos = discover_repos(root)
    if not repos:
        raise IngestError(f"no git repositories found under {root}")
    db_path = index_path(root, name)
    stats = IndexStats(db_path=str(db_path))
    store = Store.open(db_path)
    try:
        run_id = store.start_run()
        store.commit()
        changed = False
        shas: dict[str, str] = {}
        failed: list[str] = []
        for repo_path in repos:
            try:
                info = scan_repo(repo_path, config)
            except IngestError as exc:
                stats.warnings.append(str(exc))
                failed.append(repo_path.name)
                continue
            changed = _index_repo(store, info, jobs, stats) or changed
            shas[info.name] = info.head_sha
        # a repo that failed to scan this run (transient git error) keeps its previously indexed
        # rows; only a repo no longer discovered under root at all is dropped
        if store.delete_repos_not_in([*shas, *failed]):
            changed = True
        store.commit()
        fingerprint = resolver_fingerprint()
        rules_changed = store.get_meta(RESOLVER_FINGERPRINT_KEY) != fingerprint
        if changed or rules_changed or (store.count("edges") == 0 and store.count("refs") > 0):
            resolve_all(store)
            store.set_meta(RESOLVER_FINGERPRINT_KEY, fingerprint)
            store.commit()
            stats.resolved = True
        stats.repos = len(shas)
        stats.symbols = store.count("symbols")
        stats.edges = store.count("edges")
        stats.parse_errors = int(
            store.conn.execute("SELECT count(*) FROM files WHERE parse_error = 1").fetchone()[0]
        )
        store.checkpoint()
        stats.leak_scan_clean = not scan_paths([db_path])
        counts = {"symbols": stats.symbols, "edges": stats.edges, "files": store.count("files")}
        store.finish_run(run_id, shas, counts, stats.parse_errors, stats.leak_scan_clean)
        store.commit()
        store.checkpoint()
    finally:
        store.close()
    stats.duration_s = time.perf_counter() - started
    return stats
