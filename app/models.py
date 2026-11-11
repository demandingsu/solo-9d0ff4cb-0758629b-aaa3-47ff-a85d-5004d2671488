"""Pydantic 请求/响应模型。"""
from typing import Optional

from pydantic import BaseModel, Field


class SyncRequest(BaseModel):
    list_url: str = Field(..., description="公共采购网站公告列表页 URL")


class AnnouncementItem(BaseModel):
    notice_no: str
    project_no: Optional[str] = None
    notice_type: Optional[str] = None
    publish_time: Optional[str] = None
    deadline: Optional[str] = None
    ref_notice_no: Optional[str] = None
    source_url: Optional[str] = None
    conflict_reasons: list[str] = []
    effective_deadline: Optional[str] = None


class Chain(BaseModel):
    chain_id: str = Field(..., description="链标识，即首次公告编号")
    project_no: Optional[str] = None
    status: str = Field(..., description="ok / conflict / cancelled")
    effective_deadline: Optional[str] = Field(
        None, description="有效截止时间；冲突或已撤销时为 null"
    )
    announcements: list[AnnouncementItem] = []


class OrphanAnnouncement(BaseModel):
    notice_no: str
    project_no: Optional[str] = None
    notice_type: Optional[str] = None
    status: str
    conflict_reasons: list[str] = []
    effective_deadline: Optional[str] = None
    source_url: Optional[str] = None
    publish_time: Optional[str] = None
    deadline: Optional[str] = None
    ref_notice_no: Optional[str] = None


class QueryResponse(BaseModel):
    chains: list[Chain] = []
    orphans: list[OrphanAnnouncement] = []


class SyncResponse(BaseModel):
    run_id: str
    status: str
    total_listed: int
    fetched_ok: int
    failed_count: int
    kept_last: int
    message: Optional[str] = None
