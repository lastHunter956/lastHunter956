#!/usr/bin/env python3
"""
Fetch real GitHub data (contributions, streaks, PRs, Issues, stars, repository
languages, commit hours/weekdays, and pinned repositories) and generate a suite
of high-contrast, high-legibility neon terminal SVGs with internal CSS & SMIL
animations:
  - ascii-portrait.svg    (Kartik/Andrew6rant-style CRT raster cursor portrait)
  - neofetch-stats.svg    (1:1 scale high-legibility neofetch + live stats + bars)
  - header-terminal.svg   (Saviru/GitBanner-style 3-act boot -> glitch -> metallic hero)
  - languages-radar.svg   (1:1 scale animated language bars with ambient glow)
  - activity-clock.svg    (1:1 scale 24h COT spectrum + weekday rhythm)
  - cards/repo-*.svg      (2-col 1:1 scale code-window cards with ambient glow)
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
SANS_FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
HEADERS = {"User-Agent": "lastHunter956-profile-generator/3.0"}
COT_TZ = datetime.timezone(datetime.timedelta(hours=-5))

# Neon hacker terminal color tokens
BG_TOP = "#111722"
BG_BOT = "#0a0e14"
TILE_BG = "#141b24"
FRAME = "#30363d"
NEON_CYAN = "#22d3ee"
NEON_GREEN = "#39d353"
BAR_GREEN = "#26a641"
BLUE = "#58a6ff"
DARK_BLUE = "#1f6feb"
ORANGE = "#ffa657"
PURPLE = "#bc8cff"
GOLD = "#f2cc60"
RED = "#ff5f56"
INK = "#f0f6fc"
ASCII_INK = "#c9d1d9"
MUTED = "#8b949e"

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
    "finazaap": "Moneo — Personal finance tracker & smart expense analytics app.",
    "Repx": "Mobile application built with Dart & Flutter for tracking workflows.",
    "Matematica-avanzadas-maestr-a": "Master's coursework in advanced mathematics & modeling.",
    "actividad_1_DS": "Data Science foundations & interactive web data visualizations.",
    "actividad-de-constitucion": "Structured academic repository & technical documentation.",
    "parcialDS2": "Data Science II evaluation & JavaScript data analysis tools.",
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
    ordered = sorted(lang_bytes.items(), key=lambda x: -x[1])[:6]
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

    # Preserve cached activity clock if unauthenticated REST rate-limited /commits
    if sampled_commits == 0 and (DATA_DIR / "profile_stats.json").exists():
        try:
            old_prof = json.loads((DATA_DIR / "profile_stats.json").read_text())
            if old_prof.get("sampled_commits", 0) > 0:
                hours = old_prof["hours"]
                weekdays = old_prof["weekdays"]
                sampled_commits = old_prof["sampled_commits"]
        except Exception:
            pass

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

    # Preserve cached activity clock if unauthenticated REST rate-limited /commits
    if sampled_commits == 0 and (DATA_DIR / "profile_stats.json").exists():
        try:
            old_prof = json.loads((DATA_DIR / "profile_stats.json").read_text())
            if old_prof.get("sampled_commits", 0) > 0:
                hours = old_prof["hours"]
                weekdays = old_prof["weekdays"]
                sampled_commits = old_prof["sampled_commits"]
        except Exception:
            pass

    contrib_data = build_contributions_payload(username, days)
    profile_data = {
        "username": u.get("login", username),
        "name": u.get("name") or "Jesús Martínez",
        "location": u.get("location") or "Cartagena, Colombia",
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


def rich_chrome(
    width: float,
    height: float,
    title: str,
    uid: str,
    glow_a: str = NEON_CYAN,
    glow_b: str = PURPLE,
    border_col: str = FRAME,
    title_h: float = 30.0,
    pad: float = 18.0,
) -> list[str]:
    """Layered cyber-terminal frame with subtle diagonal grid & ambient corner glows."""
    parts = [
        "<defs>",
        f'<linearGradient id="bg_{uid}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{BG_TOP}"/><stop offset="100%" stop-color="{BG_BOT}"/></linearGradient>',
        f'<radialGradient id="ga_{uid}" cx="0.12" cy="0.08" r="0.75">'
        f'<stop offset="0%" stop-color="{glow_a}" stop-opacity="0.14"/>'
        f'<stop offset="100%" stop-color="{glow_a}" stop-opacity="0"/></radialGradient>',
        f'<radialGradient id="gb_{uid}" cx="0.90" cy="0.92" r="0.70">'
        f'<stop offset="0%" stop-color="{glow_b}" stop-opacity="0.12"/>'
        f'<stop offset="100%" stop-color="{glow_b}" stop-opacity="0"/></radialGradient>',
        f'<pattern id="grid_{uid}" width="24" height="24" patternUnits="userSpaceOnUse">'
        f'<path d="M 24 0 L 0 0 0 24" fill="none" stroke="#ffffff" stroke-opacity="0.025" stroke-width="0.7"/>'
        f'</pattern>',
        "</defs>",
        f'<rect width="{width}" height="{height}" rx="12" fill="url(#bg_{uid})"/>',
        f'<rect width="{width}" height="{height}" rx="12" fill="url(#grid_{uid})"/>',
        f'<rect width="{width}" height="{height}" rx="12" fill="url(#ga_{uid})"/>',
        f'<rect width="{width}" height="{height}" rx="12" fill="url(#gb_{uid})"/>',
        f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="12" fill="none" stroke="{border_col}" stroke-opacity="0.75"/>',
        f'<line x1="0" y1="{title_h}" x2="{width}" y2="{title_h}" stroke="{FRAME}" stroke-opacity="0.7"/>',
    ]
    for i, dot in enumerate([RED, "#ffbd2e", "#27c93f"]):
        parts.append(f'<circle cx="{pad + i * 16}" cy="{title_h / 2}" r="5" fill="{dot}"/>')
    parts.append(
        f'<text x="{width / 2}" y="{title_h / 2 + 4}" fill="{MUTED}" font-size="11.5" '
        f'text-anchor="middle">{html.escape(title)}</text>'
    )
    return parts


# ---------------------------------------------------------------------------
# 1. Saviru/GitBanner-inspired 3-Act Animated Hero Banner (860 x 210)
# ---------------------------------------------------------------------------
def render_header_terminal_svg(profile: dict, out_path: Path, static: bool = False) -> None:
    W, H = 860, 210
    username = profile["username"]
    name = (profile.get("name") or "Jesús Martínez").upper()
    website = profile.get("website") or "jesusmartinez.tech"

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{MONO_FONT}">',
        "<defs>",
        # Orbiting dual radial glows (r >= 1.6 prevents hard edge clipping in any SVG renderer)
        '<radialGradient id="bannerGlowA" cx="0.15" cy="0.2" r="1.6">'
        '<stop offset="0%" stop-color="#05d9e8" stop-opacity="0.26">'
        '<animate attributeName="stop-opacity" values="0.22;0.34;0.22" dur="6s" repeatCount="indefinite"/></stop>'
        '<stop offset="50%" stop-color="#0a3d62" stop-opacity="0.12"/>'
        '<stop offset="100%" stop-color="#0a3d62" stop-opacity="0"/>'
        '<animate attributeName="cx" values="0.15;0.5;0.85;0.5;0.15" dur="18s" repeatCount="indefinite"/>'
        '<animate attributeName="cy" values="0.2;0.8;0.3;0.1;0.2" dur="18s" repeatCount="indefinite"/>'
        "</radialGradient>",
        '<radialGradient id="bannerGlowB" cx="0.85" cy="0.8" r="1.6">'
        '<stop offset="0%" stop-color="#7c3aed" stop-opacity="0.26"/>'
        '<stop offset="50%" stop-color="#ff2a6d" stop-opacity="0.10"/>'
        '<stop offset="100%" stop-color="#7c3aed" stop-opacity="0"/>'
        '<animate attributeName="cx" values="0.85;0.5;0.15;0.5;0.85" dur="22s" repeatCount="indefinite"/>'
        '<animate attributeName="cy" values="0.8;0.2;0.7;0.9;0.8" dur="22s" repeatCount="indefinite"/>'
        "</radialGradient>",
        '<pattern id="diagScan" width="40" height="40" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
        '<rect width="40" height="40" fill="#070b11"/>'
        '<line x1="0" y1="0" x2="40" y2="0" stroke="#131c29" stroke-width="1"/>'
        '<line x1="0" y1="20" x2="40" y2="20" stroke="#0e1520" stroke-width="1"/>'
        "</pattern>",
        # Metallic gradient + sweeping specular shine for hero name
        '<linearGradient id="metalText" x1="0%" y1="0%" x2="0%" y2="100%">'
        '<stop offset="0%" stop-color="#ffffff"/>'
        '<stop offset="35%" stop-color="#d8e3ef"/>'
        '<stop offset="70%" stop-color="#9cb2c9"/>'
        '<stop offset="100%" stop-color="#6b8099"/>'
        "</linearGradient>",
        '<linearGradient id="shineSweep" x1="0%" y1="0%" x2="100%" y2="0%">'
        '<stop offset="0%" stop-color="#ffffff" stop-opacity="0"/>'
        '<stop offset="44%" stop-color="#ffffff" stop-opacity="0"/>'
        '<stop offset="50%" stop-color="#22d3ee" stop-opacity="0.95"/>'
        '<stop offset="56%" stop-color="#ffffff" stop-opacity="0"/>'
        '<stop offset="100%" stop-color="#ffffff" stop-opacity="0"/>'
        '<animate attributeName="x1" values="-100%;120%" dur="3.4s" repeatCount="indefinite"/>'
        '<animate attributeName="x2" values="0%;220%" dur="3.4s" repeatCount="indefinite"/>'
        "</linearGradient>",
        "</defs>",
        f'<rect width="{W}" height="{H}" rx="12" fill="url(#diagScan)"/>',
        f'<rect width="{W}" height="{H}" rx="12" fill="url(#bannerGlowA)"/>',
        f'<rect width="{W}" height="{H}" rx="12" fill="url(#bannerGlowB)"/>',
        f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="12" fill="none" stroke="{NEON_CYAN}" stroke-opacity="0.45"/>',
    ]

    if not static:
        # ACT 1: Terminal Boot Sequence (0s -> 2.4s)
        parts.append('<g id="act1">')
        parts.append(
            f'<text x="30" y="46" font-size="14" fill="{NEON_GREEN}">'
            f'{html.escape(username)}@github:~$ <tspan fill="{INK}">./init-profile.sh --mode=hacker</tspan></text>'
        )
        parts.append(f'<text x="30" y="76" font-size="16" font-weight="700" fill="{PURPLE}">Booting neural terminal workspace...</text>')
        parts.append(f'<rect x="30" y="94" width="800" height="16" rx="4" fill="#161b22" stroke="{FRAME}"/>')
        parts.append(
            f'<rect x="30" y="94" width="0" height="16" rx="4" fill="{NEON_GREEN}">'
            f'<animate attributeName="width" values="0;800" dur="2.1s" fill="freeze"/></rect>'
        )
        for bi, (delay, msg) in enumerate([
            (0.35, "[OK] Kernel 6.12.0-sec initialized · Cartagena, CO"),
            (0.90, "[OK] Syncing GitHub GraphQL telemetry & 70 repositories..."),
            (1.45, "[OK] Launching interactive terminal profile..."),
        ]):
            parts.append(
                f'<text x="30" y="{134 + bi * 22}" font-size="13" fill="{MUTED}" opacity="0">{html.escape(msg)}'
                f'<animate attributeName="opacity" values="0;1" begin="{delay:.2f}s" dur="0.2s" fill="freeze"/></text>'
            )
        parts.append('<animate attributeName="opacity" values="1;0" begin="2.3s" dur="0.3s" fill="freeze"/></g>')

        # ACT 2: RGB Chromatic Glitch Screen (2.5s -> 4.6s)
        parts.append('<g id="act2" opacity="0">')
        parts.append(
            f'<text x="{W / 2}" y="102" font-family="{SANS_FONT}" font-size="38" font-weight="800" '
            f'text-anchor="middle" fill="#ff2a6d" opacity="0.65">WELCOME @{html.escape(username)}'
            f'<animate attributeName="x" values="430;434;426;432;430" dur="0.35s" begin="2.5s" repeatCount="6"/></text>'
        )
        parts.append(
            f'<text x="{W / 2}" y="102" font-family="{SANS_FONT}" font-size="38" font-weight="800" '
            f'text-anchor="middle" fill="{NEON_CYAN}" opacity="0.65">WELCOME @{html.escape(username)}'
            f'<animate attributeName="x" values="430;426;434;428;430" dur="0.35s" begin="2.5s" repeatCount="6"/></text>'
        )
        parts.append(
            f'<text x="{W / 2}" y="102" font-family="{SANS_FONT}" font-size="38" font-weight="800" '
            f'text-anchor="middle" fill="{INK}">WELCOME @{html.escape(username)}</text>'
        )
        parts.append(
            f'<text x="{W / 2}" y="142" font-size="15" text-anchor="middle" fill="{NEON_GREEN}">'
            f'[ ACCESS GRANTED · {html.escape(website)} ]</text>'
        )
        parts.append('<animate attributeName="opacity" values="0;1" begin="2.5s" dur="0.25s" fill="freeze"/>')
        parts.append('<animate attributeName="opacity" values="1;0" begin="4.4s" dur="0.3s" fill="freeze"/></g>')

    # ACT 3: Persistent Metallic Embossed Hero (fades in at 4.6s and holds forever)
    hero_opacity = "1" if static else "0"
    parts.append(f'<g id="act3" opacity="{hero_opacity}">')
    parts.append(
        f'<text x="{W / 2}" y="44" font-size="13" text-anchor="middle" fill="{NEON_GREEN}" letter-spacing="2">'
        f'ROOT@GITHUB // @{html.escape(username.upper())}</text>'
    )
    parts.append(
        f'<text x="{W / 2}" y="98" font-family="{SANS_FONT}" font-size="44" font-weight="800" '
        f'text-anchor="middle" letter-spacing="2" fill="url(#metalText)">{html.escape(name)}</text>'
    )
    if not static:
        parts.append(
            f'<text x="{W / 2}" y="98" font-family="{SANS_FONT}" font-size="44" font-weight="800" '
            f'text-anchor="middle" letter-spacing="2" fill="url(#shineSweep)">{html.escape(name)}</text>'
        )
    parts.append(f'<line x1="190" y1="118" x2="670" y2="118" stroke="{NEON_CYAN}" stroke-width="2" stroke-opacity="0.75"/>')
    parts.append(
        f'<text x="{W / 2}" y="148" font-size="16" font-weight="700" text-anchor="middle" fill="{NEON_CYAN}">'
        f'Systems Engineer · Data Scientist · AI &amp; InfoSec Builder</text>'
    )
    parts.append(
        f'<text x="{W / 2}" y="178" font-size="13.5" text-anchor="middle" fill="{ASCII_INK}">'
        f'Cartagena, Colombia  ·  <tspan fill="{INK}">{html.escape(website)}</tspan>  ·  '
        f'<tspan fill="{NEON_GREEN}">{profile.get("public_repos", 70)} Repositories</tspan></text>'
    )
    if not static:
        parts.append('<animate attributeName="opacity" values="0;1" begin="4.6s" dur="0.7s" fill="freeze"/>')
    parts.append("</g></svg>")

    out_path.write_text("".join(parts), encoding="utf-8")
    print(f"wrote {out_path} ({W}x{H})")


# ---------------------------------------------------------------------------
# 2. Kartik-style CRT Raster ASCII Portrait SVG (840 x 880)
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
    art_w = 800.0
    cell_w = art_w / cols
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

    row_dur = 0.08
    stagger = 0.065  # fast sweeping CRT raster cursor (~5s total)

    css = """
