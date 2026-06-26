#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""产业拓扑数据模型。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class Direction(str, Enum):
    UPSTREAM = "upstream"      # peer 是 source 的上游
    DOWNSTREAM = "downstream"  # peer 是 source 的下游
    PEER = "peer"              # 同业/竞品/替代品


class RelationType(str, Enum):
    # 上游类
    SUPPLIER = "supplier"
    RAW_MATERIAL = "raw_material"
    EQUIPMENT = "equipment"
    FOUNDRY_PACKAGING = "foundry_packaging"
    COMPONENT = "component"      # 零部件，上游类，提供具体零部件/模组
    # 下游类
    CUSTOMER = "customer"
    ODM = "odm"
    DISTRIBUTOR = "distributor"
    APPLICATION = "application"
    SERVICE = "service"          # 服务，上游类，提供研发/运维/外包服务
    # 同业类
    COMPETITOR = "competitor"
    SUBSTITUTE = "substitute"
    # 兜底
    OTHER = "other"


@dataclass(frozen=True)
class TopologyNode:
    code: str
    name: str
    market: str
    sector: str
    pct_chg: Optional[float]
    market_cap_str: str
    size_level: int  # 1-6
    expanded: bool
    stale: bool
    is_center: bool


@dataclass(frozen=True)
class TopologyEdge:
    source: str
    target: str
    direction: Direction
    relation: RelationType
    label: str       # "关系·依据"
    evidence: str


@dataclass(frozen=True)
class CachedRelation:
    source_code: str
    source_market: str
    peer_code: str
    peer_market: str
    peer_name: str
    relation: RelationType
    direction: Direction
    evidence: str
    expires_at: Optional[datetime]
    is_empty: bool
    peer_market_cap: Optional[float] = None
    peer_market_cap_str: str = ""
