"""Date-preserving Bilibili pool reader. AGPL-3.0-or-later.

Only explicit player-output requests use this provider. Native authentication,
quotas, output filters and the original stored XML remain owned by Misaka.
"""
import asyncio
import copy
import contextvars
import importlib
import json
import re
import time
import zlib
from collections import OrderedDict
from dataclasses import dataclass, field

import httpx
from fastapi import HTTPException


class RecentUnavailable(Exception):
    pass


@dataclass
class RecentRequest:
    manager: object
    latest_count: int
    task: object = field(default_factory=asyncio.current_task)
    applied: bool = False
    stats: dict = field(default_factory=dict)
    error: str = ''


recent_request = contextvars.ContextVar('pakku_recent_request', default=None)
MAX_RESPONSE = 8 * 1024 * 1024
MAX_CACHE = 16 * 1024 * 1024
MAX_ROWS = 100000


def _varint(data, offset):
    value = shift = 0
    while offset < len(data) and shift < 70:
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
        shift += 7
    raise RecentUnavailable('弹幕数据格式不完整')


def proto_fields(data):
    offset = 0
    while offset < len(data):
        tag, offset = _varint(data, offset)
        number, wire = tag >> 3, tag & 7
        if not number:
            raise RecentUnavailable('弹幕数据字段无效')
        if wire == 0:
            value, offset = _varint(data, offset)
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            value, offset = data[offset:offset + size], offset + size
        elif wire == 2:
            size, offset = _varint(data, offset)
            value, offset = data[offset:offset + size], offset + size
        else:
            raise RecentUnavailable('不支持的弹幕数据字段')
        if offset > len(data):
            raise RecentUnavailable('弹幕数据被截断')
        yield number, wire, value


def decode_segment(data):
    rows = []
    for number, wire, value in proto_fields(data):
        if number != 1 or wire != 2:
            continue
        dm = {key: val for key, _, val in proto_fields(value)}
        ident = dm.get(12)
        ident = ident.decode('ascii') if isinstance(ident, bytes) else str(dm.get(1, ''))
        if not ident.isdecimal():
            continue
        rows.append({
            'id': ident, 'sendtime': int(dm.get(8, 0)),
            'p': f"{int(dm.get(2, 0)) / 1000:g},{int(dm.get(3, 1))},{int(dm.get(4, 25))},{int(dm.get(5, 16777215))},[bilibili]",
            'm': dm.get(7, b'').decode('utf-8', errors='replace'),
            'weight': int(dm.get(9, 0)),
        })
    return rows


def decode_xml(data):
    # Public pools occasionally contain illegal XML control characters. Do not
    # touch the stored source file, and never resolve XML entities/DTDs.
    import xml.etree.ElementTree as ET
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise RecentUnavailable('不支持带外部实体的弹幕 XML')
    cleaned = re.sub(rb'[\x00-\x08\x0b\x0c\x0e-\x1f]', b'', data)
    try:
        root = ET.fromstring(cleaned)
    except ET.ParseError:
        raise RecentUnavailable('来源 XML 格式无效') from None
    rows = []
    for element in root.iter('d'):
        parts = element.get('p', '').split(',')
        if len(parts) < 8 or not parts[7].isdecimal():
            continue
        try:
            rows.append({'id': parts[7], 'sendtime': int(parts[4]),
                         'p': ','.join(parts[:4] + ['[bilibili]']),
                         'm': element.text or ''})
        except ValueError:
            continue
    return rows


def union_pools(pools, now=None):
    """ID de-duplication precedes chronological selection, never text merging."""
    now = now if now is not None else time.time()
    rows = {}
    for pool in pools:
        for item in pool:
            stamp = item.get('sendtime', 0)
            if not 946684800 <= stamp <= now + 300 or not item.get('m'):
                continue
            ident = item['id']
            if ident not in rows:
                rows[ident] = dict(item)
            if len(rows) > MAX_ROWS:
                raise RecentUnavailable('来源弹幕过多，请稍后重试')
    return list(rows.values())


def _decompress(data, encoding):
    encoding = encoding.lower().strip()
    if encoding in ('', 'identity'):
        return data
    if encoding not in ('gzip', 'deflate'):
        raise RecentUnavailable('来源使用了不支持的压缩格式')
    formats = [16 + zlib.MAX_WBITS] if encoding == 'gzip' else [zlib.MAX_WBITS, -zlib.MAX_WBITS]
    for fmt in formats:
        try:
            decoder = zlib.decompressobj(fmt)
            result = decoder.decompress(data, MAX_RESPONSE + 1)
            if len(result) > MAX_RESPONSE or not decoder.eof:
                raise RecentUnavailable('来源响应超出大小限制')
            return result
        except zlib.error:
            continue
    raise RecentUnavailable('来源压缩数据无效')


