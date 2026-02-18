"""
MINDEX Multi-Source Image Fetcher
==================================
Searches multiple sources for species images:
- iNaturalist (primary - highest quality research-grade photos)
- Wikipedia (via Wikimedia Commons)
- GBIF (occurrence photos)
- Mushroom Observer
- Flickr (Creative Commons)
- Bing Images (web scraping fallback)
- Google Images (web scraping fallback)

Priority order ensures we get the best available image for each species.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import settings


@dataclass
class ImageResult:
    """Represents a found image from any source."""
    url: str
    source: str
    species_name: str
    width: int = 0
    height: int = 0
    quality_score: float = 0.0
    photographer: Optional[str] = None
    license: Optional[str] = None
    source_url: Optional[str] = None  # Link back to source page
    thumbnail_url: Optional[str] = None
    medium_url: Optional[str] = None
    original_url: Optional[str] = None
    attribution: Optional[str] = None
    
    @property
    def best_url(self) -> str:
        """Return the best available URL (prefer medium for web display)."""
        return self.medium_url or self.url
    
    @property
    def hq_url(self) -> str:
        """Return the highest quality URL (for HQ ingestion - always original)."""
        return self.original_url or self.medium_url or self.url


class MultiSourceImageFetcher:
    """
    Fetches images for species from multiple sources.
    
    Usage:
        async with MultiSourceImageFetcher() as fetcher:
            image = await fetcher.find_best_image("Amanita muscaria")
            if image:
                print(f"Found image from {image.source}: {image.url}")
    """
    
    # Source priority order (higher = better)
    SOURCE_PRIORITY = {
        "inat": 100,        # iNaturalist - highest quality
        "mushroom_observer": 90,
        "wikipedia": 85,
        "wikimedia": 85,
        "flickr": 70,
        "gbif": 60,
        "bing": 40,
        "google": 35,
        "instagram": 30,
        "x": 25,
    }
    
    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        self.client: Optional[httpx.AsyncClient] = None
        self._rate_limits: Dict[str, float] = {}  # source -> last request time
        
    async def __aenter__(self):
        self.client = httpx.AsyncClient(
            timeout=self.timeout,
            headers={
                "User-Agent": "MINDEX-ImageScraper/1.0 (Mycosoft Fungal Database; https://mycosoft.io)",
            },
            follow_redirects=True,
        )
        return self
    
    async def __aexit__(self, *args):
        if self.client:
            await self.client.aclose()
    
    async def _rate_limit(self, source: str, min_delay: float = 0.3):
        """Enforce rate limiting per source."""
        last_req = self._rate_limits.get(source, 0)
        elapsed = time.time() - last_req
        if elapsed < min_delay:
            await asyncio.sleep(min_delay - elapsed)
        self._rate_limits[source] = time.time()
    
    # =========================================================================
    # iNaturalist - Primary Source
    # =========================================================================
    
    async def fetch_inat_images(self, species_name: str, limit: int = 8) -> List[ImageResult]:
        """Fetch images from iNaturalist API."""
        results = []
        await self._rate_limit("inat", 0.3)
        
        try:
            headers = {"User-Agent": "MINDEX/1.0"}
            if settings.inat_api_token:
                headers["Authorization"] = f"Bearer {settings.inat_api_token}"
            
            # First, find the taxon
            resp = await self.client.get(
                f"{settings.inat_base_url}/taxa/autocomplete",
                params={"q": species_name, "per_page": 1, "is_active": "true"},
                headers=headers,
            )
            
            if resp.status_code != 200:
                return results
            
            data = resp.json()
            if not data.get("results"):
                return results
            
            taxon = data["results"][0]
            taxon_id = taxon["id"]
            
            # Get the default photo from the taxon itself
            default_photo = taxon.get("default_photo")
            if default_photo:
                url = default_photo.get("url", "")
                results.append(ImageResult(
                    url=url,
                    source="inat",
                    species_name=species_name,
                    thumbnail_url=url,
                    medium_url=url.replace("square", "medium") if url else None,
                    original_url=url.replace("square", "original") if url else None,
                    quality_score=95,  # Default photos are curated
                    photographer=default_photo.get("attribution"),
                    license=default_photo.get("license_code"),
                ))
            
            # Also get top research-grade observation photos
            await self._rate_limit("inat", 0.2)
            obs_resp = await self.client.get(
                f"{settings.inat_base_url}/observations",
                params={
                    "taxon_id": taxon_id,
                    "photos": "true",
                    "quality_grade": "research",
                    "order_by": "votes",
                    "per_page": limit,
                },
                headers=headers,
            )
            
            if obs_resp.status_code == 200:
                for obs in obs_resp.json().get("results", []):
                    for photo in obs.get("photos", [])[:3]:  # Max 3 per observation to reach target 8
                        url = photo.get("url", "")
                        if url and len(results) < limit:
                            quality = 80
                            if obs.get("faves_count", 0) > 10:
                                quality = 90
                            if obs.get("quality_grade") == "research":
                                quality += 5
                            
                            results.append(ImageResult(
                                url=url,
                                source="inat",
                                species_name=species_name,
                                thumbnail_url=url,
                                medium_url=url.replace("square", "medium"),
                                original_url=url.replace("square", "original"),
                                quality_score=quality,
                                photographer=obs.get("user", {}).get("login"),
                                license=photo.get("license_code"),
                            ))
                            
        except Exception as e:
            print(f"[iNat] Error fetching {species_name}: {e}")
        
        return results
    
    # =========================================================================
    # Wikipedia / Wikimedia Commons
    # =========================================================================
    
    async def fetch_wikipedia_images(self, species_name: str) -> List[ImageResult]:
        """Fetch images from Wikipedia/Wikimedia Commons."""
        results = []
        await self._rate_limit("wikipedia", 0.5)
        
        try:
            # Try Wikipedia API for page images
            resp = await self.client.get(
                f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(species_name)}",
                headers={"User-Agent": "MINDEX-ETL/1.0"},
            )
            
            if resp.status_code == 200:
                data = resp.json()
                
                # Get the thumbnail/original image
                if data.get("thumbnail"):
                    thumb = data["thumbnail"]
                    url = thumb.get("source", "")
                    
                    # Try to get higher resolution
                    original = data.get("originalimage", {}).get("source", url)
                    
                    results.append(ImageResult(
                        url=url,
                        source="wikipedia",
                        species_name=species_name,
                        thumbnail_url=url,
                        original_url=original,
                        medium_url=url,
                        width=thumb.get("width", 0),
                        height=thumb.get("height", 0),
                        quality_score=85,
                        source_url=data.get("content_urls", {}).get("desktop", {}).get("page"),
                    ))
            
            # Also try Wikimedia Commons directly
            await self._rate_limit("wikimedia", 0.5)
            commons_resp = await self.client.get(
                "https://commons.wikimedia.org/w/api.php",
                params={
                    "action": "query",
                    "format": "json",
                    "generator": "search",
                    "gsrsearch": f'"{species_name}" filetype:bitmap',
                    "gsrnamespace": 6,  # File namespace
                    "gsrlimit": 5,
                    "prop": "imageinfo",
                    "iiprop": "url|size|user",
                },
            )
            
            if commons_resp.status_code == 200:
                data = commons_resp.json()
                pages = data.get("query", {}).get("pages", {})
                
                for page in pages.values():
                    imageinfo = page.get("imageinfo", [{}])[0]
                    url = imageinfo.get("url", "")
                    
                    if url and not any(r.url == url for r in results):
                        results.append(ImageResult(
                            url=url,
                            source="wikimedia",
                            species_name=species_name,
                            original_url=url,
                            width=imageinfo.get("width", 0),
                            height=imageinfo.get("height", 0),
                            quality_score=80,
                            photographer=imageinfo.get("user"),
                        ))
                        
        except Exception as e:
            print(f"[Wikipedia] Error fetching {species_name}: {e}")
        
        return results
    
    # =========================================================================
    # GBIF (occurrence photos)
    # =========================================================================
    
    async def fetch_gbif_images(self, species_name: str, limit: int = 8) -> List[ImageResult]:
        """Fetch images from GBIF occurrence records."""
        results = []
        await self._rate_limit("gbif", 0.3)
        
        try:
            # Search for species
            resp = await self.client.get(
                f"{settings.gbif_base_url}/species/match",
                params={"name": species_name, "kingdom": "Fungi"},
            )
            
            if resp.status_code != 200:
                return results
            
            data = resp.json()
            species_key = data.get("speciesKey") or data.get("usageKey")
            
            if not species_key:
                return results
            
            # Get occurrences with media
            await self._rate_limit("gbif", 0.3)
            occ_resp = await self.client.get(
                f"{settings.gbif_base_url}/occurrence/search",
                params={
                    "taxonKey": species_key,
                    "mediaType": "StillImage",
                    "limit": limit * 2,  # Get more to filter
                },
            )
            
            if occ_resp.status_code == 200:
                for occ in occ_resp.json().get("results", []):
                    for media in occ.get("media", []):
                        if media.get("type") == "StillImage":
                            url = media.get("identifier", "")
                            if url and len(results) < limit:
                                results.append(ImageResult(
                                    url=url,
                                    source="gbif",
                                    species_name=species_name,
                                    original_url=url,
                                    quality_score=60,
                                    photographer=media.get("creator"),
                                    license=media.get("license"),
                                ))
                                
        except Exception as e:
            print(f"[GBIF] Error fetching {species_name}: {e}")
        
        return results
    
    # =========================================================================
    # Mushroom Observer
    # =========================================================================
    
    async def fetch_mushroom_observer_images(self, species_name: str, limit: int = 8) -> List[ImageResult]:
        """Fetch images from Mushroom Observer."""
        results = []
        await self._rate_limit("mushroom_observer", 0.5)
        
        try:
            # Mushroom Observer API
            resp = await self.client.get(
                "https://mushroomobserver.org/api2/observations",
                params={
                    "name": species_name,
                    "detail": "low",
                    "format": "json",
                },
            )
            
            if resp.status_code == 200:
                data = resp.json()
                observations = data.get("results", [])
                
                for obs in observations[:limit]:
                    # Get observation details for images
                    obs_id = obs.get("id")
                    if obs_id:
                        await self._rate_limit("mushroom_observer", 0.3)
                        detail_resp = await self.client.get(
                            f"https://mushroomobserver.org/api2/observations/{obs_id}",
                            params={"detail": "high", "format": "json"},
                        )
                        
                        if detail_resp.status_code == 200:
                            detail = detail_resp.json().get("results", [{}])[0]
                            images = detail.get("images", [])
                            
                            for img in images[:2]:
                                img_id = img.get("id")
                                if img_id:
                                    # Construct image URLs
                                    url = f"https://mushroomobserver.org/images/640/{img_id}.jpg"
                                    results.append(ImageResult(
                                        url=url,
                                        source="mushroom_observer",
                                        species_name=species_name,
                                        thumbnail_url=f"https://mushroomobserver.org/images/thumb/{img_id}.jpg",
                                        medium_url=url,
                                        original_url=f"https://mushroomobserver.org/images/orig/{img_id}.jpg",
                                        quality_score=85,
                                        photographer=img.get("owner", {}).get("login_name"),
                                        license=img.get("license"),
                                        source_url=f"https://mushroomobserver.org/observations/{obs_id}",
                                    ))
                                    
        except Exception as e:
            print(f"[MushroomObserver] Error fetching {species_name}: {e}")
        
        return results
    
    # =========================================================================
    # Flickr (Creative Commons)
    # =========================================================================
    
    async def fetch_flickr_images(self, species_name: str, limit: int = 8) -> List[ImageResult]:
        """Fetch images from Flickr (CC licensed)."""
        results = []
        await self._rate_limit("flickr", 0.5)
        
        try:
            # Flickr search (using public feed - no API key needed)
            search_term = f"{species_name} mushroom fungus"
            resp = await self.client.get(
                "https://api.flickr.com/services/feeds/photos_public.gne",
                params={
                    "tags": species_name.replace(" ", ","),
                    "format": "json",
                    "nojsoncallback": 1,
                },
            )
            
            if resp.status_code == 200:
                # Flickr returns JSONP, need to parse
                text = resp.text
                if text.startswith("jsonFlickrFeed("):
                    text = text[15:-1]  # Remove wrapper
                
                try:
                    data = json.loads(text)
                    for item in data.get("items", [])[:limit]:
                        # Extract image URL from media
                        media = item.get("media", {})
                        url = media.get("m", "")  # Medium size
                        
                        if url:
                            results.append(ImageResult(
                                url=url,
                                source="flickr",
                                species_name=species_name,
                                medium_url=url,
                                original_url=url.replace("_m.", "_b."),  # Large
                                thumbnail_url=url.replace("_m.", "_s."),  # Small
                                quality_score=70,
                                photographer=item.get("author"),
                                source_url=item.get("link"),
                            ))
                except json.JSONDecodeError:
                    pass
                    
        except Exception as e:
            print(f"[Flickr] Error fetching {species_name}: {e}")
        
        return results
    
    # =========================================================================
    # Bing Image Search (web scraping fallback)
    # =========================================================================
    
    async def fetch_bing_images(self, species_name: str, limit: int = 5) -> List[ImageResult]:
        """Scrape images from Bing Image Search (fallback)."""
        results = []
        await self._rate_limit("bing", 1.0)  # Be conservative
        
        try:
            query = f"{species_name} mushroom fungus"
            resp = await self.client.get(
                "https://www.bing.com/images/search",
                params={"q": query, "first": 1, "form": "HDRSC2"},
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "text/html,application/xhtml+xml",
                },
            )
            
            if resp.status_code == 200:
                # Extract image URLs from HTML (simplified)
                content = resp.text
                
                # Find m= parameters which contain image URLs
                import re
                pattern = r'"murl":"(https?://[^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"'
                matches = re.findall(pattern, content)
                
                for url in matches[:limit]:
                    url = url.replace("\\u0026", "&")
                    results.append(ImageResult(
                        url=url,
                        source="bing",
                        species_name=species_name,
                        original_url=url,
                        quality_score=40,
                    ))
                    
        except Exception as e:
            print(f"[Bing] Error fetching {species_name}: {e}")
        
        return results
    
    # =========================================================================
    # Main Interface
    # =========================================================================
    
    async def find_all_images(
        self,
        species_name: str,
        sources: Optional[List[str]] = None,
    ) -> List[ImageResult]:
        """
        Find images from all sources for a species.
        
        Args:
            species_name: Scientific name of the species
            sources: Optional list of sources to query (default: all)
        
        Returns:
            List of ImageResult sorted by quality/priority
        """
        all_sources = sources or ["inat", "wikipedia", "mushroom_observer", "gbif", "flickr", "bing"]
        
        # Create tasks for all sources
        tasks = []
        for source in all_sources:
            if source == "inat":
                tasks.append(self.fetch_inat_images(species_name))
            elif source == "wikipedia":
                tasks.append(self.fetch_wikipedia_images(species_name))
            elif source == "mushroom_observer":
                tasks.append(self.fetch_mushroom_observer_images(species_name))
            elif source == "gbif":
                tasks.append(self.fetch_gbif_images(species_name))
            elif source == "flickr":
                tasks.append(self.fetch_flickr_images(species_name))
            elif source == "bing":
                tasks.append(self.fetch_bing_images(species_name))
        
        # Run all in parallel
        results_lists = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Flatten and filter
        all_results = []
        for result_list in results_lists:
            if isinstance(result_list, list):
                all_results.extend(result_list)
        
        # Sort by source priority * quality score
        def sort_key(img: ImageResult) -> float:
            priority = self.SOURCE_PRIORITY.get(img.source, 10)
            return priority * (img.quality_score / 100)
        
        all_results.sort(key=sort_key, reverse=True)
        return all_results
    
    async def find_images_for_species(
        self,
        species_name: str,
        target_count: int = 8,
        sources: Optional[List[str]] = None,
    ) -> List[ImageResult]:
        """
        Find up to target_count images for a species from multiple sources.
        
        Deduplicates by URL and returns highest-quality images first.
        Target is 8 per species for Ancestry gallery display.
        """
        all_images = await self.find_all_images(species_name, sources)
        seen_urls: set = set()
        unique: List[ImageResult] = []
        for img in all_images:
            url = img.url or img.medium_url or img.original_url
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique.append(img)
                if len(unique) >= target_count:
                    break
        return unique[:target_count]
    
    async def find_best_image(
        self,
        species_name: str,
        sources: Optional[List[str]] = None,
    ) -> Optional[ImageResult]:
        """
        Find the single best image for a species.
        
        Searches sources in priority order and returns the first good result.
        """
        all_images = await self.find_all_images(species_name, sources)
        return all_images[0] if all_images else None


# Convenience function for synchronous code
def find_species_image_sync(species_name: str) -> Optional[Dict]:
    """
    Synchronous wrapper to find the best image for a species.
    
    Returns a dict with image info or None if not found.
    """
    async def _run():
        async with MultiSourceImageFetcher() as fetcher:
            result = await fetcher.find_best_image(species_name)
            if result:
                return {
                    "url": result.url,
                    "medium_url": result.medium_url or result.url,
                    "original_url": result.original_url or result.url,
                    "source": result.source,
                    "quality_score": result.quality_score,
                    "photographer": result.photographer,
                    "license": result.license,
                    "attribution": result.attribution,
                }
            return None
    
    return asyncio.run(_run())


if __name__ == "__main__":
    # Test the fetcher
    async def test():
        async with MultiSourceImageFetcher() as fetcher:
            species = ["Amanita muscaria", "Trametes versicolor", "Morchella esculenta"]
            
            for name in species:
                print(f"\n{'='*60}")
                print(f"Searching: {name}")
                print("="*60)
                
                images = await fetcher.find_all_images(name)
                print(f"Found {len(images)} images:")
                
                for i, img in enumerate(images[:5], 1):
                    print(f"  {i}. [{img.source}] Score: {img.quality_score:.0f}")
                    print(f"     URL: {img.url[:80]}...")
    
    asyncio.run(test())
