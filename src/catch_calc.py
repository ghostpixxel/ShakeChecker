"""Pure catch-probability math (Gen 3/4 formula as used by PokeMMO).

The shake/probability math is ported 1:1 from the PokeMMO Hub implementation
(src/hooks/useCatchRate.jsx, github.com/PokeMMO-Tools/pokemmo-hub). The
conditional ball multipliers are ported from the PokeMMO-specific catch
calculator (c4vv/CatchCalc, pokeballs.js). This module performs no I/O.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

X_CAP = 255.0
SHAKE_SCALE = 65536.0

# Enemy types that the Net Ball boosts (OpenCV-agnostic; lower-cased on use).
NET_TYPES = frozenset({"water", "bug"})


@dataclass(frozen=True)
class BattleContext:
    """Everything a conditional ball rule needs about the current battle.

    `turns_completed` is 0 during the first turn (so Quick Ball is active and
    Timer Ball is x1), then 1 after the first turn resolves, etc.
    `turns_asleep` is how many turns the enemy has been asleep (Dream Ball)."""

    turns_completed: int = 0
    turns_asleep: int = 0
    enemy_asleep: bool = False  # current sleep status (Dream Ball requires it)
    enemy_types: tuple[str, ...] = ()
    enemy_level: int = 1
    dusk_active: bool = False  # night or cave (Dusk Ball condition)
    already_caught: bool = False  # Repeat Ball condition (unconfirmed rule)


def _quick(ctx: BattleContext) -> float:
    return 5.0 if ctx.turns_completed == 0 else 1.0


def _timer(ctx: BattleContext) -> float:
    return 1.0 + min(3.0, ctx.turns_completed * 0.3)


def _net(ctx: BattleContext) -> float:
    return 3.5 if NET_TYPES & {t.lower() for t in ctx.enemy_types} else 1.0


def _nest(ctx: BattleContext) -> float:
    return min(max(7.0 - 0.2 * (ctx.enemy_level - 1), 1.0), 4.0)


def _dusk(ctx: BattleContext) -> float:
    return 2.5 if ctx.dusk_active else 1.0


# Dream Ball by consecutive sleep turns (PokeMMO capture calculator): 0/1/2/3
# turns -> 1x / 1.5x / 2.5x / 4x; more turns stay at the 4x cap.
_DREAM_BY_SLEEP = (1.0, 1.5, 2.5, 4.0)


def _dream(ctx: BattleContext) -> float:
    # The boost only applies while the enemy is actually asleep; otherwise 1x.
    if not ctx.enemy_asleep:
        return 1.0
    return _DREAM_BY_SLEEP[min(max(ctx.turns_asleep, 0), 3)]


# Conditional ball rules, keyed by the "rule" field in balls.json.
BALL_RULES: dict[str, Callable[[BattleContext], float]] = {
    "quick": _quick,
    "timer": _timer,
    "net": _net,
    "nest": _nest,
    "dusk": _dusk,
    "dream": _dream,
}


def ball_multiplier(ball: dict, ctx: BattleContext) -> float:
    """Resolve a ball's catch multiplier: a flat `rate`, or a conditional
    `rule` evaluated against `ctx`."""
    if "rate" in ball:
        return float(ball["rate"])
    rule = ball.get("rule")
    if rule in BALL_RULES:
        return BALL_RULES[rule](ctx)
    raise ValueError(f"ball {ball.get('id')!r} has neither a known rule nor a rate")


def x_value(
    hp_fraction: float,
    base_catch_rate: float,
    ball_rate: float = 1.0,
    status_rate: float = 1.0,
) -> float:
    """The pre-shake quantity `x`; catch is guaranteed at x >= 255.

    `hp_fraction` is currentHP / maxHP in (0, 1]; max HP cancels out of the
    original formula, so the fraction read off the HP bar is sufficient.
    """
    if not 0.0 < hp_fraction <= 1.0:
        raise ValueError(f"hp_fraction must be in (0, 1], got {hp_fraction}")
    if base_catch_rate <= 0:
        raise ValueError(f"base_catch_rate must be positive, got {base_catch_rate}")
    return ((3.0 - 2.0 * hp_fraction) / 3.0) * base_catch_rate * ball_rate * status_rate


def catch_probability(
    hp_fraction: float,
    base_catch_rate: float,
    ball_rate: float = 1.0,
    status_rate: float = 1.0,
) -> float:
    """Probability in [0, 1] that a single throw catches (four shake checks)."""
    x = x_value(hp_fraction, base_catch_rate, ball_rate, status_rate)
    if x >= X_CAP:
        return 1.0
    y = SHAKE_SCALE / (X_CAP / x) ** 0.25
    return (y / SHAKE_SCALE) ** 4
