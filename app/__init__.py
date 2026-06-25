"""de-swarm FastAPI gateway.

Wraps the local Ollama `de-sql-3b-v2` model in a production HTTP API
with three core endpoints:

  POST /query              NL → SQL (no execution)
  POST /execute            SQL → rows
  POST /query-and-execute  NL → SQL → rows

Plus introspection:
  GET  /health
  GET  /schemas
  GET  /schemas/{name}

All SQL execution is read-only and sandboxed.
"""
__version__ = "0.1.0"
