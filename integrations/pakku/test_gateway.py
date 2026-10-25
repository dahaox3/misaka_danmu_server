"""Protocol tests for the standalone output extension; no app lifespan or downloads."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import xml.etree.ElementTree as ET

import pakku_gateway as gateway


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, payload, status=200, path='/api/v1/test/api/v2/comment/123',
                      query=b'', headers=None, chunks=None, merge=None, latest=0, recent_error='', recent_stats=None):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        sent = []
        async def app(scope, receive, send):
            if recent_error:
                gateway.recent_request.get().error = recent_error
            if recent_stats is not None:
                ctx = gateway.recent_request.get()
                ctx.applied, ctx.stats = True, recent_stats
            await send({'type': 'http.response.start', 'status': status,
                        'headers': headers or [(b'content-type', b'application/json'),
                                               (b'content-length', str(len(raw)).encode())]})
            parts = chunks or [raw]
            for i, part in enumerate(parts):
                await send({'type': 'http.response.body', 'body': part, 'more_body': i < len(parts)-1})
        async def send(message):
            sent.append(message)
        async def get(key, default=None):
            return str(latest) if key == 'danmakuPakkuLatestCount' else default
        owner = SimpleNamespace(state=SimpleNamespace(config_manager=SimpleNamespace(get=get)))
        merger = merge or AsyncMock(side_effect=lambda comments, manager: comments)
        with patch.object(gateway, 'apply_pakku_merge', merger):
            await gateway.PakkuOutputMiddleware(app, owner)(
                {'type': 'http', 'method': 'GET', 'path': path, 'query_string': query}, None, send)
        return sent, merger

    async def test_auth_error_unchanged(self):
        raw = b'{"detail":"Unauthorized"}'
        sent, merge = await self.request(raw, status=401)
        self.assertEqual(sent[0]['status'], 401)
        self.assertEqual(sent[1]['body'], raw)
        merge.assert_not_awaited()

    async def test_pending_and_failed_unchanged(self):
        for status in ('pending', 'failed'):
            raw = json.dumps({'status': status, 'comments': []}).encode()
            sent, merge = await self.request(raw)
            self.assertEqual(sent[1]['body'], raw)
            merge.assert_not_awaited()

    async def test_non_bilibili_native_rows_are_all_given_to_merger_despite_latest_limit(self):
        rows = [{'p': f'{i},1,123,[dandanplay]', 'm': str(i)} for i in range(3)]
        merger = AsyncMock(return_value=rows[:2])
        sent, merge = await self.request({'comments': rows}, latest=1, merge=merger)
        self.assertEqual(sent[0]['status'], 200)
        self.assertEqual(json.loads(sent[1]['body'])['count'], 2)
        self.assertEqual(merge.await_args.args[0], rows)
        headers = dict(sent[0]['headers'])
        self.assertEqual(headers[b'x-pakku-recent-mode'], b'all')
        self.assertNotIn(b'x-pakku-recent-selected', headers)

    async def test_bilibili_applied_recent_selection_keeps_counts_and_mode(self):
        rows = [{'p': '1,1,123,[bilibili]', 'm': 'recent'}]
        sent, _ = await self.request({'comments': rows}, latest=5000,
              recent_stats={'selectedCount': 5000, 'availableCount': 9000, 'partial': True})
        self.assertEqual(sent[0]['status'], 200)
        headers = dict(sent[0]['headers'])
        self.assertEqual(headers[b'x-pakku-recent-mode'], b'bilibili')
        self.assertEqual(headers[b'x-pakku-recent-selected'], b'5000')
        self.assertEqual(headers[b'x-pakku-recent-partial'], b'1')

    async def test_recent_auth_failure_never_fetches_or_merges(self):
        sent, merge = await self.request(b'{"detail":"Unauthorized"}', status=401, latest=3000)
        self.assertEqual(sent[0]['status'], 401)
        merge.assert_not_awaited()

    async def test_recent_source_failure_remains_explicit_in_native_error_envelope(self):
        sent, merge = await self.request({'success': False, 'errorCode': 503},
                                        latest=3000, recent_error='来源暂时不可用',
                                        headers=[(b'content-type', b'application/json'),
                                                 (b'access-control-allow-origin', b'https://player.example')])
        self.assertEqual(sent[0]['status'], 503)
        data = json.loads(sent[1]['body'])
        self.assertEqual(data['detail'], '来源暂时不可用')
        self.assertEqual(data['comments'], [])
        self.assertEqual(dict(sent[0]['headers'])[b'access-control-allow-origin'], b'https://player.example')
        merge.assert_not_awaited()

    async def test_json_merge_color_font_and_metadata(self):
        result = [{'p': '1,5,16777215,[bilibili]', 'm': '[3] hello',
                   'fontSize': 40, '_pakku_repeat_count': 3}]
        sent, merge = await self.request({'comments': result}, merge=AsyncMock(return_value=result))
        data = json.loads(sent[1]['body'])
        self.assertEqual(data['count'], 1)
        item = data['comments'][0]
        parts = item['p'].split(',')
        self.assertEqual(parts[:2], ['1', '5'])
        self.assertTrue(0 <= int(parts[2]) <= 16777215)
        self.assertEqual(parts[3], '[bilibili]')
        self.assertEqual(item['fontSize'], 40)
        self.assertNotIn('_pakku_repeat_count', item)
        self.assertEqual(int(dict(sent[0]['headers'])[b'content-length']), len(sent[1]['body']))
        merge.assert_awaited_once()

    async def test_xml_preserves_position_font_and_escapes_text(self):
        rows = [{'p': '2,4,123,[bilibili]', 'm': '<hello>&\x01', 'fontSize': 38}]
        sent, _ = await self.request({'comments': rows}, query=b'format=xml', latest=5000)
        self.assertIn(b'application/xml', dict(sent[0]['headers'])[b'content-type'])
        node = ET.fromstring(sent[1]['body']).find('d')
        self.assertEqual(node.attrib['p'].split(',')[:4], ['2', '4', '38', '123'])
        self.assertEqual(node.text, '<hello>&')

    async def test_engine_exception_returns_exact_native_response(self):
        raw = b'{"count":1,"comments":[{"cid":7,"p":"1,1,123,[bili]","m":"hello"}]}'
        sent, _ = await self.request(raw, merge=AsyncMock(side_effect=RuntimeError('offline')))
        self.assertEqual(sent[1]['body'], raw)

    async def test_unrelated_response_streams_without_buffering(self):
        sent, merge = await self.request(b'abcdef', path='/health', chunks=[b'abc', b'def'])
        self.assertEqual([x['body'] for x in sent[1:]], [b'abc', b'def'])
        self.assertTrue(sent[1]['more_body'])
        merge.assert_not_awaited()

    async def test_oversized_response_streams_remaining_chunks(self):
        with patch.object(gateway, '_MAX_BYTES', 3):
            sent, merge = await self.request(b'abcdef', chunks=[b'ab', b'cd', b'ef'])
        self.assertEqual(b''.join(x['body'] for x in sent[1:]), b'abcdef')
        self.assertTrue(sent[1]['more_body'])
        self.assertFalse(sent[-1]['more_body'])
        merge.assert_not_awaited()

    async def test_empty_response_and_extcomment(self):
        sent, merge = await self.request({'count': 0, 'comments': []}, path='/api/v1/test/extcomment', latest=5000)
        self.assertEqual(json.loads(sent[1]['body']), {'count': 0, 'comments': []})
        merge.assert_awaited_once()


if __name__ == '__main__':
    unittest.main(verbosity=2)
