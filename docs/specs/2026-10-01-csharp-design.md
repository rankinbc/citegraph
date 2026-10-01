# citegraph C# support - Design Spec

Date: 2026-10-01
Status: Design approved (option 2: declared-type receivers); spec under review; implementation plan not yet written
Extends: [docs/design.md](../design.md) (the as-built M1 spec). Where this file is silent, M1 rules hold.

## 1. Goal

citegraph v0.1 indexes Python only. Add C# so the same nine MCP tools answer callers, callees, call paths, config keys
and repo overviews for .NET codebases, measured with the same eval discipline as Python: compiler-grade bootstrapped
labels, a grep baseline, calibrated confidence and a CI regression gate. The public README then shows Python and C#
results side by side.

### Success criteria

| Criterion | Target |
|---|---|
| C# Level-1 eval | citegraph F1 beats the C# grep baseline on `what_calls` and `what_does_it_call` |
| Calibration | every C# rule bin with at least 10 edges within 0.15 of nominal, or the value adjusted to match (as in M1) |
| Python regression | every Python F1, calibration value and per-question answer identical to the committed Python results |
| Index time | eShop pinned release fully indexed in under 15 s on the developer laptop |
| Tool latency | p95 under 200 ms on the combined corpus |
| Tests | full suite green on Ubuntu and Windows; new extractor covered by fixture tests |

### Non-goals

TypeScript; a Roslyn-based precise mode; applying declared types to Python type hints (follow-up); generic type
argument inference; extension-method resolution beyond name matching; properties and events as separate symbols.

## 2. Approach

Lightweight static analysis with tree-sitter-c-sharp (MIT, PyPI `tree-sitter-c-sharp`), no compiler and no build. C#
declares types explicitly, so the extractor records the declared types of fields, constructor and method parameters,
and locals, and rewrites a call on such a receiver (`_orders.PlaceOrder(...)`) into a typed reference
(`IOrderService.PlaceOrder`). The resolver then resolves the type through namespaces and `using` directives and looks
up the member. Everything else falls back to the M1 name rules, stoplist and candidate cap.

## 3. Extraction (`src/citegraph/extract/csharp.py`)

Language id `csharp`, extension `.cs`. `ingest.LANGUAGE_BY_EXT` gains `.cs`; `CitegraphConfig.languages` defaults to
`["python", "csharp"]`.

### Names

- Namespace: block-scoped (`namespace A.B { }`) and file-scoped (`namespace A.B;`); nested namespaces concatenate.
- Qualified names: `<namespace>.<Type>[.<NestedType>...].<Member>`. A file with no namespace uses the global
  namespace (no prefix).
- `files.module` stores the file's first namespace (empty for the global namespace).
- Each C# file emits one `module`-kind symbol named after its namespace (qualified name = namespace, or the file stem
  for the global namespace); top-level statements (`Program.cs` without a class) are attributed to it.

### Symbols

| C# construct | Symbol kind | Name |
|---|---|---|
| class, record, record struct, struct, enum | `class` | type name |
| interface | `interface` | type name |
| method, local function owner | `method` | method name |
| constructor | `method` | `.ctor` (as SCIP names it) |
| static constructor | `method` | `.cctor` |

Overloads share one qualified name (the store already allows duplicate qualified names). Partial classes produce one
symbol per declaration; queries treat same-named symbols as one name. Calls inside lambdas, local functions, property
accessors and field initializers are attributed to the enclosing method, or to the type when there is no method.
`param_count` is the declared parameter count; `visibility` is `public` for `public`/`protected`/`internal` members and
`private` otherwise.

### References

| Syntax | Reference |
|---|---|
| `Foo(...)` | call, `to_name="Foo"` |
| `a.b.Foo(...)`, `Type.Foo(...)` | call, dotted `to_name` (same convention as Python) |
| `x.Foo(...)` where `x` has a declared type `T` in scope | call, `to_name="Foo"`, `receiver_type="T"` |
| `this.Foo(...)`, `base.Foo(...)` | call, `to_name="this.Foo"` / `"base.Foo"` |
| `new T(...)`, `new()` with a declared target type | instantiate (resolved to the class symbol) |
| `class C : Base, IFoo` | inherit, one per base type |
| `await`-ed and generic calls (`Foo<T>(...)`) | as above, type arguments dropped |

Declared types come from field and property declarations, constructor and method parameters, local declarations with
an explicit type, `var x = new T(...)`, `using var x = new T(...)`, and `out T x` / pattern variables (`is T x`). Generic
types keep their base name (`IRepository<Order>` -> `IRepository`); nullable and array markers are dropped. A name
shadowed in an inner scope uses the innermost declaration.

### Imports

| Directive | ImportFact |
|---|---|
| `using A.B;` | `local_name="*"`, `target="A.B"` (namespace import) |
| `using X = A.B.C;` | `local_name="X"`, `target="A.B.C"` (alias) |
| `using static A.B.C;` | `local_name="*static"`, `target="A.B.C"` |
| `global using A.B;` (any form) | as above with a `*global` marker; applies to every C# file in the same repo |

### Config reads (origin `code-read`)

String-literal first arguments or indexers of: `configuration["A:B"]` / `Configuration["A:B"]` (any receiver whose
declared type or name ends in `Configuration`), `GetSection("A")`, `GetValue<T>("A")`, `GetConnectionString("Name")`
(recorded as `ConnectionStrings:Name`), `GetRequiredSection("A")`, and `Environment.GetEnvironmentVariable("X")`. Values
are never read. `appsettings*.json` keys are already extracted by the M1 config extractor and normalize to the same
`a:b` keys.

