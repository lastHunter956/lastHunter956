#!/usr/bin/env python3
"""
Fetch real GitHub contributions and profile/repository stats for a user and
generate three self-contained terminal-style SVGs with internal CSS animations:
  1. contrib-heatmap.svg  — 53-week GitHub contribution heatmap
  2. ascii-portrait.svg   — monochrome ASCII portrait from profile photo
  3. stats.svg            — neofetch-style developer & activity stats card

Works both with GITHUB_TOKEN (official GitHub GraphQL + REST API) and without
a token (GitHub public contributions calendar HTML + public REST API).
"""

import argparse
import datetime
import html
import io
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageEnhance, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
ASSETS_DIR = ROOT / "assets"

DEFAULT_USER = os.environ.get("GH_PROFILE_USER", "lastHunter956")
MONO_FONT = "Menlo, Consolas, 'DejaVu Sans Mono', 'Liberation Mono', monospace"
HEADERS = {"User-Agent": "lastHunter956-profile-generator/1.0"}

# Shared terminal color tokens
BG_TOP = "#111722"
BG_BOT = "#0d1117"
HEAT_BG_TOP = "#0d1420"
HEAT_BG_BOT = "#0a0e14"
TILE_BG = "#161b22"
FRAME = "#30363d"
HEAT_FRAME = "#1f6feb"
MUTED = "#7d8590"
INK = "#e6edf3"
ASCII_INK = "#c9d1d9"
GREEN = "#39d353"
BAR_GREEN = "#26a641"
CYAN = "#22d3ee"
GOLD = "#f2cc60"
PURPLE = "#bc8cff"
RED = "#ff5f56"

PALETTE = ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353", "#69f0a0"]

GRAPHQL_QUERY = """
query($login: String!) {
  user(login: $login) {
    name
    login
    bio
    location
    avatarUrl
    websiteUrl
    followers { totalCount }
    repositories(first: 100, ownerAffiliations: OWNER, isFork: false, orderBy: {field: STARGAZERS, direction: DESC}) {
      totalCount
      nodes {
        stargazerCount
        primaryLanguage { name }
      }
    }
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            contributionCount
            contributionLevel
          }
        }
      }
    }
  }
}
"""

GQL_LEVEL_MAP = {
    "NONE": 0,
    "FIRST_QUARTILE": 1,
    "SECOND_QUARTILE": 2,
    "THIRD_QUARTILE": 3,
    "FOURTH_QUARTILE": 4,
}


