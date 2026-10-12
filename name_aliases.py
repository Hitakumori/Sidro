"""Account-specific corrections for OCR spellings of player names.

Normalizes and validates a bounded set of exact aliases while retaining
meaningful punctuation for account matching in scan workflows.
"""

MAX_NAME_LENGTH = 64
MAX_ALIASES_PER_ACCOUNT = 20


def normalize_alias_text(value: object) -> str:
    """Ignore letter case and outer whitespace, retaining punctuation."""
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or len(value) > MAX_NAME_LENGTH or not value.isprintable():
        return ""
    if not any(character.isalnum() for character in value):
        return ""
    return value.casefold()


def sanitize_name_aliases(data: object) -> dict[str, list[str]]:
    """Validate settings/imports and deduplicate each account's spellings."""
    if not isinstance(data, dict):
        return {}
    clean = {}
    for owner, values in data.items():
        owner_key = normalize_alias_text(owner)
        if not owner_key or not isinstance(values, list):
            continue
        aliases = clean.setdefault(owner_key, [])
        seen = {normalize_alias_text(alias) for alias in aliases}
        for value in values:
            key = normalize_alias_text(value)
            if not key or key == owner_key or key in seen:
                continue
            if len(aliases) >= MAX_ALIASES_PER_ACCOUNT:
                break
            aliases.append(value.strip())
            seen.add(key)
        if not aliases:
            clean.pop(owner_key, None)
    return clean


def aliases_for_account(name: str, mapping: object) -> frozenset[str]:
    """Settings keys are normalized on load/save; other accounts are ignored."""
    owner_key = normalize_alias_text(name)
    if not owner_key or not isinstance(mapping, dict):
        return frozenset()
    values = mapping.get(owner_key, [])
    if not isinstance(values, list):
        return frozenset()
    return frozenset(key for value in values if (key := normalize_alias_text(value)))


def name_alias_matches(name: str, observed: str, mapping: object) -> bool:
    key = normalize_alias_text(observed)
    return bool(key and key in aliases_for_account(name, mapping))
