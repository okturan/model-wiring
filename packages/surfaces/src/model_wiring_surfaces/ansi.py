"""Pure ANSI/Unicode render primitives with control-character sanitization."""

from __future__ import annotations

import re
import textwrap
from collections.abc import Iterable
from dataclasses import dataclass

from .controller import CandidateView, ProviderView, SelectionView

CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
ESCAPE = re.compile(r"(?:\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])|\x9b[0-?]*[ -/]*[@-~])")
WIDE_LAYOUT = 96


@dataclass(frozen=True)
class AnsiTheme:
    reset: str = "\x1b[0m"
    bold: str = "\x1b[1m"
    dim: str = "\x1b[2m"
    cyan: str = "\x1b[38;5;81m"
    blue: str = "\x1b[38;5;111m"
    green: str = "\x1b[38;5;114m"
    yellow: str = "\x1b[38;5;221m"
    red: str = "\x1b[38;5;203m"
    selected: str = "\x1b[48;5;24m\x1b[38;5;231m"


def safe_text(value: object) -> str:
    text = ESCAPE.sub("", str(value))
    return CONTROL.sub("", text).replace("\r", " ").replace("\n", " ")


def render_pipeline(
    view: SelectionView, *, color: bool = True, compact: bool = False
) -> str:
    profile_ready = not view.auth_required or view.credential_profile is not None
    stages = (
        ("1", "PROVIDER", view.provider_chosen, view.focus == "providers"),
        (
            "2",
            "MODEL",
            view.selected_model is not None,
            view.focus == "models" and view.selected_model is None,
        ),
        ("3", "MODE", view.selected_model is not None, False),
        (
            "4",
            "ACCESS",
            view.selected_model is not None and profile_ready,
            False,
        ),
        ("5", "READY", view.route_ready, False),
    )
    pieces: list[str] = []
    theme = AnsiTheme()
    for number, label, complete, active in stages:
        marker = "✓" if complete else ("●" if active else "○")
        body = f"{marker} {label}" if compact else f"{marker} {number} {label}"
        if color:
            paint = theme.green if complete else (theme.cyan if active else theme.dim)
            body = f"{paint}{body}{theme.reset}"
        pieces.append(body)
    if compact:
        arrow = f" {theme.dim}›{theme.reset} " if color else " > "
    else:
        arrow = f" {theme.dim}──▶{theme.reset} " if color else " --> "
    return arrow.join(pieces)


def render_screen(
    view: SelectionView,
    *,
    width: int = 100,
    height: int = 30,
    color: bool = True,
) -> str:
    width = max(52, width)
    height = max(16, height)
    theme = AnsiTheme()
    title = _paint("MODEL ROUTE", theme.bold + theme.cyan, color, theme)
    summary = _catalog_summary(view)
    lines = [
        title
        + "  "
        + _paint(_truncate(summary, max(18, width - 15)), theme.dim, color, theme),
        render_pipeline(view, color=color, compact=width < WIDE_LAYOUT),
        "",
        _paint(_search_line(view), theme.blue, color, theme),
        "",
    ]

    reserved = 2 + (1 if view.error else 0)
    content_height = max(5, height - len(lines) - reserved)
    if width >= WIDE_LAYOUT:
        lines.extend(
            _wide_content(
                view,
                width=width,
                height=content_height,
                color=color,
                theme=theme,
            )
        )
    elif view.focus == "providers":
        lines.append(_paint("CHOOSE PROVIDER", theme.cyan, color, theme))
        lines.extend(
            _provider_lines(
                view,
                width=width,
                height=max(4, content_height - 1),
                color=color,
                theme=theme,
            )
        )
    else:
        lines.append(
            _paint(
                f"MODELS · {safe_text(view.active_provider_name or view.active_provider or '')}",
                theme.cyan,
                color,
                theme,
            )
        )
        lines.extend(
            _model_lines(
                view,
                width=width,
                height=max(4, content_height - 1),
                color=color,
                theme=theme,
                include_heading=False,
            )
        )

    if view.error:
        lines.append(
            _paint(
                _truncate(f"! {safe_text(view.error)}", width), theme.red, color, theme
            )
        )
    lines.append("")
    lines.append(_paint(_footer(view, width), theme.dim, color, theme))
    return "\n".join(lines[:height])


def _catalog_summary(view: SelectionView) -> str:
    summary = (
        f"{view.provider_count:,} provider{'s' if view.provider_count != 1 else ''} / "
        f"{view.model_count:,} model{'s' if view.model_count != 1 else ''}"
    )
    if (
        view.runnable_provider_count != view.provider_count
        or view.runnable_model_count != view.model_count
    ):
        summary += (
            f"  ·  {view.runnable_provider_count:,} "
            f"provider{'s' if view.runnable_provider_count != 1 else ''} / "
            f"{view.runnable_model_count:,} "
            f"model{'s' if view.runnable_model_count != 1 else ''} runnable here"
        )
    return summary


