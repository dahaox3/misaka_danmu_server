"""Output extension around the unmodified official application. AGPLv3.

No changes to rate limiting, integrity validation, download logic or authentication.
"""
import json
import logging
import random
import re
from urllib.parse import parse_qs

from src.api.dandan.pakku_merge import apply_pakku_merge
from src.api.dandan.danmaku_color import parse_palette, DEFAULT_RANDOM_COLOR_PALETTE
from src.api.dandan.pakku_output import comment_output
from src.db import models
from src.core.pakku_recent import RecentRequest, RecentUnavailable, recent_request

logger = logging.getLogger(__name__)
_PATH = re.compile(r'^/api/v1/[^/]+/(?:api/v2/)?(?:comment/\d+|extcomment)$')
_MAX_BYTES = 32 * 1024 * 1024


class PakkuOutputMiddleware:
    def __init__(self, app, owner_app):
        self.app = app
        self.owner_app = owner_app

    async def __call__(self, scope, receive, send):
        if scope.get('type') != 'http' or scope.get('method') != 'GET' or not _PATH.fullmatch(scope.get('path', '')):
            return await self.app(scope, receive, send)
        manager = self.owner_app.state.config_manager
        count = int(await manager.get('danmakuPakkuLatestCount', '0'))
        ctx = RecentRequest(manager, count)
        context_token = recent_request.set(ctx)
        state = {'start': None, 'body': [], 'size': 0, 'pass': False}

        async def intercept(message):
            if message['type'] == 'http.response.start':
                headers = dict(message.get('headers', []))
                if message['status'] != 200 or b'application/json' not in headers.get(b'content-type', b'') or headers.get(b'content-encoding'):
                    state['pass'] = True
                    return await send(message)
                state['start'] = message
                return
            if message['type'] != 'http.response.body' or state['pass']:
                return await send(message)
            state['body'].append(message.get('body', b''))
            state['size'] += len(message.get('body', b''))
            if state['size'] > _MAX_BYTES:
                state['pass'] = True
                await send(state['start'])
                await send({**message, 'body': b''.join(state['body'])})
                state['body'].clear()
                return
            if message.get('more_body', False):
                return
            raw = b''.join(state['body'])
            try:
                data = json.loads(raw)
                if count and ctx.error:
                    raise RecentUnavailable(ctx.error)
                # Authentication errors and pending/failing jobs retain their exact protocol.
                if not isinstance(data, dict) or data.get('success') is False or data.get('status') in ('pending', 'failed') or not isinstance(data.get('comments'), list):
                    raise ValueError('not a completed comment response')
                if count and (ctx.error or not ctx.applied):
                    raise RecentUnavailable(ctx.error or '此来源未提供发送日期；最新发送筛选目前支持B站，请选择全部')
                manager = self.owner_app.state.config_manager
                merged = await apply_pakku_merge(data['comments'], manager)
                # Native JSON has already removed font size. Preserve its color slot.
                palette = parse_palette(await manager.get('danmakuRandomColorPalette', DEFAULT_RANDOM_COLOR_PALETTE))
                for item in merged:
                    if item.get('_pakku_repeat_count', 0) >= 3:
                        parts = item['p'].split(',')
                        if len(parts) >= 3:
                            parts[2] = str(random.choice(palette))
                            item['p'] = ','.join(parts)
                processed = [models.Comment(cid=i, p=c['p'], m=c['m']) for i, c in enumerate(merged)]
                output_format = parse_qs(scope.get('query_string', b'').decode('ascii', 'ignore')).get('format', ['json'])[0]
                if output_format not in ('json', 'xml'):
                    output_format = 'json'
                response = comment_output(merged, processed, output_format)
                if hasattr(response, 'body'):
                    body = response.body
                    content_type = response.headers['content-type'].encode()
                else:
                    body = json.dumps(response.model_dump(exclude_none=True), ensure_ascii=False, separators=(',', ':')).encode()
                    content_type = b'application/json'
                start = dict(state['start'])
                start['headers'] = [(k,v) for k,v in start.get('headers', []) if k.lower() not in (b'content-length', b'content-type', b'etag', b'content-encoding')]
                if count and ctx.stats:
                    start['headers'] += [(b'x-pakku-recent-selected', str(ctx.stats.get('selectedCount', 0)).encode()),
                                         (b'x-pakku-recent-available', str(ctx.stats.get('availableCount', 0)).encode()),
                                         (b'x-pakku-recent-partial', b'1' if ctx.stats.get('partial') else b'0')]
                start['headers'] += [(b'content-length', str(len(body)).encode()), (b'content-type', content_type)]
                await send(start)
                await send({'type':'http.response.body','body':body,'more_body':False})
            except RecentUnavailable as exc:
                body = json.dumps({'success': False, 'detail': str(exc), 'count': 0, 'comments': []}, ensure_ascii=False).encode()
                start = dict(state['start'])
                start['status'] = 503
                start['headers'] = [(b'content-type', b'application/json'), (b'content-length', str(len(body)).encode())]
                await send(start)
                await send({'type': 'http.response.body', 'body': body, 'more_body': False})
            except Exception:
                # Includes an unavailable merge engine; native responses remain usable.
                await send(state['start'])
                await send({'type':'http.response.body','body':raw,'more_body':False})

        try:
            await self.app(scope, receive, intercept)
        finally:
            recent_request.reset(context_token)
