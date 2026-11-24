#!/usr/bin/env python3
"""Generate dark/light GitHub Stats, Top Languages, and Codeforces SVGs."""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

USERNAME = os.environ.get("GITHUB_USERNAME", "Krishna27Singh")
CF_HANDLE = os.environ.get("CF_HANDLE", "Krishna_Singh27")
GITHUB_TOKEN = os.environ.get("GH_STATS_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
OUT_DIR = Path(os.environ.get("OUT_DIR", "dist"))

THEMES = {
    "dark": {
        "bg": "#0d1117",
        "card": "#161b22",
        "border": "#30363d",
        "title": "#f0f6fc",
        "text": "#e6edf3",
        "muted": "#8b949e",
        "accent": "#58a6ff",
        "green": "#3fb950",
        "bar_bg": "#21262d",
    },
    "light": {
        "bg": "#ffffff",
        "card": "#f6f8fa",
        "border": "#d0d7de",
        "title": "#1f2328",
        "text": "#1f2328",
        "muted": "#656d76",
        "accent": "#0969da",
        "green": "#1a7f37",
        "bar_bg": "#eaeef2",
    },
}

CF_RANK_COLORS = {
    "newbie": "#8b949e",
    "pupil": "#3fb950",
    "specialist": "#39c5cf",
    "expert": "#58a6ff",
    "candidate master": "#bc8cff",
    "master": "#f0883e",
    "international master": "#f0883e",
    "grandmaster": "#f85149",
    "international grandmaster": "#f85149",
    "legendary grandmaster": "#f85149",
}


def http_json(url: str, payload: dict | None = None, headers: dict | None = None) -> dict:
    req_headers = {"User-Agent": "Krishna27Singh-Profile-Generator"}
    if headers:
        req_headers.update(headers)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=req_headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def graphql(query: str, variables: dict) -> dict:
    res = http_json(
        "https://api.github.com/graphql",
        payload={"query": query, "variables": variables},
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    if "errors" in res:
        raise RuntimeError(f"GraphQL error: {res['errors']}")
    return res["data"]


def fetch_github_stats() -> dict:
    user_query = """
    query($login: String!) {
      user(login: $login) {
        createdAt
        followers { totalCount }
        pullRequests { totalCount }
        issues { totalCount }
        repositoriesContributedTo(
          contributionTypes: [COMMIT, ISSUE, PULL_REQUEST, REPOSITORY]
          includeUserRepositories: true
        ) { totalCount }
      }
    }
    """
    user = graphql(user_query, {"login": USERNAME})["user"]
    created_at = dt.datetime.fromisoformat(user["createdAt"].replace("Z", "+00:00"))
    now = dt.datetime.now(dt.timezone.utc)

    # Fetch all owned non-fork repositories (paginated) for stars + languages
    repo_query = """
    query($login: String!, $cursor: String) {
      user(login: $login) {
        repositories(
          first: 100
          after: $cursor
          ownerAffiliations: OWNER
          isFork: false
          orderBy: {field: UPDATED_AT, direction: DESC}
        ) {
          pageInfo { hasNextPage endCursor }
          nodes {
            stargazerCount
            languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
              edges {
                size
                node { name color }
              }
            }
          }
        }
      }
    }
    """
    total_stars = 0
    lang_bytes: Counter[str] = Counter()
    lang_colors: dict[str, str] = {}
    cursor = None
    while True:
        repo_data = graphql(repo_query, {"login": USERNAME, "cursor": cursor})["user"]["repositories"]
        for repo in repo_data["nodes"]:
            total_stars += repo["stargazerCount"]
            for edge in repo["languages"]["edges"]:
                name = edge["node"]["name"]
                lang_bytes[name] += edge["size"]
                if edge["node"].get("color"):
                    lang_colors[name] = edge["node"]["color"]
        if not repo_data["pageInfo"]["hasNextPage"]:
            break
        cursor = repo_data["pageInfo"]["endCursor"]

    # Fetch exact multi-year commits + calendar days
    contrib_query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          totalCommitContributions
          restrictedContributionsCount
          totalPullRequestReviewContributions
          contributionCalendar {
            totalContributions
            weeks {
              contributionDays {
                date
                contributionCount
              }
            }
          }
        }
      }
    }
    """
    total_commits = 0
    total_reviews = 0
    days_map: dict[str, int] = {}

    for year in range(created_at.year, now.year + 1):
        start = max(created_at, dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc))
        end = min(now, dt.datetime(year, 12, 31, 23, 59, 59, tzinfo=dt.timezone.utc))
        col = graphql(
            contrib_query,
            {
                "login": USERNAME,
                "from": start.isoformat().replace("+00:00", "Z"),
                "to": end.isoformat().replace("+00:00", "Z"),
            },
        )["user"]["contributionsCollection"]

        total_commits += col["totalCommitContributions"] + col["restrictedContributionsCount"]
        total_reviews += col["totalPullRequestReviewContributions"]
        for week in col["contributionCalendar"]["weeks"]:
            for day in week["contributionDays"]:
                days_map[day["date"]] = max(days_map.get(day["date"], 0), day["contributionCount"])

    # Compute streaks
    sorted_dates = sorted(days_map.keys())
    longest_streak = 0
    run = 0
    for d in sorted_dates:
        if days_map[d] > 0:
            run += 1
            longest_streak = max(longest_streak, run)
        else:
            run = 0

    today_str = now.strftime("%Y-%m-%d")
    yesterday_str = (now - dt.timedelta(days=1)).strftime("%Y-%m-%d")
    current_streak = 0
    anchor = today_str if days_map.get(today_str, 0) > 0 else yesterday_str
    if anchor in days_map and days_map[anchor] > 0:
        cur_dt = dt.date.fromisoformat(anchor)
        while days_map.get(cur_dt.isoformat(), 0) > 0:
            current_streak += 1
            cur_dt -= dt.timedelta(days=1)

    total_lang_bytes = sum(lang_bytes.values()) or 1
    top_langs = [
        {
            "name": name,
            "pct": (size / total_lang_bytes) * 100.0,
            "color": lang_colors.get(name, "#8b949e"),
        }
        for name, size in lang_bytes.most_common(6)
    ]

    return {
        "commits": total_commits,
        "prs": user["pullRequests"]["totalCount"],
        "issues": user["issues"]["totalCount"],
        "stars": total_stars,
        "repos_contributed": user["repositoriesContributedTo"]["totalCount"],
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "reviews": total_reviews,
        "top_langs": top_langs,
        "updated_utc": now.strftime("%b %d · %H:%M UTC"),
    }


def fetch_codeforces_stats() -> dict | None:
    try:
        info_res = http_json(f"https://codeforces.com/api/user.info?handles={CF_HANDLE}")
        status_res = http_json(f"https://codeforces.com/api/user.status?handle={CF_HANDLE}")
        if info_res.get("status") != "OK" or status_res.get("status") != "OK":
            return None

        user = info_res["result"][0]
        solved: dict[str, list[str]] = {}
        for sub in status_res["result"]:
            if sub.get("verdict") == "OK":
                prob = sub.get("problem", {})
                key = f"{prob.get('contestId', '')}_{prob.get('index', '')}"
                if key not in solved:
                    solved[key] = prob.get("tags", [])

        tag_counts: Counter[str] = Counter()
        for tags in solved.values():
            for tag in tags:
                tag_counts[tag] += 1

        return {
            "handle": user.get("handle", CF_HANDLE),
            "rating": user.get("rating", 0),
            "max_rating": user.get("maxRating", 0),
            "rank": user.get("rank", "unrated"),
            "solved": len(solved),
            "top_tags": [t for t, _ in tag_counts.most_common(4)],
        }
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError):
        return None


def fmt_num(n: int) -> str:
    return f"{n:,}"


def render_stats_svg(stats: dict, theme_name: str) -> str:
    t = THEMES[theme_name]
    tiles = [
        ("All-Time Commits", fmt_num(stats["commits"]), t["accent"]),
        ("Pull Requests", fmt_num(stats["prs"]), t["title"]),
        ("Issues Opened", fmt_num(stats["issues"]), t["title"]),
        ("Stars Earned", fmt_num(stats["stars"]), t["title"]),
        ("Current Streak", f"{stats['current_streak']}d", t["green"]),
        ("Longest Streak", f"{stats['longest_streak']}d", t["title"]),
    ]

    tile_svgs = []
    for idx, (label, value, val_color) in enumerate(tiles):
        col = idx % 3
        row = idx // 3
        x = 20 + col * 155
        y = 54 + row * 60
        tile_svgs.append(
            f"""
            <rect x="{x}" y="{y}" width="145" height="50" rx="8" fill="{t['card']}" stroke="{t['border']}" stroke-width="1"/>
            <text x="{x + 12}" y="{y + 20}" fill="{t['muted']}" font-size="10.5" font-weight="500">{html.escape(label)}</text>
            <text x="{x + 12}" y="{y + 40}" fill="{val_color}" font-size="16" font-weight="700" font-family="ui-monospace, SFMono-Regular, Menlo, monospace">{html.escape(value)}</text>
            """
        )

    return f"""<svg width="495" height="196" viewBox="0 0 495 196" fill="none" xmlns="http://www.w3.org/2000/svg">
  <style>
    text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }}
  </style>
  <rect x="0.5" y="0.5" width="494" height="195" rx="10" fill="{t['bg']}" stroke="{t['border']}" stroke-width="1"/>
  <text x="20" y="32" fill="{t['title']}" font-size="14" font-weight="700">GitHub Engineering Activity</text>
  <text x="475" y="32" text-anchor="end" fill="{t['muted']}" font-size="10.5" font-family="ui-monospace, SFMono-Regular, Menlo, monospace">@{html.escape(USERNAME)}</text>
  {''.join(tile_svgs)}
  <text x="20" y="185" fill="{t['muted']}" font-size="10" font-family="ui-monospace, SFMono-Regular, Menlo, monospace">Auto-synced via GitHub GraphQL</text>
  <text x="475" y="185" text-anchor="end" fill="{t['muted']}" font-size="10" font-family="ui-monospace, SFMono-Regular, Menlo, monospace">Updated {html.escape(stats['updated_utc'])}</text>