@keyframes blink {
  0%, 49%  { opacity: 1; }
  50%, 100% { opacity: 0; }
}
.cur { animation: blink 1s steps(1, end) infinite; }
""".strip()

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas_w}" height="{canvas_h}" '
        f'viewBox="0 0 {canvas_w} {canvas_h}" font-family="{MONO_FONT}">',
        f"<style>{css}</style>",
    ]
    parts.extend(
        rich_chrome(
            canvas_w, canvas_h, f"{username}@github: ~$ ./portrait.sh",
            "pbg", glow_a=NEON_GREEN, glow_b=NEON_CYAN, border_col=FRAME, title_h=title_h, pad=pad,
        )
    )

    art_top = title_h + pad * 0.35
    font_size = cell_w / 0.60
    for ry, line in enumerate(rows_txt):
        y = art_top + ry * cell_h + cell_h * 0.74
        row_y = art_top + ry * cell_h
        stripped = line.rstrip()
        if not stripped:
            continue
        delay = ry * stagger
        text_el = (
            f'<text xml:space="preserve" x="{pad}" y="{y:.1f}" fill="{ASCII_INK}" '
            f'font-size="{font_size:.2f}">{html.escape(stripped)}</text>'
        )
        if static:
            parts.append(text_el)
            continue

        # Kartik/Andrew6rant sweeping horizontal clip wipe + riding neon cursor
        parts.append(
            f'<clipPath id="r{ry}"><rect x="{pad}" y="{row_y:.1f}" height="{cell_h:.2f}" width="0">'
            f'<animate attributeName="width" from="0" to="{art_w}" begin="{delay:.3f}s" '
            f'dur="{row_dur:.2f}s" fill="freeze"/></rect></clipPath>'
        )
        parts.append(f'<g clip-path="url(#r{ry})">{text_el}</g>')
        parts.append(
            f'<rect y="{row_y + 1:.1f}" width="{cell_w:.1f}" height="{cell_h - 2:.1f}" fill="{NEON_CYAN}" opacity="0">'
            f'<animate attributeName="x" from="{pad}" to="{pad + art_w}" begin="{delay:.3f}s" '
            f'dur="{row_dur:.2f}s" fill="freeze"/>'
            f'<set attributeName="opacity" to="0.9" begin="{delay:.3f}s"/>'
            f'<set attributeName="opacity" to="0" begin="{delay + row_dur:.3f}s"/></rect>'
        )

    status_line_y = title_h + art_h + pad * 0.35
    status_y = status_line_y + 22
    parts.append(f'<line x1="0" y1="{status_line_y:.1f}" x2="{canvas_w}" y2="{status_line_y:.1f}" stroke="{FRAME}"/>')
    prompt_prefix = f"{username}@github:~$ whoami "
    parts.append(
        f'<text x="{pad}" y="{status_y:.1f}" fill="{MUTED}" font-size="18">'
        f'<tspan fill="{NEON_GREEN}">{html.escape(username)}@github</tspan>:~$ whoami '
        f'<tspan fill="{INK}" font-weight="700">{html.escape(display_name)}</tspan></text>'
    )
    cursor_x = pad + (len(prompt_prefix) + len(display_name) + 0.8) * 18 * 0.602
    cur_cls = "" if static else 'class="cur"'
    parts.append(f'<rect {cur_cls} x="{cursor_x:.1f}" y="{status_y - 16:.1f}" width="10" height="19" fill="{NEON_GREEN}"/>')
    parts.append("</svg>")

    out_path.write_text("".join(parts), encoding="utf-8")
    print(f"wrote {out_path} ({canvas_w}x{canvas_h})")


# ---------------------------------------------------------------------------
# 3. High-Legibility Neofetch + Live Stats SVG (1:1 scale 480x503 -> 430px)
# ---------------------------------------------------------------------------
def short_date(d: str | None) -> str:
    if not d:
        return "—"
    return datetime.date.fromisoformat(d).strftime("%b %-d")


def span_label(s: dict) -> str:
    if not s.get("length"):
        return "start today"
    return f'{short_date(s["start"])} - {short_date(s["end"])}'


def render_neofetch_stats_svg(contrib: dict, profile: dict, out_path: Path, static: bool = False) -> None:
    # 480x491 displayed at 430px matches ascii-portrait (420px -> 440px tall) pixel-for-pixel
    W, H = 480, 491
    pad = 18.0
    title_h = 30.0
    username = profile["username"]

    cur = contrib["current_streak"]
    lng = contrib["longest_streak"]
    n_days = len(contrib["days"]) or 365

    # Kartik-inspired orange keys + crisp values (concise & large!)
    neofetch_rows = [
        ("Role", "Systems Eng · Data Sci · AI"),
        ("Focus", "AI Solutions · Web · InfoSec"),
        ("Web", profile.get("website") or "jesusmartinez.tech"),
        ("Loc", "Cartagena, Colombia"),
    ]

    tiles = [
        ("streak", f'{cur["length"]}d', f'max: {lng["length"]}d ({span_label(lng)})', NEON_GREEN),
        ("contributions", f'{contrib["total_contributions"]:,}', f'{contrib["active_days"]}/{n_days} active days', NEON_CYAN),
        ("PRs & Issues", f'{profile.get("total_prs", 0)} / {profile.get("total_issues", 0)}', "pull requests / issues", BLUE),
        ("repos & stars", f'{profile.get("public_repos", 0)} / {profile.get("total_stars", 0)}', f'repos / stars · {profile.get("followers", 0)} followers', GOLD),
    ]

    css = """
