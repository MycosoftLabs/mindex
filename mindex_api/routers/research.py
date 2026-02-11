"""
Research Router - Feb 11, 2026

Search for research papers using OpenAlex API.
Provides access to open access scientific literature.

Endpoints:
- GET /api/mindex/research - Search research papers
- GET /api/mindex/research/{paper_id} - Get paper details
"""

import httpx
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/research", tags=["research"])

# OpenAlex API base URL (no auth required)
OPENALEX_BASE = "https://api.openalex.org"

# User agent for polite API usage
OPENALEX_HEADERS = {
    "User-Agent": "Mycosoft/1.0 (https://mycosoft.com; contact@mycosoft.com)"
}


class Author(BaseModel):
    """Research paper author"""
    name: str
    institution: Optional[str] = None
    orcid: Optional[str] = None


class ResearchPaper(BaseModel):
    """Research paper result"""
    id: str
    title: str
    authors: List[Author] = Field(default_factory=list)
    abstract: Optional[str] = None
    publication_date: Optional[str] = None
    journal: Optional[str] = None
    doi: Optional[str] = None
    url: Optional[str] = None
    open_access_url: Optional[str] = None
    citation_count: int = 0
    concepts: List[str] = Field(default_factory=list)
    source: str = "openalex"


class ResearchSearchResponse(BaseModel):
    """Response from research search"""
    papers: List[ResearchPaper]
    total: int
    query: str
    source: str = "openalex"


@router.get("", response_model=ResearchSearchResponse)
async def search_research(
    search: str = Query(..., min_length=2, description="Search query"),
    limit: int = Query(20, ge=1, le=100, description="Maximum results"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    year_from: Optional[int] = Query(None, description="Filter papers from this year"),
    year_to: Optional[int] = Query(None, description="Filter papers to this year"),
    open_access_only: bool = Query(False, description="Only return open access papers"),
) -> ResearchSearchResponse:
    """
    Search for research papers related to fungi and mycology.
    
    Uses OpenAlex API for access to millions of research papers.
    Results are filtered to prioritize fungal/mycological content.
    """
    try:
        # Build OpenAlex search query
        # Add fungi-related context to improve relevance
        enhanced_query = f"{search} fungi OR mushroom OR mycology OR fungal"
        
        params = {
            "search": enhanced_query,
            "per_page": limit,
            "page": (offset // limit) + 1 if limit > 0 else 1,
        }
        
        # Add filters
        filters = []
        if year_from:
            filters.append(f"from_publication_date:{year_from}-01-01")
        if year_to:
            filters.append(f"to_publication_date:{year_to}-12-31")
        if open_access_only:
            filters.append("is_oa:true")
        
        if filters:
            params["filter"] = ",".join(filters)
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{OPENALEX_BASE}/works",
                params=params,
                headers=OPENALEX_HEADERS,
            )
            response.raise_for_status()
            data = response.json()
        
        papers = []
        for work in data.get("results", []):
            paper = _parse_openalex_work(work)
            if paper:
                papers.append(paper)
        
        return ResearchSearchResponse(
            papers=papers,
            total=data.get("meta", {}).get("count", len(papers)),
            query=search,
            source="openalex",
        )
    
    except httpx.HTTPError as e:
        logger.error(f"OpenAlex API error: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to fetch research papers: {e}")
    except Exception as e:
        logger.error(f"Research search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{paper_id}", response_model=ResearchPaper)
async def get_paper_details(paper_id: str) -> ResearchPaper:
    """
    Get details for a specific research paper by ID.
    
    Paper ID should be an OpenAlex work ID (e.g., W2023123456).
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{OPENALEX_BASE}/works/{paper_id}",
                headers=OPENALEX_HEADERS,
            )
            response.raise_for_status()
            work = response.json()
        
        paper = _parse_openalex_work(work)
        if not paper:
            raise HTTPException(status_code=404, detail="Paper not found")
        
        return paper
    
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Paper not found")
        raise HTTPException(status_code=502, detail=f"Failed to fetch paper: {e}")
    except Exception as e:
        logger.error(f"Paper details error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _parse_openalex_work(work: Dict[str, Any]) -> Optional[ResearchPaper]:
    """Parse an OpenAlex work into our ResearchPaper model"""
    try:
        # Extract authors
        authors = []
        for authorship in work.get("authorships", [])[:10]:  # Limit to 10 authors
            author = authorship.get("author", {})
            institution = None
            if authorship.get("institutions"):
                institution = authorship["institutions"][0].get("display_name")
            
            authors.append(Author(
                name=author.get("display_name", "Unknown"),
                institution=institution,
                orcid=author.get("orcid"),
            ))
        
        # Extract concepts/keywords
        concepts = []
        for concept in work.get("concepts", [])[:5]:  # Limit to 5 concepts
            if concept.get("display_name"):
                concepts.append(concept["display_name"])
        
        # Get best URL
        url = work.get("doi") or work.get("id")
        open_access_url = None
        if work.get("open_access", {}).get("is_oa"):
            open_access_url = work.get("open_access", {}).get("oa_url")
        
        # Get journal name
        journal = None
        if work.get("primary_location", {}).get("source"):
            journal = work["primary_location"]["source"].get("display_name")
        
        return ResearchPaper(
            id=work.get("id", "").replace("https://openalex.org/", ""),
            title=work.get("title", "Untitled"),
            authors=authors,
            abstract=work.get("abstract"),
            publication_date=work.get("publication_date"),
            journal=journal,
            doi=work.get("doi"),
            url=url,
            open_access_url=open_access_url,
            citation_count=work.get("cited_by_count", 0),
            concepts=concepts,
            source="openalex",
        )
    except Exception as e:
        logger.warning(f"Failed to parse OpenAlex work: {e}")
        return None
