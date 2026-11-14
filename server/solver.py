"""A narrow interface for a future external solver implementation.

The web application never manufactures solver output. Add a provider implementing
SolverAdapter, then inject it into Application when the service is available.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol


@dataclass(frozen=True)
class SolverSpot:
    game_type: str
    effective_stack_bb: float
    hero_position: str
    hero_cards: List[str]
    board_cards: List[str]
    actions: List[Dict[str, object]]


@dataclass(frozen=True)
class SolverResult:
    provider: str
    actions: List[Dict[str, object]]
    explanation: Optional[str] = None


class SolverAdapter(Protocol):
    def available(self) -> bool:
        ...

    def solve(self, spot: SolverSpot) -> SolverResult:
        ...


class UnavailableSolver:
    def available(self) -> bool:
        return False

    def solve(self, spot: SolverSpot) -> SolverResult:
        raise NotImplementedError("솔버 연결 준비 중")