async def _select(rows, count):
    import os
    async with httpx.AsyncClient(timeout=httpx.Timeout(18, connect=2), trust_env=False) as client:
        response = await client.post(os.environ.get('MISAKA_PAKKU_URL', 'http://pakku:17769/merge'),
                                     json={'comments': rows, 'selectionOnly': True, 'latestCount': count})
        response.raise_for_status()
        selected = response.json()['comments']
    allowed = {row['id'] for row in rows}
    if not isinstance(selected, list) or len(selected) > count or any(row.get('id') not in allowed for row in selected):
        raise RecentUnavailable('最新弹幕筛选结果无效')
    return selected


class RecentCommentProvider:
    def __init__(self):
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.inflight = {}
        self.retry_after = {}
        self.semaphore = asyncio.Semaphore(2)

    async def _get(self, client, url):
        # Read undecoded bytes: old optional Python Brotli bindings must not be
        # invoked by a new acquisition path. Only bounded gzip/deflate is used.
        async with client.stream('GET', url) as response:
            response.raise_for_status()
            raw = bytearray()
            async for chunk in response.aiter_raw():
                raw.extend(chunk)
                if len(raw) > MAX_RESPONSE:
                    raise RecentUnavailable('来源响应超出大小限制')
            raw = _decompress(bytes(raw), response.headers.get('content-encoding', ''))
        if raw.lstrip().startswith(b'{'):
            raise RecentUnavailable('B站暂未返回可用弹幕，请稍后重试')
        return raw

    async def _fetch(self, aid, cid, manager):
        mode = await manager.get('proxyMode', 'none')
        enabled = (await manager.get('proxyEnabled', 'false')).lower() == 'true'
        proxy = (await manager.get('proxyUrl', '')) if enabled and mode == 'http_socks' else None
        if enabled and mode not in ('none', 'http_socks'):
            raise RecentUnavailable('最新弹幕目前支持直连或 HTTP/SOCKS 代理')
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.bilibili.com/', 'Accept-Encoding': 'identity'}
        async with self.semaphore, httpx.AsyncClient(proxy=proxy or None, headers=headers,
                timeout=httpx.Timeout(12, connect=5), trust_env=False, follow_redirects=False) as client:
            view = await self._get(client, f'https://api.bilibili.com/x/v2/dm/web/view?type=1&oid={cid}&pid={aid}')
            total = 0
            for number, wire, value in proto_fields(view):
                if number == 4 and wire == 2:
                    total = int(dict((n, v) for n, _, v in proto_fields(value)).get(2, 0))
            if not 1 <= total <= 32:
                raise RecentUnavailable('未取得有效的B站弹幕分段信息')
            per_video = asyncio.Semaphore(2)
            async def segment(index):
                async with per_video:
                    raw = await self._get(client, f'https://api.bilibili.com/x/v2/dm/web/seg.so?type=1&oid={cid}&segment_index={index}')
                    return decode_segment(raw)
            xml_future = self._get(client, f'https://comment.bilibili.com/{cid}.xml')
            results = await asyncio.gather(xml_future, *(segment(i) for i in range(1, total + 1)))
            xml_rows = decode_xml(results[0])
            rows = union_pools([xml_rows, *results[1:]])
            if not rows:
                raise RecentUnavailable('来源未返回带发送日期的弹幕')
            return rows, {'xmlCount': len(xml_rows), 'segmentCount': sum(map(len, results[1:])),
                          'availableCount': len(rows), 'fetchedAt': int(time.time()), 'segments': total}

    async def _cached(self, aid, cid, manager):
        key = (aid, cid)
        now = time.monotonic()
        cached = self.cache.get(key)
        if cached and cached[0] > now:
            self.cache.move_to_end(key)
            return copy.deepcopy(cached[1])
        if self.retry_after.get(key, 0) > now:
            raise RecentUnavailable('来源暂时不可用，请稍后重试')
        if key not in self.inflight:
            if len(self.inflight) >= 4:
                raise RecentUnavailable('最新弹幕正在处理中，请稍后重试')
            self.inflight[key] = asyncio.create_task(asyncio.wait_for(self._fetch(aid, cid, manager), 28))
            def cleanup(task):
                if self.inflight.get(key) is task:
                    self.inflight.pop(key, None)
                if not task.cancelled():
                    task.exception()  # Retrieve failures even if every caller disconnected.
            self.inflight[key].add_done_callback(cleanup)
        task = self.inflight[key]
        try:
            result = await asyncio.shield(task)
            if key not in self.cache or self.cache[key][1] is not result:
                size = len(json.dumps(result, ensure_ascii=False).encode())
                old = self.cache.pop(key, None)
                if old:
                    self.cache_bytes -= old[2]
                while self.cache and (len(self.cache) >= 8 or self.cache_bytes + size > MAX_CACHE):
                    _, (_, _, removed_size) = self.cache.popitem(last=False)
                    self.cache_bytes -= removed_size
                if size <= MAX_CACHE:
                    self.cache[key] = (time.monotonic() + 300, result, size)
                    self.cache_bytes += size
            return copy.deepcopy(result)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.retry_after[key] = time.monotonic() + 20
            if len(self.retry_after) > 128:
                self.retry_after = {k: v for k, v in self.retry_after.items() if v > time.monotonic()}
            raise RecentUnavailable('无法获取最新弹幕；未使用历史弹幕替代，请稍后重试或选择全部') from None
        finally:
            if task.done() and self.inflight.get(key) is task:
                self.inflight.pop(key, None)

    async def comments(self, session, episode_id, count, manager):
        from sqlalchemy import select
        from src.db.orm_models import Episode, AnimeSource
        row = (await session.execute(select(Episode.providerEpisodeId, Episode.sourceUrl,
                        AnimeSource.providerName).join(AnimeSource, Episode.sourceId == AnimeSource.id)
                        .where(Episode.id == episode_id))).first()
        if row is None:
            return None, {}
        if row.providerName.lower() != 'bilibili':
            raise RecentUnavailable('此来源没有可用的发送日期；最新发送筛选目前支持B站，请选择全部以使用其他来源')
        # Misaka's Bilibili identifier is the exact aid,cid pair. Never choose
        # the first same-numbered season entry: that can be an episode preview.
        match = re.fullmatch(r'(\d{1,20}),(\d{1,20})', row.providerEpisodeId or '')
        if not match or not all(int(x) > 0 for x in match.groups()):
            raise RecentUnavailable('该分集缺少有效B站视频标识，请重新选择正片来源')
        aid, cid = map(int, match.groups())
        rows, info = await self._cached(aid, cid, manager)
        selected = await _select(rows, count)
        stamps = [x['sendtime'] for x in selected]
        info.update({'selectedCount': len(selected), 'requestedCount': count,
                     'newestSendTime': max(stamps, default=0), 'oldestSendTime': min(stamps, default=0)})
        selected.sort(key=lambda x: float(x['p'].split(',')[0]))
        return selected, info

    def install(self, app):
        """Scoped data-reader hooks; all official source files remain intact."""
        from src.db import crud
        native = importlib.import_module('src.api.dandan.comments')
        if getattr(crud.fetch_comments, '_pakku_recent', False) is True:
            raise RuntimeError('Recent provider already installed')
        def current():
            ctx = recent_request.get()
            return ctx if ctx and ctx.latest_count and ctx.task is asyncio.current_task() else None
        for name in ('fetch_comments', 'fetch_merged_comments'):
            original = getattr(crud, name)
            async def wrapped(session, episode_id, _original=original):
                ctx = current()
                if ctx is None:
                    return await _original(session, episode_id)
                try:
                    rows, info = await self.comments(session, episode_id, ctx.latest_count, ctx.manager)
                    if rows is None:
                        return await _original(session, episode_id)
                    ctx.applied, ctx.stats = True, info
                    return rows
                except RecentUnavailable as exc:
                    ctx.error = str(exc)
                    raise HTTPException(503, detail=ctx.error) from None
            wrapped._pakku_recent = True
            setattr(crud, name, wrapped)
        # Uniform-sampling caches belong to the full historical output. A recent
        # request must neither read those old rows nor poison them for later use.
        # FastAPI/Starlette may run the endpoint in a child task. Bind the scope
        # only at the authenticated endpoint, while inherited background tasks
        # still have a different identity and keep the original database reader.
        import functools
        for route in app.routes:
            dependant = getattr(route, 'dependant', None)
            call = getattr(dependant, 'call', None)
            if getattr(call, '__module__', '') != native.__name__:
                continue
            @functools.wraps(call)
            async def endpoint(*args, _call=call, **kwargs):
                ctx = recent_request.get()
                if ctx:
                    ctx.task = asyncio.current_task()
                return await _call(*args, **kwargs)
            dependant.call = endpoint
        old_get, old_set = native.get_db_cache, native.set_db_cache
        prefix = native.SAMPLED_COMMENTS_CACHE_PREFIX
        async def scoped_get(*args, **kwargs):
            if current() is not None and len(args) > 1 and args[1] == prefix:
                return None
            return await old_get(*args, **kwargs)
        async def scoped_set(*args, **kwargs):
            if current() is not None and len(args) > 1 and args[1] == prefix:
                return None
            return await old_set(*args, **kwargs)
        native.get_db_cache, native.set_db_cache = scoped_get, scoped_set
