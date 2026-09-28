# Architecture

```mermaid
flowchart LR
  R[git repos] --> I[ingest]
  I --> X[extract<br/>tree-sitter, process pool]
  X --> S[(store<br/>SQLite WAL, FTS5)]
  RD[redact<br/>sanitizer] -. every write .-> S
  S --> RS[resolve<br/>rules + confidence]
  RS --> S
  S --> Q[query<br/>Answer envelope]
  Q --> M[MCP server<br/>read-only, audited]
  Q --> C[CLI]
  RD -. every response .-> M
  M --> A[(audit JSONL)]
```

| Unit | Owns | Never does |
|---|---|---|
| ingest | repo discovery, tracked files, HEAD sha | read file contents |
| extract | symbols, references, imports, config key names | touch the database |
| redact | secret shapes, sanitizer, leak scan | log or print a matched value |
| store | schema, the only write path | store source text or config values |
| resolve | edges with rule and confidence | guess targets outside the index |
| query | every read, the evidence envelope | write |
| mcp | 9 tools, audit, egress sanitizing | shell out (except `git rev-parse HEAD`), write |
