from __future__ import annotations

import html
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import yaml

from api_client import APIClientError, DEFAULT_BASE_URL, ZafronixAPIClient
from scoring import (
    build_owner_lookup,
    canonical_team_name,
    compute_team_records,
    normalise_matches,
    rank_people,
    rank_worst_teams,
    records_from_standings,
)
from storage import CacheStore, utc_now_iso


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.yaml"
DB_PATH = APP_DIR / "storage.db"


st.set_page_config(
    page_title="World Cup Sweepstake",
    page_icon=":trophy:",
    layout="wide",
)


def inject_css() -> None:
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.4rem;
            padding-bottom: 2rem;
        }
        .sweep-card {
            border: 1px solid #dde3ea;
            border-radius: 8px;
            padding: 16px 18px;
            background: #ffffff;
            min-height: 138px;
        }
        .sweep-card__label {
            color: #52606d;
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0;
            text-transform: uppercase;
            margin-bottom: 8px;
        }
        .sweep-card__title {
            color: #111827;
            font-size: 1.45rem;
            line-height: 1.2;
            font-weight: 750;
            margin-bottom: 8px;
        }
        .sweep-card__meta {
            color: #3f4d5a;
            font-size: 0.95rem;
            line-height: 1.45;
        }
        .status-pill {
            display: inline-block;
            border: 1px solid #d5dde5;
            border-radius: 999px;
            padding: 2px 9px;
            color: #334155;
            background: #f8fafc;
            font-size: 0.82rem;
        }
        .rank-line + .rank-line {
            border-top: 1px solid #edf1f5;
            margin-top: 8px;
            padding-top: 8px;
        }
        .rank-line__team {
            color: #111827;
            font-weight: 750;
        }
        .bracket-scroll {
            overflow-x: auto;
            padding-bottom: 8px;
        }
        .bracket-grid {
            display: grid;
            grid-template-columns: repeat(6, minmax(210px, 1fr));
            gap: 12px;
            min-width: 1320px;
        }
        .bracket-round {
            display: flex;
            flex-direction: column;
            gap: 10px;
        }
        .bracket-round__title {
            color: #475569;
            font-size: 0.86rem;
            font-weight: 750;
            margin: 0 0 2px;
            text-align: center;
        }
        .bracket-match {
            background: #ffffff;
            border: 1px solid #d9e1ea;
            border-radius: 8px;
            padding: 9px;
        }
        .bracket-match__meta {
            color: #52606d;
            display: flex;
            font-size: 0.76rem;
            gap: 7px;
            justify-content: space-between;
            margin-bottom: 7px;
            white-space: nowrap;
        }
        .bracket-team {
            align-items: center;
            border: 1px solid #e6ecf2;
            border-radius: 6px;
            display: flex;
            gap: 6px;
            justify-content: space-between;
            min-height: 34px;
            padding: 5px 7px;
        }
        .bracket-team + .bracket-team {
            margin-top: 5px;
        }
        .bracket-team__name {
            color: #111827;
            font-size: 0.86rem;
            font-weight: 700;
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .bracket-team__owner {
            color: #64748b;
            font-size: 0.74rem;
            font-weight: 600;
        }
        .bracket-team__score {
            color: #111827;
            font-size: 0.9rem;
            font-weight: 800;
            margin-left: auto;
        }
        div[data-testid="stMetricValue"] {
            font-size: 1.45rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Missing config file: {CONFIG_PATH}")
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}

    sweepstake = config.get("sweepstake") or {}
    if not isinstance(sweepstake, dict):
        raise ValueError("config.yaml must contain a sweepstake mapping of friend name to team list.")

    return config


def get_api_key() -> str:
    env_value = os.getenv("ZAFRONIX_API_KEY", "").strip()
    if env_value:
        return env_value

    try:
        return str(st.secrets.get("zafronix_api_key", "")).strip()
    except Exception:
        return ""


def metadata_key(name: str, year: int) -> str:
    return f"{name}:{year}"


def ensure_cache(
    *,
    year: int,
    api_key: str,
    base_url: str,
    refresh_interval_seconds: int,
    force: bool = False,
) -> dict[str, Any]:
    store = CacheStore(DB_PATH)
    last_refresh_key = metadata_key("last_refresh_utc", year)
    error_key = metadata_key("last_refresh_error", year)

    missing_payload = any(
        payload is None
        for payload in (
            store.get_payload("/matches", {"year": year}),
            store.get_payload("/standings", {"year": year}),
            store.get_payload("/tournaments"),
        )
    )
    should_refresh = force or missing_payload or store.metadata_is_stale(
        last_refresh_key, refresh_interval_seconds
    )

    if should_refresh:
        try:
            client = ZafronixAPIClient(api_key=api_key, cache_store=store, base_url=base_url)
            results = client.refresh_tournament_bundle(year)
            warnings = [result.warning for result in results.values() if result.warning]
            store.set_metadata(last_refresh_key, utc_now_iso())
            store.set_metadata(error_key, " | ".join(warnings) if warnings else "")
        except APIClientError as exc:
            store.set_metadata(error_key, str(exc))

    return {
        "matches": store.get_payload("/matches", {"year": year}),
        "standings": store.get_payload("/standings", {"year": year}),
        "tournaments": store.get_payload("/tournaments"),
        "last_refresh_utc": store.get_metadata(last_refresh_key),
        "last_refresh_error": store.get_metadata(error_key),
    }


@st.cache_data(ttl=60, show_spinner=False)
def build_dashboard_snapshot(
    matches_payload_json: str,
    standings_payload_json: str,
    draw_json: str,
    upcoming_fixture_days: int,
    current_match_window_hours: int,
) -> dict[str, Any]:
    matches_payload = json.loads(matches_payload_json)
    standings_payload = json.loads(standings_payload_json)
    draw = json.loads(draw_json)

    matches = normalise_matches(matches_payload)
    all_records = compute_team_records(matches, draw, group_stage_only=False)
    group_records = compute_team_records(matches, draw, group_stage_only=True)
    standings_records = records_from_standings(standings_payload, draw)

    if standings_records and not any(record.played for record in group_records):
        group_records = standings_records
    if standings_records and not any(record.played for record in all_records):
        all_records = standings_records

    knockout = knockout_match_rows(matches_payload)
    current = current_matches(matches, current_match_window_hours)
    upcoming = upcoming_matches(matches, upcoming_fixture_days)
    people = rank_people(all_records, draw)
    movements, movement_context = leaderboard_movements(matches, draw, people)

    return {
        "matches": matches,
        "knockout_matches": knockout,
        "current_matches": current,
        "upcoming_matches": upcoming,
        "all_records": all_records,
        "group_records": group_records,
        "worst_teams": rank_worst_teams(group_records),
        "people": people,
        "people_movements": movements,
        "movement_context": movement_context,
    }


def safe_json(payload: Any) -> str:
    return json.dumps(payload if payload is not None else {}, sort_keys=True, default=str)


def extract_api_matches(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("matches", "fixtures", "data", "results", "items"):
        if key in payload:
            return extract_api_matches(payload[key])
    return []


KNOCKOUT_STAGE_LABELS = {
    "round_of_32": "Round of 32",
    "round_of_16": "Round of 16",
    "quarter_final": "Quarter-final",
    "semi_final": "Semi-final",
    "final": "Final",
    "third_place": "Third place",
}

KNOCKOUT_STAGE_ORDER = [
    "round_of_32",
    "round_of_16",
    "quarter_final",
    "semi_final",
    "final",
    "third_place",
]

KNOCKOUT_STAGE_ALIASES = {
    "r32": "round_of_32",
    "r16": "round_of_16",
    "qf": "quarter_final",
    "sf": "semi_final",
    "thirdplace": "third_place",
    "third_place": "third_place",
    "final": "final",
}


def knockout_stage_key(match: dict[str, Any]) -> str | None:
    raw_stage = str(match.get("stageNormalized") or match.get("stage") or "").strip()
    normalized = raw_stage.casefold().replace("-", "_").replace(" ", "_")
    normalized = KNOCKOUT_STAGE_ALIASES.get(normalized, normalized)
    return normalized if normalized in KNOCKOUT_STAGE_LABELS else None


def knockout_match_rows(payload: Any) -> list[dict[str, Any]]:
    matches = []
    for match in extract_api_matches(payload):
        stage_key = knockout_stage_key(match)
        if not stage_key:
            continue
        enriched = dict(match)
        enriched["_stage_key"] = stage_key
        matches.append(enriched)

    matches.sort(
        key=lambda item: (
            KNOCKOUT_STAGE_ORDER.index(item["_stage_key"]),
            int(item.get("matchNo") or 0),
        )
    )
    return matches


def render_card(label: str, title: str, meta: str) -> None:
    st.markdown(
        f"""
        <div class="sweep-card">
            <div class="sweep-card__label">{html.escape(label)}</div>
            <div class="sweep-card__title">{html.escape(title)}</div>
            <div class="sweep-card__meta">{meta}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_knockout_bracket(matches: list[dict[str, Any]], draw: dict[str, list[str]]) -> None:
    grouped = {stage: [] for stage in KNOCKOUT_STAGE_ORDER}
    for match in matches:
        stage_key = match.get("_stage_key")
        if stage_key in grouped:
            grouped[stage_key].append(match)

    rounds = []
    for stage_key in KNOCKOUT_STAGE_ORDER:
        stage_matches = grouped[stage_key]
        if not stage_matches:
            continue

        cards = "".join(render_bracket_match(match, draw) for match in stage_matches)
        rounds.append(
            "<section class=\"bracket-round\">"
            f"<h4 class=\"bracket-round__title\">{html.escape(KNOCKOUT_STAGE_LABELS[stage_key])}</h4>"
            f"{cards}"
            "</section>"
        )

    if not rounds:
        return

    st.markdown(
        "<div class=\"bracket-scroll\"><div class=\"bracket-grid\">"
        + "".join(rounds)
        + "</div></div>",
        unsafe_allow_html=True,
    )


def render_bracket_match(match: dict[str, Any], draw: dict[str, list[str]]) -> str:
    kickoff = parse_raw_match_datetime(match)
    kickoff_display = kickoff.astimezone().strftime("%d %b, %H:%M") if kickoff else ""
    match_no = match.get("matchNo") or ""
    status = bracket_status(match)
    meta_parts = [
        f"M{match_no}" if match_no else "",
        kickoff_display,
        status,
    ]
    meta = "".join(f"<span>{html.escape(part)}</span>" for part in meta_parts if part)
    return (
        "<article class=\"bracket-match\">"
        f"<div class=\"bracket-match__meta\">{meta}</div>"
        f"{render_bracket_team(match, 'home', draw)}"
        f"{render_bracket_team(match, 'away', draw)}"
        "</article>"
    )


def parse_raw_match_datetime(match: dict[str, Any]) -> datetime | None:
    for key in ("kickoffUtc", "datetime", "utcDate", "date"):
        value = match.get(key)
        if not value or not isinstance(value, str):
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


def bracket_status(match: dict[str, Any]) -> str:
    status = str(match.get("status") or "").strip()
    phase = str(match.get("livePhase") or "").strip()
    if status and phase:
        return f"{status.title()} {phase}"
    return status.title() if status else ""


def render_bracket_team(match: dict[str, Any], side: str, draw: dict[str, list[str]]) -> str:
    team = match.get(f"{side}Team") or match.get(f"{side}Ref") or "TBD"
    score = match.get(f"{side}Score")
    owner = bracket_team_owner(str(team), draw) if match.get(f"{side}Team") else ""
    owner_markup = f"<div class=\"bracket-team__owner\">{html.escape(owner)}</div>" if owner else ""
    score_markup = (
        f"<div class=\"bracket-team__score\">{html.escape(str(score))}</div>"
        if score is not None
        else ""
    )
    return (
        "<div class=\"bracket-team\">"
        "<div>"
        f"<div class=\"bracket-team__name\">{html.escape(str(team))}</div>"
        f"{owner_markup}"
        "</div>"
        f"{score_markup}"
        "</div>"
    )


def bracket_team_owner(team: str, draw: dict[str, list[str]]) -> str:
    owner_lookup = build_owner_lookup(draw)
    return owner_lookup.get(canonical_team_name(team), "")


def format_worst_teams_meta(records: list[Any]) -> str:
    rows = []
    for index, record in enumerate(records[:3], start=1):
        owner = html.escape(record.owner or "Unassigned")
        team = html.escape(record.name)
        rows.append(
            "<div class=\"rank-line\">"
            f"<span class=\"rank-line__team\">{index}. {team}</span> "
            f"<span class=\"status-pill\">{owner}</span><br>"
            f"{record.points} pts | GD {record.goal_difference:+d} | GF {record.goals_for}"
            "</div>"
        )
    return "".join(rows)


def people_dataframe(people: list[Any], movements: dict[str, str] | None = None) -> pd.DataFrame:
    movements = movements or {}
    rows = []
    for index, person in enumerate(people, start=1):
        rows.append(
            {
                "Rank": index,
                "Move": movements.get(person.name, ""),
                "Person": person.name,
                "Played": person.played,
                "Points": person.points,
                "GD": person.goal_difference,
                "GF": person.goals_for,
                "GA": person.goals_against,
                "Teams": ", ".join(person.teams),
            }
        )
    return pd.DataFrame(rows)


def parse_match_datetime(match: Any) -> datetime | None:
    candidates = [
        match.kickoff,
        match.raw.get("kickoffUtc") if isinstance(match.raw, dict) else None,
        match.raw.get("datetime") if isinstance(match.raw, dict) else None,
        match.raw.get("utcDate") if isinstance(match.raw, dict) else None,
        match.raw.get("date") if isinstance(match.raw, dict) else None,
    ]

    for value in candidates:
        if not value or not isinstance(value, str):
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    return None


def upcoming_matches(matches: list[Any], days: int) -> list[Any]:
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=max(1, days))
    fixtures = []

    for match in matches:
        if match.played:
            continue
        kickoff = parse_match_datetime(match)
        if kickoff is None:
            continue
        kickoff_utc = kickoff.astimezone(timezone.utc)
        if now <= kickoff_utc <= end:
            fixtures.append(match)

    fixtures.sort(key=lambda match: parse_match_datetime(match) or datetime.max.replace(tzinfo=timezone.utc))
    return fixtures


def current_matches(matches: list[Any], window_hours: int) -> list[Any]:
    now = datetime.now(timezone.utc)
    current = []
    window = timedelta(hours=max(1, window_hours))

    for match in matches:
        if match.played:
            continue

        status = (match.status or "").strip().lower()
        kickoff = parse_match_datetime(match)
        kickoff_utc = kickoff.astimezone(timezone.utc) if kickoff else None

        is_live_status = status in {"live", "in progress", "in_progress", "1h", "2h", "ht", "half-time"}
        is_inside_window = bool(kickoff_utc and kickoff_utc <= now <= kickoff_utc + window)

        if is_live_status or is_inside_window:
            current.append(match)

    current.sort(key=lambda match: parse_match_datetime(match) or datetime.max.replace(tzinfo=timezone.utc))
    return current


def completed_matches(matches: list[Any]) -> list[Any]:
    fallback = datetime.min.replace(tzinfo=timezone.utc)
    results = [match for match in matches if match.played]
    results.sort(key=lambda match: parse_match_datetime(match) or fallback)
    return results


def format_match_context(match: Any) -> str:
    kickoff = parse_match_datetime(match)
    kickoff_display = kickoff.astimezone().strftime("%d %b, %H:%M") if kickoff else "latest match"
    return (
        f"Movement is versus the table before {match.home_team} "
        f"{match.home_score}-{match.away_score} {match.away_team} ({kickoff_display})."
    )


def leaderboard_movements(
    matches: list[Any],
    draw: dict[str, list[str]],
    current_people: list[Any],
) -> tuple[dict[str, str], str | None]:
    results = completed_matches(matches)
    if not results:
        return {}, None

    latest_match = results[-1]
    previous_matches = [match for match in matches if match is not latest_match]
    previous_records = compute_team_records(previous_matches, draw, group_stage_only=False)
    previous_people = rank_people(previous_records, draw)
    previous_ranks = {person.name: index for index, person in enumerate(previous_people, start=1)}

    movements: dict[str, str] = {}
    for current_rank, person in enumerate(current_people, start=1):
        previous_rank = previous_ranks.get(person.name)
        if previous_rank is None:
            movements[person.name] = ""
            continue

        delta = previous_rank - current_rank
        if delta > 0:
            movements[person.name] = f"↑ {delta}"
        elif delta < 0:
            movements[person.name] = f"↓ {abs(delta)}"
        else:
            movements[person.name] = "→"

    return movements, format_match_context(latest_match)


def team_with_owner(team: str, owner_lookup: dict[str, str]) -> str:
    owner = owner_lookup.get(canonical_team_name(team))
    return f"{team} ({owner})" if owner else team


def fixtures_dataframe(matches: list[Any], draw: dict[str, list[str]]) -> pd.DataFrame:
    owner_lookup = build_owner_lookup(draw)
    rows = []
    for match in matches:
        kickoff = parse_match_datetime(match)
        kickoff_display = (
            kickoff.astimezone().strftime("%a %d %b, %H:%M %Z") if kickoff else match.kickoff or ""
        )
        rows.append(
            {
                "Kickoff": kickoff_display,
                "Home": team_with_owner(match.home_team, owner_lookup),
                "Away": team_with_owner(match.away_team, owner_lookup),
                "Venue": match.raw.get("stadium", "") if isinstance(match.raw, dict) else "",
                "City": match.raw.get("city", "") if isinstance(match.raw, dict) else "",
                "Status": match.status or "",
                "Stage": match.stage or "",
            }
        )
    return pd.DataFrame(rows)


def current_games_dataframe(matches: list[Any], draw: dict[str, list[str]]) -> pd.DataFrame:
    owner_lookup = build_owner_lookup(draw)
    rows = []
    for match in matches:
        kickoff = parse_match_datetime(match)
        kickoff_display = (
            kickoff.astimezone().strftime("%a %d %b, %H:%M %Z") if kickoff else match.kickoff or ""
        )
        phase = match.raw.get("livePhase", "") if isinstance(match.raw, dict) else ""
        status = (match.status or "in progress").title()
        status_display = f"{status} {phase}".strip()
        score = (
            f"{match.home_score}-{match.away_score}"
            if match.home_score is not None and match.away_score is not None
            else ""
        )
        rows.append(
            {
                "Kickoff": kickoff_display,
                "Home": team_with_owner(match.home_team, owner_lookup),
                "Score": score,
                "Away": team_with_owner(match.away_team, owner_lookup),
                "Venue": match.raw.get("stadium", "") if isinstance(match.raw, dict) else "",
                "City": match.raw.get("city", "") if isinstance(match.raw, dict) else "",
                "Status": status_display,
                "Stage": match.stage or "",
            }
        )
    return pd.DataFrame(rows)


def format_timestamp(value: str | None) -> str:
    if not value:
        return "Never"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone().strftime("%d %b %Y, %H:%M:%S %Z")
    except ValueError:
        return value


def validate_draw(draw: dict[str, list[str]]) -> list[str]:
    warnings: list[str] = []
    for person, teams in draw.items():
        if len(teams or []) != 4:
            warnings.append(f"{person} has {len(teams or [])} teams configured; expected 4.")
    return warnings


def main() -> None:
    inject_css()
    config = load_config()
    draw = config.get("sweepstake") or {}
    year = int(config.get("year", 2026))
    refresh_interval_seconds = int(config.get("refresh_interval_seconds", 600))
    upcoming_fixture_days = int(config.get("upcoming_fixture_days", 4))
    current_match_window_hours = int(config.get("current_match_window_hours", 3))
    base_url = (config.get("api") or {}).get("base_url", DEFAULT_BASE_URL)
    api_key = get_api_key()

    with st.sidebar:
        st.title("Sweepstake")
        year = st.number_input("Tournament year", min_value=1930, max_value=2100, value=year, step=4)
        st.caption(f"Cache refresh interval: {refresh_interval_seconds // 60} minutes")
        st.caption(f"Upcoming fixtures window: {upcoming_fixture_days} days")
        st.caption(f"Current match window: {current_match_window_hours} hours")
        manual_refresh = st.button("Refresh now", type="primary", use_container_width=True)

        st.divider()
        st.caption("API")
        st.write(base_url)
        if api_key:
            st.success("API key loaded")
        else:
            st.error("API key missing")

    if manual_refresh:
        with st.spinner("Refreshing Zafronix cache..."):
            ensure_cache(
                year=int(year),
                api_key=api_key,
                base_url=base_url,
                refresh_interval_seconds=refresh_interval_seconds,
                force=True,
            )
            st.cache_data.clear()
        st.rerun()

    data = ensure_cache(
        year=int(year),
        api_key=api_key,
        base_url=base_url,
        refresh_interval_seconds=refresh_interval_seconds,
    )

    if config.get("browser_autorefresh", True):
        components.html(
            f"<script>setTimeout(function() {{ window.parent.location.reload(); }}, {refresh_interval_seconds * 1000});</script>",
            height=0,
        )

    st.title("World Cup Sweepstake")
    st.caption(
        f"Last API cache validation: {format_timestamp(data['last_refresh_utc'])}"
    )

    if data.get("last_refresh_error"):
        st.warning(data["last_refresh_error"])

    for warning in validate_draw(draw):
        st.warning(warning)

    if not draw:
        st.info("Add friends and their four teams to config.yaml to populate the sweepstake tables.")

    snapshot = build_dashboard_snapshot(
        safe_json(data.get("matches")),
        safe_json(data.get("standings")),
        safe_json(draw),
        upcoming_fixture_days,
        current_match_window_hours,
    )

    worst_teams = snapshot["worst_teams"][:3]
    best_person = snapshot["people"][0] if snapshot["people"] else None

    card_1, card_2 = st.columns(2)
    with card_1:
        if worst_teams:
            render_card("Worst Team", "Bottom 3", format_worst_teams_meta(worst_teams))
        else:
            render_card("Worst Team", "No results yet", "Waiting for completed group-stage games")
    with card_2:
        if best_person:
            meta = (
                f"{best_person.points} pts | GD {best_person.goal_difference:+d} | "
                f"GF {best_person.goals_for}<br>{html.escape(', '.join(best_person.teams))}"
            )
            render_card("Best Combined Record", best_person.name, meta)
        else:
            render_card("Best Combined Record", "No draw configured", "Add teams to config.yaml")

    knockout = snapshot["knockout_matches"]
    if knockout:
        st.subheader("Knockout Bracket")
        render_knockout_bracket(knockout, draw)

    st.subheader("People Leaderboard")
    if snapshot["movement_context"]:
        st.caption(snapshot["movement_context"])
    leaderboard_height = min(720, 42 + (len(snapshot["people"]) + 1) * 35)
    st.dataframe(
        people_dataframe(snapshot["people"], snapshot["people_movements"]),
        use_container_width=True,
        hide_index=True,
        height=leaderboard_height,
    )

    current = snapshot["current_matches"]
    if current:
        st.subheader("Current Games")
        st.dataframe(current_games_dataframe(current, draw), use_container_width=True, hide_index=True)

    st.subheader(f"Upcoming Fixtures - Next {upcoming_fixture_days} Days")
    fixtures = snapshot["upcoming_matches"]
    if fixtures:
        st.dataframe(fixtures_dataframe(fixtures, draw), use_container_width=True, hide_index=True)
    else:
        st.info("No upcoming fixtures found in the cached API data for this window.")


if __name__ == "__main__":
    main()
