"""citegraph command line."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import click

from citegraph import __version__
from citegraph.audit import AuditLog, summarize
from citegraph.cli.eval_commands import eval_group
from citegraph.config import ConfigError, load_config
from citegraph.home import index_path
from citegraph.indexer import index_root
from citegraph.ingest import IngestError
from citegraph.query import TOOLS
from citegraph.redact import configure_extra_patterns, sanitize
from citegraph.redact.leakscan import scan_paths

ROOT = click.Path(exists=True, file_okay=False, path_type=Path)


def _parse_param(raw: str) -> tuple[str, object]:
    if "=" not in raw:
        raise click.BadParameter(f"expected KEY=VALUE, got {raw!r}", param_hint="PARAMS")
    key, value = raw.split("=", 1)
    try:
        return key, json.loads(value)
    except json.JSONDecodeError:
        return key, value


def _configure_redaction(root: Path) -> None:
    """Apply citegraph.toml's extra redaction patterns to every response and audit line, before any tool runs."""
    try:
        configure_extra_patterns(load_config(root).extra_redaction_patterns)
    except ConfigError as exc:
        raise click.ClickException(sanitize(str(exc))) from exc


@click.group()
@click.version_option(__version__, prog_name="citegraph")
def main() -> None:
    """citegraph: evidence-tagged code graph for AI coding agents."""


main.add_command(eval_group)


@main.command()
@click.argument("root", type=ROOT)
@click.option("--name", default=None, help="Index name (defaults to the root folder name).")
@click.option("--jobs", type=int, default=None, help="Worker processes for parsing (default: CPU count).")
def index(root: Path, name: str | None, jobs: int | None) -> None:
    """Index every git repo under ROOT (or ROOT itself if it is a repo)."""
    try:
        stats = index_root(root, name=name, jobs=jobs)
    except (IngestError, ConfigError) as exc:
        raise click.ClickException(sanitize(str(exc))) from exc
    for warning in stats.warnings:
        click.echo(f"warning: {warning}", err=True)
    click.echo(
        f"indexed {stats.repos} repos: {stats.files_changed} files changed, {stats.files_deleted} deleted, "
        f"{stats.skipped_large} skipped (too large), {stats.symbols} symbols, {stats.edges} edges "
        f"in {stats.duration_s:.1f}s"
    )
    click.echo(f"index: {stats.db_path}")
    if not stats.leak_scan_clean:
        raise click.ClickException(
            f"leak scan found secret-shaped values in the index; delete {stats.db_path} and run `citegraph index` "
            f"again, and report a bug if it persists (`citegraph leak-scan {stats.db_path}` lists the locations, "
            "never the values)"
        )


@main.command()
@click.option("--root", required=True, type=ROOT)
@click.option("--name", default=None)
def serve(root: Path, name: str | None) -> None:
    """Run the read-only MCP server over stdio."""
    from citegraph.mcp.server import build_server  # imported here: keeps `citegraph --help` fast

    _configure_redaction(root)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    build_server(index_path(root, name)).run()


def _run_tool(tool: str, kwargs: dict[str, object], root: Path, name: str | None) -> dict[str, object]:
    from citegraph.mcp.server import ToolRunner

    _configure_redaction(root)
    fn = TOOLS[tool]
    runner = ToolRunner(index_path(root, name), AuditLog())
    return runner.run(tool, kwargs, lambda ctx: fn(ctx, **kwargs), client="cli")


@main.command()
@click.argument("tool")
@click.argument("params", nargs=-1)
@click.option("--root", required=True, type=ROOT)
@click.option("--name", default=None)
def query(tool: str, params: tuple[str, ...], root: Path, name: str | None) -> None:
    """Run one tool, e.g. `citegraph query what_calls symbol=charge depth=2 --root ~/src`."""
    if tool not in TOOLS:
        raise click.BadParameter(f"unknown tool; choose from {', '.join(sorted(TOOLS))}", param_hint="TOOL")
    payload = _run_tool(tool, dict(_parse_param(p) for p in params), root, name)
    click.echo(json.dumps(payload, indent=2))
    if "error" in payload:
        sys.exit(1)


@main.command()
@click.option("--root", required=True, type=ROOT)
@click.option("--name", default=None)
def status(root: Path, name: str | None) -> None:
    """Show what is indexed and whether it is stale."""
    payload = _run_tool("status", {}, root, name)
    click.echo(json.dumps(payload, indent=2))
    if "error" in payload:
        sys.exit(1)


@main.group()
def audit() -> None:
    """Inspect the MCP audit log."""


@audit.command("tail")
@click.option("-n", "count", default=20, show_default=True)
def audit_tail(count: int) -> None:
    for entry in AuditLog().entries(days=1)[-count:]:
        click.echo(json.dumps(entry))


@audit.command("stats")
@click.option("--days", default=7, show_default=True)
def audit_stats(days: int) -> None:
    click.echo(f"{'tool':<20}{'count':>7}{'errors':>8}{'p50 ms':>9}{'p95 ms':>9}")
    for tool, s in summarize(AuditLog().entries(days=days)).items():
        click.echo(
            f"{tool:<20}{int(s['count']):>7}{int(s['errors']):>8}{s['p50_ms']:>9.1f}{s['p95_ms']:>9.1f}"
        )


@main.command("leak-scan")
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option(
    "--fail-on-findings", is_flag=True, help="Exit 1 when anything is found (for pre-commit and CI)."
)
def leak_scan(paths: tuple[Path, ...], fail_on_findings: bool) -> None:
    """Scan files or folders (including an index .db) for secret-shaped values. Prints locations only."""
    findings = scan_paths(paths)
    for finding in findings:
        click.echo(str(finding))
    click.echo(f"{len(findings)} finding(s)", err=True)
    if findings and fail_on_findings:
        sys.exit(1)
