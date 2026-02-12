"""
Aggressive Web Scraper - Bypass Rate Limits
=============================================
Direct web scraping when APIs are rate-limited.
Uses multiple techniques to maximize data extraction.

WARNING: Use responsibly. This bypasses API rate limits.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Dict, Generator, List, Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("mindex_aggressive_scraper")

# User agents to rotate
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
]


class AggressiveScraper:
    """
    Multi-technique web scraper for maximum data extraction.
    """
    
    def __init__(self, max_retries: int = 5, min_delay: float = 0.1, max_delay: float = 1.0):
        self.max_retries = max_retries
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.session_cookies = {}
        
    def get_headers(self) -> Dict[str, str]:
        """Get randomized headers to avoid detection."""
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "max-age=0",
        }
        
    def random_delay(self):
        """Random delay to appear more human."""
        time.sleep(random.uniform(self.min_delay, self.max_delay))
        
    def fetch_page(self, url: str, **kwargs) -> Optional[str]:
        """Fetch a page with retries and rotation."""
        for attempt in range(self.max_retries):
            try:
                with httpx.Client(follow_redirects=True, timeout=30.0) as client:
                    resp = client.get(
                        url,
                        headers=self.get_headers(),
                        cookies=self.session_cookies,
                        **kwargs
                    )
                    
                    # Update cookies
                    self.session_cookies.update(dict(resp.cookies))
                    
                    if resp.status_code == 429:
                        # Rate limited - exponential backoff
                        wait_time = (2 ** attempt) * 5
                        logger.warning(f"Rate limited on {url}, waiting {wait_time}s")
                        time.sleep(wait_time)
                        continue
                        
                    resp.raise_for_status()
                    return resp.text
                    
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} failed for {url}: {e}")
                self.random_delay()
                
        logger.error(f"All attempts failed for {url}")
        return None
        
    def parse_html(self, html: str) -> BeautifulSoup:
        """Parse HTML content."""
        return BeautifulSoup(html, "html.parser")


class WikipediaFungiScraper(AggressiveScraper):
    """
    Scrape Wikipedia for fungal species pages.
    Extracts taxonomy, descriptions, images, and citations.
    """
    
    BASE_URL = "https://en.wikipedia.org"
    CATEGORY_URL = "https://en.wikipedia.org/wiki/Category:Fungi"
    
    def get_category_pages(self, category_url: str) -> Generator[str, None, None]:
        """Get all pages in a Wikipedia category."""
        url = category_url
        
        while url:
            html = self.fetch_page(url)
            if not html:
                break
                
            soup = self.parse_html(html)
            
            # Find all article links in category
            content = soup.find("div", {"id": "mw-pages"})
            if content:
                for link in content.find_all("a"):
                    href = link.get("href", "")
                    if href.startswith("/wiki/") and ":" not in href:
                        yield urljoin(self.BASE_URL, href)
                        
            # Check for next page
            next_link = soup.find("a", string="next page")
            url = urljoin(self.BASE_URL, next_link["href"]) if next_link else None
            
            self.random_delay()
            
    def get_subcategories(self, category_url: str) -> List[str]:
        """Get subcategory URLs."""
        html = self.fetch_page(category_url)
        if not html:
            return []
            
        soup = self.parse_html(html)
        subcats = []
        
        subcat_div = soup.find("div", {"id": "mw-subcategories"})
        if subcat_div:
            for link in subcat_div.find_all("a"):
                href = link.get("href", "")
                if "Category:" in href:
                    subcats.append(urljoin(self.BASE_URL, href))
                    
        return subcats
        
    def extract_species_info(self, url: str) -> Optional[Dict[str, Any]]:
        """Extract species information from a Wikipedia page."""
        html = self.fetch_page(url)
        if not html:
            return None
            
        soup = self.parse_html(html)
        
        # Get title
        title_elem = soup.find("h1", {"id": "firstHeading"})
        title = title_elem.get_text().strip() if title_elem else None
        
        if not title:
            return None
            
        info = {
            "canonical_name": title,
            "source": "wikipedia",
            "source_url": url,
            "rank": "species",
        }
        
        # Get taxobox data
        taxobox = soup.find("table", {"class": "infobox biota"})
        if taxobox:
            for row in taxobox.find_all("tr"):
                th = row.find("th")
                td = row.find("td")
                if th and td:
                    key = th.get_text().strip().lower()
                    value = td.get_text().strip()
                    
                    if "kingdom" in key:
                        info["kingdom"] = value
                    elif "phylum" in key or "division" in key:
                        info["phylum"] = value
                    elif "class" in key:
                        info["class"] = value
                    elif "order" in key:
                        info["order"] = value
                    elif "family" in key:
                        info["family"] = value
                    elif "genus" in key:
                        info["genus"] = value
                        
        # Get first paragraph (description)
        content = soup.find("div", {"class": "mw-parser-output"})
        if content:
            first_p = content.find("p", recursive=False)
            if first_p:
                info["description"] = first_p.get_text().strip()[:2000]
                
        # Get main image
        infobox_img = soup.find("td", {"class": "infobox-image"})
        if infobox_img:
            img = infobox_img.find("img")
            if img and img.get("src"):
                src = img["src"]
                if src.startswith("//"):
                    src = "https:" + src
                info["image_url"] = src
                
        return info
        
    def scrape_all_fungi(self, max_species: Optional[int] = None) -> Generator[Dict, None, None]:
        """Scrape all fungal species from Wikipedia."""
        logger.info("Starting Wikipedia fungi scrape...")
        
        # Start with main fungi category and subcategories
        categories_to_process = [
            "https://en.wikipedia.org/wiki/Category:Fungi",
            "https://en.wikipedia.org/wiki/Category:Edible_fungi",
            "https://en.wikipedia.org/wiki/Category:Poisonous_fungi",
            "https://en.wikipedia.org/wiki/Category:Fungal_plant_pathogens_and_diseases",
            "https://en.wikipedia.org/wiki/Category:Medicinal_fungi",
            "https://en.wikipedia.org/wiki/Category:Mycorrhizal_fungi",
            "https://en.wikipedia.org/wiki/Category:Yeast",
            "https://en.wikipedia.org/wiki/Category:Molds",
            "https://en.wikipedia.org/wiki/Category:Lichens",
        ]
        
        seen_urls = set()
        count = 0
        
        for cat_url in categories_to_process:
            if max_species and count >= max_species:
                break
                
            logger.info(f"Processing category: {cat_url}")
            
            # Get subcategories
            subcats = self.get_subcategories(cat_url)
            for subcat in subcats[:20]:  # Limit subcategory depth
                if subcat not in categories_to_process:
                    categories_to_process.append(subcat)
                    
            # Process pages in category
            for page_url in self.get_category_pages(cat_url):
                if page_url in seen_urls:
                    continue
                    
                seen_urls.add(page_url)
                
                info = self.extract_species_info(page_url)
                if info:
                    count += 1
                    logger.info(f"[{count}] Scraped: {info.get('canonical_name')}")
                    yield info
                    
                if max_species and count >= max_species:
                    break
                    
        logger.info(f"Wikipedia scrape complete: {count} species")


class IndexFungorumScraper(AggressiveScraper):
    """
    Scrape Index Fungorum for taxonomic data.
    """
    
    BASE_URL = "http://www.indexfungorum.org"
    SEARCH_URL = "http://www.indexfungorum.org/names/names.asp"
    
    def search_species(self, genus: str) -> Generator[Dict, None, None]:
        """Search for species by genus."""
        html = self.fetch_page(
            self.SEARCH_URL,
            params={"SearchTerm": genus, "SearchType": "Species"}
        )
        
        if not html:
            return
            
        soup = self.parse_html(html)
        
        # Parse results table
        table = soup.find("table", {"class": "SearchResults"})
        if not table:
            return
            
        for row in table.find_all("tr")[1:]:  # Skip header
            cells = row.find_all("td")
            if len(cells) >= 3:
                yield {
                    "canonical_name": cells[0].get_text().strip(),
                    "authority": cells[1].get_text().strip(),
                    "source": "index_fungorum",
                    "rank": "species",
                }


class MycoPortalScraper(AggressiveScraper):
    """
    Scrape MycoPortal for North American fungal occurrence data.
    """
    
    BASE_URL = "https://mycoportal.org"
    SEARCH_URL = "https://mycoportal.org/portal/collections/list.php"
    
    def search_occurrences(
        self, 
        taxon: Optional[str] = None,
        state: Optional[str] = None,
        limit: int = 1000
    ) -> Generator[Dict, None, None]:
        """Search for occurrence records."""
        params = {
            "usethes": 1,
            "taxontype": 2,
            "submitaction": "Load Specimens",
        }
        
        if taxon:
            params["taxa"] = taxon
        if state:
            params["state"] = state
            
        # This would need session handling and form submission
        # Simplified example
        html = self.fetch_page(self.SEARCH_URL, params=params)
        
        if not html:
            return
            
        soup = self.parse_html(html)
        
        # Parse results
        results = soup.find_all("div", {"class": "specimen-card"})
        for result in results[:limit]:
            name = result.find("span", {"class": "sciname"})
            location = result.find("span", {"class": "locality"})
            date = result.find("span", {"class": "date"})
            
            if name:
                yield {
                    "taxon_name": name.get_text().strip(),
                    "location": location.get_text().strip() if location else None,
                    "observed_at": date.get_text().strip() if date else None,
                    "source": "mycoportal",
                }


class PubMedFungiScraper(AggressiveScraper):
    """
    Scrape PubMed for mycological research papers.
    Uses the public E-utilities API with aggressive pagination.
    """
    
    SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    FETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    
    def search_papers(
        self, 
        query: str = "fungi[MeSH Terms] OR mycology[MeSH Terms]",
        max_results: int = 10000
    ) -> Generator[Dict, None, None]:
        """Search and fetch fungal research papers."""
        # Search for paper IDs
        params = {
            "db": "pubmed",
            "term": query,
            "retmax": min(max_results, 10000),
            "retmode": "json",
        }
        
        html = self.fetch_page(self.SEARCH_URL, params=params)
        if not html:
            return
            
        try:
            import json
            data = json.loads(html)
            ids = data.get("esearchresult", {}).get("idlist", [])
        except Exception:
            return
            
        # Fetch paper details in batches
        batch_size = 100
        for i in range(0, len(ids), batch_size):
            batch_ids = ids[i:i + batch_size]
            
            fetch_params = {
                "db": "pubmed",
                "id": ",".join(batch_ids),
                "rettype": "abstract",
                "retmode": "xml",
            }
            
            xml = self.fetch_page(self.FETCH_URL, params=fetch_params)
            if not xml:
                continue
                
            # Parse XML (simplified)
            soup = self.parse_html(xml)
            
            for article in soup.find_all("PubmedArticle"):
                title = article.find("ArticleTitle")
                abstract = article.find("AbstractText")
                pmid = article.find("PMID")
                
                if title:
                    yield {
                        "title": title.get_text().strip(),
                        "abstract": abstract.get_text().strip() if abstract else None,
                        "pmid": pmid.get_text().strip() if pmid else None,
                        "source": "pubmed",
                    }
                    
            self.random_delay()


# Combined aggressive scraper
class CombinedAggressiveScraper:
    """
    Orchestrates all aggressive scrapers.
    """
    
    def __init__(self):
        self.wikipedia = WikipediaFungiScraper()
        self.index_fungorum = IndexFungorumScraper()
        self.mycoportal = MycoPortalScraper()
        self.pubmed = PubMedFungiScraper()
        
    def scrape_all(self, max_per_source: int = 10000) -> Dict[str, int]:
        """Run all scrapers and return counts."""
        results = {}
        
        # Wikipedia
        logger.info("=== WIKIPEDIA SCRAPE ===")
        wiki_count = 0
        for species in self.wikipedia.scrape_all_fungi(max_species=max_per_source):
            wiki_count += 1
            # TODO: Insert into database
        results["wikipedia"] = wiki_count
        
        # PubMed papers
        logger.info("=== PUBMED SCRAPE ===")
        pubmed_count = 0
        for paper in self.pubmed.search_papers(max_results=max_per_source):
            pubmed_count += 1
            # TODO: Insert into database
        results["pubmed"] = pubmed_count
        
        return results


def main():
    """Test the scrapers."""
    logging.basicConfig(level=logging.INFO)
    
    scraper = CombinedAggressiveScraper()
    results = scraper.scrape_all(max_per_source=100)
    
    print(f"\nResults: {results}")


if __name__ == "__main__":
    main()
