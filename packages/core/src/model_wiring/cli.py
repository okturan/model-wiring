"""Unix-oriented command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .access import provider_access
from .api import ProviderService, api_server, issue_token
from .auth import AuthBroker, KeyringSecretStore, MemorySecretStore
from .broker import CredentialBroker
from .catalog import Catalog, ModelsDevSource, load_overlay
from .contracts import CredentialProfile, SelectionIntent
from .discovery import discover_environment_profiles
from .errors import AmbiguousSelection, CredentialError, ModelProviderError
from .gateway import InferenceGateway, gateway_routes, gateway_server
from .login import DELEGATED_CANDIDATES, LoginBroker, LoginResult
from .pool import POOL_STRATEGIES, CredentialPool
from .popularity import provider_popularity_key
from .probe import PROBE_DRIVERS, Prober, gateway_probe_drivers
from .profiles import ProfileRegistry
from .selection import Selector


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="model-wiring")
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

    access = commands.add_parser(
        "access", help="inspect how providers can be connected"
    )
    access_commands = access.add_subparsers(dest="access_command", required=True)
    access_list = access_commands.add_parser(
        "list", help="every provider and its access routes"
    )
    access_list.add_argument(
        "--state",
        choices=("configured", "connectable", "unavailable"),
        help="only show providers in this credential state",
    )
    _json_flag(access_list)
    access_show = access_commands.add_parser(
        "show", help="how one provider can be connected"
    )
    access_show.add_argument("provider")
    _json_flag(access_show)
    access_status = access_commands.add_parser(
        "status", help="credentials that are already configured"
    )
    _json_flag(access_status)
    access_probe = access_commands.add_parser(
        "probe", help="check whether stored credentials still work"
    )
    access_probe.add_argument(
        "profile", nargs="?", help="probe one profile instead of every profile"
    )
    access_probe.add_argument(
        "--no-record",
        action="store_true",
        help="do not write the outcome back to the profile",
    )
    access_probe.add_argument("--store", help="secret store name (default: keyring)")
    _json_flag(access_probe)

    login = commands.add_parser("login", help="connect a provider")
    login.add_argument("provider")
    login.add_argument(
        "--route", help="access route id, when a provider offers several"
    )
    login.add_argument(
        "--value",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="supply a credential non-interactively; repeat for a bundle",
    )
    login.add_argument(
        "--choice", help="identifier of an existing sign-in to delegate to"
    )
    login.add_argument(
        "--from-environment",
        action="store_true",
        help="promote the values already exported in this shell into storage",
    )
    login.add_argument(
        "--describe",
        action="store_true",
        help="print what this login would ask for, and stop",
    )
    login.add_argument("--store", help="secret store name (default: keyring)")
    _json_flag(login)

    logout = commands.add_parser("logout", help="remove a stored credential profile")
    logout.add_argument("profile")
    logout.add_argument("--store", help="secret store name (default: keyring)")
    _json_flag(logout)

    server = commands.add_parser(
        "serve",
        help="serve the secret-free loopback JSON API; prints the bearer token "
        "every request must carry",
    )
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8765)
    server.add_argument(
        "--allow-origin",
        help="exact browser origin allowed to call the loopback API",
    )
    _json_flag(server)

    gateway = commands.add_parser(
        "gateway",
        help="serve provider-shaped routes that inject credentials at egress",
    )
    gateway.add_argument("--host", default="127.0.0.1")
    gateway.add_argument("--port", type=int, default=8766)
    gateway.add_argument(
        "--provider",
        action="append",
        default=[],
        help="expose only this provider; repeat for several",
    )
    gateway.add_argument("--store", help="secret store name (default: keyring)")
    _json_flag(gateway)
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
        if args.command == "access":
            return _access_command(args, catalog, profiles)
        if args.command in {"login", "logout"}:
            return _login_command(args, catalog, profiles)
        if args.command == "serve":
            return _serve_command(args, catalog, profiles)
        if args.command == "gateway":
            return _gateway_command(args, catalog, profiles)
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
                catalog.snapshot.providers.values(), key=provider_popularity_key
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


def _access_command(
    args: argparse.Namespace, catalog: Catalog, profiles: ProfileRegistry
) -> int:
    stored = tuple(profiles.list())
    if args.access_command == "probe":
        store_name = getattr(args, "store", None) or "keyring"
        auth = AuthBroker(profiles, stores=_secret_stores(store_name))
        # The gateway is the egress path, not a listener: nothing is bound here.
        # A provider whose route declares a verification call is checked with a
        # real authenticated request; every other provider stays unverified.
        gateway = InferenceGateway(
            CredentialBroker(auth, catalog=catalog), gateway_routes(catalog)
        )
        prober = Prober(
            auth, drivers={**PROBE_DRIVERS, **gateway_probe_drivers(gateway)}
        )
        record = not args.no_record
        results = (
            [prober.probe(args.profile, record=record)]
            if args.profile
            else list(prober.probe_all(record=record))
        )
        _emit(
            {"items": [result.to_dict() for result in results]},
            json_output=args.json,
        )
        return 0 if all(item.state != "unavailable" for item in results) else 1
    if args.access_command == "status":
        _emit(
            {"items": [profile.to_dict() for profile in stored]},
            json_output=args.json,
        )
        return 0
    if args.access_command == "show":
        provider = catalog.provider(args.provider)
        access = provider_access(provider, _profiles_for(stored, provider.id))
        _emit(access.to_dict(), json_output=args.json)
        return 0
    items = []
    for provider in sorted(
        catalog.snapshot.providers.values(), key=provider_popularity_key
    ):
        access = provider_access(provider, _profiles_for(stored, provider.id))
        if args.state and access.credential_state != args.state:
            continue
        items.append(
            {
                "id": provider.id,
                "name": provider.name,
                "credential_state": access.credential_state,
                "required_variables": list(access.required_variables),
                "routes": [route.to_dict() for route in access.routes],
            }
        )
    _emit({"items": items}, json_output=args.json)
    return 0


def _login_command(
    args: argparse.Namespace, catalog: Catalog, profiles: ProfileRegistry
) -> int:
    store_name = args.store or "keyring"
    broker = LoginBroker(
        catalog,
        profiles=profiles,
        stores=_secret_stores(store_name),
        default_store=store_name,
        delegated_candidates=DELEGATED_CANDIDATES,
    )
    if args.command == "logout":
        removed = broker.logout(args.profile)
        _emit(
            {"profile": args.profile, "removed": removed},
            json_output=args.json,
        )
        return 0 if removed else 1
    if args.from_environment:
        result = broker.promote_environment(args.provider, store=store_name)
        _emit(result.to_dict(), json_output=args.json)
        return 0
    session = broker.begin(args.provider, route_id=args.route, store=store_name)
    if args.describe:
        _emit(session.to_dict(), json_output=args.json)
        return 0
    response = _login_response(args, session)
    if response is None:
        # Nothing to submit yet: describe the prompt so a caller can collect it.
        _emit(session.to_dict(), json_output=args.json)
        return 0
    outcome = broker.advance(session, response, store=store_name)
    if isinstance(outcome, LoginResult):
        _emit(outcome.to_dict(), json_output=args.json)
        return 0
    _emit(outcome.to_dict(), json_output=args.json)
    return 0


def _login_response(args: argparse.Namespace, session: Any) -> dict[str, Any] | None:
    if args.choice:
        return {"choice": args.choice}
    if args.value:
        return dict(_parse_assignment(item) for item in args.value)
    return None


def _parse_assignment(value: str) -> tuple[str, str]:
    name, separator, secret = value.partition("=")
    if not separator or not name.strip():
        raise ValueError(f"expected NAME=VALUE, got {value!r}")
    return name.strip(), secret


def _serve_command(
    args: argparse.Namespace, catalog: Catalog, profiles: ProfileRegistry
) -> int:
    """Bind, print the token once, then serve until interrupted."""

    token = issue_token()
    server = api_server(
        ProviderService(catalog, profiles),
        token=token,
        host=args.host,
        port=args.port,
        allowed_origin=args.allow_origin,
    )
    try:
        _emit(
            {"base_url": _base_url(args.host, server.server_address), "token": token},
            json_output=args.json,
        )
        server.serve_forever()
    finally:
        server.server_close()
    return 0


def _gateway_command(
    args: argparse.Namespace, catalog: Catalog, profiles: ProfileRegistry
) -> int:
    """Serve the routes the catalogue declares, with credentials injected."""

    store_name = args.store or "keyring"
    broker = CredentialBroker(
        AuthBroker(profiles, stores=_secret_stores(store_name)), catalog=catalog
    )
    gateway = InferenceGateway(
        broker, gateway_routes(catalog, provider_ids=args.provider or None)
    )
    server = gateway_server(gateway, host=args.host, port=args.port)
    try:
        base_url = _base_url(args.host, server.server_address)
        payload = {
            "base_url": base_url,
            "token": gateway.token,
            "routes": [
                {
                    "provider_id": route.provider_id,
                    "route_id": route.id,
                    "url": f"{base_url}{route.prefix}",
                    "methods": list(route.methods),
                    "paths": list(route.paths),
                }
                for route in gateway.routes
            ],
        }
        _emit(payload, json_output=args.json)
        if not args.json:
            for route in payload["routes"]:
                print(
                    f"route {route['provider_id']} {route['route_id']}: {route['url']}"
                )
        server.serve_forever()
    finally:
        server.server_close()
    return 0


def _base_url(host: str, address: tuple[Any, ...]) -> str:
    """The address a client should point at, with the port actually bound."""

    port = int(address[1])
    return f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"


def _profiles_for(
    profiles: Sequence[CredentialProfile], provider_id: str
) -> tuple[CredentialProfile, ...]:
    return tuple(
        profile
        for profile in profiles
        if profile.provider_id == provider_id and profile.enabled
    )


def _secret_stores(name: str) -> dict[str, Any]:
    stores: dict[str, Any] = {"memory": MemorySecretStore()}
    if name == "keyring":
        try:
            stores["keyring"] = KeyringSecretStore()
        except CredentialError:
            # Reported only if the user actually asks to store something.
            pass
    return stores
