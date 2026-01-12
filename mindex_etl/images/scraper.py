"""
MINDEX Fungal Image Scraper - Comprehensive image collection system

Sources: iNaturalist, Wikipedia, Flickr, Mushroom Observer
Target: 10M+ images with best HQ colorful image for each species
"""

import asyncio
import hashlib
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass

import httpx

LOCAL_IMAGE_DIR = Path("C:/Users/admin2/Desktop/MYCOSOFT/DATA/mindex_images")


@dataclass
class ScrapedImage:
    url: str
    source: str
    species_name: str
    width: int = 0
    height: int = 0
    quality_score: float = 0.0
    popularity_score: float = 0.0
    color_score: float = 0.0
    photographer: Optional[str] = None
    license: Optional[str] = None
    local_path: Optional[str] = None
    content_hash: Optional[str] = None
    mindex_id: Optional[str] = None
    
    @property
    def total_score(self) -> float:
        return self.quality_score * 0.4 + self.popularity_score * 0.3 + self.color_score * 0.3


class FungalImageScraper:
    def __init__(self, output_dir: Path = LOCAL_IMAGE_DIR):
        self.output_dir = output_dir
        self.client = httpx.AsyncClient(timeout=60.0)
        self.downloaded_hashes = set()
        output_dir.mkdir(parents=True, exist_ok=True)
    
    async def close(self):
        await self.client.aclose()
    
    async def scrape_inaturalist(self, species_name: str, max_images: int = 50) -> List[ScrapedImage]:
        images = []
        try:
            headers = {"User-Agent": "MINDEX/1.0"}
            token = os.getenv("INAT_API_TOKEN", "")
            if token:
                headers["Authorization"] = f"Bearer {token}"
            
            resp = await self.client.get(
                "https://api.inaturalist.org/v1/taxa/autocomplete",
                params={"q": species_name, "per_page": 1},
                headers=headers,
            )
            if resp.status_code != 200:
                return images
            
            data = resp.json()
            if not data.get("results"):
                return images
            
            taxon_id = data["results"][0]["id"]
            
            obs_resp = await self.client.get(
                "https://api.inaturalist.org/v1/observations",
                params={
                    "taxon_id": taxon_id,
                    "photos": "true",
                    "quality_grade": "research",
                    "order_by": "votes",
                    "per_page": max_images,
                },
                headers=headers,
            )
            
            if obs_resp.status_code != 200:
                return images
            
            for obs in obs_resp.json().get("results", []):
                for photo in obs.get("photos", []):
                    url = photo.get("url", "").replace("square", "original")
                    if url:
                        images.append(ScrapedImage(
                            url=url,
                            source="inat",
                            species_name=species_name,
                            photographer=obs.get("user", {}).get("login"),
                            quality_score=80 if obs.get("quality_grade") == "research" else 50,
                            popularity_score=min(100, obs.get("faves_count", 0) * 5),
                        ))
            
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"iNat error: {e}")
        return images
    
    async def download_image(self, img: ScrapedImage) -> bool:
        try:
            resp = await self.client.get(img.url)
            if resp.status_code != 200:
                return False
            
            content_hash = hashlib.sha256(resp.content).hexdigest()
            if content_hash in self.downloaded_hashes:
                return False
            self.downloaded_hashes.add(content_hash)
            img.content_hash = content_hash
            
            species_safe = re.sub(r'[^\w\s-]', '', img.species_name).replace(' ', '_')[:50]
            mindex_id = f"MYCO-IMG-{content_hash[:8].upper()}"
            img.mindex_id = mindex_id
            ext = "jpg"
            
            first_letter = species_safe[0].upper() if species_safe else "X"
            save_dir = self.output_dir / first_letter / species_safe
            save_dir.mkdir(parents=True, exist_ok=True)
            
            filename = f"{img.source}_{species_safe}_{datetime.now().strftime('%Y%m%d')}_{mindex_id}.{ext}"
            filepath = save_dir / filename
            
            with open(filepath, 'wb') as f:
                f.write(resp.content)
            
            img.local_path = str(filepath)
            print(f"  Downloaded: {filename}")
            return True
        except Exception as e:
            print(f"Download error: {e}")
            return False
    
    async def scrape_species(self, species_name: str) -> Dict:
        print(f"Scraping: {species_name}")
        images = await self.scrape_inaturalist(species_name, 20)
        
        downloaded = []
        for img in sorted(images, key=lambda x: x.quality_score, reverse=True)[:10]:
            if await self.download_image(img):
                downloaded.append(img)
            await asyncio.sleep(0.2)
        
        best = max(downloaded, key=lambda x: x.total_score) if downloaded else None
        return {"species": species_name, "best": best, "count": len(downloaded)}


async def main():
    print("MINDEX Image Scraper")
    scraper = FungalImageScraper()
    try:
        for species in ["Amanita muscaria", "Morchella esculenta", "Cantharellus cibarius"]:
            await scraper.scrape_species(species)
    finally:
        await scraper.close()


if __name__ == "__main__":
    asyncio.run(main())
