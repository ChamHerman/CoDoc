from __future__ import annotations

import json
from pathlib import Path

from codoc.indexer import build_index


def test_build_index_extracts_and_links_fastapi_endpoints(tmp_path: Path) -> None:
    repo = tmp_path / "api_repo"
    (repo / "src").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)

    (repo / "src" / "routes.py").write_text(
        '''from fastapi import APIRouter, FastAPI

app = FastAPI()
router = APIRouter(prefix="/v1")


@router.get("/users/{user_id}")
async def fetch_user(user_id: str) -> dict[str, str]:
    """Fetch a user profile by ID."""
    return {"id": user_id}


@app.post("/webhooks/stripe")
def handle_stripe_webhook(event_id: str) -> dict[str, bool]:
    """Receive Stripe webhook events."""
    return {"ok": True}
''',
        encoding="utf-8",
    )

    (repo / "docs" / "api.md").write_text(
        """# HTTP API

## Fetch User

Send a `GET /v1/users/{user_id}` request to retrieve a profile.

## Stripe Webhooks

Configure your dashboard to call `/webhooks/stripe` via HTTP POST.
""",
        encoding="utf-8",
    )

    out_path = tmp_path / "api_graph.json"
    graph, _ = build_index(repo_root=repo, output_path=out_path)

    chunks_by_id = {c.chunk_id: c for c in graph.code_chunks}
    fetch_chunk = chunks_by_id["src/routes.py::fetch_user"]
    webhook_chunk = chunks_by_id["src/routes.py::handle_stripe_webhook"]

    assert fetch_chunk.chunk_type == "endpoint"
    assert fetch_chunk.metadata["http_methods"] == ["GET"]
    assert fetch_chunk.metadata["route_path"] == "/v1/users/{user_id}"

    assert webhook_chunk.chunk_type == "endpoint"
    assert webhook_chunk.metadata["http_methods"] == ["POST"]
    assert webhook_chunk.metadata["route_path"] == "/webhooks/stripe"

    sections_by_path = {s.heading_path: s for s in graph.doc_sections}
    fetch_sec = sections_by_path["HTTP API > Fetch User"].section_id
    webhook_sec = sections_by_path["HTTP API > Stripe Webhooks"].section_id

    edges = {(e.source_section_id, e.target_chunk_id) for e in graph.edges}
    assert (fetch_sec, "src/routes.py::fetch_user") in edges
    assert (webhook_sec, "src/routes.py::handle_stripe_webhook") in edges

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    persisted_fetch = next(
        c for c in payload["code_chunks"] if c["chunk_id"] == "src/routes.py::fetch_user"
    )
    assert persisted_fetch["chunk_type"] == "endpoint"


def test_build_index_extracts_and_links_config_schemas_and_typed_constants(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "config_repo"
    (repo / "src").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)

    (repo / "src" / "settings.py").write_text(
        '''from dataclasses import dataclass
from pydantic import BaseModel

RATE_LIMIT_RPS: int = 100


class BillingSettings(BaseModel):
    """Payment gateway configuration."""

    webhook_secret: str
    retry_limit: int = 3


@dataclass(frozen=True)
class CacheOptions:
    """In-memory cache tuning."""

    ttl_seconds: int = 300
''',
        encoding="utf-8",
    )

    (repo / "docs" / "configuration.md").write_text(
        """# Configuration

## Rate Limiting

Requests are capped at RATE_LIMIT_RPS per minute.

## Billing

Set `webhook_secret` and `BillingSettings.retry_limit` before enabling live charges.

## Caching

Adjust `ttl_seconds` to tune eviction.
""",
        encoding="utf-8",
    )

    graph, _ = build_index(repo_root=repo, output_path=tmp_path / "cfg_graph.json")

    chunks_by_id = {c.chunk_id: c for c in graph.code_chunks}
    rate_chunk = chunks_by_id["src/settings.py::RATE_LIMIT_RPS"]
    billing_chunk = chunks_by_id["src/settings.py::BillingSettings"]
    cache_chunk = chunks_by_id["src/settings.py::CacheOptions"]

    assert rate_chunk.chunk_type == "schema"
    assert rate_chunk.metadata["config_keys"] == ["RATE_LIMIT_RPS"]

    assert billing_chunk.chunk_type == "schema"
    assert billing_chunk.metadata["config_keys"] == ["webhook_secret", "retry_limit"]

    assert cache_chunk.chunk_type == "schema"
    assert cache_chunk.metadata["config_keys"] == ["ttl_seconds"]

    sections_by_path = {s.heading_path: s for s in graph.doc_sections}
    edges = {(e.source_section_id, e.target_chunk_id) for e in graph.edges}

    assert (
        sections_by_path["Configuration > Rate Limiting"].section_id,
        "src/settings.py::RATE_LIMIT_RPS",
    ) in edges
    assert (
        sections_by_path["Configuration > Billing"].section_id,
        "src/settings.py::BillingSettings",
    ) in edges
    assert (
        sections_by_path["Configuration > Caching"].section_id,
        "src/settings.py::CacheOptions",
    ) in edges


