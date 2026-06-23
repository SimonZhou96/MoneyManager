from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from industry_topology.service import TopologyService

from .auth import get_db

router = APIRouter(prefix="/api/topology", tags=["topology"])


def get_topology_service(db=Depends(get_db)) -> TopologyService:
    return TopologyService(db)


class GraphRequest(BaseModel):
    code: str
    market: str
    depth: int = 3


class ExpandRequest(BaseModel):
    code: str
    market: str
    depth: int = 2
    existing_codes: List[str] = []


class RefreshRequest(BaseModel):
    code: str
    market: str


@router.get("/search")
def search(q: str = Query(..., min_length=1), limit: int = Query(10, le=50),
           svc: TopologyService = Depends(get_topology_service)):
    return {"ok": True, "data": svc.search(q, limit)}


@router.post("/graph")
def graph(req: GraphRequest, svc: TopologyService = Depends(get_topology_service)):
    return {"ok": True, "data": svc.build_graph(req.code, req.market, req.depth)}


@router.post("/expand")
def expand(req: ExpandRequest, svc: TopologyService = Depends(get_topology_service)):
    return {"ok": True, "data": svc.expand(req.code, req.market, req.depth, req.existing_codes)}


@router.post("/refresh")
def refresh(req: RefreshRequest, svc: TopologyService = Depends(get_topology_service)):
    return {"ok": True, "data": svc.refresh(req.code, req.market)}
