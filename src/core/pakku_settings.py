"""Validated output-only pakku settings. Local extension, 2026-10-20, AGPLv3."""
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class PakkuOptions(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    distanceEnabled: bool = True
    maxDistance: int = Field(default=5, ge=0, le=20)
    cosineEnabled: bool = True
    cosineThreshold: int = Field(default=45, ge=1, le=100)
    pinyin: bool = True
    trimEnding: bool = True
    trimSpace: bool = True
    trimWidth: bool = True
    crossMode: bool = False
    processScroll: bool = True
    processTop: bool = True
    processBottom: bool = True
    forceRules: list[list[str]] = Field(default_factory=lambda: [['^23{2,}$', '23333'], ['^6{3,}$', '66666']], max_length=50)
    forceContinue: bool = True
    forceApplySingles: bool = False
    whitelist: list[str] = Field(default_factory=list, max_length=50)
    markPosition: Literal['prefix', 'suffix', 'off'] = 'prefix'
    markStyle: Literal['brackets', 'pakku', 'subscript'] = 'brackets'
    markMinCount: int = Field(default=2, ge=2, le=10000)
    mergeMinCount: int = Field(default=2, ge=2, le=10000)
    representativePercent: int = Field(default=0, ge=0, le=100)
    textSelection: Literal['common', 'first', 'shortest', 'longest'] = 'common'
    modeElevation: bool = False
    includeSourceCounts: bool = True
    enlarge: bool = False
    shrinkThreshold: int = Field(default=0, ge=0, le=1000)
    dropThreshold: int = Field(default=0, ge=0, le=1000)

    @field_validator('forceRules')
    @classmethod
    def rules_valid(cls, rules):
        if any(len(row) != 2 or not row[0] or any(len(x) > 200 for x in row) for row in rules):
            raise ValueError('替换规则必须为[正则,替换文本]，每项最多200字符')
        return rules

    @field_validator('whitelist')
    @classmethod
    def whitelist_valid(cls, rules):
        if any(not x or len(x) > 200 for x in rules):
            raise ValueError('白名单规则不能为空，每条最多200字符')
        return rules


class PakkuSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    enabled: bool = False
    mode: Literal['exact', 'similar'] = 'exact'
    windowSeconds: int = Field(default=30, ge=1, le=120)
    latestCount: int = Field(default=0, ge=0, le=1000000)
    options: PakkuOptions = Field(default_factory=PakkuOptions)


class PreviewComment(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    p: str = Field(max_length=200)
    m: str = Field(max_length=2000)
    sendtime: int = Field(default=0, ge=0)
    id: str = Field(default='', max_length=100)


class PakkuPreview(PakkuSettings):
    comments: list[PreviewComment] = Field(max_length=500)


async def read_pakku_settings(manager):
    # Preserve the three fields used by the initial deployment and user changes.
    return PakkuSettings(
        enabled=(await manager.get('danmakuPakkuEnabled', 'false')).lower() == 'true',
        mode=await manager.get('danmakuPakkuMode', 'exact'),
        windowSeconds=int(await manager.get('danmakuPakkuWindowSeconds', '30')),
        latestCount=int(await manager.get('danmakuPakkuLatestCount', '0')),
        options=PakkuOptions.model_validate(json.loads(await manager.get('danmakuPakkuOptions', '{}'))),
    )