def api_headers(token: str | None) -> dict[str, str]:
    headers = dict(HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def compute_current_streak(days: list[dict]) -> tuple[int, str | None, str | None]:
    if not days:
        return 0, None, None
    idx = len(days) - 1
    if days[idx]["count"] == 0:
        idx -= 1  # today isn't over yet
    streak = 0
    end_idx = idx
    while idx >= 0 and days[idx]["count"] > 0:
        streak += 1
        idx -= 1
    start_idx = idx + 1
    if streak == 0:
        return 0, None, None
    return streak, days[start_idx]["date"], days[end_idx]["date"]


def compute_longest_streak(days: list[dict]) -> tuple[int, str | None, str | None]:
    longest = run = 0
    longest_start = longest_end = None
    run_start_idx = 0
    for i, d in enumerate(days):
        if d["count"] > 0:
            if run == 0:
                run_start_idx = i
            run += 1
            if run > longest:
                longest = run
                longest_start = days[run_start_idx]["date"]
                longest_end = d["date"]
        else:
            run = 0
    return longest, longest_start, longest_end


def build_contributions_payload(username: str, days: list[dict]) -> dict:
    days = sorted(days, key=lambda d: d["date"])
    total = sum(d["count"] for d in days)
    active_days = sum(1 for d in days if d["count"] > 0)
    best = max(days, key=lambda d: d["count"]) if days else {"date": "", "count": 0}
    cur_len, cur_start, cur_end = compute_current_streak(days)
    long_len, long_start, long_end = compute_longest_streak(days)

    monthly: dict[str, int] = {}
    for d in days:
        key = d["date"][:7]
        monthly[key] = monthly.get(key, 0) + d["count"]
    monthly_list = [{"month": k, "total": v} for k, v in sorted(monthly.items())]

    return {
        "username": username,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "range": {"start": days[0]["date"], "end": days[-1]["date"]},
        "total_contributions": total,
        "active_days": active_days,
        "avg_per_active_day": round(total / active_days, 1) if active_days else 0.0,
        "current_streak": {"length": cur_len, "start": cur_start, "end": cur_end},
        "longest_streak": {"length": long_len, "start": long_start, "end": long_end},
        "best_day": {"date": best["date"], "count": best["count"]},
        "monthly": monthly_list,
        "days": days,
    }


def fetch_via_graphql(username: str, token: str) -> tuple[dict, dict] | None:
    resp = requests.post(
        "https://api.github.com/graphql",
        json={"query": GRAPHQL_QUERY, "variables": {"login": username}},
        headers=api_headers(token),
        timeout=30,
    )
    if resp.status_code != 200:
        return None
    payload = resp.json()
    user = (payload.get("data") or {}).get("user")
    if not user:
        return None

    days = []
    cal = user["contributionsCollection"]["contributionCalendar"]
    for wk in cal["weeks"]:
        for day in wk["contributionDays"]:
            cnt = int(day["contributionCount"])
            lvl = GQL_LEVEL_MAP.get(day["contributionLevel"], 0)
            if cnt >= 15 and lvl == 4:
                lvl = 5
            days.append({"date": day["date"], "count": cnt, "level": lvl})

    repos = user["repositories"]["nodes"]
    total_stars = sum(r.get("stargazerCount", 0) for r in repos)
    lang_counts: dict[str, int] = {}
    for r in repos:
        lang = (r.get("primaryLanguage") or {}).get("name")
        if lang:
            lang_counts[lang] = lang_counts.get(lang, 0) + 1
    top_langs = [k for k, _ in sorted(lang_counts.items(), key=lambda x: -x[1])[:4]]

    contrib_data = build_contributions_payload(username, days)
    profile_data = {
        "username": user["login"],
        "name": user.get("name") or username,
        "location": user.get("location") or "Cartagena, Colombia",
        "bio": user.get("bio") or "",
        "website": user.get("websiteUrl") or "jesus-martinez.me",
        "avatar_url": user.get("avatarUrl") or "",
        "public_repos": user["repositories"]["totalCount"],
        "total_stars": total_stars,
        "followers": user["followers"]["totalCount"],
        "top_languages": top_langs,
    }
    return contrib_data, profile_data


def fetch_via_public(username: str, token: str | None) -> tuple[dict, dict]:
    # 1. Public contributions calendar HTML
    cal_url = f"https://github.com/users/{username}/contributions"
    r_cal = requests.get(cal_url, headers=HEADERS, timeout=30)
    r_cal.raise_for_status()
    soup = BeautifulSoup(r_cal.text, "html.parser")
    cells = soup.select("td.ContributionCalendar-day")
    if not cells:
        raise RuntimeError("No contribution calendar cells found in GitHub HTML")

    days = []
    for td in cells:
        date = td.get("data-date")
        if not date:
            continue
        td_id = td.get("id")
        tip = soup.find("tool-tip", attrs={"for": td_id}) if td_id else None
        text = tip.get_text(strip=True) if tip else ""
        if re.search(r"no contributions", text, re.I):
            count = 0
        else:
            m = re.match(r"(\d+)", text)
            count = int(m.group(1)) if m else 0
        lvl = int(td.get("data-level") or 0)
        if count >= 15 and lvl == 4:
            lvl = 5
        days.append({"date": date, "count": count, "level": lvl})

    # 2. Public REST API user + repos
    hdrs = api_headers(token)
    r_user = requests.get(f"https://api.github.com/users/{username}", headers=hdrs, timeout=20)
    r_user.raise_for_status()
    u = r_user.json()

    r_repos = requests.get(
        f"https://api.github.com/users/{username}/repos?per_page=100&type=owner",
        headers=hdrs,
        timeout=20,
    )
    repos = r_repos.json() if r_repos.status_code == 200 else []
    if not isinstance(repos, list):
        repos = []
    total_stars = sum(r.get("stargazers_count", 0) for r in repos if not r.get("fork"))
    lang_counts: dict[str, int] = {}
    for r in repos:
        if r.get("fork"):
            continue
        lang = r.get("language")
        if lang:
            lang_counts[lang] = lang_counts.get(lang, 0) + 1
    top_langs = [k for k, _ in sorted(lang_counts.items(), key=lambda x: -x[1])[:4]]

    contrib_data = build_contributions_payload(username, days)
    profile_data = {
        "username": u.get("login", username),
        "name": u.get("name") or "Jesús Martínez",
        "location": u.get("location") or "Cartagena, Colombia",
        "bio": u.get("bio") or "",
        "website": u.get("blog") or "jesus-martinez.me",
        "avatar_url": u.get("avatar_url") or "",
        "public_repos": int(u.get("public_repos", len(repos))),
        "total_stars": total_stars,
        "followers": int(u.get("followers", 0)),
        "top_languages": top_langs or ["Python", "TypeScript", "Jupyter Notebook"],
    }
    return contrib_data, profile_data


def fetch_github_data(username: str) -> tuple[dict, dict]:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    contrib_cache = DATA_DIR / "contributions.json"
    profile_cache = DATA_DIR / "profile_stats.json"

    try:
        if token:
            gql = fetch_via_graphql(username, token)
            if gql is not None:
                return gql
        return fetch_via_public(username, token)
    except Exception as exc:
        if contrib_cache.exists() and profile_cache.exists():
            print(f"[warn] Live fetch failed ({exc}); using cached JSON data", file=sys.stderr)
            return json.loads(contrib_cache.read_text()), json.loads(profile_cache.read_text())
        raise


def terminal_chrome(width: float, height: float, title: str, bg_id: str,
                    top_col: str, bot_col: str, frame_col: str,
                    frame_opacity: float = 1.0, pad: float = 20.0, title_h: float = 30.0) -> list[str]:
    stroke_attr = f'stroke="{frame_col}" stroke-opacity="{frame_opacity}"'
    parts = [
        "<defs>",
        f'<linearGradient id="{bg_id}" x1="0" y1="0" x2="0" y2="1">',
        f'<stop offset="0" stop-color="{top_col}"/><stop offset="1" stop-color="{bot_col}"/>',
        "</linearGradient>",
        "</defs>",
        f'<rect width="{width}" height="{height}" rx="12" fill="url(#{bg_id})"/>',
        f'<rect x="0.5" y="0.5" width="{width-1}" height="{height-1}" rx="12" fill="none" {stroke_attr}/>',
        f'<line x1="0" y1="{title_h}" x2="{width}" y2="{title_h}" {stroke_attr}/>',
    ]
    for i, dot in enumerate([RED, "#ffbd2e", "#27c93f"]):
        parts.append(f'<circle cx="{pad + i * 16}" cy="{title_h / 2}" r="5" fill="{dot}"/>')
    parts.append(
        f'<text x="{width / 2}" y="{title_h / 2 + 4}" fill="{MUTED}" font-size="12" '
        f'text-anchor="middle">{html.escape(title)}</text>'
    )
    return parts


# ---------------------------------------------------------------------------
# 1. Contribution Heatmap SVG
# ---------------------------------------------------------------------------
def build_grid(days: list[dict]) -> list[list]:
    first = datetime.date.fromisoformat(days[0]["date"])
    lead_pad = (first.weekday() + 1) % 7  # Sunday = 0
    grid: list[list] = []
    col: list = [None] * lead_pad
    for d in days:
        dt = datetime.date.fromisoformat(d["date"])
        weekday = (dt.weekday() + 1) % 7
        while len(col) < weekday:
            col.append(None)
        lvl = max(0, min(len(PALETTE) - 1, int(d.get("level", 0))))
        col.append((d["date"], int(d["count"]), lvl))
        if len(col) == 7:
            grid.append(col)
            col = []
    if col:
        while len(col) < 7:
            col.append(None)
        grid.append(col)
    return grid


def render_heatmap_svg(data: dict, out_path: Path, static: bool = False) -> None:
    username = data["username"]
    grid = build_grid(data["days"])
    n_cols = len(grid)

    cell = 12
    gap = 3
    step = cell + gap
    pad = 22
    left_label_w = 30
    top_label_h = 20
    title_h = 30
    stats_h = 88

    art_w = n_cols * step
    art_h = 7 * step
    canvas_w = pad + left_label_w + art_w + pad
    canvas_h = title_h + top_label_h + art_h + stats_h + pad

    month_labels = []
    seen_months = set()
    for ci, column in enumerate(grid):
        for entry in column:
            if entry is None:
                continue
            dt = datetime.date.fromisoformat(entry[0])
            key = (dt.year, dt.month)
            if key not in seen_months and dt.day <= 7:
                seen_months.add(key)
                month_labels.append((ci, dt.strftime("%b")))
            break

    css = """
@keyframes cellIn {
  0%   { opacity: 0; transform: translateY(-6px); }
  100% { opacity: 1; transform: translateY(0); }
}
.c { opacity: 0; animation: cellIn 0.42s cubic-bezier(.2,.8,.2,1) both; }
@media (prefers-reduced-motion: reduce) {
  .c { opacity: 1 !important; transform: none !important; animation: none !important; }
}
""".strip()

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas_w}" height="{canvas_h}" '
        f'viewBox="0 0 {canvas_w} {canvas_h}" '
        f'font-family="{MONO_FONT}">',
        f"<style>{css}</style>",
    ]
    parts.extend(
        terminal_chrome(
            canvas_w, canvas_h,
            f"{username}@github: ~/contributions --graph",
            "hbg", HEAT_BG_TOP, HEAT_BG_BOT, HEAT_FRAME, frame_opacity=0.55, pad=pad, title_h=title_h,
        )
    )

    grid_top = title_h + top_label_h
    grid_left = pad + left_label_w

    for ci, label in month_labels:
        x = grid_left + ci * step
        parts.append(f'<text x="{x}" y="{title_h + 14}" fill="{MUTED}" font-size="10">{label}</text>')

    for wi, wname in [(1, "Mon"), (3, "Wed"), (5, "Fri")]:
        y = grid_top + wi * step + cell * 0.78
        parts.append(f'<text x="{pad}" y="{y:.1f}" fill="{MUTED}" font-size="9">{wname}</text>')

    for ci, column in enumerate(grid):
        gx = grid_left + ci * step
        for ri, entry in enumerate(column):
            if entry is None:
                continue
            date_s, count, lvl = entry
            gy = grid_top + ri * step
            delay = ci * 0.018 + ri * 0.045
            plural = "s" if count != 1 else ""
            cls_attr = "" if static else f'class="c" style="animation-delay:{delay:.3f}s"'
            parts.append(
                f'<rect {cls_attr} x="{gx}" y="{gy}" width="{cell}" height="{cell}" rx="2.5" '
                f'fill="{PALETTE[lvl]}"><title>{date_s}: {count} contribution{plural}</title></rect>'
            )

    leg_y = grid_top + art_h + 6
    leg_x = canvas_w - pad - (len(PALETTE) * (cell - 1) + 70)
    parts.append(f'<text x="{leg_x}" y="{leg_y + cell * 0.8:.1f}" fill="{MUTED}" font-size="10" text-anchor="end">Less</text>')
    lx = leg_x + 8
    for color in PALETTE:
        parts.append(f'<rect x="{lx}" y="{leg_y}" width="{cell - 1}" height="{cell - 1}" rx="2.2" fill="{color}"/>')
        lx += cell
    parts.append(f'<text x="{lx + 4}" y="{leg_y + cell * 0.8:.1f}" fill="{MUTED}" font-size="10">More</text>')

    sep_y = leg_y + cell + 14
    parts.append(f'<line x1="0" y1="{sep_y}" x2="{canvas_w}" y2="{sep_y}" stroke="{HEAT_FRAME}" stroke-opacity="0.25"/>')

    cs = data["current_streak"]["length"]
    ls = data["longest_streak"]["length"]
    total = data["total_contributions"]
    best = data["best_day"]
    rng = data["range"]

    ly = sep_y + 24
    parts.append(
        f'<text x="{pad}" y="{ly}" font-size="13" fill="{GREEN}">'
        f'<tspan font-weight="700">{total:,}</tspan>'
        f'<tspan fill="{MUTED}"> contributions in the last year</tspan></text>'
    )
    parts.append(
        f'<text x="{canvas_w - pad}" y="{ly}" font-size="12" fill="{MUTED}" text-anchor="end">'
        f'{rng["start"]} -> {rng["end"]}</text>'
    )
    ly += 24
    parts.append(
        f'<text x="{pad}" y="{ly}" font-size="13" fill="{MUTED}">current streak '
        f'<tspan fill="{CYAN}" font-weight="700">{cs} days</tspan>'
        f'<tspan fill="{MUTED}">   &#183;   longest </tspan>'
        f'<tspan fill="{CYAN}" font-weight="700">{ls} days</tspan></text>'
    )
    best_label = f'best day: {best["count"]} on {best["date"]}'
    parts.append(
        f'<text x="{canvas_w - pad}" y="{ly}" font-size="12" fill="{GOLD}" text-anchor="end">'
        f'{html.escape(best_label)}</text>'
    )
    parts.append("</svg>")

    out_path.write_text("".join(parts), encoding="utf-8")
    print(f"wrote {out_path} ({canvas_w}x{canvas_h})")


