from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Match:
    home_team: str
    away_team: str
    home_score: int | None = None
    away_score: int | None = None
    kickoff: str | None = None
    stage: str | None = None
    group: str | None = None
    status: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def played(self) -> bool:
        if self.home_score is None or self.away_score is None:
            return False

        if not self.status:
            return True

        status = self.status.strip().lower()
        unplayed_statuses = {
            "scheduled",
            "not started",
            "not_started",
            "upcoming",
            "pending",
            "postponed",
            "cancelled",
            "canceled",
            "abandoned",
            "live",
            "in progress",
            "in_progress",
            "1h",
            "2h",
            "ht",
            "half-time",
        }
        return status not in unplayed_statuses

    @property
    def is_group_stage(self) -> bool:
        if self.group:
            return True

        if not self.stage:
            # If the API shape omits stage metadata, avoid accidentally hiding
            # valid results from the group-stage prize table.
            return True

        stage = self.stage.strip().lower()
        return "group" in stage or stage in {"first stage", "round 1", "first round"}


@dataclass
class TeamRecord:
    name: str
    owner: str | None = None
    group: str | None = None
    goal_difference_override: int | None = None
    played: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    goals_for: int = 0
    goals_against: int = 0
    points: int = 0

    @property
    def goal_difference(self) -> int:
        if self.goal_difference_override is not None:
            return self.goal_difference_override
        return self.goals_for - self.goals_against

    def add_result(self, goals_for: int, goals_against: int) -> None:
        self.goal_difference_override = None
        self.played += 1
        self.goals_for += goals_for
        self.goals_against += goals_against

        if goals_for > goals_against:
            self.wins += 1
            self.points += 3
        elif goals_for == goals_against:
            self.draws += 1
            self.points += 1
        else:
            self.losses += 1


@dataclass
class PersonRecord:
    name: str
    teams: list[str]
    played: int = 0
    points: int = 0
    goal_difference: int = 0
    goals_for: int = 0
    goals_against: int = 0
