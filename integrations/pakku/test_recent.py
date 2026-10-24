"""Date parsing, bounded pool union and request-scoped hook regression tests."""
import asyncio
import importlib.util
import json
import sys
import types
import unittest
import httpx
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('pakku_recent_under_test', ROOT / 'src/core/pakku_recent.py')
recent = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = recent
spec.loader.exec_module(recent)


def varint(n):
    out = bytearray()
    while n > 127:
        out.append((n & 127) | 128)
        n >>= 7
    return bytes(out + bytes([n]))


def integer(n, value):
    return varint(n << 3) + varint(value)


def string(n, value):
    return varint(n << 3 | 2) + varint(len(value)) + value


class ParseTests(unittest.TestCase):
    def test_real_protocol_fields_and_large_id(self):
        dm = integer(1, 90071992547409931) + integer(2, 1500) + integer(3, 5)
        dm += integer(4, 25) + integer(5, 123) + string(7, 'hello'.encode()) + integer(8, 1792800000)
        dm += string(12, b'90071992547409931')
        item = recent.decode_segment(string(1, dm))[0]
        self.assertEqual(item['sendtime'], 1792800000)
        self.assertEqual(item['id'], '90071992547409931')
        self.assertEqual(item['p'], '1.5,5,25,123,[bilibili]')

    def test_xml_keeps_date_and_rejects_dtd(self):
        xml = b'<i><d p="1,1,25,123,1792800000,0,user,90071992547409931">&lt;hi&gt;</d></i>'
        item = recent.decode_xml(xml)[0]
        self.assertEqual(item['sendtime'], 1792800000)
        self.assertEqual(item['m'], '<hi>')
        with self.assertRaises(recent.RecentUnavailable):
            recent.decode_xml(b'<!DOCTYPE i><i/>')

    def test_union_deduplicates_ids_not_equal_text_and_excludes_unknown_dates(self):
        a = {'id': '1', 'sendtime': 1792800000, 'm': 'same'}
        b = {'id': '2', 'sendtime': 1792800001, 'm': 'same'}
        self.assertEqual(len(recent.union_pools([[a, b], [a, {**a, 'id': '3', 'sendtime': 0}]], now=1792800002)), 2)

    def test_truncated_proto_and_brotli_are_rejected(self):
        with self.assertRaises(recent.RecentUnavailable):
            list(recent.proto_fields(b'\x0a\x05x'))
        with self.assertRaises(recent.RecentUnavailable):
            recent._decompress(b'bad', 'br')


class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_acquisition_is_shared_and_callers_get_owned_rows(self):
        provider = recent.RecentCommentProvider()
        async def fetch(*args):
            await asyncio.sleep(.01)
            return ([{'id': '1', 'sendtime': 1792800000, 'm': 'safe'}], {'availableCount': 1})
        provider._fetch = AsyncMock(side_effect=fetch)
        a, b = await asyncio.gather(provider._cached(1, 2, None), provider._cached(1, 2, None))
        a[0][0]['m'] = 'changed'
        self.assertEqual(b[0][0]['m'], 'safe')
        self.assertEqual((await provider._cached(1, 2, None))[0][0]['m'], 'safe')
        provider._fetch.assert_awaited_once()

    async def test_failed_acquisition_does_not_return_historical_rows(self):
        provider = recent.RecentCommentProvider()
        provider._fetch = AsyncMock(side_effect=RuntimeError('must not expose this detail'))
        for _ in range(2):
            with self.assertRaisesRegex(recent.RecentUnavailable, '稍后重试'):
                await provider._cached(1, 2, None)
        provider._fetch.assert_awaited_once()