# ---------------------------------------------------------------------------
# 2. ASCII Portrait SVG
# ---------------------------------------------------------------------------
def prepare_portrait(src_img: Image.Image) -> Image.Image:
    """Isolate studio portrait subject onto clean black background for ASCII."""
    arr = np.array(src_img.convert("RGB"), dtype=np.float64)
    h, w, _ = arr.shape
    ys_idx, xs_idx = np.mgrid[0:h, 0:w]
    gray = 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]
    sat = np.max(arr, axis=2) - np.min(arr, axis=2)

    # Quadratic vignette background model fitted on neutral outer border (float64)
    bmask = (
        (ys_idx < h * 0.08)
        | ((xs_idx < w * 0.12) & (ys_idx < h * 0.65))
        | ((xs_idx > w * 0.88) & (ys_idx < h * 0.65))
    )
    nx = (xs_idx / w - 0.5) * 2.0
    ny = (ys_idx / h - 0.5) * 2.0
    pts = bmask & (sat < 10.0)
    if pts.sum() > 20:
        X = np.column_stack([
            np.ones(pts.sum()), nx[pts], ny[pts],
            nx[pts] ** 2, ny[pts] ** 2, nx[pts] * ny[pts],
        ])
        # Normal equations in pure float64 avoids BLAS matmul overflow warnings
        xtx = np.einsum("ij,ik->jk", X, X)
        xty = np.einsum("ij,i->j", X, gray[pts])
        coef = np.linalg.solve(xtx + 1e-6 * np.eye(6), xty)
        bg_est = (
            coef[0]
            + coef[1] * nx
            + coef[2] * ny
            + coef[3] * (nx ** 2)
            + coef[4] * (ny ** 2)
            + coef[5] * (nx * ny)
        )
        bg_est = np.clip(bg_est, 0.0, 255.0)
    else:
        bg_est = np.full_like(gray, 28.0)

    diff = gray - bg_est
    in_head_x = (xs_idx > w * 0.22) & (xs_idx < w * 0.78)
    hair_mask = (diff < -10.0) & in_head_x & (ys_idx > h * 0.06) & (ys_idx < h * 0.56)
    shirt_mask = (ys_idx > h * 0.74) & (diff < -8.0) & (xs_idx > w * 0.03) & (xs_idx < w * 0.97)
    fg = (sat > 11.0) | (diff > 16.0) | hair_mask | shirt_mask

    # Clean morphological close/open & soft edge feather
    fg_img = Image.fromarray((fg * 255).astype(np.uint8))
    fg_clean = fg_img.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))
    mask = np.array(fg_clean.filter(ImageFilter.GaussianBlur(2.5)), dtype=np.float64) / 255.0
    mask = np.clip((mask - 0.22) / 0.78, 0.0, 1.0)

    # Enhance facial ridges (eyes, glasses, brows, lips, collar) while keeping smooth skin tones
    gray_u8 = Image.fromarray(np.clip(gray, 0, 255).astype(np.uint8))
    blur_med = np.array(gray_u8.filter(ImageFilter.GaussianBlur(10)), dtype=np.float64)
    blur_fine = np.array(gray_u8.filter(ImageFilter.GaussianBlur(1.2)), dtype=np.float64)
    local_detail = blur_fine - blur_med

    norm_gray = np.clip((gray - 10.0) / 215.0, 0.0, 1.0)
    subject_tone = 0.16 + 0.84 * (norm_gray ** 0.76) + 0.48 * (local_detail / 128.0)
    out = np.clip(subject_tone * mask, 0.0, 1.0) * 255.0
    return Image.fromarray(out.astype(np.uint8), mode="L")


