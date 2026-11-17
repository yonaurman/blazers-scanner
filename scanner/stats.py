"""Player game log and averages from ESPN's public game log endpoint (unofficial, so failures are non-fatal)."""

import logging

from . import config
from .http import get_session
from .storage import utc_now

logger = logging.getLogger(__name__)

GAMELOG_URL = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/{id}/gamelog"

# ESPN stat name -> our key. "made-attempted" stats are split into two keys.
_SIMPLE = {"minutes": "min", "points": "pts", "totalRebounds": "reb", "assists": "ast",
           "steals": "stl", "blocks": "blk", "turnovers": "tov", "fouls": "pf"}
_SPLIT = {"fieldGoalsMade-fieldGoalsAttempted": ("fgm", "fga"),
          "threePointFieldGoalsMade-threePointFieldGoalsAttempted": ("tpm", "tpa"),
          "freeThrowsMade-freeThrowsAttempted": ("ftm", "fta")}


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_line(names: list[str], values: list[str]) -> dict:
    """Turn ESPN's parallel names/values arrays into {'pts': 27.0, 'fgm': 11.0, 'fga': 19.0, ...}."""
    line: dict = {}
    for name, value in zip(names, values):
        if name in _SIMPLE:
            line[_SIMPLE[name]] = _num(value)
        elif name in _SPLIT:
            made, _, att = str(value).partition("-")
            line[_SPLIT[name][0]], line[_SPLIT[name][1]] = _num(made), _num(att)
    return line


def _team_score(event: dict) -> tuple[int | None, int | None]:
    """(our score, their score), whichever side we were on."""
    home, away = _num(event.get("homeTeamScore")), _num(event.get("awayTeamScore"))
    if home is None or away is None:
        return None, None
    ours, theirs = (away, home) if event.get("atVs") == "@" else (home, away)
    return int(ours), int(theirs)


def averages(games: list[dict]) -> dict:
    """Per-game averages over the given games; shooting percentages are totals-based, like ESPN's."""
    if not games:
        return {}

    def avg(key):
        vals = [g[key] for g in games if g.get(key) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    def pct(made, att):
        a = sum(g.get(att) or 0 for g in games)
        return round(100 * sum(g.get(made) or 0 for g in games) / a, 1) if a else None

    out = {k: avg(k) for k in ("min", "pts", "reb", "ast", "stl", "blk", "tov", "pf")}
    out.update(fg_pct=pct("fgm", "fga"), fg3_pct=pct("tpm", "tpa"), ft_pct=pct("ftm", "fta"))
    return out


def parse_gamelog(data: dict) -> dict | None:
    """Regular-season games (newest first) with season and recent averages, or None if there are none."""
    season = next((s for s in data.get("seasonTypes", []) if "Regular Season" in s.get("displayName", "")), None)
    if not season:
        return None
    names, events = data.get("names", []), data.get("events", {})
    games, team = [], None
    for category in season.get("categories", []):
        for entry in category.get("events", []):
            event = events.get(entry.get("eventId"))
            if not event:
                continue
            ours, theirs = _team_score(event)
            team = team or (event.get("team") or {}).get("abbreviation")
            games.append({
                "date": event.get("gameDate"),
                "home": event.get("atVs") != "@",
                "opponent": (event.get("opponent") or {}).get("abbreviation", ""),
                "opponent_name": (event.get("opponent") or {}).get("displayName", ""),
                "result": event.get("gameResult"),
                "score": ours, "opp_score": theirs,
                "url": f"https://www.espn.com/nba/boxscore/_/gameId/{event.get('id')}",
                **_parse_line(names, entry.get("stats", [])),
            })
    # Games with no minutes are DNPs and do not count toward per-game averages.
    games = [g for g in games if g.get("min")]
    if not games:
        return None
    games.sort(key=lambda g: g["date"] or "", reverse=True)
    recent = games[:config.PLAYER_STATS_RECENT_GAMES]
    return {
        "season": season.get("displayName"),
        "team": team,
        "games_played": len(games),
        "averages": averages(games),
        "recent_count": len(recent),
        "recent_averages": averages(recent),
        "games": games,
    }


def fetch_player_stats(player_id: int = config.PLAYER_ESPN_ID) -> dict | None:
    """Fetch and parse the player's game log. Returns None on any failure (callers should degrade gracefully)."""
    try:
        res = get_session().get(GAMELOG_URL.format(id=player_id), timeout=config.REQUEST_TIMEOUT_SEC)
        res.raise_for_status()
        stats = parse_gamelog(res.json())
    except Exception as e:
        logger.warning("Player stats fetch failed: %s", e)
        return None
    if stats:
        stats.update(player_id=player_id, player="Deni Avdija", fetched_at=utc_now(),
                     source_url=f"https://www.espn.com/nba/player/gamelog/_/id/{player_id}")
    return stats