class PoolFailureTests(unittest.IsolatedAsyncioTestCase):
    async def manager(self):
        async def get(key, default=None):
            return default
        return types.SimpleNamespace(get=get)

    def xml(self):
        return b'<i><d p="1,1,25,123,1792800000,0,user,1">current</d></i>'

    def segment(self):
        dm = integer(1, 2) + integer(2, 2000) + string(7, b'segmented') + integer(8, 1792800001)
        return string(1, dm)

    async def test_risk_control_keeps_current_xml_and_stops_remaining_segments(self):
        provider = recent.RecentCommentProvider()
        async def get(client, url):
            if url.endswith('.xml'):
                await asyncio.sleep(.01)  # A fast segment failure must not cancel this request.
                return self.xml()
            if '/view?' in url:
                return string(4, integer(2, 4))
            raise recent.BilibiliApiError(-352)
        provider._get = AsyncMock(side_effect=get)
        rows, info = await provider._fetch(1, 2, await self.manager())
        self.assertEqual([row['id'] for row in rows], ['1'])
        self.assertEqual(info['apiCodes'], [-352])
        self.assertTrue(info['partial'])
        self.assertEqual(info['segmentsFetched'], 0)
        self.assertIn('当前XML', info['warning'])
        segment_calls = [c for c in provider._get.call_args_list if '/seg.so?' in c.args[1]]
        self.assertLessEqual(len(segment_calls), 2)

    async def test_xml_failure_keeps_dated_segment_pool_with_warning(self):
        provider = recent.RecentCommentProvider()
        async def get(client, url):
            if url.endswith('.xml'):
                raise OSError('do not expose raw upstream failures')
            return string(4, integer(2, 1)) if '/view?' in url else self.segment()
        provider._get = AsyncMock(side_effect=get)
        rows, info = await provider._fetch(1, 2, await self.manager())
        self.assertEqual([row['id'] for row in rows], ['2'])
        self.assertTrue(info['partial'])
        self.assertFalse(info['xmlAvailable'])
        self.assertEqual(info['segmentsFetched'], 1)
        self.assertNotIn('OSError', info['warning'])

    async def test_all_pool_failures_never_use_historical_rows(self):
        provider = recent.RecentCommentProvider()
        provider._get = AsyncMock(side_effect=recent.BilibiliApiError(-352))
        with self.assertRaises(recent.RecentUnavailable):
            await provider._fetch(1, 2, await self.manager())

    async def test_healthy_pools_preserve_complete_status(self):
        provider = recent.RecentCommentProvider()
        async def get(client, url):
            if url.endswith('.xml'):
                return self.xml()
            return string(4, integer(2, 1)) if '/view?' in url else self.segment()
        provider._get = AsyncMock(side_effect=get)
        rows, info = await provider._fetch(1, 2, await self.manager())
        self.assertEqual(len(rows), 2)
        self.assertFalse(info['partial'])
        self.assertEqual(info['warning'], '')

    async def test_json_api_error_keeps_only_safe_error_code(self):
        provider = recent.RecentCommentProvider()
        transport = httpx.MockTransport(lambda request: httpx.Response(200,
            stream=httpx.ByteStream(b'{"code":-352,"message":"do not expose this payload"}')))
        async with httpx.AsyncClient(transport=transport) as client:
            with self.assertRaises(recent.BilibiliApiError) as caught:
                await provider._get(client, 'https://api.bilibili.com/test')
        self.assertEqual(caught.exception.code, -352)
        self.assertNotIn('payload', str(caught.exception))


class ScopedHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_endpoint_child_task_uses_recent_background_and_nonplayer_do_not(self):
        old = AsyncMock(return_value=[{'m': 'historical'}])
        old_set = AsyncMock()
        crud = types.SimpleNamespace(fetch_comments=old, fetch_merged_comments=old)
        native = types.SimpleNamespace(__name__='src.api.dandan.comments',
                 get_db_cache=AsyncMock(return_value='historical cache'),
                 set_db_cache=old_set, SAMPLED_COMMENTS_CACHE_PREFIX='sampled')
        async def endpoint():
            selected = await crud.fetch_comments(None, 123)
            background = await asyncio.create_task(crud.fetch_comments(None, 123))
            cached = await native.get_db_cache(None, 'sampled', 'key')
            await native.set_db_cache(None, 'sampled', 'key', {})
            return selected, background, cached
        endpoint.__module__ = native.__name__
        route = types.SimpleNamespace(dependant=types.SimpleNamespace(call=endpoint))
        provider = recent.RecentCommentProvider()
        provider.comments = AsyncMock(return_value=([{'m': 'recent'}], {'selectedCount': 1}))
        db = types.ModuleType('src.db'); db.crud = crud
        with patch.dict(sys.modules, {'src.db': db}), patch.object(recent.importlib, 'import_module', return_value=native):
            provider.install(types.SimpleNamespace(routes=[route]))
        ctx = recent.RecentRequest(None, 3000)
        token = recent.recent_request.set(ctx)
        try:
            selected, background, cached = await asyncio.create_task(route.dependant.call())
            self.assertEqual(selected, [{'m': 'recent'}])
            self.assertEqual(background, [{'m': 'historical'}])
            self.assertIsNone(cached)
            old_set.assert_not_awaited()
        finally:
            recent.recent_request.reset(token)
        self.assertEqual(await crud.fetch_comments(None, 123), [{'m': 'historical'}])


if __name__ == '__main__':
    unittest.main(verbosity=2)
