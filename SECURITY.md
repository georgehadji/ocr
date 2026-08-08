# Security Policy

## Reporting a vulnerability

Email **vivlosbooks@gmail.com** with a description and, if possible, a
reproduction. Please don't open a public issue for anything that could be
actively exploitable before a fix ships.

## Scope

OmniOCR processes documents supplied by the operator (not arbitrary public
input) across three editions:

- **Desktop** — local Streamlit UI, single user.
- **Server** — FastAPI + RQ, API-key gated.
- **Cloud** — FastAPI + Celery + Redis, multi-tenant.

Upload handling (`infrastructure/security.py`) validates magic bytes and size
before any document reaches the pipeline. Report any bypass of that check as
a vulnerability.

## What's already covered

- Secrets are read from environment variables and masked in `Settings`'
  `repr` — never hardcoded, never logged in full.
- SQL in `corrections_store.py` is parameterized.
- `subprocess` calls (Calamari, Ketos) use argument lists, never shell
  strings.
- The optional Calamari engine (GPLv3) is subprocess-isolated; core never
  imports it directly, and `scripts/check_license_isolation.py` enforces
  that mechanically in CI.
- `bandit` and `pip-audit` run in CI on every push (Linux).

## Supported versions

Pre-1.0; only `main` is supported. There is no LTS branch yet.
