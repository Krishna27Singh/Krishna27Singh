#!/usr/bin/env python3
"""Generate GitHub SVG graphs (Streak Ring, Language Donut, 30-Day Activity) and update README activity table."""

from __future__ import annotations

import datetime as dt
import html
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

USERNAME = os.environ.get("GITHUB_USERNAME", "Krishna27Singh")
GITHUB_TOKEN = os.environ.get("GH_STATS_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
OUT_DIR = Path(os.environ.get("OUT_DIR", "dist"))
README_PATH = Path(os.environ.get("README_PATH", "README.md"))

TABLE_START = "<!-- ACTIVITY_TABLE:START -->"
TABLE_END = "<!-- ACTIVITY_TABLE:END -->"

THEMES = {
    "dark": {
        "bg": "#0d1117",
        "card": "#161b22",
        "border": "#30363d",
        "grid": "#21262d",
        "title": "#f0f6fc",
        "text": "#e6edf3",
        "muted": "#8b949e",
        "accent": "#58a6ff",
        "green": "#3fb950",
        "track": "#21262d",
    },
    "light": {
        "bg": "#ffffff",
        "card": "#f6f8fa",
        "border": "#d0d7de",
        "grid": "#eaeef2",
        "title": "#1f2328",
        "text": "#1f2328",
        "muted": "#656d76",
        "accent": "#0969da",
        "green": "#1a7f37",
        "track": "#eaeef2",
    },
}


def graphql(query: str, variables: dict) -> dict:
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "Krishna27Singh-Profile-Generator",
        },
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        res = json.loads(resp.read().decode("utf-8"))
    if "errors" in res:
        raise RuntimeError(f"GraphQL error: {res['errors']}")
    return res["data"]


