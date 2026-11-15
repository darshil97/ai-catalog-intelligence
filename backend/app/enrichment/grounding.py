"""Checks that generated text says nothing its cited facts don't support (D37).

Pure and deterministic. Every title, sentence and bullet (a claim) must cite facts that exist,
use each one it cites, and keep to them: quantities must equal a cited fact's value under M6's
unit rules, and specification phrases, colours and materials must appear in a cited fact.
Banned phrases never pass. Problems are returned as plain sentences, which the LLM is shown
when it is asked again.
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.enrichment.facts import Fact
from app.enrichment.models import Claim, EnrichmentReply
from app.normalization import Rules, normalize
from app.normalization.values import clean_text, fold, parse_number

_NUMBER = re.compile(r"\d[\d.,]*\d|\d")
_TOKEN = re.compile(r"[a-z0-9]+")
_FACT_ID = re.compile(r"\bF\d+\b")
# A word holding letters and digits, such as "TG-500X" or "1500ml".
_MIXED = re.compile(r"(?<![\w])(?=[\w./-]*\d)(?=[\w./-]*[^\W\d_])\w[\w./-]*\w(?![\w])", re.UNICODE)
_STOPWORDS = frozenset(
    {"and", "the", "for", "with", "of", "a", "an", "in", "on", "to", "or", "by", "set", "pack"}
)
_SENTENCE_END = re.compile(r"[.!?]$")
# Counts written as words. "One", "single" and the like say a count of 1 when they cite one, but
# are too common in ordinary wording ("one-touch lid") to be refused on their own.
_NUMBER_WORDS = {
    "one": 1, "single": 1, "singly": 1, "individually": 1, "individual": 1,
    "two": 2, "pair": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "dozen": 12,
}  # fmt: skip
_LOOSE_NUMBER_WORDS = frozenset({"one", "single", "singly", "individually", "individual"})
# Everything but letters, digits and the symbols banned phrases use ("#1", "100%", "world's").
_PUNCTUATION = re.compile(r"[^\w#%'’]|_")


@dataclass(frozen=True)
class Checked:
    """A reply that passed, with each claim's facts."""

    title: str
    description: str
    bullets: tuple[str, ...]
    claims: tuple[dict[str, Any], ...]
    cited_attribute_ids: tuple[str, ...]


def _phrase_text(text: str) -> str:
    """Folded text with punctuation as spaces, padded so phrases match whole words."""
    return " " + " ".join(_PUNCTUATION.sub(" ", fold(text)).split()) + " "


def _clean_phrase(phrase: str) -> str:
    return " ".join(_PUNCTUATION.sub(" ", fold(phrase)).split())


def _has_phrase(text: str, phrase: str) -> bool:
    """Whether a folded, padded text holds a cleaned phrase as whole words (or as written)."""
    if phrase and not phrase[0].isalnum():
        return phrase in text
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) is not None


