"""Compatible JSON plus opt-in XML output with per-comment font sizes. AGPLv3."""
import math
import re
import xml.etree.ElementTree as ET
from fastapi import Response
from fastapi.responses import JSONResponse
from src.db import models

_XML_INVALID = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]')


def _number(value, fallback):
    try:
        result = float(value)
        return result if math.isfinite(result) else fallback
    except (TypeError, ValueError):
        return fallback


def comment_output(raw_comments, processed, output_format='json'):
    if output_format == 'xml':
        root = ET.Element('i')
        ET.SubElement(root, 'chatserver').text = 'misaka'
        ET.SubElement(root, 'maxlimit').text = str(len(raw_comments))
        for index, item in enumerate(raw_comments):
            parts = item.get('p', '').split(',')
            source_at = next((i for i, x in enumerate(parts) if '[' in x and ']' in x), len(parts))
            time = _number(parts[0] if parts else '', 0)
            mode = int(_number(parts[1] if len(parts) > 1 else '', 1))
            color_at = 3 if source_at == 4 else 2
            color = int(_number(parts[color_at] if len(parts) > color_at else '', 16777215))
            base_size = _number(parts[2], 25) if source_at == 4 else 25
            size = max(8, min(96, _number(item.get('fontSize'), base_size)))
            node = ET.SubElement(root, 'd', p=f'{time:g},{mode},{size:g},{max(0,min(16777215,color))},0,0,0,{index}')
            node.text = _XML_INVALID.sub('', item.get('m', ''))
        return Response(ET.tostring(root, encoding='utf-8', xml_declaration=True), media_type='application/xml')
    if any('fontSize' in item for item in raw_comments):
        # Keep p in the original dandanplay order. fontSize is an explicit extension,
        # not an extra comma-separated slot that could be mistaken for the color.
        values = []
        for item, comment in zip(raw_comments, processed):
            value = comment.model_dump()
            if 'fontSize' in item:
                value['fontSize'] = max(8, min(96, _number(item['fontSize'], 25)))
            values.append(value)
        return JSONResponse({'count': len(values), 'comments': values})
    return models.CommentResponse(count=len(processed), comments=processed)
