"""Date-preserving Bilibili pool reader. AGPL-3.0-or-later.

Only explicit player-output requests use this provider. Native authentication,
quotas, output filters and the original stored XML remain owned by Misaka.
"""
import asyncio
import copy
import json
import os
import re
import time
import zlib
from collections import OrderedDict
from dataclasses import dataclass, field

import httpx
from fastapi import HTTPException


class RecentUnavailable(Exception):
    pass


class BilibiliApiError(RecentUnavailable):
    def __init__(self, code):
        self.code = code if isinstance(code, int) else None
        super().__init__('B站暂未返回可用弹幕，请稍后重试')


@dataclass
class RecentRequest:
    manager: object
    latest_count: int
    applied: bool = False
    stats: dict = field(default_factory=dict)
    error: str = ''


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
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(18, connect=2), trust_env=False) as client:
            response = await client.post(os.environ.get('MISAKA_PAKKU_URL', 'http://pakku:17769/merge'),
                                         json={'comments': rows, 'selectionOnly': True, 'latestCount': count})
            response.raise_for_status()
            selected = response.json()['comments']
        allowed = {row['id'] for row in rows}
        if not isinstance(selected, list) or len(selected) > count or any(
            not isinstance(row, dict) or row.get('id') not in allowed for row in selected
        ):
            raise ValueError('invalid selection')
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise RecentUnavailable('最新弹幕筛选引擎未返回可用结果，请稍后重试') from exc
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
            try:
                code = json.loads(raw).get('code')
            except (ValueError, AttributeError):
                code = None
            raise BilibiliApiError(code)
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
            async def xml_pool():
                return decode_xml(await self._get(client, f'https://comment.bilibili.com/{cid}.xml'))

            async def segment_pool():
                view = await self._get(client, f'https://api.bilibili.com/x/v2/dm/web/view?type=1&oid={cid}&pid={aid}')
                total = 0
                for number, wire, value in proto_fields(view):
                    if number == 4 and wire == 2:
                        total = int(dict((n, v) for n, _, v in proto_fields(value)).get(2, 0))
                if not 1 <= total <= 32:
                    raise RecentUnavailable('未取得有效的B站弹幕分段信息')
                per_video = asyncio.Semaphore(2)
                blocked = False

                async def segment(index):
                    nonlocal blocked
                    async with per_video:
                        if blocked:
                            raise RecentUnavailable('已暂停受限弹幕池的剩余请求')
                        try:
                            raw = await self._get(client, f'https://api.bilibili.com/x/v2/dm/web/seg.so?type=1&oid={cid}&segment_index={index}')
                            return decode_segment(raw)
                        except BilibiliApiError as exc:
                            if exc.code in (-352, -412):
                                blocked = True
                            raise

                # Wait for every started request before closing its HTTP client.
                results = await asyncio.gather(*(segment(i) for i in range(1, total + 1)), return_exceptions=True)
                good = [item for item in results if not isinstance(item, Exception)]
                errors = [item for item in results if isinstance(item, Exception)]
                return good, total, errors

            xml_result, segment_result = await asyncio.gather(xml_pool(), segment_pool(), return_exceptions=True)
            xml_ok = not isinstance(xml_result, Exception)
            xml_rows = xml_result if xml_ok else []
            if isinstance(segment_result, Exception):
                segment_pools, total, errors = [], 0, [segment_result]
            else:
                segment_pools, total, errors = segment_result
            rows = union_pools([xml_rows, *segment_pools])
            if not rows:
                raise RecentUnavailable('来源未返回带发送日期的弹幕')
            partial = not xml_ok or bool(errors)
            codes = sorted({exc.code for exc in errors if isinstance(exc, BilibiliApiError) and exc.code is not None})
            if codes and not segment_pools:
                warning = f'B站分段接口受限（{", ".join(map(str, codes))}），本次仅从当前XML弹幕池选择；不代表全站完整的最新弹幕。'
            elif partial:
                warning = '部分B站弹幕池暂不可用，本次仅在成功获取的当前弹幕中选择；不代表全站完整的最新弹幕。'
            else:
                warning = ''
            return rows, {'xmlCount': len(xml_rows), 'segmentCount': sum(map(len, segment_pools)),
                          'availableCount': len(rows), 'fetchedAt': int(time.time()), 'segments': total,
                          'xmlAvailable': xml_ok, 'segmentsFetched': len(segment_pools),
                          'partial': partial, 'warning': warning, 'apiCodes': codes}

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
                    ttl = 60 if result[1].get('partial') else 300
                    self.cache[key] = (time.monotonic() + ttl, result, size)
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

    async def comments(self, database, episode_id, count, manager):
        row = await database.episode.get_episode_provider_info(episode_id)
        if row is None:
            return None, {}
        if row['providerName'].lower() != 'bilibili':
            return None, {'selectionMode': 'all', 'provider': row['providerName'].lower()}
        # Misaka's Bilibili identifier is the exact aid,cid pair. Never choose
        # the first same-numbered season entry: that can be an episode preview.
        match = re.fullmatch(r'(\d{1,20}),(\d{1,20})', row['providerEpisodeId'] or '')
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

    async def read_for_player(self, database, episode_id, native_reader, ctx):
        """仅在已鉴权的播放读取中筛选，不修改全局读取器或后台任务。"""
        try:
            rows, info = await self.comments(database, episode_id, ctx.latest_count, ctx.manager)
        except RecentUnavailable as exc:
            ctx.error = str(exc)
            raise HTTPException(503, detail=ctx.error) from exc
        if rows is None:
            return await native_reader(episode_id)
        ctx.applied, ctx.stats = True, info
        return rows
