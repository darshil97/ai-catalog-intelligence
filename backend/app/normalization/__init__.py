"""Normalization: standard values, the value each product shows, and variant flags (M6)."""

from app.normalization.select import (
    CONFLICT,
    MISSING,
    REVIEWER_SET,
    UNCLEAR,
    VERIFIED,
    Choice,
    agree,
    choose,
)
from app.normalization.values import BLOCKING, Normal, Rules, normalize
from app.normalization.variants import ProductSummary, VariantPair, find_variants

__all__ = [
    "BLOCKING",
    "CONFLICT",
    "MISSING",
    "REVIEWER_SET",
    "UNCLEAR",
    "VERIFIED",
    "Choice",
    "Normal",
    "ProductSummary",
    "Rules",
    "VariantPair",
    "agree",
    "choose",
    "find_variants",
    "normalize",
]