def rest_commit_search_count() -> int:
    """Best-effort total authored commit count across all repos accessible to the token."""
    try:
        q = urllib.parse.quote(f"author:{USERNAME}")
        req = urllib.request.Request(
            f"https://api.github.com/search/commits?q={q}&per_page=1",
            headers={
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "Krishna27Singh-Profile-Generator",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return int(data.get("total_count", 0))
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError):
        return 0


def fetch_github_stats() -> dict:
    user_query = """
    query($login: String!, $issueQuery: String!, $prQuery: String!) {
      user(login: $login) {
        createdAt
        pullRequests { totalCount }
        issues { totalCount }
        repositoriesContributedTo(
          contributionTypes: [COMMIT, ISSUE, PULL_REQUEST, REPOSITORY]
          includeUserRepositories: true
        ) { totalCount }
      }
      issueSearch: search(query: $issueQuery, type: ISSUE, first: 1) { issueCount }
      prSearch: search(query: $prQuery, type: ISSUE, first: 1) { issueCount }
    }
    """
    top = graphql(
        user_query,
        {
            "login": USERNAME,
            "issueQuery": f"author:{USERNAME} is:issue",
            "prQuery": f"author:{USERNAME} is:pr",
        },
    )
    user = top["user"]
    created_at = dt.datetime.fromisoformat(user["createdAt"].replace("Z", "+00:00"))
    now = dt.datetime.now(dt.timezone.utc)

    # Owned non-fork repos for stars + byte-weighted languages
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

    contrib_query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          totalCommitContributions
          restrictedContributionsCount
          totalIssueContributions
          totalPullRequestContributions
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

    def fetch_window(start_dt: dt.datetime, end_dt: dt.datetime) -> dict:
        return graphql(
            contrib_query,
            {
                "login": USERNAME,
                "from": start_dt.isoformat().replace("+00:00", "Z"),
                "to": end_dt.isoformat().replace("+00:00", "Z"),
            },
        )["user"]["contributionsCollection"]

    all_time_contributions = 0
    all_time_commits = 0
    all_time_issues_col = 0
    all_time_prs_col = 0
    all_time_reviews = 0
    this_year_stats: dict[str, int] = {}
    days_map: dict[str, int] = {}

    for year in range(created_at.year, now.year + 1):
        start = max(created_at, dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc))
        end = min(now, dt.datetime(year, 12, 31, 23, 59, 59, tzinfo=dt.timezone.utc))
        col = fetch_window(start, end)

        yr_contrib = col["contributionCalendar"]["totalContributions"]
        yr_commits = col["totalCommitContributions"] + col["restrictedContributionsCount"]
        yr_issues = col["totalIssueContributions"]
        yr_prs = col["totalPullRequestContributions"]
        yr_reviews = col["totalPullRequestReviewContributions"]

        all_time_contributions += yr_contrib
        all_time_commits += yr_commits
        all_time_issues_col += yr_issues
        all_time_prs_col += yr_prs
        all_time_reviews += yr_reviews

        if year == now.year:
            this_year_stats = {
                "contributions": yr_contrib,
                "commits": yr_commits,
                "prs": yr_prs,
                "issues": yr_issues,
                "reviews": yr_reviews,
            }

        for week in col["contributionCalendar"]["weeks"]:
            for day in week["contributionDays"]:
                days_map[day["date"]] = max(days_map.get(day["date"], 0), day["contributionCount"])

    # Dedicated last-30-days window for accurate rolling breakdown
    col_30 = fetch_window(now - dt.timedelta(days=30), now)
    last_30_stats = {
        "contributions": col_30["contributionCalendar"]["totalContributions"],
        "commits": col_30["totalCommitContributions"] + col_30["restrictedContributionsCount"],
        "prs": col_30["totalPullRequestContributions"],
        "issues": col_30["totalIssueContributions"],
        "reviews": col_30["totalPullRequestReviewContributions"],
    }

    # Combine sources to prevent undercounting external/org repos
    search_commits = rest_commit_search_count()
    best_commits = max(all_time_commits, search_commits)
    best_prs = max(user["pullRequests"]["totalCount"], top["prSearch"]["issueCount"], all_time_prs_col)
    best_issues = max(user["issues"]["totalCount"], top["issueSearch"]["issueCount"], all_time_issues_col)

    # Compute streaks
    longest_streak = 0
    run = 0
    for d in sorted(days_map.keys()):
        if days_map[d] > 0:
            run += 1
            longest_streak = max(longest_streak, run)
        else:
            run = 0

    today = now.date()
    current_streak = 0
    anchor = today if days_map.get(today.isoformat(), 0) > 0 else (today - dt.timedelta(days=1))
    if days_map.get(anchor.isoformat(), 0) > 0:
        cur_dt = anchor
        while days_map.get(cur_dt.isoformat(), 0) > 0:
            current_streak += 1
            cur_dt -= dt.timedelta(days=1)

    # Last 30 days daily series for area chart
    last_30_series = []
    for offset in range(29, -1, -1):
        day_dt = today - dt.timedelta(days=offset)
        last_30_series.append({"date": day_dt, "count": days_map.get(day_dt.isoformat(), 0)})

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
        "all_time": {
            "contributions": all_time_contributions,
            "commits": best_commits,
            "prs": best_prs,
            "issues": best_issues,
            "reviews": all_time_reviews,
        },
        "this_year": this_year_stats,
        "last_30_summary": last_30_stats,
        "stars": total_stars,
        "repos": user["repositoriesContributedTo"]["totalCount"],
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "last_30_series": last_30_series,
        "top_langs": top_langs,
        "year": now.year,
        "updated_utc": now.strftime("%b %d · %H:%M UTC"),
    }


