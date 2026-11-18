#!/usr/bin/env python3
"""
Fetch real GitHub data (contributions, streaks, PRs, Issues, stars, repository
languages, commit hours/weekdays, and pinned repositories) and generate a suite
of terminal-hacker animated SVGs with internal CSS @keyframes animations:
  - header-terminal.svg   (typing effect Linux/developer banner)
  - ascii-portrait.svg    (animated monochrome ASCII portrait)
  - neofetch-stats.svg    (neofetch + streaks + PRs/Issues/stars + monthly bars)
  - languages-radar.svg   (animated terminal language progress bars)
  - activity-clock.svg    (24h COT commit spectrum + day-of-week bars)
  - cards/repo-*.svg      (code-window cards for pinned repositories)
  - github-contribution-grid-snake-dark.svg (initial fallback until Platane/snk runs)
"""

import argparse
import datetime
import html
import io
import json
import os
import re
import sys
import textwrap
from pathlib import Path

import numpy as np
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageEnhance, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
ASSETS_DIR = ROOT / "assets"
CARDS_DIR = ROOT / "cards"

DEFAULT_USER = os.environ.get("GH_PROFILE_USER", "lastHunter956")
MONO_FONT = "Menlo, Consolas, 'DejaVu Sans Mono', 'Liberation Mono', monospace"
HEADERS = {"User-Agent": "lastHunter956-profile-generator/2.0"}
COT_TZ = datetime.timezone(datetime.timedelta(hours=-5))

# Neon hacker terminal color palette
BG_TOP = "#111722"
BG_BOT = "#0d1117"
HEAD_BG_TOP = "#0f1724"
HEAD_BG_BOT = "#090d14"
TILE_BG = "#161b22"
FRAME = "#30363d"
NEON_CYAN = "#22d3ee"
NEON_GREEN = "#39d353"
BAR_GREEN = "#26a641"
BLUE = "#58a6ff"
DARK_BLUE = "#1f6feb"
PURPLE = "#bc8cff"
GOLD = "#f2cc60"
RED = "#ff5f56"
INK = "#e6edf3"
ASCII_INK = "#c9d1d9"
MUTED = "#7d8590"

LANG_COLORS = {
    "Python": "#3572A5",
    "TypeScript": "#3178c6",
    "JavaScript": "#f1e05a",
    "Jupyter Notebook": "#DA5B0B",
    "Dart": "#00B4AB",
    "HTML": "#e34c26",
    "CSS": "#563d7c",
    "Java": "#b07219",
    "Shell": "#89e051",
    "C++": "#f34b7d",
    "Go": "#00ADD8",
}

