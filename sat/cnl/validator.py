"""CNL validator — validates CNL syntax and provides helpful error messages."""

from __future__ import annotations

from sat.cnl.models import CNLParseError, ParsedCNL
from sat.cnl.parser import parse_cnl


def validate_cnl(text: str) -> list[CNLParseError]:
    """Parse *text* and return a list of validation errors (empty = valid)."""
    result: ParsedCNL = parse_cnl(text)
    return result.errors


def validate_and_raise(text: str) -> ParsedCNL:
    """Parse *text*.  Raise :class:`ValueError` if there are any parse errors."""
    result = parse_cnl(text)
    if result.errors:
        messages = "\n".join(
            f"  Line {e.line}: {e.message}" for e in result.errors
        )
        raise ValueError(f"CNL validation failed:\n{messages}")
    return result
