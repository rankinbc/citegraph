"""Queue links: a job sent by name, matched to the function registered under that name."""

from pathlib import Path

from citegraph.resolve.rules import CONFIDENCE
from tests.helpers import build_store, edge_set

TASKS = (
    'namespace Shop.Jobs;\npublic static class Tasks { public const string Classify = "classify_stems"; }\n'
)
ACTORS = (
    "import dramatiq\n"
    "\n"
    "\n"
    '@dramatiq.actor(actor_name="classify_stems")\n'
    "def classify(version_id):\n"
    "    pass\n"
    "\n"
    "\n"
    "@dramatiq.actor\n"
    "def run_triage(job_id):\n"
    "    pass\n"
)


def edges(tmp_path: Path, files: dict[str, str]) -> set[tuple[str, str, str, str]]:
    return edge_set(build_store(tmp_path / "i.db", {"a": files}))


def test_csharp_send_by_constant_and_by_literal_reach_python_actors(tmp_path: Path) -> None:
    files = {
        "bff/Tasks.cs": TASKS,
        "bff/Api.cs": (
            "using Shop.Jobs;\n"
            "namespace Shop.Api;\n"
            "public class Endpoints\n"
            "{\n"
            "    public async Task Classify(IJobQueue queue) => await queue.EnqueueAsync(Tasks.Classify, new object[0]);\n"
            '    public async Task Triage(IJobQueue queue) => await queue.EnqueueAsync("run_triage", new object[0]);\n'
            "}\n"
        ),
        "worker/jobs.py": ACTORS,
    }
    queue_edges = {e for e in edges(tmp_path, files) if e[3] == "queue_match"}
    assert queue_edges == {
        ("Shop.Api.Endpoints.Classify", "worker.jobs.classify", "call", "queue_match"),
        ("Shop.Api.Endpoints.Triage", "worker.jobs.run_triage", "call", "queue_match"),
    }
    assert CONFIDENCE["queue_match"] == 0.9


def test_python_sends(tmp_path: Path) -> None:
    files = {
        "worker/jobs.py": ACTORS,
        "api/kick.py": (
            "from dramatiq import Message\n"
            "from worker.jobs import classify\n"
            "\n"
            "\n"
            "def kick(broker, sock):\n"
            '    classify.send("v1")\n'
            '    broker.enqueue(Message(actor_name="run_triage", args=(), kwargs={}, options={}))\n'
            '    sock.send(b"x")\n'
        ),
    }
    queue_edges = {e for e in edges(tmp_path, files) if e[3] == "queue_match"}
    assert queue_edges == {
        ("api.kick.kick", "worker.jobs.classify", "call", "queue_match"),
        ("api.kick.kick", "worker.jobs.run_triage", "call", "queue_match"),
    }


def test_unknown_jobs_and_unresolvable_constants_stay_unlinked(tmp_path: Path) -> None:
    files = {
        "bff/Api.cs": (
            "namespace Shop.Api;\n"
            "public class Endpoints\n"
            "{\n"
            '    public void A(IJobQueue q) { q.Enqueue("no_such_job"); }\n'
            "    public void B(IJobQueue q) { q.Enqueue(Missing.Name); }\n"
            "}\n"
        ),
        "worker/jobs.py": ACTORS,
    }
    assert {e for e in edges(tmp_path, files) if e[3] == "queue_match"} == set()


def test_two_handlers_for_one_job_name_are_ambiguous(tmp_path: Path) -> None:
    files = {
        "bff/Api.cs": 'namespace Shop.Api;\npublic class E { public void A(IJobQueue q) { q.Enqueue("run_triage"); } }\n',
        "worker/jobs.py": ACTORS,
        "worker2/jobs.py": "import dramatiq\n\n\n@dramatiq.actor\ndef run_triage(job_id):\n    pass\n",
    }
    store = build_store(tmp_path / "i.db", {"a": files})
    rows = store.conn.execute(
        "SELECT ts.qualified_name AS t, e.rule, e.candidates FROM edges e JOIN symbols ts ON ts.id = e.to_symbol_id "
        "JOIN symbols fs ON fs.id = e.from_symbol_id WHERE fs.qualified_name = 'Shop.Api.E.A' AND e.kind = 'call' "
        "AND ts.name = 'run_triage'"
    )
    assert sorted((r["t"], r["rule"], r["candidates"]) for r in rows) == [
        ("worker.jobs.run_triage", "ambiguous", 2),
        ("worker2.jobs.run_triage", "ambiguous", 2),
    ]
