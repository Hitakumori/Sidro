"""User-provided Warframe Market token validation and local storage.

Validates the market token before replacing its saved value. This module
supports market listing access; Sidro features require no membership or
paid-access entitlement service.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import requests

import config




def save_wfm_token(token: str, env_path: Path | None = None) -> None:
    token = _clean_token(token)
    if not token:
        raise ValueError("JWT token is empty.")
    env_path = env_path or config.ENV_PATH
    env_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    replaced = False
    output: list[str] = []
    for line in lines:
        if line.startswith("WFM_TOKEN="):
            output.append(f"WFM_TOKEN={token}")
            replaced = True
        else:
            output.append(line)

    if not replaced:
        output.append(f"WFM_TOKEN={token}")

    env_path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    os.environ["WFM_TOKEN"] = token
    config.WFM_TOKEN = token


def validate_wfm_token(token: str) -> bool:
    token = _clean_token(token)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": f"{config.APP_NAME}/{config.APP_VERSION} (+https://github.com/Hitakumori/Sidro)",
        "Platform": config.WFM_PLATFORM,
        "Language": config.WFM_LANGUAGE,
        "Crossplay": "true",
        "Authorization": f"Bearer {token}",
    }
    cookies = {"JWT": token}
    # Validate against the same authenticated endpoint the app needs for
    # listings. /profile is not stable across WFM API versions.
    resp = requests.get(
        f"{config.WFM_API_BASE_V2}/orders/my",
        headers=headers,
        cookies=cookies,
        timeout=12,
        # This request has a JWT cookie. Do not forward it through redirects.
        allow_redirects=False,
    )
    return 200 <= resp.status_code < 300




def _clean_token(token: str) -> str:
    token = token.strip().strip('"').strip("'")
    match = re.search(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token)
    if match:
        return match.group(0)
    if token.upper().startswith("JWT="):
        token = token[4:]
    return token.strip().strip('"').strip("'")