def _search_line(view: SelectionView) -> str:
    scope = (
        "all providers"
        if view.focus == "providers"
        else safe_text(view.active_provider_name or view.active_provider or "provider")
    )
    query = safe_text(view.query)
    field = f"{query}▌" if query else "▌ type to search"
    return f"Search  {field}    in {scope}"


def _wide_content(
    view: SelectionView,
    *,
    width: int,
    height: int,
    color: bool,
    theme: AnsiTheme,
) -> list[str]:
    gap = 4
    provider_width = min(44, max(31, int(width * 0.34)))
    model_width = max(20, width - provider_width - gap)
    provider_heading = "PROVIDERS"
    if view.focus == "providers":
        provider_heading += " · ACTIVE"
    model_heading = f"MODELS · {safe_text(view.active_provider_name or '')}"
    if view.focus == "models":
        model_heading += " · ACTIVE"
    result = [
        _paint(
            _truncate(provider_heading, provider_width).ljust(provider_width),
            theme.cyan,
            color,
            theme,
        )
        + (" " * gap)
        + _paint(_truncate(model_heading, model_width), theme.cyan, color, theme)
    ]
    body_height = max(4, height - 1)
    providers = _provider_lines(
        view,
        width=provider_width,
        height=body_height,
        color=color,
        theme=theme,
    )
    models = _model_lines(
        view,
        width=model_width,
        height=body_height,
        color=color,
        theme=theme,
        include_heading=False,
    )
    for index in range(body_height):
        left = providers[index] if index < len(providers) else ""
        right = models[index] if index < len(models) else ""
        result.append(_pad_visible(left, provider_width) + (" " * gap) + right)
    return result


