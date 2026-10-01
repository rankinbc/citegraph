"""End to end: a monorepo's C# endpoint enqueues a job, a Python worker runs it, and an analysis package does the
work, linked by projects, a queue link and a package re-export into one call path."""

from collections.abc import Callable
from pathlib import Path

from citegraph.indexer import index_root
from citegraph.query.common import QueryContext
from citegraph.query.graph import find_path, what_calls
from citegraph.store import Store

MakeRepo = Callable[[str, dict[str, str]], Path]

MONOREPO = {
    "pyproject.toml": '[project]\nname = "site"\n',
    "components/bff/Site.sln": "",
    "components/bff/src/Api/Api.csproj": "<Project />\n",
    "components/bff/src/Api/Tasks.cs": (
        "namespace Site.Api.Services;\n"
        "public static class DramatiqTasks\n"
        "{\n"
        '    public const string ClassifyStems = "classify_stems";\n'
        "}\n"
    ),
    "components/bff/src/Api/VersionEndpoints.cs": (
        "using Site.Api.Services;\n"
        "namespace Site.Api.Endpoints;\n"
        "public static class VersionEndpoints\n"
        "{\n"
        "    private static async Task ClassifyStems(Guid versionId, IJobQueue queue)\n"
        "    {\n"
        "        await queue.EnqueueAsync(DramatiqTasks.ClassifyStems, new object[] { versionId.ToString() });\n"
        "    }\n"
        "}\n"
    ),
    "components/worker/requirements.txt": "dramatiq\n",
    "components/worker/app/__init__.py": "",
    "components/worker/app/tasks.py": (
        "import dramatiq\n"
        "\n"
        "\n"
        '@dramatiq.actor(actor_name="classify_stems", queue_name="default")\n'
        "def classify_stems(version_id):\n"
        "    from audio.stems import classify_stems as classify_audio\n"
        "\n"
        "    return classify_audio([version_id])\n"
    ),
    "components/api/requirements.txt": "fastapi\n",
    "components/api/app/__init__.py": "",
    "components/api/app/tasks.py": "def classify_stems():\n    pass\n",  # same module name, other project
    "components/analysis/pyproject.toml": '[project]\nname = "audio"\n',
    "components/analysis/src/audio/__init__.py": "",
    "components/analysis/src/audio/stems/__init__.py": "from .classify import classify_stems\n",
    "components/analysis/src/audio/stems/classify.py": (
        "def classify_one(path):\n"
        "    return path\n"
        "\n"
        "\n"
        "def classify_stems(paths):\n"
        "    return [classify_one(p) for p in paths]\n"
    ),
}

ENDPOINT = "Site.Api.Endpoints.VersionEndpoints.ClassifyStems"


def test_a_request_is_traced_across_three_projects_and_two_languages(
    make_repo: MakeRepo, repos_root: Path
) -> None:
    make_repo("site", MONOREPO)
    (repos_root / "citegraph.toml").write_text('projects = "auto"\n', encoding="utf-8")
    stats = index_root(repos_root)
    ctx = QueryContext(Store.open_read_only(Path(stats.db_path)))
    path = find_path(ctx, ENDPOINT, "audio.stems.classify.classify_one").data
    assert [(s.symbol.repo, s.symbol.qualified_name, s.via.rule if s.via else None) for s in path] == [
        ("site/components/bff", ENDPOINT, None),
        ("site/components/worker", "app.tasks.classify_stems", "queue_match"),
        ("site/components/analysis", "audio.stems.classify.classify_stems", "import_scope"),
        ("site/components/analysis", "audio.stems.classify.classify_one", "same_file"),
    ]
    callers = what_calls(ctx, "site/components/worker:app.tasks.classify_stems").data
    assert [(c.symbol.qualified_name, c.rule, c.path, c.line) for c in callers] == [
        (ENDPOINT, "queue_match", "src/Api/VersionEndpoints.cs", 7)
    ]