@keyframes fadeSlide {
  0%   { opacity: 0; transform: translateY(6px); }
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
.card { opacity: 0; animation: fadeSlide 0.42s ease-out both; }
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
        rich_chrome(
            W, H, f"{username}@github: ~$ neofetch",
            "sbg", glow_a=NEON_CYAN, glow_b=NEON_GREEN, border_col=FRAME, title_h=title_h, pad=pad,
        )
    )

    y = title_h + 26
    parts.append(
        f'<text x="{pad}" y="{y}" font-size="14" font-weight="700">'
        f'<tspan fill="{NEON_GREEN}">{html.escape(username)}</tspan><tspan fill="{MUTED}">@</tspan>'
        f'<tspan fill="{NEON_CYAN}">github</tspan></text>'
        f'<line x1="{pad + 178}" y1="{y - 4}" x2="{W - pad}" y2="{y - 4}" stroke="{FRAME}"/>'
    )

    y += 24
    for i, (key, val) in enumerate(neofetch_rows):
        cls_attr = "" if static else f'class="line" style="animation-delay:{0.06 + i * 0.06:.2f}s"'
        parts.append(
            f'<text {cls_attr} x="{pad}" y="{y}" font-size="12.5">'
            f'<tspan fill="{ORANGE}" font-weight="700">{html.escape(key)}</tspan>'
            f'<tspan x="{pad + 64}" fill="{INK}">{html.escape(val)}</tspan></text>'
        )
        y += 21

    # Blue Kartik-style section divider
    y += 6
    sec_cls = "" if static else 'class="line" style="animation-delay:0.32s"'
    parts.append(
        f'<text {sec_cls} x="{pad}" y="{y}" font-size="12.5" font-weight="700" fill="{BLUE}">'
        f'&#8212; Live GitHub Telemetry</text>'
        f'<line x1="{pad + 186}" y1="{y - 4}" x2="{W - pad}" y2="{y - 4}" stroke="{FRAME}"/>'
    )
    y += 14

    # 2x2 large legible stat tiles
    gap = 10.0
    card_w = (W - pad * 2 - gap) / 2
    card_h = 70.0
    for i, (label, val, sub, accent) in enumerate(tiles):
        col_i, row_i = i % 2, i // 2
        cx = pad + col_i * (card_w + gap)
        cy = y + row_i * (card_h + gap)
        delay = 0.40 + i * 0.08
        cls_attr = "" if static else f'class="card" style="animation-delay:{delay:.2f}s"'
        parts.append(f'<g {cls_attr}>')
        parts.append(
            f'<rect x="{cx:.1f}" y="{cy:.1f}" width="{card_w:.1f}" height="{card_h:.1f}" '
            f'rx="8" fill="{TILE_BG}" stroke="{FRAME}"/>'
        )
        parts.append(f'<line x1="{cx + 8:.1f}" y1="{cy + 0.5:.1f}" x2="{cx + card_w - 8:.1f}" y2="{cy + 0.5:.1f}" stroke="{accent}" stroke-opacity="0.65"/>')
        parts.append(f'<text x="{cx + 12:.1f}" y="{cy + 19:.1f}" fill="{MUTED}" font-size="11">{html.escape(label)}</text>')
        parts.append(f'<text x="{cx + 12:.1f}" y="{cy + 46:.1f}" fill="{accent}" font-size="22" font-weight="700">{html.escape(val)}</text>')
        parts.append(f'<text x="{cx + 12:.1f}" y="{cy + 64:.1f}" fill="{MUTED}" font-size="10.5">{html.escape(sub)}</text>')
        parts.append("</g>")

    # Monthly bar chart
    chart_top = y + 2 * card_h + gap + 12
    chart_h = H - pad - 24 - chart_top
    chart_w = W - pad * 2
    c_cls = "" if static else 'class="card" style="animation-delay:0.75s"'
    parts.append(f'<g {c_cls}>')
    parts.append(
        f'<rect x="{pad}" y="{chart_top:.1f}" width="{chart_w}" height="{chart_h:.1f}" '
        f'rx="8" fill="{TILE_BG}" stroke="{FRAME}"/>'
    )
    parts.append(f'<text x="{pad + 12}" y="{chart_top + 20:.1f}" fill="{MUTED}" font-size="11">$ contributions / month</text>')
    parts.append("</g>")

    monthly = contrib["monthly"][-12:]
    if monthly:
        plot_top = chart_top + 36
        plot_bot = chart_top + chart_h - 22
        plot_l = pad + 14
        plot_r = W - pad - 14
        slot = (plot_r - plot_l) / len(monthly)
        bar_w = slot * 0.58
        peak = max(m["total"] for m in monthly) or 1
        for i, m in enumerate(monthly):
            bh = max(3.0, (plot_bot - plot_top) * m["total"] / peak)
            bx = plot_l + i * slot + (slot - bar_w) / 2
            fill = NEON_GREEN if m["total"] == peak else BAR_GREEN
            b_cls = "" if static else f'class="bar" style="animation-delay:{0.85 + i * 0.04:.2f}s"'
            parts.append(
                f'<rect {b_cls} x="{bx:.1f}" y="{plot_bot - bh:.1f}" width="{bar_w:.1f}" '
                f'height="{bh:.1f}" rx="2.5" fill="{fill}"/>'
            )
            mon = datetime.date.fromisoformat(m["month"] + "-01").strftime("%b")[0]
            parts.append(
                f'<text x="{bx + bar_w / 2:.1f}" y="{plot_bot + 14:.1f}" fill="{MUTED}" '
                f'font-size="10.5" text-anchor="middle">{mon}</text>'
            )
            if m["total"] == peak:
                p_cls = "" if static else 'class="line" style="animation-delay:1.35s"'
                parts.append(
                    f'<text {p_cls} x="{bx + bar_w / 2:.1f}" y="{plot_bot - bh - 5:.1f}" '
                    f'fill="{INK}" font-size="10.5" font-weight="700" text-anchor="middle">{peak:,}</text>'
                )

    footer_y = H - pad + 4
    parts.append(
        f'<text x="{pad}" y="{footer_y:.1f}" font-size="11" fill="{MUTED}">'
        f'<tspan fill="{NEON_GREEN}">{html.escape(username)}@github</tspan>:~$ '
        f'<tspan fill="{INK}">ready</tspan></text>'
    )
    cur_x = pad + (len(f"{username}@github:~$ ") + 5.8) * 11 * 0.602
    cur_cls = "" if static else 'class="cur"'
    parts.append(f'<rect {cur_cls} x="{cur_x:.1f}" y="{footer_y - 10:.1f}" width="7" height="12" fill="{NEON_GREEN}"/>')
    parts.append("</svg>")

    out_path.write_text("".join(parts), encoding="utf-8")
    print(f"wrote {out_path} ({W}x{H})")


