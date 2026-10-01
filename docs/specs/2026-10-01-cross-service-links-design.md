# citegraph cross-service links - Design Spec

Date: 2026-10-01
Status: Implemented (plan `.superpowers/plans/2026-10-01-cross-service-links.md`, branch `cross-service-links`)
Extends: [docs/design.md](../design.md) and [the C# design](2026-10-01-csharp-design.md). Where this file is silent,
those rules hold.

## 1. Goal

citegraph links code across process boundaries, not only through imports. A web endpoint that enqueues a job is
linked to the worker function that runs it, so `what_calls`, `what_does_it_call` and `find_path` follow a request
from one service into another, across languages. It works on a monorepo as it is laid out today, without copying
components into separate repositories.

A throwaway spike on a real ASP.NET Core + Dramatiq system (C# BFF, Python worker, Python analysis and shared
packages) produced this chain in one `find_path` call, and is the target this spec makes permanent:

```
[bff]      VersionEndpoints.ClassifyStems                     queue_match 0.9  (enqueues "classify_stems")
[worker]   app.tasks_dramatiq.classify_stems                  import_scope 0.9 (through a package re-export)
[analysis] audio_analysis.stems.classify.classify_stems       same_file 0.95
[analysis] audio_analysis.stems.classify.classify_one
```

### Success criteria

| Criterion | Target |
|---|---|
| Queue links | every send whose job name is a literal or a constant links to the actor registered under that name; the spike solution's 7 production sends all link, its 4 forwarding sends (a variable job name) stay unlinked |
| End to end | with `projects = "auto"`, indexing the monorepo root reproduces the chain above with one `find_path` |
| Python eval | no tool's F1 decreases; every per-question change is explained in a report note; baselines updated |
| Default behavior | without a `citegraph.toml` `projects` setting, repos are indexed exactly as before (same repo names, module names, paths) |
| Tests | full suite green on Ubuntu and Windows; ruff, ruff format and strict pyright clean |

### Non-goals

HTTP route matching (endpoints matched to `HttpClient`/`requests`/`fetch` calls) and the TypeScript extractor: the
next phase. Other queue libraries (Celery, RQ, MassTransit, Azure Service Bus): the design leaves room, nothing is
built. Kafka/pub-sub topics with many consumers. Following re-exports in C# (not needed: namespaces are global).

## 2. Projects inside a repository

### Problem

A monorepo holds several projects. citegraph names a Python module by its path from the repo root (or a top-level
`src/`), so `components/worker/app/tasks_dramatiq.py` becomes `components.worker.app.tasks_dramatiq` and the worker's
own `from app.x import y` never matches. Two components can also use the same package name (`api/app` and
`worker/app`).

### Design

A project is a folder inside a git repository that citegraph indexes as its own logical repo. Everything that is
per-repo today then works per project: module names start at the project folder, lookups prefer the same project,
and cross-project matches use the cross-repo rules.

Projects are opt-in, from `citegraph.toml` at the index root:

```toml
projects = "auto"            # detect project folders (below)
# or list them explicitly:
# projects = ["components/api", "components/worker", "components/bff"]
```

`projects` defaults to `"off"`: every repository is one logical repo, as today. The setting applies to every
repository under the root.

**Auto detection.** In each repository, a folder is a project root when it holds a tracked marker file:
`pyproject.toml`, `setup.py`, `setup.cfg` or `requirements.txt` (Python), or a `*.sln` file (C#). A folder holding a
`*.csproj` is a project root only when no folder above it in the same repository holds a `*.sln`, so a .NET solution
stays one project. The repository root is never split off as a project of its own; files not under any project root
stay in a logical repo for the repository itself. A repository that fails to scan keeps its projects' rows until the
next good run.

**Assignment.** Each tracked file belongs to its nearest project root (the deepest one above it). Nested project roots
take their own files away from the outer project.

**Naming.** A project's logical repo is named `<repo>/<project path>`, for example
`AIMusicAnalysisSite/components/worker`; the remainder keeps the repository's name. Paths stored for a project's
files, and returned in evidence, are relative to the project folder. Every logical repo of one repository records the
repository's head commit, and staleness is checked per repository as today.

**Config files** (`appsettings*.json`, `.env.example` and the others) belong to their project like any other file.

**Explicit list.** Listed folders are the project roots; auto detection is off. An unknown or untracked folder in the
list is reported as an index warning.

## 3. Queue links

### What is extracted

Three new kinds of facts, each with the location it came from:

| Fact | C# | Python |
|---|---|---|
| **Queue send**: a job name sent by a symbol | an invocation whose method name is in `queues.send_methods` (default `Enqueue`, `EnqueueAsync`) and whose first argument is a string literal or a member access (`DramatiqTasks.ClassifyStems`) | `<actor>.send(...)` and `<actor>.send_with_options(...)`, where `<actor>` is a name or dotted name; `<x>.enqueue(Message(actor_name="name", ...))` with a literal name |
| **Queue handler**: a job name a function handles | (none in this phase) | a function decorated with `@dramatiq.actor` or `@actor`, with or without arguments: its `actor_name="..."` keyword, or the function's own name when that keyword is absent |
| **String constant** | a `const string` field whose value matches `^[A-Za-z_][A-Za-z0-9_.:-]{0,63}$` | (none in this phase) |

A send whose job name is neither a literal nor a member access (a variable, a parameter, an interpolated string) is
not recorded: a queue implementation that forwards `taskName` is not a send. A literal job name is recorded only
when it matches the same name shape as a constant.

`queues.send_methods` is configurable in `citegraph.toml`:

```toml
[queues]
send_methods = ["Enqueue", "EnqueueAsync", "PublishJob"]
```

**String constants and the security model.** citegraph stores names and locations, never values. Job names behind
constants are the one exception, and it is narrow: only `const string` fields, only values shaped like identifiers
(no spaces, no `/`, at most 64 characters), and every value still goes through the sanitizing write path, so a
secret-shaped value is redacted before it is stored and the post-run leak scan covers the new tables. The
exception is documented in the README's security section and in `docs/design.md` section 7.

### Data model

- Sends are rows in `refs` (an edge needs a reference row for its evidence, `edges.ref_id`): `from_qualified` is the
  sending symbol and `line` the send's location. `kind` is `queue` (`to_name` is the literal job name), `queue_const`
  (`to_name` is a dotted reference to a constant, resolved later) or `queue_actor` (Python `x.send`; `to_name` is the
  receiver, resolved later to the actor function). The protocol is `dramatiq` for every send until a second queue
  library is added.
- `queue_handlers(file_id, handler_qualified, protocol, name, line)`.
- `string_consts(file_id, qualified_name, value, line)`.
- Matching models in `models.py` (`QueueSend`, `QueueHandler`, `StringConst`) and fields on `ExtractResult`.
- Schema version 3; `Store.open` creates the new tables in an older index (they are new tables, so `CREATE TABLE IF
  NOT EXISTS` suffices) and the content fingerprint changes, so files are re-extracted.

### Resolution

After the per-language pass, a queue pass turns each send into edges:

1. Determine the job name. `literal`: the name itself. `constant`: resolve the dotted reference as a C# type name plus
   member with the C# rules (aliases, namespace walk, usings), then read that constant's value from
   `string_consts`; an unresolvable constant yields no edge. `actor` (Python): resolve the receiver with the Python
   rules to a function; when that function is a queue handler, the edge goes straight to it.
2. Find handlers with the same protocol and name, in any language and any logical repo.
3. One handler: an edge with rule `queue_match`, confidence 0.9 (initial; calibrated when a cross-service eval
   exists). Several handlers with the same name: one edge per handler with rule `ambiguous`. None: no edge.

Edges are kind `call` from the sending symbol (for C#, the canonical declaration of its group) to the handler
function, so the existing tools return them with no change. `queue_match` is the one rule allowed to link symbols of
different languages; name fallback and qualified lookups stay language-isolated. `explain_edge`'s meaning for
`queue_match`: "a job sent to a queue by name, matched to the worker function registered under that name", and its
evidence is the send's location.

## 4. Python re-export following

When a Python import target cannot be found as a symbol (`from audio_analysis.stems import classify_stems`, where
`classify_stems` is defined in `audio_analysis/stems/classify.py`), the resolver looks at the module the name is
imported from (`audio_analysis.stems`; any module, packages are the common case), searched in the referencing
project first: if that module imports the name, the lookup follows that import.
This repeats for at most 3 hops and stops on a cycle. The rule stays `import_scope`. It applies to both call
references and `import` references.

This changes Python results (the eval's `flask.Response` question misses all 15 callers today because of it). The
Python eval is re-run, each per-question change is recorded in a note in the report folder, no tool's F1 may
decrease, and `eval/baseline.json` and `eval/baseline.ci.json` are updated in the same change. The README's numbers
and the "Re-exports are not followed" limitation are updated to match.

## 5. Hand-written links

As designed in `docs/design.md` (Curated overrides), with one change: the file is
`citegraph.overrides.yaml` tracked in any indexed repository (not only at the index root), so its evidence has a
repository, a path and a commit like every other edge. Names are exact qualified names; only `kind: call`; the note
is stored in `refs.note`; an answer mixing curated and derived edges reports `derived`.

```yaml
edges:
  - from: Spectr.Bff.Endpoints.VersionEndpoints.ClassifyStems
    to: app.tasks_dramatiq.classify_stems
    kind: call
    note: Dramatiq job classify_stems
```

- `from`/`to` are qualified names, optionally `repo:qualified.name`; they resolve with the query tools' symbol
  resolution (one symbol, or one group of same-named declarations).
- Each entry becomes an edge with rule `curated`, confidence 1.0; answers that include one report `source: curated`.
  `explain_edge` shows the note.
- An entry whose `from` or `to` matches no symbol, or more than one, is listed by `status` (`overrides_unresolved`:
  file, line, reason) and produces no edge. A malformed file is a parse error for that file and produces no edges.
- The file is read like a config file: only the listed names and the note are stored; the note is sanitized.

## 6. Testing

- Extractor tests: C# sends (literal, constant, a forwarded variable that is not recorded), `const string` capture
  and its identifier-shape filter (a value with a space or `/` is not stored; a secret-shaped value is redacted),
  `send_methods` from config; Python `@dramatiq.actor` with and without `actor_name`, `actor.send`,
  `send_with_options`, `broker.enqueue(Message(actor_name=...))`.
- Resolver tests: a C# constant through a `using`, a literal, a Python `x.send`, a missing handler, two handlers with
  one name, language isolation still holding for every other rule.
- Re-export tests: one hop, two hops, a cycle, a re-export of a name that does not exist.
- Project tests: auto detection (Python markers, `.sln` claiming its `.csproj` files, the repository root never
  split), explicit list, assignment to the nearest root, names and relative paths, two projects with the same
  package name (`app`) resolving inside their own project, default `"off"` unchanged.
- Overrides tests: valid entries, unknown and ambiguous names in `status`, malformed YAML, `source: curated`.
- A fixture monorepo mirroring the target layout (a C# solution enqueuing by constant, a Python worker with actors,
  a Python analysis package that re-exports, a shared package), with hand-verified expected edges and a `find_path`
  across all four projects.
- The Python eval run before and after, compared per question.
- A manual end-to-end check on the real monorepo with `projects = "auto"`.

## 7. Docs

README: "What you can ask" gains a cross-service example ("Which endpoint triggers the `classify_stems` job, and what
does it do?"); Limitations lose "Re-exports are not followed" and the nested-package-roots item (pointing to the
`projects` setting), and gain "Only Dramatiq queues are linked; HTTP calls between services are not yet". The guide
gains a "Monorepos and projects" section and a "Linking services" section (queues and `citegraph.overrides.yaml`),
and its configuration section documents `projects` and `[queues]`. `docs/design.md` gains section 14 items for the
changes, and section 7 records the string-constant exception.
