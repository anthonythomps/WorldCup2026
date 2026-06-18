from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict
from typing import Any

from models import Match, PersonRecord, TeamRecord


def canonical_team_name(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def build_owner_lookup(draw: dict[str, list[str]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for owner, teams in draw.items():
        for team in teams or []:
            lookup[canonical_team_name(str(team))] = owner
    return lookup


def owner_for_team(team: str, owner_lookup: dict[str, str]) -> str | None:
    return owner_lookup.get(canonical_team_name(team))


def _first_present(data: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def _dig(data: Any, path: tuple[str, ...]) -> Any:
    current = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _team_name(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, dict):
        return None

    nested_team = value.get("team")
    if isinstance(nested_team, dict):
        nested_name = _team_name(nested_team)
        if nested_name:
            return nested_name

    for key in (
        "name",
        "teamName",
        "team_name",
        "country",
        "countryName",
        "displayName",
        "shortName",
        "code",
    ):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _as_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return _team_name(value)
    return str(value)


def _extract_collection(payload: Any, preferred_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        return []

    for key in (*preferred_keys, "data", "results", "items"):
        if key in payload:
            return _extract_collection(payload[key], preferred_keys)

    rows: list[dict[str, Any]] = []
    for value in payload.values():
        if isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
    return rows


def _score_from_match(item: dict[str, Any], side: str, team_obj: Any) -> int | None:
    side_keys = {
        "home": (
            "home_score",
            "homeScore",
            "home_goals",
            "homeGoals",
            "scoreHome",
            "goalsHome",
            "homeTeamScore",
        ),
        "away": (
            "away_score",
            "awayScore",
            "away_goals",
            "awayGoals",
            "scoreAway",
            "goalsAway",
            "awayTeamScore",
        ),
    }

    score = _coerce_int(_first_present(item, side_keys[side]))
    if score is not None:
        return score

    for container_key in ("score", "scores", "result", "fullTime", "full_time"):
        container = item.get(container_key)
        if not isinstance(container, dict):
            continue

        score = _coerce_int(
            _first_present(
                container,
                (
                    side,
                    f"{side}Score",
                    f"{side}_score",
                    f"{side}Goals",
                    f"{side}_goals",
                ),
            )
        )
        if score is not None:
            return score

        for nested_key in ("fullTime", "full_time", "regularTime", "regular_time", "current"):
            score = _coerce_int(_dig(container, (nested_key, side)))
            if score is not None:
                return score

    if isinstance(team_obj, dict):
        return _coerce_int(
            _first_present(team_obj, ("score", "goals", "goalsFor", "goals_for"))
        )

    return None


def normalise_matches(payload: Any) -> list[Match]:
    matches: list[Match] = []
    for item in _extract_collection(payload, ("matches", "fixtures")):
        home_obj = _first_present(item, ("home_team", "homeTeam", "home", "team1", "teamA"))
        away_obj = _first_present(item, ("away_team", "awayTeam", "away", "team2", "teamB"))

        home_team = _team_name(home_obj)
        away_team = _team_name(away_obj)
        if not home_team or not away_team:
            continue

        matches.append(
            Match(
                home_team=home_team,
                away_team=away_team,
                home_score=_score_from_match(item, "home", home_obj),
                away_score=_score_from_match(item, "away", away_obj),
                kickoff=_as_text(
                    _first_present(
                        item,
                        (
                            "kickoffUtc",
                            "kickoffUTC",
                            "kickoff_utc",
                            "utcDate",
                            "datetime",
                            "kickoff",
                            "kickoff_at",
                            "kickoffAt",
                            "date",
                            "matchDate",
                        ),
                    )
                ),
                stage=_as_text(_first_present(item, ("stage", "round", "phase", "competitionStage"))),
                group=_as_text(_first_present(item, ("group", "groupName", "group_name"))),
                status=_as_text(_first_present(item, ("status", "state", "matchStatus", "match_status"))),
                raw=item,
            )
        )
    return matches


def _ensure_record(records: dict[str, TeamRecord], team: str, owner_lookup: dict[str, str]) -> TeamRecord:
    key = canonical_team_name(team)
    if key not in records:
        records[key] = TeamRecord(name=team, owner=owner_for_team(team, owner_lookup))
    return records[key]


def compute_team_records(
    matches: list[Match],
    draw: dict[str, list[str]],
    *,
    group_stage_only: bool = False,
) -> list[TeamRecord]:
    owner_lookup = build_owner_lookup(draw)
    records: dict[str, TeamRecord] = {}

    for owner, teams in draw.items():
        for team in teams or []:
            key = canonical_team_name(str(team))
            records.setdefault(key, TeamRecord(name=str(team), owner=owner))

    for match in matches:
        if not match.played:
            continue
        if group_stage_only and not match.is_group_stage:
            continue

        home = _ensure_record(records, match.home_team, owner_lookup)
        away = _ensure_record(records, match.away_team, owner_lookup)

        if match.group:
            home.group = home.group or match.group
            away.group = away.group or match.group

        home.add_result(match.home_score or 0, match.away_score or 0)
        away.add_result(match.away_score or 0, match.home_score or 0)

    return list(records.values())


def _standings_rows(payload: Any, group: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    if isinstance(payload, list):
        for item in payload:
            rows.extend(_standings_rows(item, group))
        return rows

    if not isinstance(payload, dict):
        return rows

    team = _team_name(_first_present(payload, ("team", "country", "name", "teamName", "team_name")))
    points = _coerce_int(_first_present(payload, ("points", "pts")))
    if team and points is not None:
        enriched = dict(payload)
        enriched["_group"] = group or _first_present(payload, ("group", "groupName", "group_name"))
        rows.append(enriched)
        return rows

    next_group = group or _first_present(payload, ("group", "groupName", "group_name", "name"))
    for key in ("standings", "table", "rows", "teams", "data", "groups"):
        if key in payload:
            rows.extend(_standings_rows(payload[key], next_group))

    for key, value in payload.items():
        if key in {"standings", "table", "rows", "teams", "data", "groups"}:
            continue
        if isinstance(value, (list, dict)):
            derived_group = next_group
            if isinstance(value, list) and key.lower().startswith("group"):
                derived_group = key
            rows.extend(_standings_rows(value, derived_group))

    return rows


def records_from_standings(payload: Any, draw: dict[str, list[str]]) -> list[TeamRecord]:
    owner_lookup = build_owner_lookup(draw)
    records: dict[str, TeamRecord] = {}

    for row in _standings_rows(payload):
        team = _team_name(_first_present(row, ("team", "country", "name", "teamName", "team_name")))
        if not team:
            continue

        played = _coerce_int(_first_present(row, ("played", "matchesPlayed", "mp", "gamesPlayed"))) or 0
        wins = _coerce_int(_first_present(row, ("wins", "won", "w"))) or 0
        draws = _coerce_int(_first_present(row, ("draws", "drawn", "d"))) or 0
        losses = _coerce_int(_first_present(row, ("losses", "lost", "l"))) or 0
        goals_for = _coerce_int(_first_present(row, ("goalsFor", "goals_for", "gf", "for"))) or 0
        goals_against = _coerce_int(
            _first_present(row, ("goalsAgainst", "goals_against", "ga", "against"))
        ) or 0
        gd = _coerce_int(_first_present(row, ("goalDifference", "goal_difference", "gd")))
        points = _coerce_int(_first_present(row, ("points", "pts"))) or 0

        record = TeamRecord(
            name=team,
            owner=owner_for_team(team, owner_lookup),
            group=row.get("_group"),
            goal_difference_override=gd,
            played=played,
            wins=wins,
            draws=draws,
            losses=losses,
            goals_for=goals_for,
            goals_against=goals_against,
            points=points,
        )
        records[canonical_team_name(team)] = record

    return list(records.values())


def rank_worst_teams(records: list[TeamRecord]) -> list[TeamRecord]:
    return sorted(records, key=lambda item: (item.points, item.goal_difference, item.goals_for, item.name))


def rank_best_teams(records: list[TeamRecord]) -> list[TeamRecord]:
    return sorted(records, key=lambda item: (-item.points, -item.goal_difference, -item.goals_for, item.name))


def rank_people(records: list[TeamRecord], draw: dict[str, list[str]]) -> list[PersonRecord]:
    by_team = {canonical_team_name(record.name): record for record in records}
    leaderboard: list[PersonRecord] = []

    for person, teams in draw.items():
        person_record = PersonRecord(name=person, teams=[str(team) for team in teams or []])
        for team in person_record.teams:
            record = by_team.get(canonical_team_name(team), TeamRecord(name=team, owner=person))
            person_record.played += record.played
            person_record.points += record.points
            person_record.goal_difference += record.goal_difference
            person_record.goals_for += record.goals_for
            person_record.goals_against += record.goals_against
        leaderboard.append(person_record)

    return sorted(
        leaderboard,
        key=lambda item: (-item.points, -item.goal_difference, -item.goals_for, item.name),
    )


def team_record_rows(records: list[TeamRecord]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        row = asdict(record)
        row["goal_difference"] = record.goal_difference
        rows.append(row)
    return rows