</svg>
"""


def render_langs_svg(stats: dict, theme_name: str) -> str:
    t = THEMES[theme_name]
    langs = stats["top_langs"]
    bar_x, bar_y, bar_w, bar_h = 20, 52, 455, 10

    bar_segments = []
    cur_x = float(bar_x)
    total_pct = sum(l["pct"] for l in langs) or 100.0
    for lang in langs:
        seg_w = (lang["pct"] / total_pct) * bar_w
        bar_segments.append(
            f'<rect x="{cur_x:.2f}" y="{bar_y}" width="{seg_w + 0.5:.2f}" height="{bar_h}" fill="{html.escape(lang["color"])}"/>'
        )
        cur_x += seg_w

    legend_items = []
    for idx, lang in enumerate(langs):
        col = idx % 2
        row = idx // 2
        x = 20 + col * 232
        y = 88 + row * 30
        legend_items.append(
            f"""
            <circle cx="{x + 5}" cy="{y - 4}" r="4.5" fill="{html.escape(lang['color'])}"/>
            <text x="{x + 16}" y="{y}" fill="{t['text']}" font-size="12" font-weight="600">{html.escape(lang['name'])}</text>
            <text x="{x + 215}" y="{y}" text-anchor="end" fill="{t['muted']}" font-size="11.5" font-family="ui-monospace, SFMono-Regular, Menlo, monospace">{lang['pct']:.1f}%</text>
            """
        )

    return f"""<svg width="495" height="196" viewBox="0 0 495 196" fill="none" xmlns="http://www.w3.org/2000/svg">
  <style>
    text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }}
  </style>
  <defs>
    <clipPath id="bar-clip">
      <rect x="{bar_x}" y="{bar_y}" width="{bar_w}" height="{bar_h}" rx="5"/>
    </clipPath>
  </defs>
  <rect x="0.5" y="0.5" width="494" height="195" rx="10" fill="{t['bg']}" stroke="{t['border']}" stroke-width="1"/>
  <text x="20" y="32" fill="{t['title']}" font-size="14" font-weight="700">Most Used Languages</text>
  <text x="475" y="32" text-anchor="end" fill="{t['muted']}" font-size="10.5" font-family="ui-monospace, SFMono-Regular, Menlo, monospace">Owned Repos</text>
  <rect x="{bar_x}" y="{bar_y}" width="{bar_w}" height="{bar_h}" rx="5" fill="{t['bar_bg']}"/>
  <g clip-path="url(#bar-clip)">
    {''.join(bar_segments)}
  </g>
  {''.join(legend_items)}
  <text x="20" y="185" fill="{t['muted']}" font-size="10" font-family="ui-monospace, SFMono-Regular, Menlo, monospace">Excludes forks · Byte-weighted</text>
  <text x="475" y="185" text-anchor="end" fill="{t['muted']}" font-size="10" font-family="ui-monospace, SFMono-Regular, Menlo, monospace">Updated {html.escape(stats['updated_utc'])}</text>