def render_stats_svg(stats: dict, theme_name: str) -> str:
    t = THEMES[theme_name]
    at = stats["all_time"]
    rows = [
        ("Contributions", f"{at['contributions']:,}"),
        ("Commits", f"{at['commits']:,}"),
        ("Pull Requests", f"{at['prs']:,}"),
        ("Issues Opened", f"{at['issues']:,}"),
        ("Stars Earned", f"{stats['stars']:,}"),
    ]

    row_svgs = []
    for idx, (label, val) in enumerate(rows):
        y = 64 + idx * 23
        row_svgs.append(
            f"""
            <text x="24" y="{y}" fill="{t['muted']}" font-size="12.5" font-weight="500">{html.escape(label)}</text>
            <text x="275" y="{y}" text-anchor="end" fill="{t['text']}" font-size="13" font-weight="700" font-family="ui-monospace, SFMono-Regular, Menlo, monospace">{html.escape(val)}</text>
            """
        )

    cx, cy, r = 392, 100, 45
    circ = 2 * math.pi * r
    denom = max(stats["longest_streak"], 14)
    ratio = min(1.0, stats["current_streak"] / denom) if stats["current_streak"] > 0 else 0.0
    dash = max(circ * ratio, 6.0 if stats["current_streak"] > 0 else 0.0)

    return f"""<svg width="495" height="200" viewBox="0 0 495 200" fill="none" xmlns="http://www.w3.org/2000/svg">
  <style>text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }}</style>
  <rect x="0.5" y="0.5" width="494" height="199" rx="10" fill="{t['bg']}" stroke="{t['border']}" stroke-width="1"/>
  <text x="24" y="34" fill="{t['title']}" font-size="14" font-weight="700">GitHub Stats</text>
  <line x1="304" y1="46" x2="304" y2="168" stroke="{t['border']}" stroke-width="1"/>
  {''.join(row_svgs)}
  <circle cx="{cx}" cy="{cy}" r="{r}" stroke="{t['track']}" stroke-width="8" fill="none"/>
  <circle cx="{cx}" cy="{cy}" r="{r}" stroke="{t['green']}" stroke-width="8" stroke-linecap="round"
    stroke-dasharray="{dash:.2f} {circ:.2f}" transform="rotate(-90 {cx} {cy})" fill="none"/>
  <text x="{cx}" y="{cy + 4}" text-anchor="middle" fill="{t['title']}" font-size="20" font-weight="700" font-family="ui-monospace, SFMono-Regular, Menlo, monospace">{stats['current_streak']}d</text>
  <text x="{cx}" y="{cy + 20}" text-anchor="middle" fill="{t['muted']}" font-size="10">streak</text>
  <text x="{cx}" y="168" text-anchor="middle" fill="{t['muted']}" font-size="11">Longest: <tspan fill="{t['text']}" font-weight="600">{stats['longest_streak']}d</tspan></text>
  <text x="24" y="188" fill="{t['muted']}" font-size="9.5" font-family="ui-monospace, SFMono-Regular, Menlo, monospace">@{html.escape(USERNAME)} · Updated {html.escape(stats['updated_utc'])}</text>
</svg>
"""