def test_build_index_extracts_and_links_click_typer_and_argparse_cli_commands(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "cli_frameworks_repo"
    (repo / "src").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)

    (repo / "src" / "commands.py").write_text(
        '''import argparse
import click
import typer

typer_app = typer.Typer()


@click.command("deploy-service")
@click.option("--dry-run", is_flag=True, help="Simulate deployment.")
def deploy_service(dry_run: bool) -> None:
    """Deploy the service to production."""
    pass


@typer_app.command()
def sync_cache(
    webhook_port: int = typer.Option(8080, "--webhook-port", help="Port to listen on"),
) -> None:
    """Synchronize local cache with remote state."""
    pass


def run_migrations() -> int:
    """Execute database schema migrations via CLI."""
    parser = argparse.ArgumentParser(prog="codoc-migrate")
    parser.add_argument("--target-rev", required=True)
    subparsers = parser.add_subparsers(dest="action")
    subparsers.add_parser("upgrade-db")
    return 0
''',
        encoding="utf-8",
    )

    (repo / "docs" / "cli.md").write_text(
        """# CLI Reference

## Deployment

Run `mycli deploy-service --dry-run` before rolling out changes.

## Cache Sync

Pass `--webhook-port` when starting `sync-cache`.

## Database Migrations

Invoke `codoc-migrate upgrade-db --target-rev head` after schema edits.
""",
        encoding="utf-8",
    )

    graph, _ = build_index(repo_root=repo, output_path=tmp_path / "cli_graph.json")

    chunks_by_id = {c.chunk_id: c for c in graph.code_chunks}
    deploy_chunk = chunks_by_id["src/commands.py::deploy_service"]
    sync_chunk = chunks_by_id["src/commands.py::sync_cache"]
    migrate_chunk = chunks_by_id["src/commands.py::run_migrations"]

    assert deploy_chunk.chunk_type == "cli_command"
    assert deploy_chunk.metadata["command_name"] == "deploy-service"
    assert "--dry-run" in deploy_chunk.metadata["cli_options"]

    assert sync_chunk.chunk_type == "cli_command"
    assert sync_chunk.metadata["command_name"] == "sync-cache"
    assert "--webhook-port" in sync_chunk.metadata["cli_options"]

    assert migrate_chunk.chunk_type == "cli_command"
    assert "codoc-migrate" in migrate_chunk.metadata["subcommands"]
    assert "upgrade-db" in migrate_chunk.metadata["subcommands"]
    assert "--target-rev" in migrate_chunk.metadata["cli_options"]

    sections_by_path = {s.heading_path: s for s in graph.doc_sections}
    edges = {(e.source_section_id, e.target_chunk_id) for e in graph.edges}

    assert (
        sections_by_path["CLI Reference > Deployment"].section_id,
        "src/commands.py::deploy_service",
    ) in edges
    assert (
        sections_by_path["CLI Reference > Cache Sync"].section_id,
        "src/commands.py::sync_cache",
    ) in edges
    assert (
        sections_by_path["CLI Reference > Database Migrations"].section_id,
        "src/commands.py::run_migrations",
    ) in edges


def test_edge_cases_non_config_globals_multi_routes_and_single_word_cli_commands(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "edge_repo"
    (repo / "src").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)

    (repo / "src" / "edge.py").write_text(
        '''import click
from fastapi import APIRouter, FastAPI

app = FastAPI()
api_router: APIRouter = APIRouter("/v2")
logger: object = object()


@api_router.get("/health")
@api_router.head("/health")
def health_probe() -> dict[str, bool]:
    return {"ok": True}


@click.command("serve")
def start_http_server() -> None:
    pass
''',
        encoding="utf-8",
    )

    (repo / "docs" / "edge.md").write_text(
        """# Edge Cases

## Health

Probe `HEAD /v2/health` to verify liveness. Notice `HTTP POST` is not a CLI command.

## Server

Invoke `serve` to boot the API server.
""",
        encoding="utf-8",
    )

    graph, _ = build_index(repo_root=repo, output_path=tmp_path / "edge.json")

    chunks_by_id = {c.chunk_id: c for c in graph.code_chunks}
    assert "src/edge.py::logger" not in chunks_by_id
    health_chunk = chunks_by_id["src/edge.py::health_probe"]
    assert set(health_chunk.metadata["http_methods"]) == {"GET", "HEAD"}
    assert health_chunk.metadata["route_path"] == "/v2/health"

    sections_by_path = {s.heading_path: s for s in graph.doc_sections}
    edges = {(e.source_section_id, e.target_chunk_id) for e in graph.edges}
    assert (
        sections_by_path["Edge Cases > Health"].section_id,
        "src/edge.py::health_probe",
    ) in edges
    assert (
        sections_by_path["Edge Cases > Server"].section_id,
        "src/edge.py::start_http_server",
    ) in edges
    health_refs = sections_by_path["Edge Cases > Health"].references
    assert all(r.kind != "cli_command" for r in health_refs)
