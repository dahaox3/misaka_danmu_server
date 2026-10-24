"""Optional output-only pakku bridge. Original comment files are never modified.

2026-10-20 local modification, AGPL-3.0-or-later.
The separate GPL pakku engine is accessible only on the private Docker network.
"""
import logging
import os
import time

import httpx
from src.core.pakku_settings import read_pakku_settings

logger = logging.getLogger(__name__)
_retry_after = 0.0


async def pakku_enabled(config_manager):
    return (await config_manager.get('danmakuPakkuEnabled', 'false')).lower() == 'true'


async def apply_pakku_merge(comments, config_manager):
    global _retry_after
    if not comments or not await pakku_enabled(config_manager) or time.monotonic() < _retry_after:
        return comments
    try:
        settings = await read_pakku_settings(config_manager)
        mode, window = settings.mode, settings.windowSeconds
        # Never use the configured upstream proxy for private comment processing.
        async with httpx.AsyncClient(timeout=httpx.Timeout(18, connect=2), trust_env=False) as client:
            response = await client.post(
                os.environ.get('MISAKA_PAKKU_URL', 'http://pakku:17769/merge'),
                json={'comments': comments, 'mode': mode, 'windowSeconds': window,
                      'options': settings.options.model_dump()},
            )
            response.raise_for_status()
            output = response.json()['comments']
        if not isinstance(output, list) or len(output) > len(comments) or any(
            not isinstance(c, dict) or not isinstance(c.get('p'), str) or not isinstance(c.get('m'), str)
            for c in output
        ):
            raise ValueError('invalid pakku output')
        logger.info('pakku output merge: %s -> %s, mode=%s, window=%ss',
                    len(comments), len(output), mode, window)
        return output
    except Exception:
        # Do not leak comment bodies, URLs or credentials in errors.
        _retry_after = time.monotonic() + 5
        logger.warning('pakku unavailable; returning original comments for this request')
        return comments