def get_prepped_portrait(profile: dict) -> Image.Image:
    prepped_path = ASSETS_DIR / "source-prepped.png"
    photo_path = ASSETS_DIR / "source-photo.jpg"
    if prepped_path.exists():
        return Image.open(prepped_path).convert("L")
    if photo_path.exists():
        im = prepare_portrait(Image.open(photo_path))
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        im.save(prepped_path)
        return im
    avatar_url = profile.get("avatar_url")
    if avatar_url:
        r = requests.get(avatar_url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        im = prepare_portrait(Image.open(io.BytesIO(r.content)))
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        im.save(prepped_path)
        return im
    raise FileNotFoundError("No source portrait image found in assets/ or avatar_url")


def render_ascii_portrait_svg(profile: dict, out_path: Path, static: bool = False) -> None:
    username = profile["username"]
    display_name = profile.get("name") or "Jesús Martínez"

    cols = 148
    art_w_target = 800.0
    cell_w = art_w_target / cols
    cell_h = cell_w * 15.0 / 8.0
    rows = round(cols * 8.0 / 15.0)
    ramp = " .`:-=+*cs#%@"  # sparse -> dense on dark terminal background

    pad = 20.0
    title_h = 30.0
    status_h = 30.0
    art_w = cols * cell_w
    art_h = rows * cell_h
    canvas_w = 840
    canvas_h = 880

    im = get_prepped_portrait(profile)
    im = ImageEnhance.Contrast(im).enhance(1.08)
    im = im.resize((cols, rows), Image.Resampling.LANCZOS)
    px = im.load()

    rows_txt: list[str] = []
    for y in range(rows):
        chars = []
        for x in range(cols):
            lum = px[x, y] / 255.0
            if lum < 0.13:
                chars.append(" ")
                continue
            adj = (lum - 0.13) / 0.87
            idx = int(adj * (len(ramp) - 1) + 0.5)
            idx = max(1, min(len(ramp) - 1, idx))
            chars.append(ramp[idx])
        rows_txt.append("".join(chars))

    row_dur = 0.18
    stagger = 4.6 / rows

    css = f"""
@keyframes typeRow {{
  0%   {{ opacity: 0; clip-path: inset(0 100% 0 0); }}
  15%  {{ opacity: 1; }}
  100% {{ opacity: 1; clip-path: inset(0 0% 0 0); }}
}}
@keyframes blink {{
  0%, 49%  {{ opacity: 1; }}
  50%, 100% {{ opacity: 0; }}
}}
.row {{ opacity: 0; animation: typeRow {row_dur:.2f}s linear both; }}
.cur {{ animation: blink 1s steps(1, end) infinite; }}
@media (prefers-reduced-motion: reduce) {{
  .row {{ opacity: 1 !important; clip-path: none !important; animation: none !important; }}
  .cur {{ animation: none !important; opacity: 1 !important; }}
}}
""".strip()

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas_w}" height="{canvas_h}" '
        f'viewBox="0 0 {canvas_w} {canvas_h}" '
        f'font-family="{MONO_FONT}">',
        f"<style>{css}</style>",
    ]
    parts.extend(
        terminal_chrome(
            canvas_w, canvas_h,
            f"{username}@github: ~$ ./portrait.sh",
            "pbg", BG_TOP, BG_BOT, FRAME, pad=pad, title_h=title_h,
        )
    )

    art_top = title_h + pad * 0.35
    font_size = cell_w / 0.60
    for ry, line in enumerate(rows_txt):
        y = art_top + ry * cell_h + cell_h * 0.74
        stripped = line.rstrip()
        if not stripped:
            continue
        delay = ry * stagger
        safe = html.escape(stripped)
        cls_attr = "" if static else f'class="row" style="animation-delay:{delay:.3f}s"'
        parts.append(
            f'<text {cls_attr} xml:space="preserve" x="{pad}" y="{y:.1f}" fill="{ASCII_INK}" '
            f'font-size="{font_size:.2f}">{safe}</text>'
        )

    status_line_y = title_h + art_h + pad * 0.35
    status_y = status_line_y + 19
    parts.append(f'<line x1="0" y1="{status_line_y:.1f}" x2="{canvas_w}" y2="{status_line_y:.1f}" stroke="{FRAME}"/>')
    prompt_prefix = f"{username}@github:~$ whoami "
    parts.append(
        f'<text x="{pad}" y="{status_y:.1f}" fill="{MUTED}" font-size="13">'
        f'{html.escape(prompt_prefix)}<tspan fill="{INK}">{html.escape(display_name)}</tspan></text>'
    )
    cursor_x = pad + (len(prompt_prefix) + len(display_name) + 0.8) * 13 * 0.602
    cur_cls = "" if static else 'class="cur"'
    parts.append(f'<rect {cur_cls} x="{cursor_x:.1f}" y="{status_y - 12:.1f}" width="8" height="14" fill="{GREEN}"/>')
    parts.append("</svg>")

    out_path.write_text("".join(parts), encoding="utf-8")
    print(f"wrote {out_path} ({canvas_w}x{canvas_h})")