def render_langs_svg(stats: dict, theme_name: str) -> str:
    t = THEMES[theme_name]
    langs = stats["top_langs"]
    cx, cy, r = 102, 108, 50
    circ = 2 * math.pi * r
    total_pct = sum(l["pct"] for l in langs) or 100.0

    arcs = []
    offset = 0.0
    for lang in langs:
        seg = (lang["pct"] / total_pct) * circ
        arcs.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{html.escape(lang["color"])}" '
            f'stroke-width="14" stroke-dasharray="{seg:.2f} {circ:.2f}" '
            f'stroke-dashoffset="{-offset:.2f}" transform="rotate(-90 {cx} {cy})"/>'
        )
        offset += seg

    top_name = langs[0]["name"] if langs else "Code"
    top_pct = f"{langs[0]['pct']:.0f}%" if langs else "—"

    legend = []
    for idx, lang in enumerate(langs):
        y = 62 + idx * 21
        legend.append(
            f"""
            <circle cx="196" cy="{y - 4}" r="4.5" fill="{html.escape(lang['color'])}"/>
            <text x="210" y="{y}" fill="{t['text']}" font-size="12.5" font-weight="500">{html.escape(lang['name'])}</text>
            <text x="470" y="{y}" text-anchor="end" fill="{t['muted']}" font-size="12" font-family="ui-monospace, SFMono-Regular, Menlo, monospace">{lang['pct']:.1f}%</text>
            """
        )

    return f"""<svg width="495" height="200" viewBox="0 0 495 200" fill="none" xmlns="http://www.w3.org/2000/svg">
  <style>text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }}</style>
  <rect x="0.5" y="0.5" width="494" height="199" rx="10" fill="{t['bg']}" stroke="{t['border']}" stroke-width="1"/>
  <text x="24" y="34" fill="{t['title']}" font-size="14" font-weight="700">Most Used Languages</text>
  <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{t['track']}" stroke-width="14"/>
  {''.join(arcs)}
  <text x="{cx}" y="{cy + 2}" text-anchor="middle" fill="{t['title']}" font-size="14" font-weight="700">{html.escape(top_pct)}</text>
  <text x="{cx}" y="{cy + 17}" text-anchor="middle" fill="{t['muted']}" font-size="10">{html.escape(top_name)}</text>
  {''.join(legend)}
  <text x="470" y="188" text-anchor="end" fill="{t['muted']}" font-size="9.5" font-family="ui-monospace, SFMono-Regular, Menlo, monospace">Owned repos · Excludes forks</text>
</svg>
"""


