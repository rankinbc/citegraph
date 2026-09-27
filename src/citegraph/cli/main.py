"""citegraph command line."""

from __future__ import annotations

import click

from citegraph import __version__


@click.group()
@click.version_option(__version__, prog_name="citegraph")
def main() -> None:
    """citegraph: evidence-tagged code graph for AI coding agents."""