# ---------------------------------------------------------------------------
# 3. Neofetch Stats SVG
# ---------------------------------------------------------------------------
def short_date(d: str | None) -> str:
    if not d:
        return "—"
    return datetime.date.fromisoformat(d).strftime("%b %-d")


def span_label(s: dict) -> str:
    if not s.get("length"):
        return "no active streak"
    return f'{short_date(s["start"])} – {short_date(s["end"])}'


def render_stats_svg(contrib: dict, profile: dict, out_path: Path, static: bool = False) -> None:
    W, H = 840, 880  # exact match to ascii-portrait.svg
    pad = 24.0
    title_h = 30.0
    username = profile["username"]

    cur = contrib["current_streak"]
    lng = contrib["longest_streak"]
    best = contrib["best_day"]
    n_days = len(contrib["days"]) or 365
    top_langs = " · ".join(profile.get("top_languages") or ["Python", "TypeScript"])

    neofetch_rows = [
        ("Name", profile.get("name") or "Jesús Martínez", INK),
        ("Role", "Systems Eng · Data Science · AI", CYAN),
        ("Location", profile.get("location") or "Cartagena, Colombia", INK),
        ("Repos", f'{profile.get("public_repos", 0)} public  ·  {profile.get("total_stars", 0)} stars', GOLD),
        ("Top Langs", top_langs, GREEN),
        ("Stack", "Python · Next.js · NestJS · Flask · SQL", INK),
        ("Focus", "AI Solutions · Full Stack · InfoSec", PURPLE),
        ("Web", profile.get("website") or "jesus-martinez.me", CYAN),
    ]

    tiles = [
        ("contributions (1y)", f'{contrib["total_contributions"]:,}', f'{contrib["active_days"]}/{n_days} active days', GREEN),
        ("current streak", f'{cur["length"]} days', span_label(cur), CYAN),
        ("longest streak", f'{lng["length"]} days', span_label(lng), GOLD),
        ("best day", f'{best["count"]} commits', f'{short_date(best["date"])} · avg {contrib["avg_per_active_day"]}/day', PURPLE),
    ]

    css = """
@keyframes fadeSlide {
  0%   { opacity: 0; transform: translateY(10px); }
  100% { opacity: 1; transform: translateY(0); }
}
@keyframes growBar {
  0%   { transform: scaleY(0); }
  100% { transform: scaleY(1); }
}
@keyframes blink {
  0%, 49%  { opacity: 1; }
  50%, 100% {{ opacity: 0; }}
}
.line { opacity: 0; animation: fadeSlide 0.35s ease-out both; }
.card { opacity: 0; animation: fadeSlide 0.45s ease-out both; }
.bar  { transform-box: fill-box; transform-origin: bottom; transform: scaleY(0); animation: growBar 0.55s ease-out both; }
.cur  { animation: blink 1s steps(1, end) infinite; }
@media (prefers-reduced-motion: reduce) {
  .line, .card { opacity: 1 !important; transform: none !important; animation: none !important; }
  .bar { transform: scaleY(1) !important; animation: none !important; }
  .cur { animation: none !important; opacity: 1 !important; }
}
""".replace("{{ opacity: 0; }}", "{ opacity: 0; }").strip()

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        f'font-family="{MONO_FONT}">',
        f"<style>{css}</style>",
    ]
    parts.extend(
        terminal_chrome(
            W, H,
            f"{username}@github: ~$ neofetch --github",
            "sbg", BG_TOP, BG_BOT, FRAME, pad=20.0, title_h=title_h,
        )
    )

    # Command prompt + neofetch header
    y = title_h + 34
    parts.append(
        f'<text x="{pad}" y="{y}" font-size="16" fill="{MUTED}">'
        f'<tspan fill="{GREEN}">{html.escape(username)}@github</tspan>:~$ neofetch --profile</text>'
    )
    y += 32
    parts.append(
        f'<text x="{pad}" y="{y}" font-size="20" font-weight="700" fill="{CYAN}">'
        f'{html.escape(username)}<tspan fill="{MUTED}">@</tspan><tspan fill="{GREEN}">github</tspan></text>'
    )
    y += 14
    parts.append(f'<line x1="{pad}" y1="{y}" x2="{pad + 340}" y2="{y}" stroke="{FRAME}" stroke-width="1.2"/>')

    # Neofetch key/value rows
    y += 28
    for i, (key, val, val_col) in enumerate(neofetch_rows):
        cls_attr = "" if static else f'class="line" style="animation-delay:{0.08 + i * 0.07:.2f}s"'
        parts.append(
            f'<text {cls_attr} xml:space="preserve" x="{pad}" y="{y}" font-size="17">'
            f'<tspan fill="{GREEN}" font-weight="700">{html.escape(key):<10}</tspan>'
            f'<tspan fill="{MUTED}">: </tspan>'
            f'<tspan fill="{val_col}">{html.escape(str(val))}</tspan></text>'
        )
        y += 30

    # Neofetch 8-color swatch bar
    y += 6
    swatches = [BG_BOT, RED, GREEN, GOLD, HEAT_FRAME, PURPLE, CYAN, INK]
    swatch_cls = "" if static else 'class="line" style="animation-delay:0.65s"'
    parts.append(f'<g {swatch_cls}>')
    for i, sw in enumerate(swatches):
        parts.append(
            f'<rect x="{pad + i * 32}" y="{y}" width="26" height="14" rx="3" '
            f'fill="{sw}" stroke="{FRAME}" stroke-width="0.7"/>'
        )
    parts.append("</g>")

    # Divider before activity cards
    y += 32
    parts.append(f'<line x1="{pad}" y1="{y}" x2="{W - pad}" y2="{y}" stroke="{FRAME}"/>')
    y += 24

    # 2x2 Activity Stat Cards
    gap = 14.0
    card_w = (W - pad * 2 - gap) / 2
    card_h = 104.0
    for i, (label, val, sub, accent) in enumerate(tiles):
        col_i, row_i = i % 2, i // 2
        cx = pad + col_i * (card_w + gap)
        cy = y + row_i * (card_h + gap)
        delay = 0.72 + i * 0.10
        cls_attr = "" if static else f'class="card" style="animation-delay:{delay:.2f}s"'
        parts.append(f'<g {cls_attr}>')
        parts.append(
            f'<rect x="{cx:.1f}" y="{cy:.1f}" width="{card_w:.1f}" height="{card_h:.1f}" '
            f'rx="10" fill="{TILE_BG}" stroke="{FRAME}"/>'
        )
        parts.append(f'<text x="{cx + 18:.1f}" y="{cy + 28:.1f}" fill="{MUTED}" font-size="14">$ {html.escape(label)}</text>')
        parts.append(f'<text x="{cx + 18:.1f}" y="{cy + 66:.1f}" fill="{accent}" font-size="30" font-weight="700">{html.escape(val)}</text>')
        parts.append(f'<text x="{cx + 18:.1f}" y="{cy + 90:.1f}" fill="{MUTED}" font-size="13">{html.escape(sub)}</text>')
        parts.append("</g>")

    # Monthly bars panel
    chart_top = y + 2 * card_h + gap + 14
    chart_h = H - pad - 40 - chart_top
    chart_w = W - pad * 2
    c_cls = "" if static else 'class="card" style="animation-delay:1.12s"'
    parts.append(f'<g {c_cls}>')
    parts.append(
        f'<rect x="{pad}" y="{chart_top:.1f}" width="{chart_w}" height="{chart_h:.1f}" '
        f'rx="10" fill="{TILE_BG}" stroke="{FRAME}"/>'
    )
    parts.append(f'<text x="{pad + 18}" y="{chart_top + 28:.1f}" fill="{MUTED}" font-size="15">$ contributions / month</text>')
    parts.append("</g>")

    monthly = contrib["monthly"][-12:]
    if monthly:
        plot_top = chart_top + 48
        plot_bot = chart_top + chart_h - 30
        plot_l = pad + 20
        plot_r = W - pad - 20
        slot = (plot_r - plot_l) / len(monthly)
        bar_w = slot * 0.58
        peak = max(m["total"] for m in monthly) or 1
        for i, m in enumerate(monthly):
            bh = max(3.0, (plot_bot - plot_top) * m["total"] / peak)
            bx = plot_l + i * slot + (slot - bar_w) / 2
            fill = GREEN if m["total"] == peak else BAR_GREEN
            b_cls = "" if static else f'class="bar" style="animation-delay:{1.25 + i * 0.05:.2f}s"'
            parts.append(
                f'<rect {b_cls} x="{bx:.1f}" y="{plot_bot - bh:.1f}" width="{bar_w:.1f}" '
                f'height="{bh:.1f}" rx="3" fill="{fill}"/>'
            )
            mon = datetime.date.fromisoformat(m["month"] + "-01").strftime("%b")[0]
            parts.append(
                f'<text x="{bx + bar_w / 2:.1f}" y="{plot_bot + 19:.1f}" fill="{MUTED}" '
                f'font-size="13" text-anchor="middle">{mon}</text>'
            )
            if m["total"] == peak:
                p_cls = "" if static else 'class="line" style="animation-delay:1.9s"'
                parts.append(
                    f'<text {p_cls} x="{bx + bar_w / 2:.1f}" y="{plot_bot - bh - 6:.1f}" '
                    f'fill="{INK}" font-size="13" font-weight="700" text-anchor="middle">{peak:,}</text>'
                )

    # Bottom status bar with blinking prompt
    footer_y = H - pad + 2
    prompt_txt = f"{username}@github:~$ "
    parts.append(
        f'<text x="{pad}" y="{footer_y:.1f}" font-size="13" fill="{MUTED}">'
        f'<tspan fill="{GREEN}">{html.escape(username)}@github</tspan>:~$ '
        f'<tspan fill="{INK}">ready</tspan></text>'
    )
    cur_x = pad + (len(prompt_txt) + 5.8) * 13 * 0.602
    cur_cls = "" if static else 'class="cur"'
    parts.append(f'<rect {cur_cls} x="{cur_x:.1f}" y="{footer_y - 12:.1f}" width="8" height="14" fill="{GREEN}"/>')
    parts.append("</svg>")

    out_path.write_text("".join(parts), encoding="utf-8")
    print(f"wrote {out_path} ({W}x{H})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate animated GitHub profile SVGs")
    parser.add_argument("--user", default=DEFAULT_USER, help="GitHub username")
    parser.add_argument("--static", action="store_true", help="Render static final state (for previews)")
    parser.add_argument("--out-dir", type=Path, default=ROOT, help="Output directory for SVG files")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    contrib_data, profile_data = fetch_github_data(args.user)
    (DATA_DIR / "contributions.json").write_text(json.dumps(contrib_data, indent=2), encoding="utf-8")
    (DATA_DIR / "profile_stats.json").write_text(json.dumps(profile_data, indent=2), encoding="utf-8")

    render_heatmap_svg(contrib_data, args.out_dir / "contrib-heatmap.svg", static=args.static)
    render_ascii_portrait_svg(profile_data, args.out_dir / "ascii-portrait.svg", static=args.static)
    render_stats_svg(contrib_data, profile_data, args.out_dir / "stats.svg", static=args.static)


if __name__ == "__main__":
    main()