PINNED_FALLBACK_DESC = {
    "finazaap": "Moneo — Personal finance management & intelligent expense analytics app.",
    "Repx": "Mobile application built with Dart & Flutter for tracking & workflows.",
    "Matematica-avanzadas-maestr-a": "Master's coursework in advanced mathematics & computational notebooks.",
    "actividad_1_DS": "Data Science foundations & interactive web visualization.",
    "actividad-de-constitucion": "Structured academic repository & technical documentation.",
    "parcialDS2": "Data Science II project & JavaScript data analysis tools.",
}

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
    pullRequests { totalCount }
    issues { totalCount }
    pinnedItems(first: 6, types: REPOSITORY) {
      nodes {
        ... on Repository {
          name
          description
          url
          stargazerCount
          forkCount
          primaryLanguage { name color }
        }
      }
    }
    repositories(first: 100, ownerAffiliations: OWNER, isFork: false, orderBy: {field: PUSHED_AT, direction: DESC}) {
      totalCount
      nodes {
        name
        description
        url
        stargazerCount
        forkCount
        primaryLanguage { name color }
        languages(first: 8, orderBy: {field: SIZE, direction: DESC}) {
          edges {
            size
            node { name color }
          }
        }
        defaultBranchRef {
          target {
            ... on Commit {
              history(first: 30) {
                nodes { committedDate }
              }
            }
          }
        }
      }
    }
    contributionsCollection {
      totalCommitContributions
      totalPullRequestContributions
      totalIssueContributions
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
        idx -= 1
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


def normalize_lang_breakdown(lang_bytes: dict[str, int], lang_colors: dict[str, str]) -> list[dict]:
    total = sum(lang_bytes.values()) or 1
    ordered = sorted(lang_bytes.items(), key=lambda x: -x[1])[:7]
    return [
        {
            "name": name,
            "bytes": size,
            "pct": round(size * 100.0 / total, 1),
            "color": lang_colors.get(name) or LANG_COLORS.get(name, BLUE),
        }
        for name, size in ordered
    ]


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

    lang_bytes: dict[str, int] = {}
    lang_colors: dict[str, str] = {}
    hours = [0] * 24
    weekdays = [0] * 7
    sampled_commits = 0

    for r in repos:
        for edge in (r.get("languages") or {}).get("edges") or []:
            lname = edge["node"]["name"]
            lcol = edge["node"].get("color") or LANG_COLORS.get(lname, BLUE)
            # Cap single-repo byte contribution to avoid .ipynb output JSON / vendored bloat
            lang_bytes[lname] = lang_bytes.get(lname, 0) + min(int(edge["size"]), 350_000)
            lang_colors[lname] = lcol

        target = ((r.get("defaultBranchRef") or {}).get("target") or {})
        for node in ((target.get("history") or {}).get("nodes") or []):
            dt_s = node.get("committedDate")
            if not dt_s:
                continue
            dt = datetime.datetime.fromisoformat(dt_s.replace("Z", "+00:00")).astimezone(COT_TZ)
            hours[dt.hour] += 1
            weekdays[dt.weekday()] += 1
            sampled_commits += 1

    pinned = []
    for p in (user.get("pinnedItems") or {}).get("nodes") or []:
        pl = p.get("primaryLanguage") or {}
        pinned.append({
            "name": p["name"],
            "description": (p.get("description") or "").strip(),
            "url": p["url"],
            "stars": int(p.get("stargazerCount", 0)),
            "forks": int(p.get("forkCount", 0)),
            "language": pl.get("name") or "Code",
            "color": pl.get("color") or LANG_COLORS.get(pl.get("name", ""), BLUE),
        })

    contrib_data = build_contributions_payload(username, days)
    profile_data = {
        "username": user["login"],
        "name": user.get("name") or "Jesús Martínez",
        "location": user.get("location") or "Cartagena, Colombia",
        "bio": user.get("bio") or "",
        "website": "jesusmartinez.tech",
        "avatar_url": user.get("avatarUrl") or "",
        "public_repos": int(user["repositories"]["totalCount"]),
        "total_stars": total_stars,
        "followers": int(user["followers"]["totalCount"]),
        "total_prs": int(user["pullRequests"]["totalCount"]),
        "total_issues": int(user["issues"]["totalCount"]),
        "year_commits": int(user["contributionsCollection"]["totalCommitContributions"]),
        "languages": normalize_lang_breakdown(lang_bytes, lang_colors),
        "hours": hours,
        "weekdays": weekdays,
        "sampled_commits": sampled_commits,
        "pinned_repos": pinned,
    }
    return contrib_data, profile_data


def scrape_pinned_repos(username: str, repo_map: dict[str, dict]) -> list[dict]:
    try:
        r = requests.get(f"https://github.com/{username}", headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        pinned = []
        for item in soup.select("li.pinned-item-list-item, div.pinned-item-list-item"):
            name_el = item.select_one("span.repo, a.text-bold")
            if not name_el:
                continue
            name = name_el.get_text(strip=True)
            desc_el = item.select_one("p.pinned-item-desc")
            lang_el = item.select_one('[itemprop="programmingLanguage"]')
            info = repo_map.get(name, {})
            lang = (lang_el.get_text(strip=True) if lang_el else "") or info.get("language") or "Code"
            pinned.append({
                "name": name,
                "description": (desc_el.get_text(strip=True) if desc_el else "") or (info.get("description") or ""),
                "url": info.get("html_url") or f"https://github.com/{username}/{name}",
                "stars": int(info.get("stargazers_count", 0)),
                "forks": int(info.get("forks_count", 0)),
                "language": lang,
                "color": LANG_COLORS.get(lang, BLUE),
            })
        if pinned:
            return pinned[:6]
    except Exception:
        pass
    return []


def fetch_via_public(username: str, token: str | None) -> tuple[dict, dict]:
    r_cal = requests.get(f"https://github.com/users/{username}/contributions", headers=HEADERS, timeout=30)
    r_cal.raise_for_status()
    soup = BeautifulSoup(r_cal.text, "html.parser")
    cells = soup.select("td.ContributionCalendar-day")
    if not cells:
        raise RuntimeError("No contribution calendar cells found")

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

    hdrs = api_headers(token)
    r_user = requests.get(f"https://api.github.com/users/{username}", headers=hdrs, timeout=20)
    r_user.raise_for_status()
    u = r_user.json()

    r_repos = requests.get(
        f"https://api.github.com/users/{username}/repos?per_page=100&sort=pushed",
        headers=hdrs,
        timeout=20,
    )
    repos = r_repos.json() if r_repos.status_code == 200 else []
    if not isinstance(repos, list):
        repos = []

    own_repos = [r for r in repos if not r.get("fork")]
    repo_map = {r["name"]: r for r in own_repos}
    total_stars = sum(r.get("stargazers_count", 0) for r in own_repos)

    # Language bytes from top 15 repos + capped fallback for remaining repos (avoids asset/binary skew)
    lang_bytes: dict[str, int] = {}
    for idx, r in enumerate(own_repos):
        if idx < 15:
            lr = requests.get(r["languages_url"], headers=hdrs, timeout=15)
            if lr.status_code == 200 and isinstance(lr.json(), dict):
                for lname, sz in lr.json().items():
                    lang_bytes[lname] = lang_bytes.get(lname, 0) + min(int(sz), 350_000)
                continue
        lang = r.get("language")
        if lang:
            lang_bytes[lang] = lang_bytes.get(lang, 0) + 40_000

    # Sample commit hours & weekdays from top 12 recently pushed repos
    hours = [0] * 24
    weekdays = [0] * 7
    sampled_commits = 0
    for r in own_repos[:12]:
        cr = requests.get(f"https://api.github.com/repos/{username}/{r['name']}/commits?per_page=50", headers=hdrs, timeout=15)
        if cr.status_code != 200 or not isinstance(cr.json(), list):
            continue
        for c in cr.json():
            dt_s = ((c.get("commit") or {}).get("author") or {}).get("date")
            if not dt_s:
                continue
            dt = datetime.datetime.fromisoformat(dt_s.replace("Z", "+00:00")).astimezone(COT_TZ)
            hours[dt.hour] += 1
            weekdays[dt.weekday()] += 1
            sampled_commits += 1

    # PRs & Issues total counts via search API
    total_prs = 0
    total_issues = 0
    r_pr = requests.get(f"https://api.github.com/search/issues?q=author:{username}+type:pr", headers=hdrs, timeout=15)
    if r_pr.status_code == 200:
        total_prs = int(r_pr.json().get("total_count", 0))
    r_is = requests.get(f"https://api.github.com/search/issues?q=author:{username}+type:issue", headers=hdrs, timeout=15)
    if r_is.status_code == 200:
        total_issues = int(r_is.json().get("total_count", 0))

    pinned = scrape_pinned_repos(username, repo_map)
    if not pinned:
        top_sorted = sorted(own_repos, key=lambda x: (-x.get("stargazers_count", 0), x["name"]))[:6]
        pinned = [
            {
                "name": r["name"],
                "description": r.get("description") or "",
                "url": r["html_url"],
                "stars": int(r.get("stargazers_count", 0)),
                "forks": int(r.get("forks_count", 0)),
                "language": r.get("language") or "Code",
                "color": LANG_COLORS.get(r.get("language") or "", BLUE),
            }
            for r in top_sorted
        ]

    contrib_data = build_contributions_payload(username, days)
    profile_data = {
        "username": u.get("login", username),
        "name": u.get("name") or "Jesús Martínez",
        "location": u.get("location") or "Cartagena, Bolívar, Colombia",
        "bio": u.get("bio") or "",
        "website": "jesusmartinez.tech",
        "avatar_url": u.get("avatar_url") or "",
        "public_repos": int(u.get("public_repos", len(own_repos))),
        "total_stars": total_stars,
        "followers": int(u.get("followers", 0)),
        "total_prs": total_prs,
        "total_issues": total_issues,
        "year_commits": contrib_data["total_contributions"],
        "languages": normalize_lang_breakdown(lang_bytes, LANG_COLORS),
        "hours": hours,
        "weekdays": weekdays,
        "sampled_commits": sampled_commits,
        "pinned_repos": pinned,
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
# 1. Animated Header Terminal Banner SVG
# ---------------------------------------------------------------------------
def render_header_terminal_svg(profile: dict, out_path: Path, static: bool = False) -> None:
    W, H = 860, 215
    pad = 24.0
    title_h = 30.0
    username = profile["username"]
    name = profile.get("name") or "Jesús Martínez"
    location = "Cartagena, Colombia"
    website = profile.get("website") or "jesusmartinez.tech"

    css = """
@keyframes fadeIn {
  0%   { opacity: 0; transform: translateY(6px); }
  100% { opacity: 1; transform: translateY(0); }
}
@keyframes typeClip {
  0%   { opacity: 0; clip-path: inset(0 100% 0 0); }
  5%   { opacity: 1; }
  100% { opacity: 1; clip-path: inset(0 0% 0 0); }
}
@keyframes blink {
  0%, 49%  { opacity: 1; }
  50%, 100% { opacity: 0; }
}
.sys  { opacity: 0; animation: fadeIn 0.35s ease-out 0.05s both; }
.cmd  { opacity: 0; animation: typeClip 0.75s steps(36, end) 0.25s both; }
.hero { opacity: 0; animation: typeClip 0.70s steps(24, end) 0.85s both; }
.role { opacity: 0; animation: typeClip 0.90s steps(48, end) 1.40s both; }
.tags { opacity: 0; animation: fadeIn 0.45s ease-out 2.10s both; }
.cur  { animation: blink 1s steps(1, end) infinite; }
@media (prefers-reduced-motion: reduce) {
  .sys, .cmd, .hero, .role, .tags { opacity: 1 !important; transform: none !important; clip-path: none !important; animation: none !important; }
  .cur { animation: none !important; opacity: 1 !important; }
}
""".strip()

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{MONO_FONT}">',
        f"<style>{css}</style>",
    ]
    parts.extend(
        terminal_chrome(
            W, H, f"{username}@github: ~/boot --system",
            "hdrbg", HEAD_BG_TOP, HEAD_BG_BOT, NEON_CYAN, frame_opacity=0.45, pad=20.0, title_h=title_h,
        )
    )

    c_sys = "" if static else 'class="sys"'
    c_cmd = "" if static else 'class="cmd"'
    c_hero = "" if static else 'class="hero"'
    c_role = "" if static else 'class="role"'
    c_tags = "" if static else 'class="tags"'
    c_cur = "" if static else 'class="cur"'

    parts.append(
        f'<text {c_sys} x="{pad}" y="58" font-size="12" fill="{MUTED}">'
        f'Linux {html.escape(username)}-node 6.12.0-sec #1 SMP PREEMPT_DYNAMIC x86_64 GNU/Linux</text>'
    )
    parts.append(
        f'<text {c_cmd} x="{pad}" y="82" font-size="14" fill="{MUTED}">'
        f'<tspan fill="{NEON_GREEN}">{html.escape(username)}@github</tspan>:~$ whoami --verbose</text>'
    )
    parts.append(
        f'<text {c_hero} x="{pad}" y="122" font-size="30" font-weight="700" fill="{INK}">'
        f'{html.escape(name)}  <tspan font-size="18" fill="{NEON_CYAN}">@{html.escape(username)}</tspan></text>'
    )
    role_text = "> Systems Engineer · Data Scientist · AI & InfoSec Builder"
    parts.append(
        f'<text {c_role} x="{pad}" y="152" font-size="15" fill="{NEON_GREEN}">{html.escape(role_text)}</text>'
    )
    cur_x = pad + len(role_text) * 15 * 0.602 + 6
    parts.append(f'<rect {c_cur} x="{cur_x:.1f}" y="139" width="9" height="16" fill="{NEON_CYAN}"/>')

    # Bottom status pills
    pills = [
        ("LOC", location, NEON_GREEN),
        ("WEB", website, NEON_CYAN),
        ("REPOS", f'{profile.get("public_repos", 70)} public', GOLD),
        ("MODE", "AI · Web · InfoSec", PURPLE),
    ]
    px = pad
    py = 172
    parts.append(f'<g {c_tags}>')
    for k, v, col in pills:
        label = f"[{k}] {v}"
        pw = len(label) * 12 * 0.602 + 20
        parts.append(f'<rect x="{px:.1f}" y="{py}" width="{pw:.1f}" height="24" rx="5" fill="{TILE_BG}" stroke="{FRAME}"/>')
        parts.append(
            f'<text x="{px + 10:.1f}" y="{py + 16}" font-size="12" fill="{INK}">'
            f'<tspan fill="{col}" font-weight="700">[{k}]</tspan> {html.escape(v)}</text>'
        )
        px += pw + 10
    parts.append("</g></svg>")

    out_path.write_text("".join(parts), encoding="utf-8")
    print(f"wrote {out_path} ({W}x{H})")


# ---------------------------------------------------------------------------
# 2. ASCII Portrait SVG
# ---------------------------------------------------------------------------
def prepare_portrait(src_img: Image.Image) -> Image.Image:
    arr = np.array(src_img.convert("RGB"), dtype=np.float64)
    h, w, _ = arr.shape
    ys_idx, xs_idx = np.mgrid[0:h, 0:w]
    gray = 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]
    sat = np.max(arr, axis=2) - np.min(arr, axis=2)

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
        xtx = np.einsum("ij,ik->jk", X, X)
        xty = np.einsum("ij,i->j", X, gray[pts])
        coef = np.linalg.solve(xtx + 1e-6 * np.eye(6), xty)
        bg_est = np.clip(
            coef[0] + coef[1] * nx + coef[2] * ny + coef[3] * (nx ** 2) + coef[4] * (ny ** 2) + coef[5] * (nx * ny),
            0.0, 255.0,
        )
    else:
        bg_est = np.full_like(gray, 28.0)

    diff = gray - bg_est
    in_head_x = (xs_idx > w * 0.22) & (xs_idx < w * 0.78)
    hair_mask = (diff < -10.0) & in_head_x & (ys_idx > h * 0.06) & (ys_idx < h * 0.56)
    shirt_mask = (ys_idx > h * 0.74) & (diff < -8.0) & (xs_idx > w * 0.03) & (xs_idx < w * 0.97)
    fg = (sat > 11.0) | (diff > 16.0) | hair_mask | shirt_mask

    fg_img = Image.fromarray((fg * 255).astype(np.uint8))
    fg_clean = fg_img.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))
    mask = np.array(fg_clean.filter(ImageFilter.GaussianBlur(2.5)), dtype=np.float64) / 255.0
    mask = np.clip((mask - 0.22) / 0.78, 0.0, 1.0)

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
    raise FileNotFoundError("No source portrait image found")