# ---------------------------------------------------------------------------
# 4. High-Legibility Languages Radar SVG (520x380 -> 425px)
# ---------------------------------------------------------------------------
def render_languages_radar_svg(profile: dict, out_path: Path, static: bool = False) -> None:
    W, H = 520, 380
    pad = 20.0
    title_h = 30.0
    username = profile["username"]
    langs = (profile.get("languages") or [])[:6]
    if not langs:
        langs = [{"name": "Python", "pct": 100.0, "color": "#3572A5"}]

    css = """
@keyframes fadeSlide {
  0%   { opacity: 0; transform: translateX(-6px); }
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
        rich_chrome(
            W, H, f"{username}@github: ~$ ./languages.sh",
            "lbg", glow_a=BLUE, glow_b=NEON_CYAN, border_col=FRAME, title_h=title_h, pad=pad,
        )
    )

    y = title_h + 26
    parts.append(
        f'<text x="{pad}" y="{y}" font-size="12.5" fill="{MUTED}">'
        f'<tspan fill="{NEON_GREEN}">{html.escape(username)}@github</tspan>:~$ scan-langs '
        f'<tspan fill="{NEON_CYAN}">--repos={profile.get("public_repos", 70)}</tspan></text>'
    )

    stack_y = y + 14
    stack_w = W - pad * 2
    parts.append(f'<rect x="{pad}" y="{stack_y}" width="{stack_w}" height="12" rx="6" fill="{TILE_BG}" stroke="{FRAME}"/>')
    sx = pad
    total_pct = sum(l["pct"] for l in langs) or 100.0
    for i, l in enumerate(langs):
        seg_w = max(4.0, stack_w * (l["pct"] / total_pct))
        if i == len(langs) - 1:
            seg_w = max(4.0, (pad + stack_w) - sx)
        b_cls = "" if static else f'class="bar" style="animation-delay:{0.08 + i * 0.05:.2f}s"'
        parts.append(f'<rect {b_cls} x="{sx:.1f}" y="{stack_y + 1}" width="{seg_w:.1f}" height="10" rx="3" fill="{l["color"]}"/>')
        sx += seg_w

    row_y = stack_y + 42
    bar_x = 186.0
    bar_max_w = W - pad - bar_x - 62.0
    max_pct = max(l["pct"] for l in langs) or 1.0
    row_step = 42.0

    for i, l in enumerate(langs):
        delay = 0.16 + i * 0.08
        r_cls = "" if static else f'class="row" style="animation-delay:{delay:.2f}s"'
        b_cls = "" if static else f'class="bar" style="animation-delay:{delay + 0.10:.2f}s"'
        lname = l["name"] if len(l["name"]) <= 16 else "Jupyter" if "Jupyter" in l["name"] else l["name"][:15]

        parts.append(f'<g {r_cls}>')
        parts.append(f'<circle cx="{pad + 6}" cy="{row_y - 4}" r="4.5" fill="{l["color"]}"/>')
        parts.append(f'<text x="{pad + 18}" y="{row_y}" font-size="13.5" font-weight="700" fill="{INK}">{html.escape(lname)}</text>')
        parts.append(f'<rect x="{bar_x}" y="{row_y - 12}" width="{bar_max_w:.1f}" height="14" rx="4" fill="{TILE_BG}" stroke="{FRAME}"/>')
        fill_w = max(8.0, bar_max_w * (l["pct"] / max_pct))
        parts.append(f'<rect {b_cls} x="{bar_x}" y="{row_y - 12}" width="{fill_w:.1f}" height="14" rx="4" fill="{l["color"]}"/>')
        parts.append(
            f'<text x="{W - pad}" y="{row_y}" font-size="13" font-weight="700" fill="{NEON_CYAN}" text-anchor="end">'
            f'{l["pct"]:.1f}%</text>'
        )
        parts.append("</g>")
        row_y += row_step

    footer_y = H - pad + 4
    parts.append(f'<line x1="{pad}" y1="{footer_y - 20}" x2="{W - pad}" y2="{footer_y - 20}" stroke="{FRAME}"/>')
    top_names = " / ".join(
        ("Jupyter" if "Jupyter" in l["name"] else l["name"]) for l in langs[:3]
    )
    summary = f"top: {top_names}"
    parts.append(
        f'<text x="{pad}" y="{footer_y:.1f}" font-size="11.5" fill="{MUTED}">'
        f'<tspan fill="{NEON_GREEN}">{html.escape(username)}@github</tspan>:~$ '
        f'<tspan fill="{INK}">{html.escape(summary)}</tspan></text>'
    )
    cur_x = pad + (len(f"{username}@github:~$ ") + len(summary) + 0.8) * 11.5 * 0.602
    cur_cls = "" if static else 'class="cur"'
    parts.append(f'<rect {cur_cls} x="{cur_x:.1f}" y="{footer_y - 10:.1f}" width="7" height="12" fill="{NEON_CYAN}"/>')
    parts.append("</svg>")

    out_path.write_text("".join(parts), encoding="utf-8")
    print(f"wrote {out_path} ({W}x{H})")


# ---------------------------------------------------------------------------
# 5. High-Legibility Activity Clock SVG (520x380 -> 425px)
# ---------------------------------------------------------------------------
def render_activity_clock_svg(profile: dict, out_path: Path, static: bool = False) -> None:
    W, H = 520, 380
    pad = 20.0
    title_h = 30.0
    username = profile["username"]

    hours = profile.get("hours") or [2] * 24
    weekdays = profile.get("weekdays") or [10] * 7
    sampled = int(profile.get("sampled_commits") or sum(hours))

    peak_h = max(range(24), key=lambda h: hours[h])
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    peak_d = max(range(7), key=lambda d: weekdays[d])
    persona = "Night Owl" if (peak_h >= 20 or peak_h <= 4) else ("Early Bird" if peak_h < 11 else "Day Builder")

    css = """
