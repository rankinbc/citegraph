"""Queue facts: job sends, job handlers and the string constants that name jobs."""

import textwrap

from citegraph.extract.csharp import CSharpExtractor
from citegraph.extract.python import PythonExtractor

CS = textwrap.dedent(
    """\
    namespace Shop.Jobs;

    public static class Tasks
    {
        public const string Classify = "classify_stems";
        public const string Spaced = "not a name";
        public const string Pathy = "a/b";
        public const int Retries = 3;
    }

    public class Endpoints
    {
        private readonly IJobQueue _queue;
        public async Task Post(IJobQueue queue, string taskName)
        {
            await queue.EnqueueAsync(Tasks.Classify, new object[] { 1 });
            await _queue.EnqueueAsync("run_triage", new object[] { 1 });
            _queue.Enqueue(Shop.Jobs.Tasks.Classify);
            await queue.EnqueueAsync(taskName, new object[] { 1 });
            await queue.PublishJob("custom_job");
        }
    }
    """
)

PY = textwrap.dedent(
    """\
    import dramatiq
    from dramatiq import Message, actor


    @dramatiq.actor(actor_name="classify_stems", queue_name="default")
    def classify_stems(version_id):
        pass


    @actor
    def plain_job():
        pass


    @dramatiq.actor(queue_name="coach")
    def coach_reply(msg):
        pass


    def kick(broker, sock):
        classify_stems.send("v1")
        plain_job.send_with_options(args=(), delay=10)
        broker.enqueue(Message(queue_name="default", actor_name="run_triage", args=(), kwargs={}, options={}))
        sock.send(b"x")
    """
)


def queue_refs(result):  # type: ignore[no-untyped-def]
    return [(r.from_qualified, r.kind, r.to_name) for r in result.references if r.kind.startswith("queue")]


def test_csharp_sends_by_literal_and_constant() -> None:
    result = CSharpExtractor().extract("Jobs.cs", CS.encode())
    assert queue_refs(result) == [
        ("Shop.Jobs.Endpoints.Post", "queue_const", "Tasks.Classify"),
        ("Shop.Jobs.Endpoints.Post", "queue", "run_triage"),
        ("Shop.Jobs.Endpoints.Post", "queue_const", "Shop.Jobs.Tasks.Classify"),
    ]  # a forwarded variable is not a send, and PublishJob is not a send method by default


def test_csharp_send_methods_are_configurable() -> None:
    result = CSharpExtractor(send_methods=("PublishJob",)).extract("Jobs.cs", CS.encode())
    assert queue_refs(result) == [("Shop.Jobs.Endpoints.Post", "queue", "custom_job")]


def test_csharp_string_constants_shaped_like_names() -> None:
    result = CSharpExtractor().extract("Jobs.cs", CS.encode())
    assert [(c.qualified_name, c.value, c.line) for c in result.string_consts] == [
        ("Shop.Jobs.Tasks.Classify", "classify_stems", 5)
    ]  # values with spaces or "/" and non-string constants are not stored


def test_python_actors_and_sends() -> None:
    result = PythonExtractor().extract("app/jobs.py", PY.encode())
    assert [(h.protocol, h.name, h.handler_qualified, h.line) for h in result.queue_handlers] == [
        ("dramatiq", "classify_stems", "app.jobs.classify_stems", 5),
        ("dramatiq", "plain_job", "app.jobs.plain_job", 10),
        ("dramatiq", "coach_reply", "app.jobs.coach_reply", 15),
    ]
    assert queue_refs(result) == [
        ("app.jobs.kick", "queue_actor", "classify_stems"),
        ("app.jobs.kick", "queue_actor", "plain_job"),
        ("app.jobs.kick", "queue", "run_triage"),
        ("app.jobs.kick", "queue_actor", "sock"),  # resolved later: sock is no actor, so no edge
    ]
