#!/usr/bin/env python3
"""Render the 2026-09-11 live-replay reconciliation as the README's SVG chart (QNT-489).

Every number below is copied from docs/spikes/2026-09-11-qnt466-g3-live-replay.md (session
`qnt-466-20260911124320`, reconciled window 13:00 to 14:00 UTC); nothing is queried. Stdlib
only, so the output is deterministic and tests/test_recon_chart.py can diff it byte for byte.
Regenerate with `uv run python scripts/recon_chart.py`.
"""

from pathlib import Path

OUTPUT = Path(__file__).resolve().parent.parent / "docs" / "img" / "recon-2026-09-11.svg"

WINDOW_START_S = 13 * 3600  # 13:00:00 UTC
WINDOW_END_S = 14 * 3600  # 14:00:00 UTC
GAP_START_S = 13 * 3600 + 21 * 60 + 11.624  # gap_recorded start, 13:21:11.624
GAP_END_S = 13 * 3600 + 30 * 60 + 46.517  # gap_recorded end, 13:30:46.517

# (label, count, colour) in display order.
BUCKETS = [
    ("both: feed and archive agree", 132_823, "#2b8a3e"),
    ("backfill_only, inside the recorded gap", 13_576, "#e8590c"),
    ("backfill_only, outside it (534 ms past gap end)", 1, "#c92a2a"),
    ("ws_only: feed saw it, archive did not", 0, "#c92a2a"),
]

WIDTH, HEIGHT = 760, 330
PLOT_X0, PLOT_X1 = 40, 720
INK, MUTED = "#212529", "#868e96"


def _x(seconds: float) -> float:
    span = WINDOW_END_S - WINDOW_START_S
    return PLOT_X0 + (seconds - WINDOW_START_S) / span * (PLOT_X1 - PLOT_X0)


def _hhmmss(seconds: float) -> str:
    s = round(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _text(x: float, y: float, body: str, *, size: int = 13, fill: str = INK, **attrs: str) -> str:
    extra = "".join(f' {k.replace("_", "-")}="{v}"' for k, v in attrs.items())
    return f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}"{extra}>{body}</text>'


def _timeline() -> list[str]:
    gx0, gx1 = _x(GAP_START_S), _x(GAP_END_S)
    gap_len = round(GAP_END_S - GAP_START_S)
    out = [
        _text(PLOT_X0, 58, "WebSocket feed, live hour 13:00 to 14:00 UTC", fill=MUTED),
        f'<rect x="{PLOT_X0}" y="66" width="{PLOT_X1 - PLOT_X0}" height="22" fill="#2b8a3e"/>',
        f'<rect x="{gx0:.1f}" y="62" width="{gx1 - gx0:.1f}" height="30" fill="#e8590c"/>',
        _text(
            (gx0 + gx1) / 2,
            110,
            f"recorded gap {_hhmmss(GAP_START_S)} to {_hhmmss(GAP_END_S)}"
            f" ({gap_len // 60}m {gap_len % 60}s)",
            size=12,
            fill="#e8590c",
            text_anchor="middle",
        ),
    ]
    for minute in range(0, 61, 10):
        tx = _x(WINDOW_START_S + minute * 60)
        out.append(
            _text(
                tx,
                128,
                f"{13 + minute // 60}:{minute % 60:02d}",
                size=11,
                fill=MUTED,
                text_anchor="middle",
            )
        )
    return out


def _buckets() -> list[str]:
    top, row_h, bar_h = 168, 36, 20
    label_w = 330
    bar_x0, bar_x1 = PLOT_X0 + label_w, PLOT_X1 - 70
    biggest = max(count for _, count, _ in BUCKETS)
    out = [_text(PLOT_X0, top - 14, "Trades in the hour, by which source saw them", fill=MUTED)]
    for i, (label, count, colour) in enumerate(BUCKETS):
        y = top + i * row_h
        width = count / biggest * (bar_x1 - bar_x0)
        out.append(_text(PLOT_X0, y + 15, label))
        out.append(
            f'<rect x="{bar_x0}" y="{y}" width="{width:.1f}" height="{bar_h}" fill="{colour}"/>'
        )
        out.append(_text(bar_x0 + width + 6, y + 15, f"{count:,}", font_weight="bold"))
    return out


def render_svg() -> str:
    body = [
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="#ffffff"/>',
        _text(
            PLOT_X0,
            28,
            "Feed vs archive reconciliation, 2026-09-11 (one live hour)",
            size=16,
            font_weight="bold",
        ),
        *_timeline(),
        *_buckets(),
    ]
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" font-family="Helvetica, Arial, sans-serif">',
        *(f"  {line}" for line in body),
        "</svg>",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    OUTPUT.write_text(render_svg())
    print(f"wrote {OUTPUT.relative_to(OUTPUT.parent.parent.parent)}")


if __name__ == "__main__":
    main()
