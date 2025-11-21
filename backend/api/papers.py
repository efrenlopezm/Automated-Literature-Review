import xml.etree.ElementTree as ET
import subprocess, bibtexparser
from bs4 import BeautifulSoup
from datetime import datetime
import json
import sys
import csv
import os
import time
import re
import requests

class PapersFetcher:
    def __init__(self, semantic_api_key=None, ieee_api_key=None):
        self.semantic_api_key = semantic_api_key
        self.ieee_api_key = ieee_api_key


    # -----------------------------
    # Normalized paper abstract
    # -----------------------------
    def clean_abstract(self,raw_abstract: str) -> str:
        """
        Clean abstracts that may contain JATS XML tags.
        Returns plain text without <jats:...> markup.
        """
        if not raw_abstract or not isinstance(raw_abstract, str):
            return ""

        text = BeautifulSoup(raw_abstract, "lxml").get_text(" ", strip=True)
        text = re.sub(r"<.*?>", "", text)
        return text.strip()


    # -----------------------------
    # Normalized paper template
    # -----------------------------
    def normalize_paper(self,paper_id, title, authors, venue, year, doi, pdf_url, pdf_status, source, abstract="", abstract_hit=False, paper_type="Other", last_updated=""):
        """
        Normalize paper metadata into a consistent dictionary format.
        Ensures uniform fields (ID, title, authors, venue, year, DOI, abstract, PDF info, etc.)
        across different data sources.
        """

        if isinstance(authors, list):
            authors_str = ", ".join(authors)
        else:
            authors_str = authors or ""
        abstract_clean = self.clean_abstract(abstract)
        return {
            "paper_id": paper_id,
            "title": title,
            "authors": authors_str,
            "venue": venue,
            "year": year,
            "doi": doi,
            "source": source,
            "abstract": abstract_clean,
            "abstract_hit": abstract_hit,
            "primary_keywords": [],
            "pdf_status": pdf_status,
            "pdf_url": pdf_url,
            "pdf_path": '',
            "secondary_keywords_present": {},
            "secondary_keyword_counts": {},
            "paper_type": paper_type,
            "last_updated": last_updated
        }

    # -----------------------------
    # arXiv
    # -----------------------------
    def fetch_arxiv(self, query, max_results=0, fetch_all=False):
        """
        Fetch papers from arXiv using its API.
        Parses XML feed, extracts metadata (title, authors, summary, dates, DOI, PDF link),
        and normalizes results into a standard paper format.
        """

        base_url = "http://export.arxiv.org/api/query"
        params = {"search_query": query, "start": 0, "max_results": max_results if max_results > 0 else 1000}
        response = requests.get(base_url, params=params)
        if response.status_code != 200:
            return []

        root = ET.fromstring(response.text)
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "arxiv": "http://arxiv.org/schemas/atom"
        }
        papers = []

        for entry in root.findall("atom:entry", ns):
            paper_id = entry.find("atom:id", ns).text if entry.find("atom:id", ns) is not None else None
            title = entry.find("atom:title", ns).text.strip() if entry.find("atom:title", ns) is not None else None
            summary = entry.find("atom:summary", ns).text.strip() if entry.find("atom:summary", ns) is not None else None
            published = entry.find("atom:published", ns).text if entry.find("atom:published", ns) is not None else None
            last_updated = entry.find("atom:updated", ns)
            if last_updated is not None:
                raw_time = last_updated.text
                updated = datetime.strptime(raw_time, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%d")
            else:
                updated = None
            authors = ", ".join(
                [a.find("atom:name", ns).text for a in entry.findall("atom:author", ns)]
            )

            doi_elem = entry.find("arxiv:doi", ns)
            if doi_elem is not None:
                doi = doi_elem.text
            elif paper_id:
                base_arxiv_id = paper_id.split("/")[-1].split("v")[0]
                doi = f"10.48550/arXiv.{base_arxiv_id}"
            else:
                doi = None

            pdf_url = None
            for link in entry.findall("atom:link", ns):
                if link.attrib.get("type") == "application/pdf":
                    pdf_url = link.attrib.get("href")
                    break
            pdf_status = "downloaded" if pdf_url else "unavailable"

            primary_cat = entry.find("arxiv:primary_category", ns)
            paper_type = primary_cat.attrib.get("term") if primary_cat is not None else "Unknown"
 
            papers.append(self.normalize_paper(
                paper_id=paper_id,
                title=title,
                authors=authors,
                venue="arXiv",
                year=published[:4] if published else None,
                doi=doi,
                pdf_url=pdf_url,
                pdf_status=pdf_status,
                source="arXiv",
                abstract=summary,
                abstract_hit=query.lower() in summary.lower() if summary else False,
                paper_type=paper_type,
                last_updated=updated
            ))

        return papers

    # -----------------------------
    # Semantic Scholar
    # -----------------------------
    def fetch_semantic_scholar(self, query, max_results=0, fetch_all=False):
        """
        Fetch papers from Semantic Scholar API.
        Retrieves metadata (title, authors, venue, year, abstract, PDF info) 
        and normalizes results into a standard format.
        """
        
        url = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
        headers = {"x-api-key": self.semantic_api_key}
        fields = "title,url,authors,abstract,year,venue,openAccessPdf,publicationTypes"

        papers, token = [], None
        while True:
            params = {"query": f'"{query}"', "fields": fields}
            if token:
                params["token"] = token

            response = requests.get(url, params=params, headers=headers)
            if response.status_code != 200:
                print(f"Semantic Scholar fetch error: {response.status_code} {response.text}")
                break

            data = response.json()
            batch = data.get("data", [])
            for paper in batch: 
                open_access = paper.get("openAccessPdf", {})

                pdf_url = open_access.get("url") if open_access and open_access.get("url") else None
                doi = None
                if pdf_url and pdf_url.startswith("https://doi.org/"):
                    doi = pdf_url.replace("https://doi.org/", "")
                pdf_status = "downloaded" if pdf_url else "unavailable"

                publication_types = paper.get("publicationTypes", [])
                paper_type = ", ".join(publication_types) if publication_types else None
                papers.append(self.normalize_paper(
                    paper_id=paper.get("paperId"),
                    title=paper.get("title"),
                    authors=[a["name"] for a in paper.get("authors", [])],
                    venue=paper.get("venue"),
                    year=paper.get("year"),
                    doi=doi,
                    pdf_url = pdf_url,
                    pdf_status = pdf_status,
                    source="Semantic Scholar",
                    abstract=paper.get("abstract"),
                    abstract_hit=query.lower() in (paper.get("abstract") or "").lower(),
                    paper_type=paper_type,
                    last_updated=paper.get("year")
                ))

            token = data.get("token")
            if not token:
                break
        if not fetch_all and max_results > 0:
            papers = papers[:max_results]
         
        return papers
        
    def fetch_ieee_by_member(self, query, max_results=0, fetch_all=False):
        """
        Fallback: Fetch IEEE papers via CrossRef member:263
        """
        
        if fetch_all:
            max_results = 1000
        url = "https://api.crossref.org/works"
        params = {"query": query, "rows": max_results, "filter": "member:263"}

        papers = []
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            items = response.json()["message"]["items"]
            for item in items:
                doi = item.get("DOI")
                title = item.get("title", [""])[0]
                authors = ", ".join([
                    f"{a.get('given', '')} {a.get('family', '')}".strip()
                    for a in item.get("author", [])
                ])
                year = item.get("issued", {}).get("date-parts", [[None]])[0][0]
                
                pdf_url = None
                pdf_status = "unavailable"

                if "link" in item:
                    for link in item["link"]:
                        url = link.get("URL", "")
                        if url and url.endswith(".pdf") and "xplorestaging" not in url:
                            pdf_url = url
                            pdf_status = "downloaded"
                            break

                if not pdf_url:
                    if "resource" in item and "primary" in item["resource"]:
                        pdf_url = item["resource"]["primary"].get("URL")
                    elif item.get("URL"):
                        pdf_url = item.get("URL")


                papers.append(self.normalize_paper(
                    paper_id=doi,
                    title=title,
                    authors=authors,
                    venue=item.get("container-title", ["IEEE"])[0],
                    year=year,
                    doi=doi,
                    pdf_url=pdf_url,
                    pdf_status=pdf_status,
                    source="IEEE (via CrossRef)",
                    abstract=item.get("abstract", ""),
                    abstract_hit=query.lower() in title.lower(),
                    paper_type=item.get("type",""),
                    last_updated=year
                ))

        except Exception as e:
            print(f"CrossRef IEEE fetch error: {e}")

        return papers

    # -----------------------------
    # IEEE
    # -----------------------------
    def fetch_ieee(self, query, max_results=5, fetch_all=False):
        """
        Fetch papers from IEEE Xplore API.
        Extracts metadata (title, authors, venue, year, DOI, abstract, PDF info) 
        and normalizes results into a standard format.
        """

        url = "https://ieeexploreapi.ieee.org/api/v1/search/articles"
        params = {
            "apikey": 'nw5ez8vktv2dtxrxud6xy6av',
            "querytext": query,
            "max_records": max_results,
            "format": "json"
        }

        papers = []
        try:
            response = requests.get(url, params=params)

            if response.status_code == 403:
                print("IEEE API key not active. Falling back to CrossRef (member:263).")
                return self.fetch_ieee_by_member(query, max_results, fetch_all)

            response.raise_for_status()
            data = response.json()
            for article in data.get("articles", []):

                authors = [a.get("full_name") for a in article.get("authors", [])] if article.get("authors") else []
                pdf_status = "downloaded" if article.get("pdf_url") else "unavailable"

                papers.append(self.normalize_paper(
                    paper_id=article.get("article_number") or article.get("doi"),
                    title=article.get("title"),
                    authors=authors,
                    venue=article.get("publication_title"),
                    year=article.get("publication_year"),
                    doi=article.get("doi"),
                    pdf_url=article.get("pdf_url"),
                    pdf_status=pdf_status,
                    source="IEEE Xplore",
                    abstract=article.get("abstract"),
                    abstract_hit=query.lower() in (article.get("abstract") or "").lower(),
                    last_updated=article.get("publication_year")
                ))
        except Exception as e:
            print(f"IEEE fetch error: {e}")
        return papers

    # -----------------------------
    # ACM via CrossRef member ID
    # -----------------------------
    def enrich_acm_with_doi(self, doi, query):
        """
        Enrich ACM papers via CrossRef using DOI.
        Retrieves metadata (title, authors, venue, year, abstract, PDF link) 
        and normalizes it into a standard format.
        """

        url = f"https://api.crossref.org/works/{doi}"
        try:
            resp = requests.get(url)
            resp.raise_for_status()
            item = resp.json()["message"]

            authors = []
            if "author" in item:
                for a in item["author"]:
                    full_name = " ".join(filter(None, [a.get("given"), a.get("family")]))
                    authors.append(full_name)
            
            pdf_url = ""
            if "link" in item:
                for link in item["link"]:
                    if link.get("content-type") == "application/pdf":
                        pdf_url = link.get("URL")
                        break

            last_updated = None
            if "issued" in item and "date-parts" in item["issued"]:
                year = item["issued"]["date-parts"][0]
                last_updated = "-".join(str(x) for x in year)

            return self.normalize_paper(
                paper_id=item.get("DOI"),
                title=item.get("title", [None])[0],
                authors=", ".join(authors),
                venue=item.get("container-title", [None])[0],
                year=item.get("issued", {}).get("date-parts", [[None]])[0][0],
                doi=item.get("DOI"),
                pdf_url = pdf_url,
                pdf_status = '',
                source="Google Scholar",
                abstract=item.get("abstract", ""),
                abstract_hit=query.lower() in (item.get("title", [""])[0].lower()),
                paper_type=item.get("type",""),
                last_updated=last_updated
            )
        except Exception as e:
            print(f"CrossRef enrichment failed for DOI {doi}: {e}")
            return None

    def fetch_acm_by_member(self, query, max_results=20, fetch_all=False):
        """
        Fetch papers from ACM Digital Library via CrossRef member ID.
        Extracts metadata (title, authors, venue, year, DOI, PDF link)
        and normalizes results into a standard format.
        Supports simple 'AND' logic and can fall back to direct ACM API.
        """
        if fetch_all:
            max_results = 1000

        # --- ✅ Handle simple AND logic ---
        # Example: query = "honeypot AND security"
        terms = [t.strip() for t in query.split("AND") if t.strip()]
        main_term = terms[0] if terms else query
        extra_terms = [t.lower() for t in terms[1:]]

        url = "https://api.crossref.org/works"
        params = {"query": main_term, "rows": max_results, "filter": "member:320"}

        papers = []
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            items = response.json()["message"]["items"]

            for item in items:
                title = item.get("title", [""])[0]
                authors = ", ".join([
                    f"{a.get('given', '')} {a.get('family', '')}".strip()
                    for a in item.get("author", [])
                ])

                last_updated = None
                year = [None]
                if "issued" in item and "date-parts" in item["issued"]:
                    year = item["issued"]["date-parts"][0]
                    last_updated = "-".join(str(x) for x in year)

                doi = item.get("DOI")

                pdf_url = None
                if "link" in item:
                    for link in item["link"]:
                        if "pdf" in link.get("URL", ""):
                            pdf_url = link["URL"]
                            break

                pdf_status = "downloaded" if pdf_url else "unavailable"
                combined_text = f"{title.lower()} {item.get('abstract', '').lower()}"
                if all(term in combined_text for term in extra_terms):
                    papers.append(self.normalize_paper(
                        paper_id=doi,
                        title=title,
                        authors=authors,
                        venue=item.get("container-title", ["ACM Digital Library"])[0],
                        year=year[0],
                        doi=doi,
                        pdf_url=pdf_url,
                        pdf_status=pdf_status,
                        source="ACM Digital Library",
                        abstract=item.get("abstract", ""),
                        abstract_hit=query.lower() in title.lower(),
                        paper_type=item.get("type", ""),
                        last_updated=last_updated
                    ))
        except Exception as e:
            print(f"ACM member search fetch error: {e}")

        return papers

    # -----------------------------
    # Google Scholar
    # -----------------------------
    def fetch_google_scholar(self, query: str, max_results: int = 10, fetch_all=False):
        """
        Fetch papers from Google Scholar using PyPaperBot subprocess.
        Correctly handles paging (>10 results).
        """

        papers = []
        seen_ids = set()

        try:
            dwn_dir = os.path.abspath("./downloads")
            os.makedirs(dwn_dir, exist_ok=True)

            if fetch_all:
                effective_max = 1000
            else:
                effective_max = max_results

            # number of pages needed
            pages = (effective_max // 10) + (1 if effective_max % 10 else 0)

            cmd = [
                sys.executable, "-m", "PyPaperBot",
                f"--query={query.strip()}",
                f"--scholar-pages={pages}",
                "--scholar-results=10", 
                "--restrict=0",
                f"--dwn-dir={dwn_dir}"
            ]

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"PyPaperBot failed: {result.stderr}")
                return []

            csv_file = os.path.join(dwn_dir, "result.csv")
            start_time = time.time()
            while time.time() - start_time < 20:
                if os.path.exists(csv_file) and os.path.getsize(csv_file) > 0:
                    break
                time.sleep(1)
            else:
                print("results.csv not found or empty.")
                return []

            with open(csv_file, newline="", encoding="utf-8") as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    doi = row.get("doi") or row.get("DOI")
                    uid = doi or row.get("ID") or row.get("title")

                    if uid in seen_ids:
                        continue
                    seen_ids.add(uid)

                    paper = None
                    if doi:
                        enriched = self.enrich_acm_with_doi(doi, query)
                        if enriched:
                            paper = enriched

                    if not paper:
                        paper = self.normalize_paper(
                            paper_id=doi or row.get("ID"),
                            title=row.get("title"),
                            authors=[a.strip() for a in row.get("author", "").split(";")],
                            venue=row.get("journal") or "Google Scholar",
                            year=row.get("year"),
                            doi=doi,
                            pdf_url=row.get("pdf_url") or "",
                            pdf_status="",
                            source="Google Scholar (CSV)",
                            abstract=row.get("abstract") or "",
                            abstract_hit=query.lower() in (row.get("abstract") or "").lower(),
                            last_updated=row.get("year")
                        )
                    papers.append(paper)

            # ✅ Limit to requested max_results
            if not fetch_all and len(papers) > max_results:
                papers = papers[:max_results]

        except Exception as e:
            print(f"Google Scholar fetch error: {e}")

        print("✅ Total papers fetched from Google Scholar:", len(papers))
        return papers