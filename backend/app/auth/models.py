"""Contracts for sign-in: who a verified token says the caller is, and what they may do."""

from dataclasses import dataclass

ROLE_USER = "user"
ROLE_ADMIN = "admin"

STATUS_ACTIVE = "active"
STATUS_WAITLISTED = "waitlisted"
STATUS_DISABLED = "disabled"


@dataclass(frozen=True)
class Identity:
    """The claims of a token that passed every check.

    Attributes:
        user_id: The token's `sub`, which is also the profile ID.
        email: The account email, lowercased.
        email_verified: Whether the provider verified the email. Google always does.
        role: ROLE_ADMIN or ROLE_USER. Any other role in the token counts as ROLE_USER.
        banned: Whether Neon Auth has banned the account.
    """

    user_id: str
    email: str
    email_verified: bool
    role: str
    banned: bool


@dataclass(frozen=True)
class Profile:
    """A caller whose profile exists, as used to authorize a request.

    Attributes:
        user_id: The profile ID, equal to the token's `sub`.
        email: The email from the current token.
        role: The role from the current token, so a role change applies within 15 minutes.
        status: STATUS_ACTIVE, STATUS_WAITLISTED or STATUS_DISABLED.
    """

    user_id: str
    email: str
    role: str
    status: str

    @property
    def is_admin(self) -> bool:
        """Whether this caller has the admin role."""
        return self.role == ROLE_ADMIN
