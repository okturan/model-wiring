"""Unix-oriented command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .api import ProviderService, serve
from .catalog import Catalog, ModelsDevSource, load_overlay
from .contracts import CredentialProfile, SelectionIntent
from .discovery import discover_environment_profiles
from .errors import AmbiguousSelection, ModelProviderError
from .pool import POOL_STRATEGIES, CredentialPool
from .profiles import ProfileRegistry
from .selection import Selector


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="model-provider")
    parser.add_argument("--cache", type=Path, help="Models.dev cache path")
    parser.add_argument("--profiles", type=Path, help="credential-profile SQLite path")
    parser.add_argument(
        "--overlay",
        action="append",
        default=[],
        type=Path,
        help="non-secret JSON overlay",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    catalog = commands.add_parser(
        "catalog", help="catalog discovery and synchronization"
    )
    catalog_commands = catalog.add_subparsers(dest="catalog_command", required=True)
    sync = catalog_commands.add_parser("sync")
    _json_flag(sync)
    status = catalog_commands.add_parser("status")
    _json_flag(status)
    search = catalog_commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--provider")
    search.add_argument("--limit", type=int, default=20)
    _json_flag(search)
    providers = catalog_commands.add_parser("providers")
    _json_flag(providers)

    select = commands.add_parser("select", help="resolve a secret-free selection plan")
    select.add_argument("--model")
    select.add_argument("--provider")
    select.add_argument("--query")
    select.add_argument("--role")
    select.add_argument("--variant")
    select.add_argument("--effort")
    select.add_argument("--tier")
    select.add_argument("--credential-profile")
    select.add_argument("--billing-kind")
    select.add_argument("--require", action="append", default=[])
    select.add_argument("--input", action="append", default=[])
    select.add_argument("--output", action="append", default=[])
    select.add_argument(
        "--minimum-limit",
        action="append",
        default=[],
        metavar="NAME=NUMBER",
    )
    _json_flag(select)

    profile = commands.add_parser(
        "profile", help="non-secret credential profile metadata"
    )
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    profile_list = profile_commands.add_parser("list")
    profile_list.add_argument("--provider")
    _json_flag(profile_list)
    profile_add = profile_commands.add_parser("add")
    profile_add.add_argument("--id", required=True)
    profile_add.add_argument("--provider", required=True)
    profile_add.add_argument(
        "--auth-kind",
        required=True,
        choices=(
            "api_key",
            "bearer",
            "credential_bundle",
            "oauth",
            "delegated",
            "anonymous",
        ),
    )
    profile_add.add_argument(
        "--billing-kind",
        required=True,
        choices=("api", "subscription", "marketplace", "local", "unknown"),
    )
    profile_add.add_argument("--secret-store")
    profile_add.add_argument("--secret-ref")
    profile_add.add_argument("--account-label")
    profile_add.add_argument("--priority", type=int, default=100)
    profile_add.add_argument("--scope", action="append", default=[])
    profile_add.add_argument("--delegate")
    _json_flag(profile_add)
    profile_remove = profile_commands.add_parser("remove")
    profile_remove.add_argument("profile_id")
    _json_flag(profile_remove)
    profile_claim = profile_commands.add_parser(
        "claim", help="atomically claim from a pool"
    )
    profile_claim.add_argument("--pool", required=True)
    profile_claim.add_argument("--profile", action="append", required=True)
    profile_claim.add_argument(
        "--strategy", choices=tuple(sorted(POOL_STRATEGIES)), default="fill_first"
    )
    _json_flag(profile_claim)
    profile_discover = profile_commands.add_parser(
        "discover-env", help="discover complete provider environment bundles"
    )
    profile_discover.add_argument("--save", action="store_true")
    _json_flag(profile_discover)

    server = commands.add_parser(
        "serve", help="serve the secret-free loopback JSON API"
    )
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8765)
    server.add_argument(
        "--allow-origin",
        help="exact browser origin allowed to call the loopback API",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "profile" and args.profile_command != "discover-env":
            return _profile_command(args)
        overlays = [load_overlay(path) for path in args.overlay]
        source = (
            ModelsDevSource(cache_path=args.cache) if args.cache else ModelsDevSource()
        )
        if args.command == "catalog" and args.catalog_command == "sync":
            raw, fetched_at = source.sync()
            catalog = Catalog.from_models_dev(
                raw, fetched_at=fetched_at, overlays=overlays
            )
        else:
            catalog = Catalog.from_cache_or_sync(source=source, overlays=overlays)
        if args.command == "catalog":
            return _catalog_command(args, catalog)
        if args.command == "profile":
            return _profile_discover_command(args, catalog)
        profiles = (
            ProfileRegistry(args.profiles) if args.profiles else ProfileRegistry()
        )
        if args.command == "select":
            intent = SelectionIntent(
                model=args.model,
                provider=args.provider,
                query=args.query,
                role=args.role,
                variant=args.variant,
                effort=args.effort,
                tier=args.tier,
                credential_profile=args.credential_profile,
                billing_kind=args.billing_kind,
                required_capabilities=tuple(args.require),
                input_modalities=tuple(args.input),
                output_modalities=tuple(args.output),
                minimum_limits=_parse_limits(args.minimum_limit),
            )
            plan = Selector(catalog, profiles).select(intent)
            _emit(plan.to_dict(), json_output=args.json)
            return 0
        if args.command == "serve":
            serve(
                ProviderService(catalog, profiles),
                host=args.host,
                port=args.port,
                allowed_origin=args.allow_origin,
            )
            return 0
        parser.error("unhandled command")
    except AmbiguousSelection as exc:
        _emit_error(exc, extra={"candidates": list(exc.candidates)})
        return 1
    except (ModelProviderError, ValueError) as exc:
        _emit_error(exc)
        return 1
    except KeyboardInterrupt:
        return 130
    return 2


def _catalog_command(args: argparse.Namespace, catalog: Catalog) -> int:
    if args.catalog_command in {"sync", "status"}:
        _emit(
            {
                "source": catalog.snapshot.source,
                "fetched_at": catalog.snapshot.fetched_at,
                "digest": catalog.snapshot.digest,
                "provider_count": len(catalog.snapshot.providers),
                "model_count": catalog.snapshot.model_count,
            },
            json_output=args.json,
        )
        return 0
    if args.catalog_command == "search":
        hits = catalog.search(args.query, provider=args.provider, limit=args.limit)
        _emit({"items": [hit.to_dict() for hit in hits]}, json_output=args.json)
        return 0
    if args.catalog_command == "providers":
        items = [
            provider.to_dict(include_models=False)
            for provider in sorted(
                catalog.snapshot.providers.values(), key=lambda item: item.id
            )
        ]
        _emit({"items": items}, json_output=args.json)
        return 0
    raise ValueError(f"unknown catalog command: {args.catalog_command}")


def _profile_command(args: argparse.Namespace) -> int:
    registry = ProfileRegistry(args.profiles) if args.profiles else ProfileRegistry()
    if args.profile_command == "list":
        profiles = registry.list(provider_id=args.provider)
        _emit({"items": [item.to_dict() for item in profiles]}, json_output=args.json)
        return 0
    if args.profile_command == "add":
        metadata = {"delegate": args.delegate} if args.delegate else {}
        profile = CredentialProfile(
            id=args.id,
            provider_id=args.provider,
            auth_kind=args.auth_kind,
            billing_kind=args.billing_kind,
            secret_ref=args.secret_ref,
            secret_store=args.secret_store,
            account_label=args.account_label,
            priority=args.priority,
            scopes=tuple(args.scope),
            metadata=metadata,
        )
        registry.upsert(profile)
        _emit(profile.to_dict(), json_output=args.json)
        return 0
    if args.profile_command == "remove":
        removed = registry.delete(args.profile_id)
        _emit(
            {"profile_id": args.profile_id, "removed": removed}, json_output=args.json
        )
        return 0
    if args.profile_command == "claim":
        pool = CredentialPool(args.pool, tuple(args.profile), args.strategy)
        claimed = pool.claim(registry)
        _emit(
            {
                "pool": pool.id,
                "strategy": pool.strategy,
                "profile": claimed.to_dict(),
                "usage": registry.usage(claimed.id),
            },
            json_output=args.json,
        )
        return 0
    raise ValueError(f"unknown profile command: {args.profile_command}")


def _profile_discover_command(args: argparse.Namespace, catalog: Catalog) -> int:
    discovered = discover_environment_profiles(catalog)
    if args.save:
        registry = (
            ProfileRegistry(args.profiles) if args.profiles else ProfileRegistry()
        )
        for profile in discovered:
            registry.upsert(profile)
    _emit(
        {
            "items": [profile.to_dict() for profile in discovered],
            "saved": bool(args.save),
        },
        json_output=args.json,
    )
    return 0


def _parse_limits(values: Sequence[str]) -> dict[str, int | float]:
    result: dict[str, int | float] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"minimum limit must be NAME=NUMBER: {value}")
        name, raw = value.split("=", 1)
        number = float(raw)
        result[name] = int(number) if number.is_integer() else number
    return result


def _json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="emit stable JSON")


def _emit(value: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(value, sort_keys=True, separators=(",", ":")))
        return
    if "items" in value:
        for item in value["items"]:
            if "model" in item:
                print(item["model"]["qualified_id"])
            elif "id" in item:
                print(item["id"])
        return
    for key, item in value.items():
        if isinstance(item, (str, int, float, bool)) or item is None:
            print(f"{key}: {item}")


def _emit_error(exc: Exception, *, extra: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {
        "error": {"type": exc.__class__.__name__, "message": str(exc)}
    }
    if extra:
        payload["error"].update(extra)
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")), file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
