from __future__ import annotations

import re
from dataclasses import dataclass


_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_.])"
    r"(?P<prefix>人民币|CNY|RMB|USD|美元|¥|￥|\$)?\s*"
    r"(?P<value>\d+(?:,\d{3})*(?:\.\d+)?)\s*"
    r"(?P<unit>人民币|CNY|RMB|USD|美元|元|分钟|分|小时|时|天|晚|"
    r"秒|毫秒|ms|sec(?:ond)?s?|minutes?|hours?|days?|nights?|"
    r"人|位|名|个|组|次|轮|times?|%|％)?"
    r"(?![A-Za-z0-9_])",
    re.IGNORECASE,
)

_MONEY_UNITS = {
    "人民币",
    "cny",
    "rmb",
    "usd",
    "美元",
    "元",
    "¥",
    "￥",
    "$",
}
_TIME_UNITS = {
    "分钟",
    "分",
    "小时",
    "时",
    "天",
    "晚",
    "秒",
    "毫秒",
    "ms",
    "sec",
    "second",
    "seconds",
    "minute",
    "minutes",
    "hour",
    "hours",
    "day",
    "days",
    "night",
    "nights",
}
_COUNT_UNITS = {"人", "位", "名", "个", "组", "次", "轮", "time", "times"}
_RATIO_UNITS = {"%", "％"}


@dataclass(frozen=True, slots=True)
class Quantity:
    value: float
    unit: str
    dimension: str
    start: int
    end: int


def parse_quantities(text: str) -> tuple[Quantity, ...]:
    quantities: list[Quantity] = []
    for match in _NUMBER_RE.finditer(str(text or "")):
        prefix = str(match.group("prefix") or "").strip()
        suffix = str(match.group("unit") or "").strip()
        unit = prefix or suffix
        normalized_unit = unit.casefold()
        if normalized_unit in _MONEY_UNITS:
            dimension = "money"
        elif normalized_unit in _TIME_UNITS:
            dimension = "time"
        elif normalized_unit in _COUNT_UNITS:
            dimension = "count"
        elif normalized_unit in _RATIO_UNITS:
            dimension = "ratio"
        else:
            dimension = "scalar"
        quantities.append(
            Quantity(
                value=float(match.group("value").replace(",", "")),
                unit=unit,
                dimension=dimension,
                start=match.start(),
                end=match.end(),
            )
        )
    return tuple(quantities)


def values_for_dimension(
    text: str,
    dimension: str,
    *,
    allow_scalar: bool = False,
) -> tuple[float, ...]:
    allowed = {dimension}
    if allow_scalar:
        allowed.add("scalar")
    return tuple(
        quantity.value
        for quantity in parse_quantities(text)
        if quantity.dimension in allowed
    )


def semantic_clauses(text: str) -> tuple[str, ...]:
    return tuple(
        clause.strip()
        for clause in re.split(r"[\r\n。！？；;]+", str(text or ""))
        if clause.strip()
    )
