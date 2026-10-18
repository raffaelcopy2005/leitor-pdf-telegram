"""Geração de IDs curtos e seguros para o parâmetro startapp."""

from __future__ import annotations

import re
import secrets
import string

ID_ALPHABET = string.ascii_letters + string.digits + "_-"
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
DEFAULT_ID_LENGTH = 12  # 64^12 ≈ 4,7 × 10^21 combinações


def generate_id(length: int = DEFAULT_ID_LENGTH) -> str:
    """Gera um ID aleatório (criptograficamente seguro) com caracteres [A-Za-z0-9_-]."""
    if not 1 <= length <= 64:
        raise ValueError("O tamanho do ID deve estar entre 1 e 64")
    return "".join(secrets.choice(ID_ALPHABET) for _ in range(length))


def is_valid_id(value: object) -> bool:
    return isinstance(value, str) and ID_PATTERN.fullmatch(value) is not None
