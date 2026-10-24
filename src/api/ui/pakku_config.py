"""Authenticated atomic settings and non-persistent preview. Local extension, AGPLv3."""
import json
import os
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from src.db.orm_models import Episode, AnimeSource, Anime
from src.core.pakku_recent import RecentUnavailable
from sqlalchemy.ext.asyncio import AsyncSession
from src import security
from src.db import models, get_db_session, ConfigManager
from src.db.crud.config import update_config_values_atomic
from src.api.dependencies import get_config_manager
from src.core.pakku_settings import PakkuSettings, PakkuPreview, read_pakku_settings

router = APIRouter()


async def run_engine(payload):
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(18, connect=2), trust_env=False) as client:
            response = await client.post(os.environ.get('MISAKA_PAKKU_URL', 'http://pakku:17769/merge'), json=payload)
        if response.status_code == 400:
            raise HTTPException(400, detail=response.json().get('error', '规则格式不正确'))
        response.raise_for_status()
        return response.json()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, detail='合并引擎暂时不可用，设置尚未保存，请稍后重试') from None


@router.get('/config/pakku/settings', response_model=PakkuSettings)
async def get_settings(
    current_user: models.User = Depends(security.get_current_user),
    manager: ConfigManager = Depends(get_config_manager),
):
    return await read_pakku_settings(manager)


@router.put('/config/pakku/settings', response_model=PakkuSettings)
async def put_settings(
    payload: PakkuSettings,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    manager: ConfigManager = Depends(get_config_manager),
):
    data = payload.model_dump()
    # Validate JS regular expressions with the actual engine BEFORE any DB write.
    await run_engine({**data, 'comments': []})
    values = {
        'danmakuPakkuLatestCount': str(payload.latestCount),
        'danmakuPakkuEnabled': str(payload.enabled).lower(),
        'danmakuPakkuMode': payload.mode,
        'danmakuPakkuWindowSeconds': str(payload.windowSeconds),
        'danmakuPakkuOptions': json.dumps(data['options'], ensure_ascii=False),
    }
    await update_config_values_atomic(session, values)
    for key in values:
        manager.invalidate(key)
    return payload


@router.post('/config/pakku/preview')
async def preview(
    payload: PakkuPreview,
    current_user: models.User = Depends(security.get_current_user),
):
    # Never save settings, comments, playback history or download tasks.
    return await run_engine(payload.model_dump())


class RecentPreviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    episodeId: int = Field(ge=1, lt=9223372036854775807)
    latestCount: int = Field(default=3000, ge=1, le=1000000)


@router.get('/config/pakku/recent/episodes')
async def recent_episodes(current_user: models.User = Depends(security.get_current_user),
                          session: AsyncSession = Depends(get_db_session)):
    rows = (await session.execute(select(Episode.id, Episode.title, Anime.title.label('animeTitle'))
            .join(AnimeSource, Episode.sourceId == AnimeSource.id).join(Anime, AnimeSource.animeId == Anime.id)
            .where(AnimeSource.providerName == 'bilibili')
            .order_by(Anime.id.desc(), Episode.episodeIndex.desc()).limit(1000))).all()
    return [{'value': r.id, 'label': f'{r.animeTitle} · {r.title}'} for r in rows]


@router.post('/config/pakku/recent/preview')
async def recent_preview(payload: RecentPreviewRequest, request: Request,
                         current_user: models.User = Depends(security.get_current_user),
                         session: AsyncSession = Depends(get_db_session),
                         manager: ConfigManager = Depends(get_config_manager)):
    try:
        rows, info = await request.app.state.pakku_recent_provider.comments(
            session, payload.episodeId, payload.latestCount, manager)
        if rows is None:
            raise HTTPException(404, detail='分集不存在')
        return info
    except RecentUnavailable as exc:
        raise HTTPException(503, detail=str(exc)) from None