def _stem(token: str) -> str:
    """A rough singular, so "bottles" matches "bottle" and "glasses" matches "glass"."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith(("sses", "xes", "zes", "ches", "shes")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _tokens(text: str) -> set[str]:
    return {_stem(t) for t in _TOKEN.findall(fold(text)) if t not in _STOPWORDS}


class Grounding:
    """The checks, built once from settings."""

    def __init__(
        self,
        enrichment: dict[str, Any],
        normalization: dict[str, Any],
    ) -> None:
        self.settings = enrichment
        self.rules = Rules(normalization)
        self.tolerance = Decimal(str(normalization["agreement_tolerance"]))
        self.guarded = sorted(
            {
                _clean_phrase(phrase)
                for phrase in (
                    *enrichment["claim_phrases"],
                    *enrichment["material_words"],
                    *normalization["variants"]["colour_words"],
                )
            },
            key=len,
            reverse=True,
        )
        self.banned = sorted({_clean_phrase(p) for p in enrichment["banned_phrases"]})
        self.attribute_rows = {
            "mass": {"code": "q", "data_type": "number", "unit_family": "mass"},
            "volume": {"code": "q", "data_type": "number", "unit_family": "volume"},
            "length": {"code": "q", "data_type": "number", "unit_family": "length"},
        }

    # Quantities ---------------------------------------------------------------------------

    def _fact_number(self, fact: Fact) -> Decimal | None:
        """A quantity fact's value in standard units, read from its text if a person typed it."""
        if fact.number is not None or not fact.family:
            return fact.number
        found = normalize(self.rules, self.attribute_rows[fact.family], fact.display)
        return found[0].value_number if len(found) == 1 and found[0].usable else None

    def _codes(self, text: str) -> tuple[list[str], str]:
        """Find codes such as "TG-500X" in a claim, which must be copied from a cited fact.

        Returns:
            The codes, and the text with them removed, so their digits aren't read as numbers.
        """
        codes: list[str] = []
        rest = clean_text(text)
        for match in list(_MIXED.finditer(rest)):
            word = match.group(0)
            quantity = self.rules.quantity.fullmatch(word)
            if (quantity and quantity.group(2)) or self.rules.size.fullmatch(word):
                continue
            codes.append(word)
            rest = rest.replace(word, " ", 1)
        return codes, rest

    def _quantities(self, text: str) -> tuple[list[tuple[str, Decimal]], list[str], str]:
        """Find quantities with units in a claim.

        Returns:
            (family, standard value) pairs, numbers written without a unit, and the text with
            the quantities removed.
        """
        found: list[tuple[str, Decimal]] = []
        rest = clean_text(text)
        for match in list(self.rules.size.finditer(rest)):
            numbers = [match.group(i) for i in (1, 3, 5) if match.group(i)]
            units = [match.group(i) for i in (2, 4, 6) if match.group(i)]
            if not units:
                continue
            unit = units[-1].lower()
            family = self.rules.family_of(unit)
            if family is None:
                continue
            for index, number_text in enumerate(numbers):
                own = match.group(2 + 2 * index)
                factor = self.rules.units[family].get((own or unit).lower())
                number, _ = parse_number(number_text)
                if number is not None and factor is not None:
                    found.append((family, number * factor))
            rest = rest.replace(match.group(0), " ")
        bare: list[str] = []
        for match in list(self.rules.quantity.finditer(rest)):
            number_text, unit = match.group(1), match.group(2)
            family = self.rules.family_of(unit) if unit else None
            if unit and family:
                number, _ = parse_number(number_text)
                if number is not None:
                    found.append((family, number * self.rules.units[family][unit.lower()]))
                    continue
            bare.append(number_text)
        for quantity in self.rules.quantity.finditer(rest):
            rest = rest.replace(quantity.group(0), " ", 1)
        return found, bare, rest

    def _close(self, a: Decimal, b: Decimal) -> bool:
        if a == b:
            return True
        largest = max(abs(a), abs(b))
        return largest > 0 and abs(a - b) / largest <= self.tolerance

    # One claim ----------------------------------------------------------------------------

    def _check_claim(
        self, where: str, claim: Claim, field: str, facts: dict[str, Fact]
    ) -> list[str]:
        problems: list[str] = []
        quote = claim.text if len(claim.text) <= 80 else claim.text[:77] + "..."
        label = f'{where} "{quote}"'
        if not claim.facts:
            return [f"{label} cites no facts. Every claim must cite the facts it uses."]
        unknown = [fid for fid in claim.facts if fid not in facts]
        if unknown:
            problems.append(f"{label} cites {', '.join(unknown)}, which aren't facts given.")
        cited = [facts[fid] for fid in dict.fromkeys(claim.facts) if fid in facts]
        if _FACT_ID.search(claim.text):
            problems.append(f"{label} contains a fact name; write words, not F-numbers.")
        for fact in cited:
            if fact.title_only and field != "title":
                problems.append(
                    f"{label} cites {fact.id} ({fact.name}), which belongs in the title only."
                )

        codes, without_codes = self._codes(claim.text)
        cited_folded = " ".join(fold(fact.display) for fact in cited)
        for code in codes:
            if fold(code) not in cited_folded:
                problems.append(f'{label} has "{code}", which no fact it cites gives.')
        quantities, bare, _ = self._quantities(without_codes)
        cited_numbers = [
            (fact.family, number)
            for fact in cited
            if fact.family and (number := self._fact_number(fact)) is not None
        ]
        for family, value in quantities:
            if not any(f == family and self._close(n, value) for f, n in cited_numbers):
                problems.append(
                    f"{label} states a {family} that no fact it cites gives. "
                    "Use only quantities from the facts."
                )
        cited_text = " ".join(fold(fact.display) for fact in cited)
        cited_counts = {
            fact.number for fact in cited if fact.number is not None and not fact.family
        }
        words = _TOKEN.findall(fold(claim.text))
        word_numbers = [
            (word, Decimal(_NUMBER_WORDS[word])) for word in words if word in _NUMBER_WORDS
        ]
        cited_words = set(_TOKEN.findall(cited_text))
        for word, number in word_numbers:
            if word in _LOOSE_NUMBER_WORDS or number in cited_counts or word in cited_words:
                continue
            if re.search(rf"(?<![\d.,]){number}(?![\d])", cited_text):
                continue
            problems.append(f'{label} has the number "{word}", which no fact it cites gives.')
        for number_text in bare:
            number, _ = parse_number(number_text)
            if number is not None and number in cited_counts:
                continue
            if re.search(rf"(?<![\d.,]){re.escape(number_text)}(?![\d])", cited_text):
                continue
            problems.append(
                f'{label} has the number "{number_text}", which no fact it cites gives.'
            )

        text = _phrase_text(claim.text)
        cited_phrase_text = " ".join(_phrase_text(fact.display) for fact in cited)
        for phrase in self.guarded:
            if _has_phrase(text, phrase):
                if not _has_phrase(cited_phrase_text, phrase):
                    problems.append(f'{label} says "{phrase}", which no fact it cites says.')
                # A longer phrase settles its words: "cast iron" isn't checked again as "iron".
                text = text.replace(f" {phrase} ", " ")
        for phrase in self.banned:
            if _has_phrase(text, phrase):
                problems.append(f'{label} says "{phrase}". Don\'t use superlatives or promises.')

        tokens = _tokens(claim.text)
        claim_quantities = {family for family, _ in quantities}
        for fact in cited:
            if fact.family:
                number = self._fact_number(fact)
                used = (
                    fact.family in claim_quantities
                    and number is not None
                    and any(
                        f == fact.family and self._close(number, value) for f, value in quantities
                    )
                )
            elif fact.number is not None:
                used = (
                    any(parse_number(n)[0] == fact.number for n in bare)
                    or any(number == fact.number for _, number in word_numbers)
                    or bool(_tokens(fact.display) & tokens)
                )
            else:
                used = bool(_tokens(fact.display) & tokens)
            if not used:
                problems.append(f"{label} cites {fact.id} ({fact.name}) but doesn't use it.")
        return problems

    # Copy a person wrote (M9) -------------------------------------------------------------

    def unsupported(self, texts: list[str], facts: tuple[Fact, ...]) -> list[str]:
        """What in a person's own copy no fact supports, checked against every fact.

        Their text cites nothing, so only claims are checked: quantities, codes, specification
        words, colours, materials and banned phrases. Nothing is refused; the caller warns.
        """
        ids = [fact.id for fact in facts]
        by_id = {fact.id: fact for fact in facts}
        found: list[str] = []
        for index, text in enumerate(t for t in texts if t.strip()):
            claim = Claim(text=text, facts=ids or ["F0"])
            for problem in self._check_claim(f"Line {index + 1}", claim, "description", by_id):
                if "but doesn't use it" in problem or "title only" in problem:
                    continue
                if "aren't facts given" in problem:
                    continue
                found.append(problem.replace(" which no fact it cites ", " which no value "))
        return found

    # The whole reply ----------------------------------------------------------------------

    def check(self, reply: EnrichmentReply, facts: tuple[Fact, ...]) -> Checked:
        """Check a reply and assemble its text.

        Raises:
            ValueError: listing every problem found, for the model to fix.
        """
        s = self.settings
        by_id = {fact.id: fact for fact in facts}
        problems: list[str] = []
        if len(reply.title.text) > s["title_max_chars"]:
            problems.append(
                f"The title has {len(reply.title.text)} characters; at most {s['title_max_chars']}."
            )
        if not s["bullets_min"] <= len(reply.bullets) <= s["bullets_max"]:
            problems.append(
                f"There are {len(reply.bullets)} bullets; "
                f"give {s['bullets_min']} to {s['bullets_max']}."
            )
        if not s["sentences_min"] <= len(reply.description) <= s["sentences_max"]:
            problems.append(
                f"The description has {len(reply.description)} sentences; "
                f"give {s['sentences_min']} to {s['sentences_max']}."
            )
        for index, bullet in enumerate(reply.bullets, start=1):
            if len(bullet.text) > s["bullet_max_chars"]:
                problems.append(
                    f"Bullet {index} has {len(bullet.text)} characters; "
                    f"at most {s['bullet_max_chars']}."
                )
        sentences = [
            c.text if _SENTENCE_END.search(c.text) else c.text + "." for c in reply.description
        ]
        description = " ".join(sentences)
        if len(description) > s["description_max_chars"]:
            problems.append(
                f"The description has {len(description)} characters; "
                f"at most {s['description_max_chars']}."
            )

        units: list[tuple[str, int, Claim]] = [("title", 0, reply.title)]
        units += [("description", i, c) for i, c in enumerate(reply.description)]
        units += [("bullets", i, c) for i, c in enumerate(reply.bullets)]
        for field, index, claim in units:
            where = "The title" if field == "title" else (
                f"Description sentence {index + 1}" if field == "description"
                else f"Bullet {index + 1}"
            )  # fmt: skip
            problems += self._check_claim(where, claim, field, by_id)
        if problems:
            shown = problems[:12]
            more = f" (and {len(problems) - 12} more)" if len(problems) > 12 else ""
            raise ValueError(" ".join(shown) + more)

        claims = []
        cited_ids: dict[str, None] = {}
        for field, index, claim in units:
            cited = [by_id[fid] for fid in dict.fromkeys(claim.facts)]
            for fact in cited:
                if fact.attribute_id:
                    cited_ids[str(fact.attribute_id)] = None
            text = sentences[index] if field == "description" else claim.text
            claims.append(
                {
                    "field": field,
                    "index": index,
                    "text": text,
                    "facts": [fact.citation() for fact in cited],
                }
            )
        return Checked(
            title=reply.title.text,
            description=description,
            bullets=tuple(c.text for c in reply.bullets),
            claims=tuple(claims),
            cited_attribute_ids=tuple(cited_ids),
        )
