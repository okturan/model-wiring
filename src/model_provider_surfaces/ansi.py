"""Pure ANSI/Unicode render primitives with control-character sanitization."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from .controller import SelectionView

CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
ESCAPE = re.compile(r"(?:\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])|\x9b[0-?]*[ -/]*[@-~])")


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


def render_pipeline(view: SelectionView, *, color: bool = True) -> str:
    selected = view.selected_model is not None
    profile_ready = not view.auth_required or view.credential_profile is not None
    stages = (
        ("1", "DISCOVER", True),
        ("2", "MODEL", selected),
        ("3", "MODE", selected),
        ("4", "BILLING", selected and profile_ready),
        ("5", "READY", view.route_ready),
    )
    pieces: list[str] = []
    theme = AnsiTheme()
    for number, label, complete in stages:
        marker = "●" if complete else "○"
        body = f"{marker} {number} {label}"
        if color:
            paint = theme.green if complete else theme.dim
            body = f"{paint}{body}{theme.reset}"
        pieces.append(body)
    arrow = f" {theme.dim}──▶{theme.reset} " if color else " --> "
    return arrow.join(pieces)


def render_screen(
    view: SelectionView,
    *,
    width: int = 100,
    height: int = 30,
    color: bool = True,
) -> str:
    width = max(60, width)
    height = max(16, height)
    theme = AnsiTheme()
    title = "MODEL ROUTE"
    if color:
        title = f"{theme.bold}{theme.cyan}{title}{theme.reset}"
    lines = [
        title
        + "  "
        + _paint(
            f"{view.provider_count} providers / {view.model_count} models",
            theme.dim,
            color,
            theme,
        ),
        render_pipeline(view, color=color),
        "",
        _paint(
            f"Search  /{safe_text(view.query)}",
            theme.blue,
            color,
            theme,
        ),
    ]
    left_width = max(34, int(width * 0.58))
    right_width = width - left_width - 3
    candidates = list(view.candidates)
    content_rows = max(5, height - 10)
    first = max(
        0,
        min(
            view.cursor - content_rows // 2,
            max(0, len(candidates) - content_rows),
        ),
    )
    candidates = candidates[first : first + content_rows]
    for index in range(content_rows):
        if index < len(candidates):
            candidate = candidates[index]
            pointer = "▶" if candidate.cursor else " "
            check = "✓" if candidate.selected else " "
            badges = " ".join(f"[{safe_text(item)}]" for item in candidate.badges)
            candidate_text = (
                f"{pointer} {check} {safe_text(candidate.id)}  {badges}".rstrip()
            )
            candidate_text = _truncate(candidate_text, left_width)
            if candidate.cursor:
                candidate_text = _paint(
                    candidate_text.ljust(left_width), theme.selected, color, theme
                )
            else:
                candidate_text = candidate_text.ljust(left_width)
        else:
            candidate_text = " " * left_width
        details = _detail_rows(view)
        detail_text = details[index] if index < len(details) else ""
        lines.append(f"{candidate_text} │ {_truncate(detail_text, right_width)}")
    if view.error:
        lines.append(_paint(f"! {safe_text(view.error)}", theme.red, color, theme))
    else:
        lines.append("")
    lines.append(
        _paint(
            "↑↓ move  Enter choose/confirm  / search  e effort  v variant  t tier  "
            "b billing  p profile  q quit",
            theme.dim,
            color,
            theme,
        )
    )
    return "\n".join(lines[:height])


def _detail_rows(view: SelectionView) -> list[str]:
    if not view.selected_model:
        return [
            "SELECTION",
            "",
            "Choose a model to inspect",
            "its route, modes, billing,",
            "and credential profile.",
        ]
    profile = view.credential_profile or "unresolved"
    return [
        "SELECTION",
        safe_text(view.selected_name or ""),
        safe_text(view.selected_model),
        "",
        f"capabilities  {_join(view.capabilities)}",
        f"context       {_number(view.context_limit)}",
        f"variant       {view.variant or 'default'}",
        f"effort        {view.effort or 'provider default'}",
        f"tier          {view.tier or 'provider default'}",
        f"billing       {view.billing_kind or 'unresolved'}",
        f"profile       {profile}",
    ]


def _join(values: Iterable[str]) -> str:
    return ", ".join(values) or "none declared"


def _number(value: float | None) -> str:
    return f"{value:,}" if value is not None else "not declared"


def _truncate(value: str, width: int) -> str:
    clean = safe_text(value)
    if len(clean) <= width:
        return clean
    return clean[: max(0, width - 1)] + "…"


def _paint(value: str, code: str, enabled: bool, theme: AnsiTheme) -> str:
    return f"{code}{value}{theme.reset}" if enabled else value