@keyframes fadeSlide {
  0%   { opacity: 0; transform: translateY(6px); }
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
.blk  { opacity: 0; animation: fadeSlide 0.35s ease-out both; }
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
        rich_chrome(
            W, H, f"{username}@github: ~$ ./clock.sh --tz=COT",
            "cbg", glow_a=NEON_GREEN, glow_b=PURPLE, border_col=FRAME, title_h=title_h, pad=pad,
        )
    )

    y = title_h + 26
    parts.append(
        f'<text x="{pad}" y="{y}" font-size="12.5" fill="{MUTED}">'
        f'<tspan fill="{NEON_GREEN}">{html.escape(username)}@github</tspan>:~$ clock '
        f'<tspan fill="{NEON_CYAN}">--commits={sampled} --tz=UTC-5</tspan></text>'
    )

    # Top panel: 24h spectrum
    box1_y = y + 12
    box1_h = 128.0
    box_w = W - pad * 2
    b1_cls = "" if static else 'class="blk" style="animation-delay:0.08s"'
    parts.append(f'<g {b1_cls}>')
    parts.append(f'<rect x="{pad}" y="{box1_y}" width="{box_w}" height="{box1_h}" rx="8" fill="{TILE_BG}" stroke="{FRAME}"/>')
    parts.append(f'<text x="{pad + 12}" y="{box1_y + 20}" font-size="11.5" fill="{MUTED}">hourly spectrum (00-23h)</text>')
    parts.append(
        f'<text x="{W - pad - 12}" y="{box1_y + 20}" font-size="11.5" font-weight="700" fill="{NEON_CYAN}" text-anchor="end">'
        f'Peak {peak_h:02d}:00 · {persona}</text>'
    )
    parts.append("</g>")

    plot_l = pad + 14
    plot_r = W - pad - 14
    plot_top = box1_y + 34
    plot_bot = box1_y + box1_h - 24
    slot = (plot_r - plot_l) / 24
    bar_w = slot * 0.64
    max_h = max(hours) or 1

    for hr, cnt in enumerate(hours):
        bh = max(3.0, (plot_bot - plot_top) * cnt / max_h)
        bx = plot_l + hr * slot + (slot - bar_w) / 2
        if cnt == max_h and cnt > 0:
            col = NEON_CYAN
        elif cnt >= max_h * 0.55 and cnt > 0:
            col = NEON_GREEN
        elif cnt > 0:
            col = DARK_BLUE
        else:
            col = "#21262d"
        v_cls = "" if static else f'class="vbar" style="animation-delay:{0.16 + hr * 0.02:.3f}s"'
        parts.append(
            f'<rect {v_cls} x="{bx:.1f}" y="{plot_bot - bh:.1f}" width="{bar_w:.1f}" '
            f'height="{bh:.1f}" rx="2" fill="{col}"/>'
        )
        if hr % 3 == 0 or hr == 23:
            parts.append(
                f'<text x="{bx + bar_w / 2:.1f}" y="{plot_bot + 14:.1f}" font-size="10" fill="{MUTED}" '
                f'text-anchor="middle">{hr:02d}</text>'
            )

    # Bottom panel: 7-day breakdown
    box2_y = box1_y + box1_h + 10
    box2_h = H - pad - 24 - box2_y
    b2_cls = "" if static else 'class="blk" style="animation-delay:0.50s"'
    parts.append(f'<g {b2_cls}>')
    parts.append(f'<rect x="{pad}" y="{box2_y}" width="{box_w}" height="{box2_h}" rx="8" fill="{TILE_BG}" stroke="{FRAME}"/>')
    parts.append(f'<text x="{pad + 12}" y="{box2_y + 19}" font-size="11.5" fill="{MUTED}">weekday cadence</text>')
    parts.append(
        f'<text x="{W - pad - 12}" y="{box2_y + 19}" font-size="11.5" font-weight="700" fill="{NEON_GREEN}" text-anchor="end">'
        f'Best: {day_names[peak_d]} ({weekdays[peak_d]})</text>'
    )
    parts.append("</g>")

    total_wd = sum(weekdays) or 1
    max_wd = max(weekdays) or 1
    row_start = box2_y + 30
    row_step = (box2_h - 36) / 7
    hbar_x = pad + 52
    hbar_max = box_w - 132

    for di, dname in enumerate(day_names):
        cnt = weekdays[di]
        ry = row_start + di * row_step
        pct = cnt * 100.0 / total_wd
        fw = max(6.0, hbar_max * cnt / max_wd)
        col = NEON_GREEN if di == peak_d else (NEON_CYAN if cnt >= max_wd * 0.6 else BLUE)
        h_cls = "" if static else f'class="hbar" style="animation-delay:{0.60 + di * 0.05:.2f}s"'

        parts.append(f'<text x="{pad + 12}" y="{ry + 9:.1f}" font-size="11.5" font-weight="700" fill="{INK}">{dname}</text>')
        parts.append(f'<rect x="{hbar_x}" y="{ry:.1f}" width="{hbar_max:.1f}" height="10" rx="3" fill="#0d1117" stroke="{FRAME}" stroke-width="0.6"/>')
        parts.append(f'<rect {h_cls} x="{hbar_x}" y="{ry:.1f}" width="{fw:.1f}" height="10" rx="3" fill="{col}"/>')
        parts.append(
            f'<text x="{W - pad - 12}" y="{ry + 9:.1f}" font-size="11" fill="{MUTED}" text-anchor="end">'
            f'{cnt} ({pct:.0f}%)</text>'
        )

    footer_y = H - pad + 4
    summary = f"peak: {peak_h:02d}:00 COT · {day_names[peak_d]}"
    parts.append(
        f'<text x="{pad}" y="{footer_y:.1f}" font-size="11.5" fill="{MUTED}">'
        f'<tspan fill="{NEON_GREEN}">{html.escape(username)}@github</tspan>:~$ '
        f'<tspan fill="{INK}">{html.escape(summary)}</tspan></text>'
    )
    cur_x = pad + (len(f"{username}@github:~$ ") + len(summary) + 0.8) * 11.5 * 0.602
    cur_cls = "" if static else 'class="cur"'
    parts.append(f'<rect {cur_cls} x="{cur_x:.1f}" y="{footer_y - 10:.1f}" width="7" height="12" fill="{NEON_GREEN}"/>')
    parts.append("</svg>")

    out_path.write_text("".join(parts), encoding="utf-8")
    print(f"wrote {out_path} ({W}x{H})")


