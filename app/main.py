from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
from bs4 import BeautifulSoup
import aiohttp
import asyncio
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from fastapi_cache.decorator import cache
from typing import List, Dict, Optional
import logging
from aiohttp import ClientTimeout
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize FastAPI Cache on startup."""
    logger.info("Initializing FastAPI Cache")
    FastAPICache.init(InMemoryBackend(), prefix="fastapi-cache")
    yield
    logger.info("Shutting down application")

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

async def fetch(session: aiohttp.ClientSession, url: str) -> str:
    """Fetch the HTML content of a URL asynchronously with proper error handling."""
    try:
        timeout = ClientTimeout(total=30)
        async with session.get(url, timeout=timeout) as response:
            if response.status != 200:
                logger.error(f"Failed to fetch {url}: Status {response.status}")
                raise HTTPException(status_code=response.status, detail=f"Failed to fetch data from {url}")
            return await response.text()
        
    except asyncio.TimeoutError:
        logger.error(f"Timeout while fetching {url}")
        raise HTTPException(status_code=504, detail="Request timeout")
    except Exception as e:
        logger.error(f"Error fetching {url}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch data: {str(e)}")

async def scrape_linkedin(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """Scrape internships from LinkedIn"""
    internships = []
    try:
        job_cards = soup.find_all('div', class_='job-search-card')
        for card in job_cards:
            try:
                title = card.find('h3', class_='base-search-card__title')
                company = card.find('h4', class_='base-search-card__subtitle')
                location = card.find('span', class_='job-search-card__location')
                link = card.find('a', class_='base-card__full-link')
                
                if title and company and location:
                    internships.append({
                        "title": title.text.strip(),
                        "company": company.text.strip(),
                        "location": location.text.strip(),
                        "link": link.get('href') if link else None,
                        "source": "LinkedIn"
                    })
            except Exception as e:
                logger.error(f"Error parsing LinkedIn job card: {str(e)}")
                continue
    except Exception as e:
        logger.error(f"Error scraping LinkedIn: {str(e)}")
    return internships

async def scrape_jobberman(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """Scrape internships from Jobberman Nigeria."""
    internships = []
    try:
        job_cards = soup.find_all('div', class_='search-result')  # Adjust class based on actual Jobberman HTML
        for card in job_cards:
            try:
                title = card.find('h2', class_='job-title')  # Adjust classes based on actual Jobberman HTML
                company = card.find('div', class_='company-name')
                location = card.find('div', class_='job-location')
                link = card.find('a')
                date_posted = card.find('div', class_='job-date')
                
                if title and company:
                    internships.append({
                        "title": title.text.strip(),
                        "company": company.text.strip(),
                        "location": location.text.strip() if location else "Nigeria",
                        "link": f"https://www.jobberman.com{link.get('href')}" if link else None,
                        "date_posted": date_posted.text.strip() if date_posted else None,
                        "source": "Jobberman"
                    })
            except Exception as e:
                logger.error(f"Error parsing Jobberman job card: {str(e)}")
                continue
    except Exception as e:
        logger.error(f"Error scraping Jobberman: {str(e)}")
    return internships


async def scrape_url(session: aiohttp.ClientSession, url: str) -> List[Dict[str, str]]:
    """Scrape internships from a single URL with improved error handling."""
    try:
        html = await fetch(session, url)
        soup = BeautifulSoup(html, "html.parser")

        if "linkedin.com" in url.lower():
            return await scrape_linkedin(soup)
        elif "bing.com" in url.lower():
            return await scrape_jobberman(soup)
        else:
            logger.warning(f"Unsupported URL: {url}")
            return []
    except Exception as e:
        logger.error(f"Error scraping {url}: {str(e)}")
        return []

@app.get("/", response_class=JSONResponse)
@cache(expire=300)
async def read_internships(
    category: Optional[str] = Query(None, description="Category of internships to scrape")
) -> Dict[str, List[Dict[str, str]]]:
    """Fetch internships from multiple sources with improved error handling and logging."""
    logger.info(f"Processing request for category: {category}")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1"
    }
    
    base_urls = {
       "linkedin": "https://www.linkedin.com/jobs/search?keywords=internship&location=Nigeria",
        "jobberman": "https://www.jobberman.com/jobs?experience=graduate-trainee",
        "bing": "https://www.bing.com/jobs?q=internship"
    }

    # Add category to URLs if specified
    urls = []
    if category:
        category = category.lower().strip()
        for key, base_url in base_urls.items():
            if key == "linkedin":
                urls.append(f"{base_url}+{category}")
            elif key == "jobberman":
                urls.append(f"{base_url}?q={category}")
    else:
        urls = list(base_urls.values())

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            tasks = [scrape_url(session, url) for url in urls]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            all_internships = []
            for result in results:
                if isinstance(result, list):
                    all_internships.extend(result)
                else:
                    logger.error(f"Error in gathering results: {str(result)}")
            
            logger.info(f"Retrieved {len(all_internships)} internships")
            return {"internships": all_internships}
    except Exception as e:
        logger.error(f"Error processing request: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

