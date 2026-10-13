"""Shared fail-closed parsing for the trusted read-only environment flag."""


def parse_read_only(value: str) -> bool:
    """Accept only true/false (case-insensitive); never silently enable writes."""
    match value.strip().lower():
        case "true":
            return True
        case "false":
            return False
        case _:
            raise ValueError("DISPATCH_READ_ONLY must be true or false")