### Entry points

`EntryPoint.kind` gains `program-main`: a `static Main` method, or a file containing top-level statements.

## 4. Data model changes

- `SymbolKind`, `RefKind`, `ConfigOrigin` unchanged.
- `Reference` gains `receiver_type: str | None = None`; `refs` gains a nullable `receiver_type` column (schema version
  bump; the content fingerprint changes so existing indexes re-extract).
- `EntryPoint.kind` adds `"program-main"`.
- `ImportFact.local_name` may be `"*"`, `"*static"` or `"*global"` (C# only).

## 5. Resolution

Order for a C# reference (first match wins). Each step is a named rule with a calibrated confidence; new rules start at
the values below and are adjusted by the eval exactly as in M1.

1. `this.Foo` / `base.Foo`: member of the enclosing type (or its declared base types in the same repo) -> `same_file`
   if in the same file, else `declared_type`.
2. `receiver_type` set: resolve the type name (step 4 rules) to a type symbol, then its member `Foo`; if the member is
   not declared on that type, try its base types and interfaces found in the index (one level per hop, max 3 hops)
   -> **`declared_type`** (initial 0.85). If the type cannot be resolved, fall back to step 5.
3. Plain or dotted name whose head is a type or member visible in the enclosing type or file -> `same_file`.
4. Type-name resolution for a head `T`: alias import -> `import_scope`; `<current namespace and each parent>.T`
   -> **`same_namespace`** (initial 0.9); `<each using / global-using namespace>.T` -> `import_scope`. More than one
   match -> ambiguous among them. Then the member is looked up on the resolved type.
5. Name fallback (M1 rules): `repo_unique`, `cross_repo_unique`, `ambiguous`, with the stoplist (extended with common C#
   names: `ToString`, `Equals`, `GetHashCode`, `Dispose`, `Add`, `Remove`, `Get`, `Set`, `Execute`, `Handle`,
   `Invoke`, `Configure`, `Map`, `Select`, `Where`, `Any`, `First`, `ToList`, `ToListAsync`, `SaveChangesAsync`) and the
   10-candidate cap. The C# additions apply to C# references only; the Python stoplist is unchanged.

**Language isolation (applies to Python too):** name fallback and qualified lookups never link a reference to a
symbol in a file of another language. This keeps the committed Python results unchanged when C# repos are indexed in
the same corpus.

`explain_edge` meanings gain `declared_type` and `same_namespace`. `RESOLVER_VERSION` bumps.

## 6. Evaluation

- **Corpus:** add `dotnet/eShop` (MIT) and `jasontaylordev/CleanArchitecture` (MIT), each pinned to its latest release
  tag commit (latest commit on the default branch if a repo has no release tags; recorded with a comment). Python
  repos stay pinned as they are.
- **Labels:** run `scip-dotnet` (dotnet global tool, installed into a tool path inside CITEGRAPH_HOME, not globally)
  against each repo's solution after `dotnet restore`. `scip_labels.parse_scip_symbol` learns scip-dotnet descriptors
  (non-backticked namespace segments `Name/`, method disambiguators `Name(+1).`, constructors `` `.ctor`(). ``).
  If a solution cannot be restored/indexed, that repo's labels are verified against source without SCIP, and the
  report says so.
- **Golden set:** `eval/golden/csharp.yaml`, about 40 questions: 16 `what_calls`, 16 `what_does_it_call`, 4
  `find_config_key`, 4 `find_path`; same labeling rules as Python plus: calls through an interface-typed receiver count
  as calls of the interface member (static dispatch, as SCIP records them); DI registrations
  (`services.AddScoped<IFoo, Foo>()`) are not calls of `Foo`. Committed before any eval run. About a third flagged
  `ci: true`, including at least one passing question per tool after the first run (documented as in M1).
- **Grep baseline for C#:** `evaluation/spans_csharp.py` tracks scopes by brace depth (skipping strings, chars,
  verbatim/raw strings and comments) and recognizes type and method declarations by regex; `baseline_grep` dispatches
  by file extension. Still regex only: no tree-sitter, no index.
- **Runs and reports:** `eval run --golden eval/golden/csharp.yaml --out eval/reports/<date>-csharp`; baselines
  `eval/baseline.csharp.json` and `eval/baseline.csharp.ci.json`; CI runs both the Python and the C# subsets.
  A hand-written `analysis.md` in the C# report folder, as for Python.

## 7. Docs

README: the opening and "What it is" mention Python and C#; Results shows a Python table and a C# table; Limitations
drops "Python only" and adds C#-specific limits (no generic inference, extension methods by name, reflection/DI
dispatch invisible); Install/usage unchanged. `docs/design.md` gains a "C# support" section summarizing this spec and
section 14 items for the changes. `docs/design-notes.md` gains a short C# paragraph with the measured numbers.

## 8. Testing

- Extractor fixture tests: block and file-scoped namespaces, nested types, records/structs/interfaces, overloads,
  partial classes, constructors, lambdas and local functions, declared types for each source listed in section 3,
  generics, `using` forms including global and alias, config reads, top-level statements, malformed source
  (`parse_error`).
- Resolver tests per new rule, language isolation, and inheritance hops.
- A C# sample corpus fixture (two small repos with interfaces, DI-style fields, cross-project calls and appsettings),
  mirroring the Python `sample_corpus`, with hand-verified expected edges.
- Baseline tests for brace scopes (strings and comments containing braces, expression-bodied members).
- `parse_scip_symbol` tests for scip-dotnet descriptor forms.
- The Python eval re-run must stay identical (regression gate).
