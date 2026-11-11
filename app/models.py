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
    changes_recorded: int = 0
    message: Optional[str] = None


class ChainBaseline(BaseModel):
    """某条链最近一次完整采集的可确认状态（基准）。"""
    chain_id: str = Field(..., description="链标识，即首次公告编号")
    head_notice_no: Optional[str] = None
    chain_status: Optional[str] = Field(
        None, description="确认时的链状态 ok / cancelled；待核实且从未确认时为 null"
    )
    confirmed_deadline: Optional[str] = Field(
        None, description="确认的截止时间；撤销或待核实时为 null"
    )
    needs_review: bool = Field(..., description="冲突待核实：为 true 时保留旧基准")
    first_seen_at: str
    last_checked_at: str


class DeadlineChange(BaseModel):
    """一次截止时间变动记录。"""
    chain_id: str = Field(..., description="所属链（首次公告编号）")
    change_type: str = Field(
        ..., description="advanced 提前 / postponed 延后 / revoked 撤销 / restored 恢复"
    )
    old_deadline: Optional[str] = None
    new_deadline: Optional[str] = None
    notice_no: str = Field(..., description="触发变动的公告编号")
    source_url: Optional[str] = Field(None, description="公告来源（详情页）链接")
    run_id: str
    detected_at: str


class BaselineResponse(BaseModel):
    list_url: str
    baselines: list[ChainBaseline] = []


class DeadlineChangeResponse(BaseModel):
    list_url: Optional[str] = None
    changes: list[DeadlineChange] = []
