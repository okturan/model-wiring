"""Catalog ingestion, normalization, overlays, caching, and search."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from .contracts import (
    AuthMethod,
    CatalogSnapshot,
    ModelSpec,
    ModelVariant,
    ProviderSpec,
    utc_now,
)
from .errors import CatalogError, CatalogUnavailable, SelectionNotFound

MODELS_DEV_URL = "https://models.dev/api.json"


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_digest(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def default_cache_path() -> Path:
    root = os.environ.get("XDG_CACHE_HOME")
    base = Path(root).expanduser() if root else Path.home() / ".cache"
    return base / "model-provider-kit" / "models-dev.json"


@dataclass(frozen=True)
class SearchHit:
    score: float
    provider: ProviderSpec
    model: ModelSpec

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "provider": self.provider.to_dict(include_models=False),
            "model": self.model.to_dict(),
        }


class ModelsDevSource:
    """Fetches Models.dev and keeps an atomic local cache.

    A transport can be injected in tests. It receives `(url, timeout)` and must
    return bytes.
    """

    def __init__(
        self,
        *,
        url: str = MODELS_DEV_URL,
        cache_path: Path | None = None,
        timeout: float = 20.0,
        transport: Callable[[str, float], bytes] | None = None,
    ) -> None:
        self.url = url
        self.cache_path = cache_path or default_cache_path()
        self.timeout = timeout
        self.transport = transport or self._download

    @staticmethod
    def _download(url: str, timeout: float) -> bytes:
        request = Request(url, headers={"User-Agent": "model-provider-kit/0.1"})
        with urlopen(request, timeout=timeout) as response:
            return response.read()

    def sync(self) -> tuple[Mapping[str, Any], str]:
        try:
            payload = self.transport(self.url, self.timeout)
            raw = json.loads(payload)
        except Exception as exc:  # urllib and JSON failures share one public boundary
            raise CatalogUnavailable(f"could not fetch {self.url}: {exc}") from exc
        if not isinstance(raw, dict) or not raw:
            raise CatalogError("Models.dev returned an empty or non-object catalog")

        fetched_at = utc_now()
        envelope = {
            "source": self.url,
            "fetched_at": fetched_at,
            "raw_digest": content_digest(raw),
            "catalog": raw,
        }
        self._write_cache(envelope)
        return raw, fetched_at

    def load_cache(
        self, *, max_age: timedelta | None = None
    ) -> tuple[Mapping[str, Any], str]:
        try:
            envelope = json.loads(self.cache_path.read_text(encoding="utf-8"))
            raw = envelope["catalog"]
            fetched_at = str(envelope["fetched_at"])
            expected = str(envelope["raw_digest"])
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise CatalogUnavailable(
                f"no valid catalog cache at {self.cache_path}: {exc}"
            ) from exc
        if content_digest(raw) != expected:
            raise CatalogError(f"catalog cache digest mismatch at {self.cache_path}")
        if max_age is not None:
            fetched = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - fetched > max_age:
                raise CatalogUnavailable(f"catalog cache at {self.cache_path} is stale")
        return raw, fetched_at

    def _write_cache(self, envelope: Mapping[str, Any]) -> None:
        self.cache_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self.cache_path.parent, 0o700)
        except OSError:
            pass
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".models-dev-", suffix=".json", dir=self.cache_path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(envelope, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, self.cache_path)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


class Catalog:
    """Normalized immutable provider/model catalog."""

    def __init__(self, snapshot: CatalogSnapshot) -> None:
        self.snapshot = snapshot

    @classmethod
    def from_models_dev(
        cls,
        raw: Mapping[str, Any],
        *,
        fetched_at: str | None = None,
        source: str = MODELS_DEV_URL,
        overlays: Iterable[Mapping[str, Any]] = (),
        include_default_overlays: bool = True,
    ) -> Catalog:
        providers: dict[str, ProviderSpec] = {}
        for provider_key, provider_raw in raw.items():
            if not isinstance(provider_raw, Mapping):
                continue
            provider_id = str(provider_raw.get("id") or provider_key)
            models: dict[str, ModelSpec] = {}
            for model_key, model_raw in (provider_raw.get("models") or {}).items():
                if isinstance(model_raw, Mapping):
                    model = _parse_model(provider_id, str(model_key), model_raw)
                    models[model.id] = model
            env = tuple(str(item) for item in provider_raw.get("env", ()) if item)
            auth_methods: tuple[AuthMethod, ...] = ()
            if env:
                methods = [
                    AuthMethod(
                        kind="credential_bundle", billing_kinds=("api",), env=env
                    )
                ]
                if len(env) == 1:
                    methods.append(
                        AuthMethod(kind="api_key", billing_kinds=("api",), env=env)
                    )
                auth_methods = tuple(methods)
            known = {"id", "name", "npm", "env", "api", "doc", "models"}
            providers[provider_id] = ProviderSpec(
                id=provider_id,
                name=str(provider_raw.get("name") or provider_id),
                adapter=_optional_string(provider_raw.get("npm")),
                api_url=_optional_string(provider_raw.get("api")),
                doc_url=_optional_string(provider_raw.get("doc")),
                env=env,
                auth_methods=auth_methods,
                models=models,
                metadata={
                    key: value
                    for key, value in provider_raw.items()
                    if key not in known
                },
            )

        aliases: dict[str, str] = {}
        roles: dict[str, str] = {}
        overlay_values = list(overlays)
        if include_default_overlays:
            overlay_values.insert(0, _load_default_overlay())
        for overlay in overlay_values:
            providers, overlay_aliases, overlay_roles = _apply_overlay(
                providers, overlay
            )
            aliases.update(overlay_aliases)
            roles.update(overlay_roles)

        public = {
            "providers": {
                key: provider.to_dict() for key, provider in sorted(providers.items())
            },
            "aliases": dict(sorted(aliases.items())),
            "roles": dict(sorted(roles.items())),
        }
        snapshot = CatalogSnapshot(
            providers=providers,
            source=source,
            fetched_at=fetched_at or utc_now(),
            digest=content_digest(public),
            aliases=aliases,
            roles=roles,
        )
        return cls(snapshot)

    @classmethod
    def from_cache_or_sync(
        cls,
        *,
        source: ModelsDevSource | None = None,
        overlays: Iterable[Mapping[str, Any]] = (),
        max_age: timedelta = timedelta(hours=24),
    ) -> Catalog:
        catalog_source = source or ModelsDevSource()
        try:
            raw, fetched_at = catalog_source.load_cache(max_age=max_age)
        except CatalogUnavailable:
            try:
                raw, fetched_at = catalog_source.sync()
            except CatalogUnavailable:
                # A stale verified cache is better than no discovery while its
                # timestamp remains explicit in the resulting plan.
                raw, fetched_at = catalog_source.load_cache(max_age=None)
        return cls.from_models_dev(
            raw,
            fetched_at=fetched_at,
            source=catalog_source.url,
            overlays=overlays,
        )

    def provider(self, provider_id: str) -> ProviderSpec:
        key = _normalize_identifier(provider_id)
        resolved = self.snapshot.aliases.get(key, key)
        if "/" in resolved:
            resolved = resolved.split("/", 1)[0]
        try:
            return self.snapshot.providers[resolved]
        except KeyError as exc:
            raise SelectionNotFound(f"unknown provider: {provider_id}") from exc

    def model(
        self, qualified_or_model_id: str, *, provider: str | None = None
    ) -> ModelSpec:
        alias_key = _normalize_identifier(qualified_or_model_id)
        requested = self.snapshot.aliases.get(alias_key, qualified_or_model_id)
        if "/" in requested:
            provider_id, model_id = requested.split("/", 1)
            candidate_provider = self.provider(provider_id)
            try:
                return candidate_provider.models[model_id]
            except KeyError as exc:
                raise SelectionNotFound(f"unknown model: {requested}") from exc
        if provider:
            candidate_provider = self.provider(provider)
            try:
                return candidate_provider.models[requested]
            except KeyError as exc:
                raise SelectionNotFound(
                    f"unknown model {requested!r} for provider {candidate_provider.id!r}"
                ) from exc
        matches = [
            model
            for candidate_provider in self.snapshot.providers.values()
            for model in candidate_provider.models.values()
            if model.id == requested
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise SelectionNotFound(f"unknown model: {requested}")
        from .errors import AmbiguousSelection

        candidates = tuple(sorted(model.qualified_id for model in matches))
        raise AmbiguousSelection(
            f"model ID {requested!r} exists under {len(matches)} providers; qualify it",
            candidates,
        )

    def search(
        self,
        query: str,
        *,
        provider: str | None = None,
        limit: int = 20,
    ) -> tuple[SearchHit, ...]:
        needle = _normalize(query)
        if not needle:
            return ()
        provider_id = self.provider(provider).id if provider else None
        alias_target = self.snapshot.aliases.get(_normalize_identifier(query))
        hits: list[SearchHit] = []
        for candidate_provider in self.snapshot.providers.values():
            if provider_id and candidate_provider.id != provider_id:
                continue
            for model in candidate_provider.models.values():
                qualified = _normalize(model.qualified_id)
                name = _normalize(model.name)
                family = _normalize(model.family or "")
                score = max(
                    1.0 if model.qualified_id == alias_target else 0.0,
                    _match_score(needle, qualified),
                    _match_score(needle, name) * 0.97,
                    _match_score(needle, _normalize(model.id)) * 0.99,
                    _match_score(needle, family) * 0.85,
                )
                if score >= 0.44:
                    hits.append(
                        SearchHit(score=score, provider=candidate_provider, model=model)
                    )
        hits.sort(key=lambda item: (-item.score, item.model.qualified_id))
        return tuple(hits[: max(0, limit)])


def load_overlay(path: Path) -> Mapping[str, Any]:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"invalid overlay {path}: {exc}") from exc
    if not isinstance(result, Mapping):
        raise CatalogError(f"overlay {path} must contain a JSON object")
    return result


def _load_default_overlay() -> Mapping[str, Any]:
    resource = files("model_provider").joinpath("data/default-overlays.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def _parse_model(provider_id: str, model_key: str, raw: Mapping[str, Any]) -> ModelSpec:
    model_id = str(raw.get("id") or model_key)
    capabilities = {
        key: bool(raw.get(key))
        for key in (
            "attachment",
            "reasoning",
            "tool_call",
            "structured_output",
            "temperature",
            "open_weights",
        )
        if key in raw
    }
    reasoning_values: list[str] = []
    for option in raw.get("reasoning_options") or ():
        if not isinstance(option, Mapping):
            continue
        values = option.get("values")
        if isinstance(values, list):
            reasoning_values.extend(str(value) for value in values)
    modalities = {
        str(key): tuple(str(item) for item in value)
        for key, value in (raw.get("modalities") or {}).items()
        if isinstance(value, list)
    }
    limits = {
        str(key): value
        for key, value in (raw.get("limit") or {}).items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    variants: dict[str, ModelVariant] = {}
    modes = (raw.get("experimental") or {}).get("modes") or {}
    for variant_key, variant_raw in modes.items():
        if not isinstance(variant_raw, Mapping):
            continue
        known_variant = {"cost", "provider"}
        variants[str(variant_key)] = ModelVariant(
            id=str(variant_key),
            cost=dict(variant_raw.get("cost") or {}),
            provider_options=dict(variant_raw.get("provider") or {}),
            metadata={
                key: value
                for key, value in variant_raw.items()
                if key not in known_variant
            },
        )
    known = {
        "id",
        "name",
        "description",
        "family",
        "attachment",
        "reasoning",
        "reasoning_options",
        "tool_call",
        "structured_output",
        "temperature",
        "open_weights",
        "modalities",
        "limit",
        "cost",
        "experimental",
        "status",
        "release_date",
        "last_updated",
    }
    return ModelSpec(
        id=model_id,
        provider_id=provider_id,
        name=str(raw.get("name") or model_id),
        family=_optional_string(raw.get("family")),
        description=_optional_string(raw.get("description")),
        capabilities=capabilities,
        reasoning_options=tuple(dict.fromkeys(reasoning_values)),
        modalities=modalities,
        limits=limits,
        cost=dict(raw.get("cost") or {}),
        variants=variants,
        status=_optional_string(raw.get("status")),
        release_date=_optional_string(raw.get("release_date")),
        last_updated=_optional_string(raw.get("last_updated")),
        metadata={key: value for key, value in raw.items() if key not in known},
    )


def _apply_overlay(
    original: Mapping[str, ProviderSpec], overlay: Mapping[str, Any]
) -> tuple[dict[str, ProviderSpec], dict[str, str], dict[str, str]]:
    providers = dict(original)
    for provider_id, raw in (overlay.get("providers") or {}).items():
        if not isinstance(raw, Mapping):
            raise CatalogError(f"overlay provider {provider_id!r} must be an object")
        provider_id = str(provider_id)
        base_id = raw.get("extends") or raw.get("models_from")
        base = providers.get(str(base_id)) if base_id else providers.get(provider_id)
        if base_id and base is None:
            # Versioned delegated overlays may reference a provider absent from a
            # small/offline fixture. Skipping is safer than publishing an empty
            # route that looks usable.
            continue
        include_models = raw.get("include_models")
        allowed = (
            {str(item) for item in include_models}
            if include_models is not None
            else None
        )
        models = {
            model_id: replace(model, provider_id=provider_id)
            for model_id, model in (base.models.items() if base else ())
            if allowed is None or model_id in allowed
        }
        for model_id, model_raw in (raw.get("models") or {}).items():
            if not isinstance(model_raw, Mapping):
                raise CatalogError(
                    f"overlay model {provider_id}/{model_id} must be an object"
                )
            model = _parse_model(provider_id, str(model_id), model_raw)
            models[model.id] = model
        for model_id, patch in (raw.get("model_patches") or {}).items():
            if model_id not in models or not isinstance(patch, Mapping):
                continue
            model = models[model_id]
            metadata = dict(model.metadata)
            metadata.update(patch.get("metadata") or {})
            models[model_id] = replace(
                model,
                name=str(patch.get("name") or model.name),
                reasoning_options=tuple(
                    str(item)
                    for item in patch.get("reasoning_options", model.reasoning_options)
                ),
                metadata=metadata,
            )
        env = tuple(str(item) for item in raw.get("env", base.env if base else ()))
        auth_methods_raw = raw.get("auth_methods")
        if auth_methods_raw is None:
            auth_methods = base.auth_methods if base else ()
        else:
            auth_methods = tuple(_parse_auth_method(item) for item in auth_methods_raw)
        known = {
            "extends",
            "models_from",
            "include_models",
            "name",
            "adapter",
            "api_url",
            "doc_url",
            "env",
            "auth_methods",
            "models",
            "model_patches",
            "metadata",
        }
        metadata = dict(base.metadata) if base else {}
        metadata.update(raw.get("metadata") or {})
        metadata.update({key: value for key, value in raw.items() if key not in known})
        providers[provider_id] = ProviderSpec(
            id=provider_id,
            name=str(raw.get("name") or (base.name if base else provider_id)),
            adapter=_optional_string(
                raw.get("adapter", base.adapter if base else None)
            ),
            api_url=_optional_string(
                raw.get("api_url", base.api_url if base else None)
            ),
            doc_url=_optional_string(
                raw.get("doc_url", base.doc_url if base else None)
            ),
            env=env,
            auth_methods=auth_methods,
            models=models,
            metadata=metadata,
        )
    aliases = {
        _normalize_identifier(str(key)): str(value)
        for key, value in (overlay.get("aliases") or {}).items()
    }
    roles = {
        str(key): str(value) for key, value in (overlay.get("roles") or {}).items()
    }
    return providers, aliases, roles


def _parse_auth_method(raw: Any) -> AuthMethod:
    if not isinstance(raw, Mapping):
        raise CatalogError("auth method must be an object")
    known = {"kind", "billing_kinds", "label", "env", "metadata"}
    metadata = dict(raw.get("metadata") or {})
    metadata.update({key: value for key, value in raw.items() if key not in known})
    return AuthMethod(
        kind=str(raw["kind"]),
        billing_kinds=tuple(
            str(item) for item in raw.get("billing_kinds", ("unknown",))
        ),
        label=_optional_string(raw.get("label")),
        env=tuple(str(item) for item in raw.get("env", ())),
        metadata=metadata,
    )


def _optional_string(value: Any) -> str | None:
    return str(value) if value is not None else None


def _normalize(value: str) -> str:
    return " ".join(value.lower().replace("_", " ").replace("-", " ").split())


def _normalize_identifier(value: str) -> str:
    return value.strip().lower()


def _match_score(needle: str, value: str) -> float:
    if not value:
        return 0.0
    if needle == value:
        return 1.0
    if value.startswith(needle):
        return 0.94 + min(len(needle) / max(len(value), 1), 1.0) * 0.04
    if needle in value:
        return 0.78 + min(len(needle) / max(len(value), 1), 1.0) * 0.16
    return SequenceMatcher(None, needle, value).ratio() * 0.75