</svg>
"""


def render_cf_svg(cf: dict | None, updated_utc: str, theme_name: str) -> str:
    t = THEMES[theme_name]
    if cf is None:
        return f"""<svg width="840" height="114" viewBox="0 0 840 114" fill="none" xmlns="http://www.w3.org/2000/svg">
  <style>text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }}</style>
  <rect x="0.5" y="0.5" width="839" height="113" rx="10" fill="{t['bg']}" stroke="{t['border']}" stroke-width="1"/>
  <text x="20" y="42" fill="{t['title']}" font-size="14" font-weight="700">Competitive Programming · Codeforces ({html.escape(CF_HANDLE)})</text>
  <text x="20" y="70" fill="{t['muted']}" font-size="12">Codeforces API temporarily unavailable — will retry on next 4-hour sync.</text>
</svg>
"""

    rank_str = cf["rank"] or "unrated"
    rank_color = CF_RANK_COLORS.get(rank_str.lower(), t["accent"])
    tags_line = " · ".join(cf["top_tags"]) if cf["top_tags"] else "algorithms · data structures"

    cols = [
        ("Current Rating", str(cf["rating"]), rank_color),
        ("Peak Rating", str(cf["max_rating"]), t["title"]),
        ("Problems Solved", fmt_num(cf["solved"]), t["green"]),
        ("Rank", rank_str.title(), rank_color),
    ]

    tiles = []
    for idx, (label, value, color) in enumerate(cols):
        x = 20 + idx * 202
        tiles.append(
            f"""
            <rect x="{x}" y="44" width="192" height="46" rx="8" fill="{t['card']}" stroke="{t['border']}" stroke-width="1"/>
            <text x="{x + 12}" y="61" fill="{t['muted']}" font-size="10.5" font-weight="500">{html.escape(label)}</text>
            <text x="{x + 12}" y="80" fill="{color}" font-size="14.5" font-weight="700" font-family="ui-monospace, SFMono-Regular, Menlo, monospace">{html.escape(value)}</text>
            """
        )

    return f"""<svg width="840" height="116" viewBox="0 0 840 116" fill="none" xmlns="http://www.w3.org/2000/svg">
  <style>
    text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }}
  </style>
  <rect x="0.5" y="0.5" width="839" height="115" rx="10" fill="{t['bg']}" stroke="{t['border']}" stroke-width="1"/>
  <text x="20" y="28" fill="{t['title']}" font-size="13.5" font-weight="700">Codeforces Profile · {html.escape(cf['handle'])}</text>
  <text x="820" y="28" text-anchor="end" fill="{t['muted']}" font-size="10.5" font-family="ui-monospace, SFMono-Regular, Menlo, monospace">Top tags: {html.escape(tags_line)}</text>
  {''.join(tiles)}
  <text x="820" y="106" text-anchor="end" fill="{t['muted']}" font-size="9.5" font-family="ui-monospace, SFMono-Regular, Menlo, monospace">Synced {html.escape(updated_utc)}</text>
</svg>
"""


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stats = fetch_github_stats()
    cf = fetch_codeforces_stats()

    for theme in ("dark", "light"):
        (OUT_DIR / f"stats-{theme}.svg").write_text(render_stats_svg(stats, theme), encoding="utf-8")
        (OUT_DIR / f"langs-{theme}.svg").write_text(render_langs_svg(stats, theme), encoding="utf-8")
        (OUT_DIR / f"cf-{theme}.svg").write_text(render_cf_svg(cf, stats["updated_utc"], theme), encoding="utf-8")

    print(
        f"Generated profile SVGs for {USERNAME}: "
        f"{stats['commits']} commits, {stats['prs']} PRs, {stats['issues']} issues, "
        f"{stats['stars']} stars, streak {stats['current_streak']}d."
    )


if __name__ == "__main__":
    main()