def render_activity_svg(stats: dict, theme_name: str) -> str:
    t = THEMES[theme_name]
    series = stats["last_30_series"]
    counts = [d["count"] for d in series]
    total_30 = sum(counts)
    peak_30 = max(counts) if counts else 0

    y_max = 4 if peak_30 <= 4 else ((peak_30 + 3) // 4) * 4

    x_left, x_right = 54.0, 964.0
    y_top, y_bottom = 58.0, 186.0
    plot_w = x_right - x_left
    plot_h = y_bottom - y_top
    n = len(series)

    grid_svgs = []
    for i in range(5):
        val = (y_max // 4) * i
        y = y_bottom - (val / y_max) * plot_h
        grid_svgs.append(
            f'<line x1="{x_left:.1f}" y1="{y:.1f}" x2="{x_right:.1f}" y2="{y:.1f}" '
            f'stroke="{t["grid"]}" stroke-width="1" stroke-dasharray="3 3"/>'
        )
        grid_svgs.append(
            f'<text x="{x_left - 10:.1f}" y="{y + 3.5:.1f}" text-anchor="end" fill="{t["muted"]}" '
            f'font-size="10.5" font-family="ui-monospace, SFMono-Regular, Menlo, monospace">{val}</text>'
        )

    pts = []
    for i, item in enumerate(series):
        x = x_left + (i / max(n - 1, 1)) * plot_w
        y = y_bottom - (item["count"] / y_max) * plot_h
        pts.append((x, y, item))

    line_d = " ".join(f"{'M' if i == 0 else 'L'} {x:.1f} {y:.1f}" for i, (x, y, _) in enumerate(pts))
    area_d = (
        f"M {pts[0][0]:.1f} {y_bottom:.1f} "
        + " ".join(f"L {x:.1f} {y:.1f}" for x, y, _ in pts)
        + f" L {pts[-1][0]:.1f} {y_bottom:.1f} Z"
    )

    dots = []
    x_labels = []
    for i, (x, y, item) in enumerate(pts):
        if item["count"] > 0:
            dots.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{t["bg"]}" stroke="{t["accent"]}" stroke-width="2"/>'
            )
        if i % 5 == 0 or i == n - 1:
            lbl = item["date"].strftime("%b %d")
            anchor = "start" if i == 0 else ("end" if i == n - 1 else "middle")
            x_labels.append(
                f'<text x="{x:.1f}" y="206" text-anchor="{anchor}" fill="{t["muted"]}" '
                f'font-size="10" font-family="ui-monospace, SFMono-Regular, Menlo, monospace">{lbl}</text>'
            )

    return f"""<svg width="990" height="230" viewBox="0 0 990 230" fill="none" xmlns="http://www.w3.org/2000/svg">
  <style>text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }}</style>
  <defs>
    <linearGradient id="area-grad-{theme_name}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{t['accent']}" stop-opacity="0.28"/>
      <stop offset="100%" stop-color="{t['accent']}" stop-opacity="0.0"/>
    </linearGradient>
  </defs>
  <rect x="0.5" y="0.5" width="989" height="229" rx="10" fill="{t['bg']}" stroke="{t['border']}" stroke-width="1"/>
  <text x="24" y="34" fill="{t['title']}" font-size="14" font-weight="700">Contribution Activity (Last 30 Days)</text>
  <text x="964" y="34" text-anchor="end" fill="{t['muted']}" font-size="11" font-family="ui-monospace, SFMono-Regular, Menlo, monospace">{total_30:,} contributions · Peak {peak_30}/day</text>
  {''.join(grid_svgs)}
  <path d="{area_d}" fill="url(#area-grad-{theme_name})"/>
  <path d="{line_d}" stroke="{t['accent']}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round" fill="none"/>
  {''.join(dots)}
  {''.join(x_labels)}
</svg>
"""


def update_readme_table(stats: dict) -> None:
    if not README_PATH.exists():
        return
    content = README_PATH.read_text(encoding="utf-8")
    if TABLE_START not in content or TABLE_END not in content:
        return

    l30 = stats["last_30_summary"]
    yr = stats["this_year"]
    at = stats["all_time"]

    table_md = "\n".join(
        [
            TABLE_START,
            f"| Metric | Last 30 Days | {stats['year']} | All-Time |",
            "| :--- | :---: | :---: | :---: |",
            f"| **Total Contributions** | `{l30['contributions']:,}` | `{yr['contributions']:,}` | **`{at['contributions']:,}`** |",
            f"| **Commits** | `{l30['commits']:,}` | `{yr['commits']:,}` | **`{at['commits']:,}`** |",
            f"| **Pull Requests** | `{l30['prs']:,}` | `{yr['prs']:,}` | **`{at['prs']:,}`** |",
            f"| **Issues Opened** | `{l30['issues']:,}` | `{yr['issues']:,}` | **`{at['issues']:,}`** |",
            f"| **Code Reviews** | `{l30['reviews']:,}` | `{yr['reviews']:,}` | **`{at['reviews']:,}`** |",
            f"| **Repositories Contributed To** | — | — | **`{stats['repos']:,}`** |",
            f"| **Stars Earned** | — | — | **`{stats['stars']:,}`** |",
            f"| **Streaks (Current / Longest)** | — | — | **`{stats['current_streak']}d / {stats['longest_streak']}d`** |",
            "",
            f"<sub>Auto-synced every 4 hours via GitHub GraphQL · Last updated {stats['updated_utc']}</sub>",
            TABLE_END,
        ]
    )

    pattern = re.compile(rf"{re.escape(TABLE_START)}.*?{re.escape(TABLE_END)}", re.DOTALL)
    updated = pattern.sub(table_md, content, count=1)
    if updated != content:
        README_PATH.write_text(updated, encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stats = fetch_github_stats()

    for theme in ("dark", "light"):
        (OUT_DIR / f"stats-ring-{theme}.svg").write_text(render_stats_svg(stats, theme), encoding="utf-8")
        (OUT_DIR / f"langs-donut-{theme}.svg").write_text(render_langs_svg(stats, theme), encoding="utf-8")
        (OUT_DIR / f"activity-graph-{theme}.svg").write_text(render_activity_svg(stats, theme), encoding="utf-8")

    update_readme_table(stats)

    at = stats["all_time"]
    print(
        f"Updated profile for {USERNAME}: {at['contributions']} contributions, "
        f"{at['commits']} commits, {at['prs']} PRs, {at['issues']} issues, "
        f"streak {stats['current_streak']}d."
    )


if __name__ == "__main__":
    main()
