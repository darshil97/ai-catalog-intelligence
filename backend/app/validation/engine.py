"""Runs a profile's rules over a batch and applies severities and set-aside warnings (D38)."""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import app.validation.rules  # noqa: F401 - registers the rules
from app.config import get_validation_config
from app.utils.logger import get_logger
from app.validation.base import (
    ERROR,
    INFO,
    SEVERITY_ORDER,
    WARNING,
    BatchContext,
    Row,
    fingerprint,
    registered_rules,
)

logger = get_logger(__name__)

CHECK_FAILED = "check_failed"


@dataclass(frozen=True)
class Issue:
    """One finding with its severity, ready to show."""

    rule: str
    rule_version: int
    severity: str
    product_id: UUID
    subject: str
    message: str
    fingerprint: str
    other_product_id: UUID | None = None
    fix: str | None = None
    attribute: str | None = None
    set_aside: Row | None = None

    def as_dict(self) -> Row:
        """The issue for API responses."""
        return {
            "rule": self.rule,
            "rule_version": self.rule_version,
            "severity": self.severity,
            "subject": self.subject,
            "message": self.message,
            "other_product_id": self.other_product_id,
            "fix": self.fix,
            "attribute": self.attribute,
            "set_aside": self.set_aside,
        }


@dataclass
class ProductReport:
    """A product's issues, errors first."""

    product_id: UUID
    product_key: str
    issues: list[Issue] = field(default_factory=list)

    def count(self, severity: str) -> int:
        """Issues of a severity that aren't set aside."""
        return sum(1 for i in self.issues if i.severity == severity and i.set_aside is None)

    @property
    def can_approve(self) -> bool:
        """No errors. M9 refuses to approve a product with any."""
        return self.count(ERROR) == 0


@dataclass
class BatchReport:
    """Every product's issues under one profile."""

    profile: str
    profile_name: str
    rules_version: str
    products: list[ProductReport]

    def product(self, product_id: UUID) -> ProductReport | None:
        """One product's report."""
        return next((p for p in self.products if p.product_id == product_id), None)


def check_settings() -> None:
    """Refuse settings that name rules that don't exist. Called at startup.

    Raises:
        ValueError: naming the unknown rules.
    """
    settings = get_validation_config()
    known = set(registered_rules())
    named = set(settings["severities"])
    for profile in settings["profiles"].values():
        named |= set(profile["severities"])
    unknown = sorted(named - known)
    if unknown:
        raise ValueError(f"validation config names unknown rules: {unknown}")
    missing = sorted(known - set(settings["severities"]))
    if missing:
        raise ValueError(f"validation.severities has no default for rules: {missing}")


def profile_settings(code: str | None) -> tuple[str, Row]:
    """The profile's code and settings, falling back to the default for an unknown code."""
    settings = get_validation_config()
    profiles = settings["profiles"]
    if code not in profiles:
        code = settings["default_profile"]
    assert code is not None  # noqa: S101
    return code, profiles[code]


def evaluate(
    ctx: BatchContext,
    profile_code: str | None,
    overrides: list[Row] | None = None,
) -> BatchReport:
    """Run every rule and return each product's issues.

    Args:
        ctx: The batch, from context.load_batch.
        profile_code: The batch's marketplace profile.
        overrides: Warnings set aside: product_id, rule_code, subject, fingerprint, reason,
            created_by, created_at. One applies only while its fingerprint matches, and only
            to a warning.
    """
    settings = get_validation_config()
    code, profile = profile_settings(profile_code)
    severities = {**settings["severities"], **profile["severities"]}
    aside = {(o["product_id"], o["rule_code"], o["subject"]): o for o in overrides or []}
    reports = {p.id: ProductReport(product_id=p.id, product_key=p.key) for p in ctx.products}

    for rule_code, rule in registered_rules().items():
        severity = severities.get(rule_code, WARNING)
        try:
            findings = list(rule.check(ctx, profile, settings))
        except Exception:  # noqa: BLE001 - one broken rule mustn't hide the others
            logger.error("Check failed: rule=%s batch_id=%s", rule_code, ctx.batch_id,
                         exc_info=True)  # fmt: skip
            for report in reports.values():
                report.issues.append(
                    Issue(
                        rule=CHECK_FAILED,
                        rule_version=1,
                        severity=INFO,
                        product_id=report.product_id,
                        subject=rule_code,
                        message=f"The {rule_code.replace('_', ' ')} check couldn't run.",
                        fingerprint="",
                    )
                )
            continue
        for finding in findings:
            target = reports.get(finding.product_id)
            if target is None:
                continue
            seen_print = fingerprint(rule_code, finding.subject, finding.seen)
            override = aside.get((finding.product_id, rule_code, finding.subject))
            applies = (
                override is not None
                and severity == WARNING
                and override["fingerprint"] == seen_print
            )
            target.issues.append(
                Issue(
                    rule=rule_code,
                    rule_version=rule.version,
                    severity=severity,
                    product_id=finding.product_id,
                    subject=finding.subject,
                    message=finding.message,
                    fingerprint=seen_print,
                    other_product_id=finding.other_product_id,
                    fix=finding.fix,
                    attribute=finding.attribute,
                    set_aside={
                        "reason": override["reason"],
                        "by": override["created_by"],
                        "at": override["created_at"],
                    }
                    if applies and override is not None
                    else None,
                )
            )

    for report in reports.values():
        report.issues.sort(
            key=lambda i: (i.set_aside is not None, SEVERITY_ORDER[i.severity], i.rule, i.subject)
        )
    return BatchReport(
        profile=code,
        profile_name=profile["name"],
        rules_version=settings["rules_version"],
        products=list(reports.values()),
    )


def describe_rules() -> list[dict[str, Any]]:
    """Every rule's code, version and description, for people."""
    return [
        {"code": code, "version": rule.version, "description": rule.description}
        for code, rule in sorted(registered_rules().items())
    ]
