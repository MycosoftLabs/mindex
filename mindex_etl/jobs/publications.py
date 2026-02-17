"""
Mycological Publications ETL Job

Scrapes and indexes research publications related to fungi from:
- PubMed Central (PMC)
- GBIF Literature API
- MycoBank Publications
- Semantic Scholar
- Google Scholar (with rate limiting)

Publications are indexed in MINDEX for full-text search and species linking.
"""

import asyncio
import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mindex_api.db import get_db

logger = logging.getLogger(__name__)

# Configuration
PUBMED_API_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
GBIF_LITERATURE_API = "https://api.gbif.org/v1/literature"
SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1/paper/search"
USER_AGENT = "MINDEX-ETL/1.0 (https://mycosoft.io; research@mycosoft.io)"

# Mycological search terms
FUNGI_SEARCH_TERMS = [
    "fungi",
    "mushroom",
    "mycology",
    "basidiomycota",
    "ascomycota",
    "fungal",
    "mycorrhizal",
    "yeast",
    "mold",
    "lichen",
    "psilocybin",
    "agaricales",
    "polyporales",
]


class PublicationsETL:
    """ETL job for mycological publications."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.http_client = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": USER_AGENT},
        )
        self.stats = {
            "fetched": 0,
            "inserted": 0,
            "updated": 0,
            "errors": 0,
        }

    async def ensure_schema(self) -> None:
        """
        Ensure required tables exist.

        VM 189 currently lacks `core.publications` in some deployments, so the ETL
        must be able to create it before inserting.
        """
        await self.session.execute(text("CREATE SCHEMA IF NOT EXISTS core"))
        await self.session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS core.publications (
                    id VARCHAR(64) PRIMARY KEY,
                    source VARCHAR(50) NOT NULL,
                    external_id VARCHAR(255) NOT NULL,
                    title TEXT NOT NULL,
                    authors JSONB DEFAULT '[]'::jsonb,
                    year INTEGER,
                    abstract TEXT,
                    url TEXT,
                    doi VARCHAR(255),
                    metadata JSONB DEFAULT '{}'::jsonb,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    CONSTRAINT publications_source_external_id_unique UNIQUE (source, external_id)
                )
                """
            )
        )
        await self.session.execute(text("CREATE INDEX IF NOT EXISTS idx_publications_source ON core.publications(source)"))
        await self.session.execute(text("CREATE INDEX IF NOT EXISTS idx_publications_year ON core.publications(year)"))
        await self.session.execute(text("CREATE INDEX IF NOT EXISTS idx_publications_doi ON core.publications(doi)"))
        await self.session.commit()

    async def close(self):
        await self.http_client.aclose()

    def _generate_id(self, source: str, external_id: str) -> str:
        """Generate a deterministic UUID from source and external ID."""
        content = f"{source}:{external_id}"
        return hashlib.sha256(content.encode()).hexdigest()[:32]

    async def fetch_pubmed_publications(
        self, query: str, max_results: int = 100
    ) -> List[Dict[str, Any]]:
        """Fetch publications from PubMed Central."""
        publications = []
        
        try:
            # Search for IDs
            search_url = f"{PUBMED_API_BASE}/esearch.fcgi"
            search_params = {
                "db": "pmc",
                "term": f"{query}[Title/Abstract]",
                "retmax": max_results,
                "retmode": "json",
            }
            
            response = await self.http_client.get(search_url, params=search_params)
            response.raise_for_status()
            data = response.json()
            
            id_list = data.get("esearchresult", {}).get("idlist", [])
            if not id_list:
                return publications

            # Fetch details
            fetch_url = f"{PUBMED_API_BASE}/efetch.fcgi"
            fetch_params = {
                "db": "pmc",
                "id": ",".join(id_list),
                "retmode": "xml",
            }
            
            # Note: Full XML parsing would be needed here
            # For now, return basic info from search
            for pmcid in id_list:
                publications.append({
                    "source": "pubmed",
                    "external_id": f"PMC{pmcid}",
                    "title": f"Publication PMC{pmcid}",
                    "url": f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmcid}/",
                })
                
        except Exception as e:
            logger.error(f"PubMed fetch error: {e}")
            self.stats["errors"] += 1
            
        return publications

    async def fetch_gbif_literature(
        self, query: str, max_results: int = 100
    ) -> List[Dict[str, Any]]:
        """Fetch publications from GBIF Literature API."""
        publications = []
        
        try:
            params = {
                "q": query,
                "limit": max_results,
                "relevance": "MEDIUM",
            }
            
            response = await self.http_client.get(GBIF_LITERATURE_API, params=params)
            response.raise_for_status()
            data = response.json()
            
            for result in data.get("results", []):
                publications.append({
                    "source": "gbif",
                    "external_id": str(result.get("id", "")),
                    "title": result.get("title", "Untitled"),
                    "authors": result.get("authors", []),
                    "year": result.get("year"),
                    "abstract": result.get("abstract"),
                    "doi": result.get("identifiers", {}).get("doi"),
                    "url": result.get("websites", [{}])[0].get("url") if result.get("websites") else None,
                    "topics": result.get("topics", []),
                    "countries": result.get("countriesOfCoverage", []),
                })
                
        except Exception as e:
            logger.error(f"GBIF literature fetch error: {e}")
            self.stats["errors"] += 1
            
        return publications

    async def fetch_semantic_scholar(
        self, query: str, max_results: int = 100
    ) -> List[Dict[str, Any]]:
        """Fetch publications from Semantic Scholar."""
        publications = []
        
        try:
            params = {
                "query": query,
                "limit": min(max_results, 100),
                "fields": "title,authors,year,abstract,url,citationCount,venue",
            }
            
            response = await self.http_client.get(SEMANTIC_SCHOLAR_API, params=params)
            response.raise_for_status()
            data = response.json()
            
            for paper in data.get("data", []):
                publications.append({
                    "source": "semantic_scholar",
                    "external_id": paper.get("paperId", ""),
                    "title": paper.get("title", "Untitled"),
                    "authors": [a.get("name") for a in paper.get("authors", [])],
                    "year": paper.get("year"),
                    "abstract": paper.get("abstract"),
                    "url": paper.get("url"),
                    "venue": paper.get("venue"),
                    "citation_count": paper.get("citationCount", 0),
                })
                
        except Exception as e:
            logger.error(f"Semantic Scholar fetch error: {e}")
            self.stats["errors"] += 1
            
        return publications

    async def upsert_publication(self, pub: Dict[str, Any]) -> bool:
        """Insert or update a publication in the database."""
        try:
            pub_id = self._generate_id(pub["source"], pub["external_id"])
            
            await self.session.execute(
                text("""
                    INSERT INTO core.publications (
                        id, source, external_id, title, authors, year,
                        abstract, url, doi, metadata, created_at, updated_at
                    ) VALUES (
                        :id, :source, :external_id, :title, CAST(:authors AS jsonb), :year,
                        :abstract, :url, :doi, CAST(:metadata AS jsonb), NOW(), NOW()
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        title = EXCLUDED.title,
                        authors = EXCLUDED.authors,
                        abstract = EXCLUDED.abstract,
                        url = EXCLUDED.url,
                        metadata = EXCLUDED.metadata,
                        updated_at = NOW()
                """),
                {
                    "id": pub_id,
                    "source": pub["source"],
                    "external_id": pub["external_id"],
                    "title": pub.get("title", "Untitled"),
                    "authors": json.dumps(pub.get("authors", [])),
                    "year": pub.get("year"),
                    "abstract": pub.get("abstract"),
                    "url": pub.get("url"),
                    "doi": pub.get("doi"),
                    "metadata": json.dumps({
                        "venue": pub.get("venue"),
                        "citation_count": pub.get("citation_count"),
                        "topics": pub.get("topics"),
                        "countries": pub.get("countries"),
                    }),
                },
            )
            
            await self.session.commit()
            self.stats["inserted"] += 1
            return True
            
        except Exception as e:
            logger.error(f"Failed to upsert publication: {e}")
            self.stats["errors"] += 1
            await self.session.rollback()
            return False

    async def run(
        self,
        search_terms: Optional[List[str]] = None,
        max_per_term: int = 50,
        sources: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Run the publications ETL job."""
        await self.ensure_schema()
        terms = search_terms or FUNGI_SEARCH_TERMS
        enabled_sources = sources or ["gbif", "semantic_scholar"]
        
        logger.info(f"Starting publications ETL with {len(terms)} search terms")
        
        for term in terms:
            logger.info(f"Processing term: {term}")
            
            # Fetch from enabled sources
            if "gbif" in enabled_sources:
                pubs = await self.fetch_gbif_literature(term, max_per_term)
                self.stats["fetched"] += len(pubs)
                for pub in pubs:
                    await self.upsert_publication(pub)
                    
            if "semantic_scholar" in enabled_sources:
                pubs = await self.fetch_semantic_scholar(term, max_per_term)
                self.stats["fetched"] += len(pubs)
                for pub in pubs:
                    await self.upsert_publication(pub)
                    
            if "pubmed" in enabled_sources:
                pubs = await self.fetch_pubmed_publications(term, max_per_term)
                self.stats["fetched"] += len(pubs)
                for pub in pubs:
                    await self.upsert_publication(pub)
            
            # Rate limiting between terms
            await asyncio.sleep(1)
        
        await self.close()
        
        logger.info(f"Publications ETL complete: {self.stats}")
        return self.stats


async def run_publications_etl(
    max_per_term: int = 50,
    search_terms: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Entry point for publications ETL job."""
    async for session in get_db():
        etl = PublicationsETL(session)
        return await etl.run(
            search_terms=search_terms,
            max_per_term=max_per_term,
        )


if __name__ == "__main__":
    import sys
    
    logging.basicConfig(level=logging.INFO)
    
    max_results = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    result = asyncio.run(run_publications_etl(max_per_term=max_results))
    print(f"ETL Result: {result}")
