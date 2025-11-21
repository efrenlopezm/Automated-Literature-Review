from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from api.papers import PapersFetcher
from utils.pdf_utils import PDFHandler, PDFScanner, PdfProcessor
from config import Config
from logs.logging_config import logger
from typing import List, Dict
from pydantic import BaseModel

app = FastAPI(title="Literature Review API")

# CORS for local frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class PapersRequest(BaseModel):
    papers: List[Dict]

class ScanRequest(BaseModel):
    papers: List[Dict]
    query: str
    secondary_keywords: str


fetcher = PapersFetcher(
    semantic_api_key=Config.SEMANTIC_API_KEY,
    ieee_api_key=Config.IEEE_API_KEY
)

pdf_handler = PDFHandler(download_dir="./downloads")
pdf_processor = PdfProcessor(download_dir="./downloads")

ALLOWED_FIELDS = {
    "paper_id", "title", "authors", "venue", "year", "doi", "source",
    "abstract", "abstract_hit", "primary_keywords", "pdf_status",
    "pdf_url","pdf_path" ,"secondary_keywords_present", "secondary_keyword_counts",
    "paper_type", "last_updated"
}


@app.on_event("startup")
async def startup_event():
    logger.info("🚀 FastAPI app started")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("🛑 FastAPI app shutting down")

# --------------------------
# Fetch papers endpoint
# --------------------------
@app.get("/papers")
def get_papers(query: str, fetch_all: bool, max_results: int = 5, sources: str = Query("arxiv,semantic,ieee,acm,google")):
    logger.info(f"Fetching papers | query='{query}', max_results={max_results}, sources={sources}")
    results = []
    try:
        selected_sources = [s.strip().lower() for s in sources.split(",")]

        if "arxiv" in selected_sources:
            results.extend(fetcher.fetch_arxiv(query, max_results, fetch_all))
        if "semantic" in selected_sources:
            results.extend(fetcher.fetch_semantic_scholar(query, max_results, fetch_all))
        if "ieee" in selected_sources:
            results.extend(fetcher.fetch_ieee(query, max_results, fetch_all))
        if "acm" in selected_sources:
            results.extend(fetcher.fetch_acm_by_member(query, max_results, fetch_all))
        if "google" in selected_sources:
            results.extend(fetcher.fetch_google_scholar(query, max_results, fetch_all))

        logger.info(f"Returning {len(results)} papers after processing")
        return {"results": results}

    except Exception as e:
        logger.exception(f"Error fetching papers: {e}")
        return {"error": "Something went wrong"}

@app.post("/download_papers")
def download_papers(request: PapersRequest):
    papers = request.papers
    logger.info(f"Downloading PDFs for {len(papers)} papers")
    try:
        results = pdf_processor.process(papers)
        logger.info("PDFs downloaded successfully")
        return {
            "status": "success",
            "message": f"{len(results)} PDFs downloaded",
            "results": results 
        }
    except Exception as e:
        logger.exception(f"Error downloading PDFs: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/scan_papers")
def scan_papers(request: ScanRequest):
    papers = request.papers
    query = request.query
    secondary_keywords_raw = request.secondary_keywords

    if secondary_keywords_raw.strip():
        raw_keywords = [kw.strip() for kw in secondary_keywords_raw.split(",") if kw.strip()]

        clean_keywords = [
            kw.strip(" \"“”'") for kw in raw_keywords if kw.strip()
        ]
    else:
        clean_keywords = []
    if query.strip():
        query_raw_keywords = query.replace("AND", " ").replace("and", " ").split()

    secondary_keywords = clean_keywords


    logger.info(
        f"Scanning PDFs for {len(papers)} papers "
        f"with query='{query}' and secondary keywords={secondary_keywords}"
    )
    pdf_scanner = PDFScanner(secondary_keywords=secondary_keywords)

    try:
        for i, paper in enumerate(papers):
            if paper.get("pdf_status") == "downloaded" and paper.get("pdf_path"):
                scan_results = pdf_scanner.scan_pdf(paper["pdf_path"])
                papers[i].update(scan_results)
            paper["primary_keywords"] = query_raw_keywords
        logger.info("PDFs scanned successfully")
        return {"status": "success", "results": papers}

    except Exception as e:
        logger.exception(f"Error scanning PDFs: {e}")
        return {"status": "error", "message": str(e)}
