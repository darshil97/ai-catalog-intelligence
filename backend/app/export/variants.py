"""Grouping products a person confirmed as variants into one Shopify product (D41). Pure.

Confirmed pairs (and chains of them: red with blue, blue with green) form a group. The group
becomes one product whose option is the attribute that differs: Colour, Size, or Style when
nothing obvious does. Products that differ in both colour and size aren't grouped, because
Shopify would need two options and the file says so instead of guessing.
"""

import re
from dataclasses import dataclass
from uuid import UUID

from app.export.data import Item

# Attributes that make a variant, by the option they belong to.
_COLOUR = ("color",)
_SIZE = ("capacity", "length", "width", "height", "net_weight")


@dataclass(frozen=True)
class Group:
    """Products exported as one Shopify product."""

    items: tuple[Item, ...]
    option: str | None  # Colour, Size or Style; None for a product on its own
    values: tuple[str, ...]  # each item's option value

    @property
    def lead(self) -> Item:
        return self.items[0]


def _differs(items: list[Item], codes: tuple[str, ...]) -> bool:
    return any(len({item.value(code) or "" for item in items}) > 1 for code in codes)


def _size(item: Item) -> str:
    parts = [item.value(code) for code in _SIZE if item.value(code)]
    return " · ".join(p for p in parts if p) or item.key


def group(items: list[Item], pairs: dict[frozenset[UUID], str]) -> tuple[list[Group], list[str]]:
    """Group exported products by confirmed variant pairs.

    Returns:
        The groups in the products' order (singles too), and notes for people.
    """
    by_id = {item.id: item for item in items}
    parent = {item.id: item.id for item in items}

    def root(node: UUID) -> UUID:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for pair, status in pairs.items():
        if status != "confirmed":
            continue
        a, b = tuple(pair)
        if a in by_id and b in by_id:
            parent[root(a)] = root(b)

    members: dict[UUID, list[Item]] = {}
    for item in items:
        members.setdefault(root(item.id), []).append(item)

    groups, notes, seen = [], [], set()
    for item in items:
        key = root(item.id)
        if key in seen:
            continue
        seen.add(key)
        family = members[key]
        if len(family) == 1:
            groups.append(Group((item,), None, ("",)))
            continue
        colour, size = _differs(family, _COLOUR), _differs(family, _SIZE)
        if colour and size:
            keys = ", ".join(i.key for i in family)
            notes.append(f"{keys} are variants but differ in both colour and size, so they're "
                         "exported as separate products.")  # fmt: skip
            groups.extend(Group((i,), None, ("",)) for i in family)
            continue
        if colour:
            option, values = "Colour", tuple(i.value("color") or i.key for i in family)
        elif size:
            option, values = "Size", tuple(_size(i) for i in family)
        else:
            option, values = "Style", tuple(i.key for i in family)
        groups.append(Group(tuple(family), option, values))
    return groups, notes


def group_title(group: Group) -> str:
    """The lead product's title without its own option value, such as its colour word."""
    title = group.lead.title
    if group.option is None:
        return title
    value = group.values[0]
    for word in sorted({value, *value.split()}, key=len, reverse=True):
        if len(word) > 1:
            title = re.sub(rf"(?i)(?<![\w-]){re.escape(word)}(?![\w-])", "", title)
    title = re.sub(r"\s*,\s*,", ",", re.sub(r"\s{2,}", " ", title)).strip(" ,-·")
    return title or group.lead.title