# ---------------------------------------------------------------------------
# 6. Large 1:1 Scale Code-Window Project Cards (425x200 -> 425px in 2-col grid)
# ---------------------------------------------------------------------------
def render_repo_cards(profile: dict, cards_dir: Path, static: bool = False) -> None:
    cards_dir.mkdir(parents=True, exist_ok=True)
    repos = (profile.get("pinned_repos") or [])[:6]
    W, H = 425, 200
    pad = 18.0
    title_h = 28.0

    css = """
@keyframes cardIn {
  0%   { opacity: 0; transform: translateY(6px); }
  100% { opacity: 1; transform: translateY(0); }
}
.wrap { opacity: 0; animation: cardIn 0.42s ease-out both; }
@media (prefers-reduced-motion: reduce) {
  .wrap { opacity: 1 !important; transform: none !important; animation: none !important; }
}
""".strip()

    for idx, repo in enumerate(repos, start=1):
        name = repo["name"]
        desc = repo.get("description") or PINNED_FALLBACK_DESC.get(
            name, f"{repo.get('language', 'Software')} repository by @{profile['username']}."
        )
        wrapped = textwrap.wrap(desc, width=42)[:2]
        if len(wrapped) == 2 and len(desc) > 84:
            wrapped[1] = wrapped[1][:39].rstrip() + "..."
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
            rich_chrome(
                W, H, f"~/repos/{name[:26]}",
                f"rbg{idx}", glow_a=lcol, glow_b=NEON_CYAN, border_col=FRAME, title_h=title_h, pad=16.0,
            )
        )
        parts.append(f'<line x1="12" y1="1" x2="{W - 12}" y2="1" stroke="{lcol}" stroke-width="2.5" stroke-opacity="0.85"/>')

        disp_name = name if len(name) <= 23 else name[:21] + ".."
        parts.append(f'<text x="{pad}" y="62" font-size="18" font-weight="700" fill="{NEON_CYAN}">{html.escape(disp_name)}</text>')
        parts.append(f'<rect x="{W - pad - 64}" y="45" width="64" height="22" rx="11" fill="{TILE_BG}" stroke="{FRAME}"/>')
        parts.append(f'<text x="{W - pad - 32}" y="60" font-size="10.5" font-weight="700" fill="{NEON_GREEN}" text-anchor="middle">PUBLIC</text>')

        dy = 94
        for line in wrapped:
            parts.append(f'<text x="{pad}" y="{dy}" font-size="13.5" fill="{ASCII_INK}">{html.escape(line)}</text>')
            dy += 22

        parts.append(f'<line x1="{pad}" y1="154" x2="{W - pad}" y2="154" stroke="{FRAME}" stroke-opacity="0.7"/>')
        parts.append(f'<circle cx="{pad + 6}" cy="177" r="5.5" fill="{lcol}"/>')
        parts.append(f'<text x="{pad + 18}" y="181" font-size="13" font-weight="700" fill="{INK}">{html.escape(lang)}</text>')
        meta = f"stars: {stars}  ·  forks: {forks}"
        parts.append(f'<text x="{W - pad}" y="181" font-size="12.5" fill="{MUTED}" text-anchor="end">{html.escape(meta)}</text>')
        parts.append("</g></svg>")

        out_file = cards_dir / f"repo-{idx}.svg"
        out_file.write_text("".join(parts), encoding="utf-8")
        print(f"wrote {out_file} ({W}x{H})")


# ---------------------------------------------------------------------------
# 7. Initial Snake SVG Fallback (overwritten automatically by Platane/snk)
# ---------------------------------------------------------------------------
def render_initial_snake_svg(contrib: dict, out_path: Path) -> None:
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
    parser.add_argument("--use-cache", action="store_true", help="Use cached data/*.json without network calls")
    parser.add_argument("--out-dir", type=Path, default=ROOT, help="Output directory")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    if args.use_cache and (DATA_DIR / "contributions.json").exists() and (DATA_DIR / "profile_stats.json").exists():
        contrib_data = json.loads((DATA_DIR / "contributions.json").read_text())
        profile_data = json.loads((DATA_DIR / "profile_stats.json").read_text())
    else:
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