def _provider_lines(
    view: SelectionView,
    *,
    width: int,
    height: int,
    color: bool,
    theme: AnsiTheme,
) -> list[str]:
    entries: list[tuple[int | None, str, ProviderView | None]] = []
    previous_state: str | None = None
    labels = {
        "ready": "READY NOW",
        "connect": "CONNECT ACCESS",
        "catalog": "BROWSE CATALOGUE",
    }
    for index, provider in enumerate(view.providers):
        if provider.state != previous_state:
            entries.append((None, labels[provider.state], None))
            previous_state = provider.state
        pointer = "▶" if provider.cursor else ("•" if provider.active else " ")
        if view.query:
            facts = f"{provider.match_count} match{'es' if provider.match_count != 1 else ''}"
        elif provider.state == "ready":
            facts = f"{provider.runnable_model_count}/{provider.model_count} ready"
        elif provider.state == "connect":
            method = _auth_label(
                provider.auth_methods[0] if provider.auth_methods else None
            )
            facts = f"{provider.runnable_model_count}/{provider.model_count} · {method}"
        else:
            facts = f"{provider.model_count} model{'s' if provider.model_count != 1 else ''}"
        name_width = max(8, width - len(facts) - 4)
        body = f"{pointer} {_truncate(provider.name, name_width):<{name_width}} {facts}"
        entries.append((index, _truncate(body, width), provider))

    if not entries:
        return [_paint("No providers available", theme.dim, color, theme)]
    cursor_entry = next(
        (
            position
            for position, (index, _, _) in enumerate(entries)
            if index == view.provider_cursor
        ),
        0,
    )
    start = max(0, min(cursor_entry - height // 2, max(0, len(entries) - height)))
    visible = entries[start : start + height]
    result: list[str] = []
    for _, text, provider in visible:
        if provider is None:
            result.append(_paint(text, theme.dim, color, theme))
        elif provider.cursor and view.focus == "providers":
            result.append(_paint(text.ljust(width), theme.selected, color, theme))
        elif provider.state == "ready":
            result.append(_paint(text, theme.green, color, theme))
        elif provider.state == "connect":
            result.append(_paint(text, theme.yellow, color, theme))
        else:
            result.append(text)
    return result


def _model_lines(
    view: SelectionView,
    *,
    width: int,
    height: int,
    color: bool,
    theme: AnsiTheme,
    include_heading: bool,
) -> list[str]:
    result: list[str] = []
    if include_heading:
        result.append(
            _paint(
                f"MODELS · {safe_text(view.active_provider_name or '')}",
                theme.cyan,
                color,
                theme,
            )
        )
    if not view.candidates:
        result.append(_paint("No matching models", theme.dim, color, theme))
        return result

    detail_rows = _preview_rows(view, width)
    available = max(3, height - len(detail_rows) - 1)
    candidates = _candidate_window(view.candidates, view.cursor, available)
    for candidate in candidates:
        result.append(_candidate_line(candidate, width, view.focus, color, theme))
    if detail_rows and len(result) < height:
        result.append("")
        result.extend(detail_rows[: max(0, height - len(result))])
    return result[:height]


def _candidate_window(
    candidates: tuple[CandidateView, ...], cursor: int, height: int
) -> tuple[CandidateView, ...]:
    first = max(0, min(cursor - height // 2, max(0, len(candidates) - height)))
    return candidates[first : first + height]


def _candidate_line(
    candidate: CandidateView,
    width: int,
    focus: str,
    color: bool,
    theme: AnsiTheme,
) -> str:
    pointer = "▶" if candidate.cursor else " "
    check = "✓" if candidate.selected else " "
    model_id = candidate.id.split("/", 1)[-1]
    badges = " ".join(f"[{safe_text(item)}]" for item in candidate.badges)
    prefix = f"{pointer} {check} "
    model_width = max(8, width - len(prefix) - len(badges) - (2 if badges else 0))
    text = prefix + _truncate(model_id, model_width)
    if badges:
        text += "  " + badges
    text = _truncate(text, width)
    if candidate.cursor and focus == "models":
        return _paint(text.ljust(width), theme.selected, color, theme)
    if not candidate.route_supported:
        return _paint(text, theme.dim, color, theme)
    return text


def _preview_rows(view: SelectionView, width: int) -> list[str]:
    preview = view.preview
    if preview is None:
        return []
    if not preview.route_supported:
        availability = "catalogue only"
    elif preview.usable_now:
        availability = "ready in this app"
    else:
        availability = "needs access"
    result = [
        "HIGHLIGHT",
        _truncate(preview.name, width),
        _truncate(preview.id, width),
        f"capabilities  {_join(preview.capabilities)}",
        f"context       {_number(preview.context_limit)}",
        f"availability  {availability}",
    ]
    if preview.route_supported and not preview.usable_now:
        result.append(
            _truncate(
                "needs         "
                + (", ".join(preview.required_variables) or "a stored credential"),
                width,
            )
        )
    modes: list[str] = []
    if preview.variants:
        modes.append("variant " + "/".join(preview.variants))
    if preview.efforts:
        modes.append("effort " + "/".join(preview.efforts))
    if preview.tiers:
        modes.append("tier " + "/".join(preview.tiers))
    if modes:
        result.append(_truncate("modes         " + " · ".join(modes), width))
    if preview.route_support_reason:
        result.extend(
            textwrap.wrap(
                safe_text(preview.route_support_reason),
                width=max(20, width),
                break_long_words=False,
                break_on_hyphens=False,
            )
        )
    if view.selected_model == preview.id:
        result.extend(
            [
                "",
                "CHOSEN ROUTE",
                f"effort        {view.effort or 'provider default'}",
                f"variant       {view.variant or 'provider default'}",
                f"tier          {view.tier or 'provider default'}",
                f"access        {view.access_label or 'choose access route'}",
                f"authentication {_auth_label(view.auth_kind)}",
                f"economic path {_billing_label(view.billing_kind)}",
            ]
        )
    return [_truncate(row, width) for row in result]


def _footer(view: SelectionView, width: int) -> str:
    if view.focus == "providers":
        return _truncate(
            "↑↓ provider   Enter models   type to search   Esc close", width
        )
    if view.selected_model is None:
        return _truncate(
            "↑↓ model   ← provider   Enter choose   type to search   Esc back",
            width,
        )
    if width < WIDE_LAYOUT:
        return _truncate(
            "↑↓ model   Enter confirm   ← back   E/V/T/P configure   type to search",
            width,
        )
    return _truncate(
        "↑↓ model   ← provider   Enter confirm   E effort   V variant   "
        "T tier   P access   type to search",
        width,
    )


def _auth_label(value: str | None) -> str:
    return {
        "delegated": "existing sign-in",
        "api_key": "API key",
        "credential_bundle": "credentials",
        "oauth": "OAuth sign-in",
        "anonymous": "no sign-in",
    }.get(value or "", safe_text(value or "not selected").replace("_", " "))


def _billing_label(value: str | None) -> str:
    return {
        "subscription": "subscription access",
        "api": "metered API usage",
        "marketplace": "marketplace credits",
        "local": "local compute",
    }.get(value or "", safe_text(value or "not selected").replace("_", " "))


def _join(values: Iterable[str]) -> str:
    return ", ".join(values) or "none declared"


def _number(value: float | None) -> str:
    return f"{value:,}" if value is not None else "not declared"


def _truncate(value: str, width: int) -> str:
    clean = safe_text(value)
    if len(clean) <= width:
        return clean
    return clean[: max(0, width - 1)] + "…"


def _pad_visible(value: str, width: int) -> str:
    return value + (" " * max(0, width - len(safe_text(value))))


def _paint(value: str, code: str, enabled: bool, theme: AnsiTheme) -> str:
    return f"{code}{value}{theme.reset}" if enabled else value