def render_ascii_portrait_svg(profile: dict, out_path: Path, static: bool = False) -> None:
    username = profile["username"]
    display_name = profile.get("name") or "Jesús Martínez"

    cols = 148
    art_w_target = 800.0
    cell_w = art_w_target / cols
    cell_h = cell_w * 15.0 / 8.0
    rows = round(cols * 8.0 / 15.0)
    ramp = " .`:-=+*cs#%@"

    pad = 20.0
    title_h = 30.0
    art_h = rows * cell_h
    canvas_w, canvas_h = 840, 880

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
            idx = max(1, min(len(ramp) - 1, int(adj * (len(ramp) - 1) + 0.5)))
            chars.append(ramp[idx])
        rows_txt.append("".join(chars))

    row_dur = 0.18
    stagger = 4.2 / rows

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
        f'viewBox="0 0 {canvas_w} {canvas_h}" font-family="{MONO_FONT}">',
        f"<style>{css}</style>",
    ]
    parts.extend(
        terminal_chrome(
            canvas_w, canvas_h, f"{username}@github: ~$ ./portrait.sh",
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
        cls_attr = "" if static else f'class="row" style="animation-delay:{delay:.3f}s"'
        parts.append(
            f'<text {cls_attr} xml:space="preserve" x="{pad}" y="{y:.1f}" fill="{ASCII_INK}" '
            f'font-size="{font_size:.2f}">{html.escape(stripped)}</text>'
        )

    status_line_y = title_h + art_h + pad * 0.35
    status_y = status_line_y + 19
    parts.append(f'<line x1="0" y1="{status_line_y:.1f}" x2="{canvas_w}" y2="{status_line_y:.1f}" stroke="{FRAME}"/>')
    prompt_prefix = f"{username}@github:~$ whoami "
    parts.append(
        f'<text x="{pad}" y="{status_y:.1f}" fill="{MUTED}" font-size="13">'
        f'<tspan fill="{NEON_GREEN}">{html.escape(username)}@github</tspan>:~$ whoami '
        f'<tspan fill="{INK}">{html.escape(display_name)}</tspan></text>'
    )
    cursor_x = pad + (len(prompt_prefix) + len(display_name) + 0.8) * 13 * 0.602
    cur_cls = "" if static else 'class="cur"'
    parts.append(f'<rect {cur_cls} x="{cursor_x:.1f}" y="{status_y - 12:.1f}" width="8" height="14" fill="{NEON_GREEN}"/>')
    parts.append("</svg>")

    out_path.write_text("".join(parts), encoding="utf-8")
    print(f"wrote {out_path} ({canvas_w}x{canvas_h})")


# ---------------------------------------------------------------------------
# 3. Neofetch Stats SVG (Streaks + Commits/PRs/Issues/Stars + Monthly Bars)
# ---------------------------------------------------------------------------
def short_date(d: str | None) -> str:
    if not d:
        return "—"
    return datetime.date.fromisoformat(d).strftime("%b %-d")


def span_label(s: dict) -> str:
    if not s.get("length"):
        return "start today!"
    return f'{short_date(s["start"])} – {short_date(s["end"])}'


def render_neofetch_stats_svg(contrib: dict, profile: dict, out_path: Path, static: bool = False) -> None:
    W, H = 840, 880  # Matches ascii-portrait.svg height/width
    pad = 24.0
    title_h = 30.0
    username = profile["username"]

    cur = contrib["current_streak"]
    lng = contrib["longest_streak"]
    best = contrib["best_day"]
    n_days = len(contrib["days"]) or 365

    neofetch_rows = [
        ("Name", profile.get("name") or "Jesús Martínez", INK),
        ("Role", "Systems Eng · Data Science · AI", NEON_CYAN),
        ("Location", "Cartagena, Bolívar, Colombia", INK),
        ("Web", profile.get("website") or "jesusmartinez.tech", NEON_CYAN),
        ("Repos", f'{profile.get("public_repos", 0)} public · {profile.get("followers", 0)} followers', GOLD),
        ("Focus", "AI · Full Stack · Information Security", PURPLE),
    ]

    tiles = [
        ("current streak", f'{cur["length"]}d', span_label(cur), NEON_GREEN),
        ("longest streak", f'{lng["length"]}d', span_label(lng), NEON_CYAN),
        ("contributions", f'{contrib["total_contributions"]:,}', f'{contrib["active_days"]}/{n_days} active days', GOLD),
        ("pull requests", f'{profile.get("total_prs", 0):,}', "merged & open", BLUE),
        ("issues", f'{profile.get("total_issues", 0):,}', "tracked & solved", PURPLE),
        ("stars earned", f'{profile.get("total_stars", 0):,}', f'best day: {best["count"]} ({short_date(best["date"])})', GOLD),
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
  50%, 100% { opacity: 0; }
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
""".strip()

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{MONO_FONT}">',
        f"<style>{css}</style>",
    ]
    parts.extend(
        terminal_chrome(
            W, H, f"{username}@github: ~$ neofetch --stats",
            "sbg", BG_TOP, BG_BOT, FRAME, pad=20.0, title_h=title_h,
        )
    )

    y = title_h + 32
    parts.append(
        f'<text x="{pad}" y="{y}" font-size="15" fill="{MUTED}">'
        f'<tspan fill="{NEON_GREEN}">{html.escape(username)}@github</tspan>:~$ neofetch --stats</text>'
    )
    y += 28
    parts.append(
        f'<text x="{pad}" y="{y}" font-size="19" font-weight="700" fill="{NEON_CYAN}">'
        f'{html.escape(username)}<tspan fill="{MUTED}">@</tspan><tspan fill="{NEON_GREEN}">github</tspan></text>'
    )
    y += 12
    parts.append(f'<line x1="{pad}" y1="{y}" x2="{pad + 320}" y2="{y}" stroke="{FRAME}" stroke-width="1.2"/>')

    y += 26
    for i, (key, val, val_col) in enumerate(neofetch_rows):
        cls_attr = "" if static else f'class="line" style="animation-delay:{0.06 + i * 0.06:.2f}s"'
        parts.append(
            f'<text {cls_attr} xml:space="preserve" x="{pad}" y="{y}" font-size="16">'
            f'<tspan fill="{NEON_GREEN}" font-weight="700">{html.escape(key):<9}</tspan>'
            f'<tspan fill="{MUTED}">: </tspan>'
            f'<tspan fill="{val_col}">{html.escape(str(val))}</tspan></text>'
        )
        y += 26

    # 8-color swatch bar
    y += 4
    swatches = [BG_BOT, RED, NEON_GREEN, GOLD, DARK_BLUE, PURPLE, NEON_CYAN, INK]
    sw_cls = "" if static else 'class="line" style="animation-delay:0.45s"'
    parts.append(f'<g {sw_cls}>')
    for i, sw in enumerate(swatches):
        parts.append(
            f'<rect x="{pad + i * 32}" y="{y}" width="25" height="13" rx="3" '
            f'fill="{sw}" stroke="{FRAME}" stroke-width="0.7"/>'
        )
    parts.append("</g>")

    y += 28
    parts.append(f'<line x1="{pad}" y1="{y}" x2="{W - pad}" y2="{y}" stroke="{FRAME}"/>')
    y += 18

    # 3 cols x 2 rows of stat cards
    gap = 12.0
    card_w = (W - pad * 2 - gap * 2) / 3
    card_h = 104.0
    for i, (label, val, sub, accent) in enumerate(tiles):
        col_i, row_i = i % 3, i // 3
        cx = pad + col_i * (card_w + gap)
        cy = y + row_i * (card_h + gap)
        delay = 0.55 + i * 0.08
        cls_attr = "" if static else f'class="card" style="animation-delay:{delay:.2f}s"'
        parts.append(f'<g {cls_attr}>')
        parts.append(
            f'<rect x="{cx:.1f}" y="{cy:.1f}" width="{card_w:.1f}" height="{card_h:.1f}" '
            f'rx="10" fill="{TILE_BG}" stroke="{FRAME}"/>'
        )
        parts.append(f'<text x="{cx + 16:.1f}" y="{cy + 27:.1f}" fill="{MUTED}" font-size="13">$ {html.escape(label)}</text>')
        parts.append(f'<text x="{cx + 16:.1f}" y="{cy + 65:.1f}" fill="{accent}" font-size="28" font-weight="700">{html.escape(val)}</text>')
        parts.append(f'<text x="{cx + 16:.1f}" y="{cy + 89:.1f}" fill="{MUTED}" font-size="12">{html.escape(sub)}</text>')
        parts.append("</g>")

    # Monthly bars panel
    chart_top = y + 2 * card_h + gap + 14
    chart_h = H - pad - 36 - chart_top
    chart_w = W - pad * 2
    c_cls = "" if static else 'class="card" style="animation-delay:1.05s"'
    parts.append(f'<g {c_cls}>')
    parts.append(
        f'<rect x="{pad}" y="{chart_top:.1f}" width="{chart_w}" height="{chart_h:.1f}" '
        f'rx="10" fill="{TILE_BG}" stroke="{FRAME}"/>'
    )
    parts.append(f'<text x="{pad + 18}" y="{chart_top + 28:.1f}" fill="{MUTED}" font-size="14">$ contributions / month</text>')
    parts.append("</g>")

    monthly = contrib["monthly"][-12:]
    if monthly:
        plot_top = chart_top + 52
        plot_bot = chart_top + chart_h - 30
        plot_l = pad + 20
        plot_r = W - pad - 20
        slot = (plot_r - plot_l) / len(monthly)
        bar_w = slot * 0.58
        peak = max(m["total"] for m in monthly) or 1
        for i, m in enumerate(monthly):
            bh = max(4.0, (plot_bot - plot_top) * m["total"] / peak)
            bx = plot_l + i * slot + (slot - bar_w) / 2
            fill = NEON_GREEN if m["total"] == peak else BAR_GREEN
            b_cls = "" if static else f'class="bar" style="animation-delay:{1.15 + i * 0.05:.2f}s"'
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
                p_cls = "" if static else 'class="line" style="animation-delay:1.80s"'
                parts.append(
                    f'<text {p_cls} x="{bx + bar_w / 2:.1f}" y="{plot_bot - bh - 7:.1f}" '
                    f'fill="{INK}" font-size="13" font-weight="700" text-anchor="middle">{peak:,}</text>'
                )

    footer_y = H - pad + 4
    prompt_txt = f"{username}@github:~$ "
    parts.append(
        f'<text x="{pad}" y="{footer_y:.1f}" font-size="13" fill="{MUTED}">'
        f'<tspan fill="{NEON_GREEN}">{html.escape(username)}@github</tspan>:~$ '
        f'<tspan fill="{INK}">system online</tspan></text>'
    )
    cur_x = pad + (len(prompt_txt) + 13.8) * 13 * 0.602
    cur_cls = "" if static else 'class="cur"'
    parts.append(f'<rect {cur_cls} x="{cur_x:.1f}" y="{footer_y - 12:.1f}" width="8" height="14" fill="{NEON_GREEN}"/>')
    parts.append("</svg>")

    out_path.write_text("".join(parts), encoding="utf-8")
    print(f"wrote {out_path} ({W}x{H})")


# ---------------------------------------------------------------------------
# 4. Languages Radar SVG (Animated Terminal Progress Bars)
# ---------------------------------------------------------------------------
def render_languages_radar_svg(profile: dict, out_path: Path, static: bool = False) -> None:
    W, H = 840, 560
    pad = 24.0
    title_h = 30.0
    username = profile["username"]
    langs = profile.get("languages") or [
        {"name": "Python", "pct": 42.0, "color": "#3572A5"},
        {"name": "TypeScript", "pct": 24.0, "color": "#3178c6"},
        {"name": "Jupyter Notebook", "pct": 16.0, "color": "#DA5B0B"},
        {"name": "Dart", "pct": 10.0, "color": "#00B4AB"},
        {"name": "HTML", "pct": 5.0, "color": "#e34c26"},
        {"name": "JavaScript", "pct": 3.0, "color": "#f1e05a"},
    ]
    langs = langs[:7]

    css = """
@keyframes fadeSlide {
  0%   { opacity: 0; transform: translateX(-8px); }
  100% { opacity: 1; transform: translateX(0); }
}
@keyframes fillBar {
  0%   { transform: scaleX(0); }
  100% { transform: scaleX(1); }
}
@keyframes blink {
  0%, 49%  { opacity: 1; }
  50%, 100% { opacity: 0; }
}
.row { opacity: 0; animation: fadeSlide 0.35s ease-out both; }
.bar { transform-box: fill-box; transform-origin: left; transform: scaleX(0); animation: fillBar 0.75s cubic-bezier(.2,.8,.2,1) both; }
.cur { animation: blink 1s steps(1, end) infinite; }
@media (prefers-reduced-motion: reduce) {
  .row { opacity: 1 !important; transform: none !important; animation: none !important; }
  .bar { transform: scaleX(1) !important; animation: none !important; }
  .cur { animation: none !important; opacity: 1 !important; }
}
""".strip()

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{MONO_FONT}">',
        f"<style>{css}</style>",
    ]
    parts.extend(
        terminal_chrome(
            W, H, f"{username}@github: ~$ ./languages.sh --radar",
            "lbg", BG_TOP, BG_BOT, FRAME, pad=20.0, title_h=title_h,
        )
    )

    y = title_h + 34
    parts.append(
        f'<text x="{pad}" y="{y}" font-size="15" fill="{MUTED}">'
        f'<tspan fill="{NEON_GREEN}">{html.escape(username)}@github</tspan>:~$ scan-langs '
        f'<tspan fill="{NEON_CYAN}">--repos={profile.get("public_repos", 70)}</tspan></text>'
    )

    # Stacked overview progress bar
    stack_y = y + 22
    stack_w = W - pad * 2
    parts.append(f'<rect x="{pad}" y="{stack_y}" width="{stack_w}" height="14" rx="7" fill="{TILE_BG}" stroke="{FRAME}"/>')
    sx = pad
    total_pct = sum(l["pct"] for l in langs) or 100.0
    for i, l in enumerate(langs):
        seg_w = max(4.0, stack_w * (l["pct"] / total_pct))
        if i == len(langs) - 1:
            seg_w = max(4.0, (pad + stack_w) - sx)
        b_cls = "" if static else f'class="bar" style="animation-delay:{0.10 + i * 0.06:.2f}s"'
        rx = 'rx="4"' if (i == 0 or i == len(langs) - 1) else ""
        parts.append(f'<rect {b_cls} x="{sx:.1f}" y="{stack_y + 1}" width="{seg_w:.1f}" height="12" {rx} fill="{l["color"]}"/>')
        sx += seg_w

    # Individual animated language progress bars
    row_y = stack_y + 48
    bar_x = 240.0
    bar_max_w = W - pad - bar_x - 86.0
    max_pct = max(l["pct"] for l in langs) or 1.0
    row_step = 52.0

    for i, l in enumerate(langs):
        delay = 0.20 + i * 0.09
        r_cls = "" if static else f'class="row" style="animation-delay:{delay:.2f}s"'
        b_cls = "" if static else f'class="bar" style="animation-delay:{delay + 0.12:.2f}s"'

        parts.append(f'<g {r_cls}>')
        parts.append(f'<circle cx="{pad + 8}" cy="{row_y - 5}" r="5.5" fill="{l["color"]}"/>')
        parts.append(f'<text x="{pad + 22}" y="{row_y}" font-size="16" font-weight="700" fill="{INK}">{html.escape(l["name"])}</text>')
        parts.append(f'<rect x="{bar_x}" y="{row_y - 14}" width="{bar_max_w:.1f}" height="16" rx="5" fill="{TILE_BG}" stroke="{FRAME}"/>')
        fill_w = max(8.0, bar_max_w * (l["pct"] / max_pct))
        parts.append(f'<rect {b_cls} x="{bar_x}" y="{row_y - 14}" width="{fill_w:.1f}" height="16" rx="5" fill="{l["color"]}"/>')
        parts.append(
            f'<text x="{W - pad}" y="{row_y}" font-size="15" font-weight="700" fill="{NEON_CYAN}" text-anchor="end">'
            f'{l["pct"]:.1f}%</text>'
        )
        parts.append("</g>")
        row_y += row_step

    footer_y = H - pad + 2
    parts.append(f'<line x1="{pad}" y1="{footer_y - 28}" x2="{W - pad}" y2="{footer_y - 28}" stroke="{FRAME}"/>')
    top_names = " / ".join(l["name"] for l in langs[:3])
    summary = f"primary: {top_names}"
    parts.append(
        f'<text x="{pad}" y="{footer_y:.1f}" font-size="13" fill="{MUTED}">'
        f'<tspan fill="{NEON_GREEN}">{html.escape(username)}@github</tspan>:~$ '
        f'<tspan fill="{INK}">{html.escape(summary)}</tspan></text>'
    )
    cur_x = pad + (len(f"{username}@github:~$ ") + len(summary) + 0.8) * 13 * 0.602
    cur_cls = "" if static else 'class="cur"'
    parts.append(f'<rect {cur_cls} x="{cur_x:.1f}" y="{footer_y - 12:.1f}" width="8" height="14" fill="{NEON_CYAN}"/>')
    parts.append("</svg>")

    out_path.write_text("".join(parts), encoding="utf-8")
    print(f"wrote {out_path} ({W}x{H})")


# ---------------------------------------------------------------------------
# 5. Activity Clock SVG (24h COT Hourly Spectrum + Weekday Breakdown)
# ---------------------------------------------------------------------------
def render_activity_clock_svg(profile: dict, out_path: Path, static: bool = False) -> None:
    W, H = 840, 560
    pad = 24.0
    title_h = 30.0
    username = profile["username"]

    hours = profile.get("hours") or [2] * 24
    weekdays = profile.get("weekdays") or [10] * 7
    sampled = int(profile.get("sampled_commits") or sum(hours))

    peak_h = max(range(24), key=lambda h: hours[h])
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    peak_d = max(range(7), key=lambda d: weekdays[d])

    persona = "Night Owl" if (peak_h >= 20 or peak_h <= 4) else ("Early Bird" if peak_h < 11 else "Daylight Builder")

    css = """
@keyframes fadeSlide {
  0%   { opacity: 0; transform: translateY(8px); }
  100% { opacity: 1; transform: translateY(0); }
}
@keyframes growY {
  0%   { transform: scaleY(0); }
  100% { transform: scaleY(1); }
}
@keyframes growX {
  0%   { transform: scaleX(0); }
  100% { transform: scaleX(1); }
}
@keyframes blink {
  0%, 49%  { opacity: 1; }
  50%, 100% { opacity: 0; }
}
.blk  { opacity: 0; animation: fadeSlide 0.38s ease-out both; }
.vbar { transform-box: fill-box; transform-origin: bottom; transform: scaleY(0); animation: growY 0.55s ease-out both; }
.hbar { transform-box: fill-box; transform-origin: left; transform: scaleX(0); animation: growX 0.65s ease-out both; }
.cur  { animation: blink 1s steps(1, end) infinite; }
@media (prefers-reduced-motion: reduce) {
  .blk { opacity: 1 !important; transform: none !important; animation: none !important; }
  .vbar { transform: scaleY(1) !important; animation: none !important; }
  .hbar { transform: scaleX(1) !important; animation: none !important; }
  .cur { animation: none !important; opacity: 1 !important; }
}
""".strip()

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{MONO_FONT}">',
        f"<style>{css}</style>",
    ]
    parts.extend(
        terminal_chrome(
            W, H, f"{username}@github: ~$ ./clock.sh --tz=UTC-5",
            "cbg", BG_TOP, BG_BOT, FRAME, pad=20.0, title_h=title_h,
        )
    )

    y = title_h + 34
    parts.append(
        f'<text x="{pad}" y="{y}" font-size="15" fill="{MUTED}">'
        f'<tspan fill="{NEON_GREEN}">{html.escape(username)}@github</tspan>:~$ activity-clock '
        f'<tspan fill="{NEON_CYAN}">--commits={sampled} --tz=COT</tspan></text>'
    )

    # Top panel: 24-hour coding spectrum
    box1_y = y + 18
    box1_h = 200.0
    box_w = W - pad * 2
    b1_cls = "" if static else 'class="blk" style="animation-delay:0.08s"'
    parts.append(f'<g {b1_cls}>')
    parts.append(f'<rect x="{pad}" y="{box1_y}" width="{box_w}" height="{box1_h}" rx="10" fill="{TILE_BG}" stroke="{FRAME}"/>')
    parts.append(f'<text x="{pad + 18}" y="{box1_y + 26}" font-size="14" fill="{MUTED}">$ commits by hour (00h - 23h UTC-5)</text>')
    parts.append(
        f'<text x="{W - pad - 18}" y="{box1_y + 26}" font-size="13" font-weight="700" fill="{NEON_CYAN}" text-anchor="end">'
        f'Peak: {peak_h:02d}:00 · {persona}</text>'
    )
    parts.append("</g>")

    plot_l = pad + 22
    plot_r = W - pad - 22
    plot_top = box1_y + 48
    plot_bot = box1_y + box1_h - 32
    slot = (plot_r - plot_l) / 24
    bar_w = slot * 0.64
    max_h = max(hours) or 1

    for hr, cnt in enumerate(hours):
        bh = max(4.0, (plot_bot - plot_top) * cnt / max_h)
        bx = plot_l + hr * slot + (slot - bar_w) / 2
        if cnt == max_h and cnt > 0:
            col = NEON_CYAN
        elif cnt >= max_h * 0.55 and cnt > 0:
            col = NEON_GREEN
        elif cnt > 0:
            col = DARK_BLUE
        else:
            col = "#21262d"
        v_cls = "" if static else f'class="vbar" style="animation-delay:{0.18 + hr * 0.025:.3f}s"'
        parts.append(
            f'<rect {v_cls} x="{bx:.1f}" y="{plot_bot - bh:.1f}" width="{bar_w:.1f}" '
            f'height="{bh:.1f}" rx="3" fill="{col}"/>'
        )
        if hr % 3 == 0 or hr == 23:
            parts.append(
                f'<text x="{bx + bar_w / 2:.1f}" y="{plot_bot + 18:.1f}" font-size="11" fill="{MUTED}" '
                f'text-anchor="middle">{hr:02d}</text>'
            )

    # Bottom panel: 7-day breakdown
    box2_y = box1_y + box1_h + 16
    box2_h = H - pad - 38 - box2_y
    b2_cls = "" if static else 'class="blk" style="animation-delay:0.55s"'
    parts.append(f'<g {b2_cls}>')
    parts.append(f'<rect x="{pad}" y="{box2_y}" width="{box_w}" height="{box2_h}" rx="10" fill="{TILE_BG}" stroke="{FRAME}"/>')
    parts.append(f'<text x="{pad + 18}" y="{box2_y + 26}" font-size="14" fill="{MUTED}">$ commits by day of week</text>')
    parts.append(
        f'<text x="{W - pad - 18}" y="{box2_y + 26}" font-size="13" font-weight="700" fill="{NEON_GREEN}" text-anchor="end">'
        f'Most active: {day_names[peak_d]} ({weekdays[peak_d]})</text>'
    )
    parts.append("</g>")

    total_wd = sum(weekdays) or 1
    max_wd = max(weekdays) or 1
    row_start = box2_y + 46
    row_step = (box2_h - 56) / 7
    hbar_x = pad + 74
    hbar_max = box_w - 165

    for di, dname in enumerate(day_names):
        cnt = weekdays[di]
        ry = row_start + di * row_step
        pct = cnt * 100.0 / total_wd
        fw = max(6.0, hbar_max * cnt / max_wd)
        col = NEON_GREEN if di == peak_d else (NEON_CYAN if cnt >= max_wd * 0.6 else BLUE)
        h_cls = "" if static else f'class="hbar" style="animation-delay:{0.65 + di * 0.06:.2f}s"'

        parts.append(f'<text x="{pad + 18}" y="{ry + 11:.1f}" font-size="13" font-weight="700" fill="{INK}">{dname}</text>')
        parts.append(f'<rect x="{hbar_x}" y="{ry:.1f}" width="{hbar_max:.1f}" height="12" rx="4" fill="#0d1117" stroke="{FRAME}" stroke-width="0.6"/>')
        parts.append(f'<rect {h_cls} x="{hbar_x}" y="{ry:.1f}" width="{fw:.1f}" height="12" rx="4" fill="{col}"/>')
        parts.append(
            f'<text x="{W - pad - 18}" y="{ry + 11:.1f}" font-size="12" fill="{MUTED}" text-anchor="end">'
            f'{cnt} ({pct:.0f}%)</text>'
        )

    footer_y = H - pad + 2
    summary = f"peak window: {peak_h:02d}:00 COT · {day_names[peak_d]}"
    parts.append(
        f'<text x="{pad}" y="{footer_y:.1f}" font-size="13" fill="{MUTED}">'
        f'<tspan fill="{NEON_GREEN}">{html.escape(username)}@github</tspan>:~$ '
        f'<tspan fill="{INK}">{html.escape(summary)}</tspan></text>'
    )
    cur_x = pad + (len(f"{username}@github:~$ ") + len(summary) + 0.8) * 13 * 0.602
    cur_cls = "" if static else 'class="cur"'
    parts.append(f'<rect {cur_cls} x="{cur_x:.1f}" y="{footer_y - 12:.1f}" width="8" height="14" fill="{NEON_GREEN}"/>')
    parts.append("</svg>")

    out_path.write_text("".join(parts), encoding="utf-8")
    print(f"wrote {out_path} ({W}x{H})")


# ---------------------------------------------------------------------------
# 6. Code-Window Pinned Project Cards (cards/repo-1.svg .. repo-6.svg)
# ---------------------------------------------------------------------------
def render_repo_cards(profile: dict, cards_dir: Path, static: bool = False) -> None:
    cards_dir.mkdir(parents=True, exist_ok=True)
    repos = (profile.get("pinned_repos") or [])[:6]
    W, H = 420, 210
    pad = 18.0
    title_h = 28.0

    css = """
@keyframes cardIn {
  0%   { opacity: 0; transform: translateY(8px); }
  100% { opacity: 1; transform: translateY(0); }
}
.wrap { opacity: 0; animation: cardIn 0.45s ease-out both; }
@media (prefers-reduced-motion: reduce) {
  .wrap { opacity: 1 !important; transform: none !important; animation: none !important; }
}
""".strip()

    for idx, repo in enumerate(repos, start=1):
        name = repo["name"]
        desc = repo.get("description") or PINNED_FALLBACK_DESC.get(
            name, f"{repo.get('language', 'Software')} repository by @{profile['username']}."
        )
        wrapped = textwrap.wrap(desc, width=44)[:2]
        if len(wrapped) == 2 and len(desc) > 88:
            wrapped[1] = wrapped[1][:41].rstrip() + "..."
        lang = repo.get("language") or "Code"
        lcol = repo.get("color") or LANG_COLORS.get(lang, BLUE)
        stars = int(repo.get("stars", 0))
        forks = int(repo.get("forks", 0))

        delay = (idx - 1) * 0.08
        w_cls = "" if static else f'class="wrap" style="animation-delay:{delay:.2f}s"'

        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{MONO_FONT}">',
            f"<style>{css}</style>",
            f'<g {w_cls}>',
        ]
        parts.extend(
            terminal_chrome(
                W, H, f"~/repos/{name[:26]}",
                f"rbg{idx}", BG_TOP, BG_BOT, FRAME, pad=16.0, title_h=title_h,
            )
        )
        # Subtle neon top highlight
        parts.append(f'<line x1="12" y1="1" x2="{W - 12}" y2="1" stroke="{lcol}" stroke-width="2" stroke-opacity="0.75"/>')

        # Title + PUBLIC badge
        disp_name = name if len(name) <= 24 else name[:22] + ".."
        parts.append(f'<text x="{pad}" y="62" font-size="17" font-weight="700" fill="{NEON_CYAN}">{html.escape(disp_name)}</text>')
        parts.append(f'<rect x="{W - pad - 60}" y="46" width="60" height="20" rx="10" fill="{TILE_BG}" stroke="{FRAME}"/>')
        parts.append(f'<text x="{W - pad - 30}" y="60" font-size="10" fill="{NEON_GREEN}" text-anchor="middle">PUBLIC</text>')

        # Description lines
        dy = 94
        for line in wrapped:
            parts.append(f'<text x="{pad}" y="{dy}" font-size="13" fill="{ASCII_INK}">{html.escape(line)}</text>')
            dy += 22

        # Bottom separator & metadata
        parts.append(f'<line x1="{pad}" y1="162" x2="{W - pad}" y2="162" stroke="{FRAME}" stroke-opacity="0.6"/>')
        parts.append(f'<circle cx="{pad + 6}" cy="184" r="5" fill="{lcol}"/>')
        parts.append(f'<text x="{pad + 18}" y="188" font-size="12" fill="{INK}">{html.escape(lang)}</text>')
        meta = f"stars:{stars}  forks:{forks}"
        parts.append(f'<text x="{W - pad}" y="188" font-size="12" fill="{MUTED}" text-anchor="end">{html.escape(meta)}</text>')
        parts.append("</g></svg>")

        out_file = cards_dir / f"repo-{idx}.svg"
        out_file.write_text("".join(parts), encoding="utf-8")
        print(f"wrote {out_file} ({W}x{H})")


# ---------------------------------------------------------------------------
# 7. Initial Snake SVG Fallback (overwritten automatically by Platane/snk)
# ---------------------------------------------------------------------------
def render_initial_snake_svg(contrib: dict, out_path: Path) -> None:
    """Create a valid dark contribution grid snake SVG as initial placeholder before Platane/snk runs."""
    days = contrib["days"]
    first = datetime.date.fromisoformat(days[0]["date"])
    lead_pad = (first.weekday() + 1) % 7
    grid: list[list] = []
    col: list = [None] * lead_pad
    for d in days:
        dt = datetime.date.fromisoformat(d["date"])
        wd = (dt.weekday() + 1) % 7
        while len(col) < wd:
            col.append(None)
        col.append(int(d.get("level", 0)))
        if len(col) == 7:
            grid.append(col)
            col = []
    if col:
        while len(col) < 7:
            col.append(None)
        grid.append(col)

    palette = ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353", "#39d353"]
    cell, gap, pad = 12, 3, 18
    step = cell + gap
    W = pad * 2 + len(grid) * step
    H = pad * 2 + 7 * step

    # Build a serpentine path across the grid for the CSS-animated snake preview
    path_pts = []
    for ci in range(len(grid)):
        rows_order = range(7) if ci % 2 == 0 else range(6, -1, -1)
        for ri in rows_order:
            x = pad + ci * step + cell / 2
            y = pad + ri * step + cell / 2
            path_pts.append(f"{x:.1f},{y:.1f}")
    path_d = "M " + " L ".join(path_pts)

    css = """
@keyframes snakeMove {
  0%   { stroke-dashoffset: 6200; }
  100% { stroke-dashoffset: 0; }
}
.snake {
  stroke-dasharray: 95 6200;
  animation: snakeMove 14s linear infinite;
}
@media (prefers-reduced-motion: reduce) {
  .snake { animation: none !important; stroke-dasharray: none !important; }
}
""".strip()

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
        f"<style>{css}</style>",
        f'<rect width="{W}" height="{H}" rx="10" fill="#0d1117"/>',
    ]
    for ci, column in enumerate(grid):
        for ri, lvl in enumerate(column):
            if lvl is None:
                continue
            x = pad + ci * step
            y = pad + ri * step
            parts.append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2.5" fill="{palette[lvl]}"/>')

    parts.append(
        f'<path class="snake" d="{path_d}" fill="none" stroke="{NEON_CYAN}" stroke-width="7" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
    )
    parts.append("</svg>")
    out_path.write_text("".join(parts), encoding="utf-8")
    print(f"wrote initial {out_path} ({W}x{H})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate viral terminal GitHub profile SVGs")
    parser.add_argument("--user", default=DEFAULT_USER, help="GitHub username")
    parser.add_argument("--static", action="store_true", help="Render final static frames for previews")
    parser.add_argument("--out-dir", type=Path, default=ROOT, help="Output directory")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    contrib_data, profile_data = fetch_github_data(args.user)
    (DATA_DIR / "contributions.json").write_text(json.dumps(contrib_data, indent=2), encoding="utf-8")
    (DATA_DIR / "profile_stats.json").write_text(json.dumps(profile_data, indent=2), encoding="utf-8")

    render_header_terminal_svg(profile_data, args.out_dir / "header-terminal.svg", static=args.static)
    render_ascii_portrait_svg(profile_data, args.out_dir / "ascii-portrait.svg", static=args.static)
    render_neofetch_stats_svg(contrib_data, profile_data, args.out_dir / "neofetch-stats.svg", static=args.static)
    render_languages_radar_svg(profile_data, args.out_dir / "languages-radar.svg", static=args.static)
    render_activity_clock_svg(profile_data, args.out_dir / "activity-clock.svg", static=args.static)
    render_repo_cards(profile_data, args.out_dir / "cards", static=args.static)

    snake_path = args.out_dir / "github-contribution-grid-snake-dark.svg"
    if not snake_path.exists() or args.static:
        render_initial_snake_svg(contrib_data, snake_path)


if __name__ == "__main__":
    main()
