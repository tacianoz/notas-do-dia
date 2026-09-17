"""
MEA Scraper for Press Releases, Media Briefings, and Speeches & Statements

O site do MEA (reformulado em 2026) renderiza as listagens via AJAX:
- Listagem: GET /FrontEnd/FetchPublicationListingData?publicationId=...&page=1&PageSize=...&PLngId=1
- Detalhe:  GET /FrontEnd/FetchPublicationDetailData?pkid=...&languageId=1
Ambos retornam fragmentos de HTML prontos para parsear.
"""
from bs4 import BeautifulSoup
from datetime import date
from typing import List, Dict, Optional
from urllib.parse import urlencode
from app.logger import logger
from app.config import (
    MEA_BASE_URL,
    MEA_LISTING_API_URL,
    MEA_DETAIL_API_URL,
    MEA_PUBLICATION_IDS
)
from app.infrastructure.scrapers.base_scraper import BaseScraper
import re


class MEAScraper(BaseScraper):
    """Scraper for MEA sections: Press Releases, Media Briefings, Speeches & Statements"""

    LISTING_PAGE_SIZE = 50

    def __init__(self):
        super().__init__()
        self._primed = False

    def _ajax_headers(self) -> Dict[str, str]:
        """Headers que deixam a requisição parecida com o XHR do próprio site"""
        return {
            'Referer': f"{MEA_BASE_URL}/press-releases.htm",
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': 'text/html, */*; q=0.01',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
        }

    def _ensure_primed(self) -> None:
        """Visita a home do MEA uma vez para coletar cookies do WAF antes do AJAX.

        A sessão é persistente (BaseScraper), então os cookies obtidos aqui são
        reaproveitados nas chamadas AJAX seguintes.
        """
        if self._primed:
            return
        self.fetch_page(MEA_BASE_URL + '/')
        self._primed = True

    def _fetch_listing_html(self, publication_id: int) -> Optional[str]:
        """Busca o fragmento HTML da listagem via endpoint AJAX do site"""
        self._ensure_primed()
        params = {
            'publicationId': publication_id,
            'KeywordName': '',
            'SortBy': '',
            'page': 1,
            'PageSize': self.LISTING_PAGE_SIZE,
            'DateRange': '',
            'IsInternalMEA': 'false',
            'PLngId': 1,
        }
        url = f"{MEA_LISTING_API_URL}?{urlencode(params)}"
        return self.fetch_page(url, headers=self._ajax_headers())

    def _parse_listing(self, html: str, tipo: str, target_dates: List[date]) -> List[Dict]:
        """Parse documents from the AJAX listing fragment"""
        soup = BeautifulSoup(html, 'html.parser')
        docs = []

        boxes = soup.select('div.pressRelesastBox')
        if not boxes:
            # Fallback: qualquer container que tenha um link de detalhe (dtl/<id>)
            boxes = [a.find_parent(['div', 'li']) or a for a in soup.find_all('a', href=re.compile(r'dtl/\d+'))]

        if not boxes:
            logger.warning(f"Não encontrou lista de documentos para {tipo}")
            return docs

        for box in boxes:
            a = box.select_one('h3.pressTitle a') or box.find('a', href=re.compile(r'dtl/\d+'))
            if not a or not a.get('href'):
                continue

            title = a.get_text(strip=True)
            link = a['href']
            if link.startswith('/'):
                link = MEA_BASE_URL + link
            elif not link.startswith('http'):
                link = f"{MEA_BASE_URL}/{link}"

            doc_date = self._extract_date_from_item(box)
            if not doc_date:
                logger.warning(f"Não foi possível extrair data para: {title}")
                continue

            if doc_date in target_dates:
                docs.append({
                    'tipo': tipo,
                    'title': title,
                    'link': link,
                    'date': doc_date
                })

        return docs

    def _extract_date_from_item(self, box) -> Optional[date]:
        """Extract date from a listing item (span.date, com fallback para o texto)"""
        date_tag = box.select_one('span.date')
        if date_tag:
            parsed = self.parse_date_string(date_tag.get_text(strip=True))
            if parsed:
                return parsed

        # Fallback: procurar uma data em qualquer trecho do texto do item
        match = re.search(r'\d{1,2}\s+\w+,?\s+\d{4}', box.get_text())
        if match:
            return self.parse_date_string(match.group(0))

        return None

    def _extract_pkid(self, link: str) -> Optional[int]:
        """Extrai o id do documento de links como /press-releases?dtl/41290/Titulo"""
        match = re.search(r'dtl/(\d+)', link)
        return int(match.group(1)) if match else None

    def _fetch_document_content(self, link: str) -> str:
        """Busca o conteúdo completo do documento via endpoint AJAX de detalhe"""
        pkid = self._extract_pkid(link)
        if pkid:
            self._ensure_primed()
            url = f"{MEA_DETAIL_API_URL}?{urlencode({'pkid': pkid, 'languageId': 1})}"
            html = self.fetch_page(url, headers=self._ajax_headers())
            if html:
                soup = BeautifulSoup(html, 'html.parser')
                for unwanted in soup(['script', 'style']):
                    unwanted.decompose()
                content = soup.get_text(separator='\n', strip=True)
                if len(content) > 100:
                    return content

        # Fallback: tentar extrair da página de detalhe diretamente
        return self.extract_content(link)

    def _get_section(self, tipo: str, target_dates: List[date]) -> List[Dict]:
        """Busca e parseia uma seção do MEA, incluindo conteúdo completo"""
        logger.info(f"Buscando {tipo} para datas: {target_dates}")
        html = self._fetch_listing_html(MEA_PUBLICATION_IDS[tipo])
        if not html:
            self.registrar_falha_de_fonte(tipo)
            return []

        docs = self._parse_listing(html, tipo, target_dates)

        for doc in docs:
            doc['content'] = self._fetch_document_content(doc['link'])
            if not doc['content']:
                doc['content'] = "Content not available for this document."

        logger.info(f"Encontrados {len(docs)} {tipo}")
        return docs

    def get_press_releases(self, target_dates: List[date]) -> List[Dict]:
        """Get MEA Press Releases for target dates"""
        return self._get_section('MEA - Press Releases', target_dates)

    def get_media_briefings(self, target_dates: List[date]) -> List[Dict]:
        """Get MEA Media Briefings for target dates"""
        return self._get_section('MEA - Media Briefings', target_dates)

    def get_speeches_statements(self, target_dates: List[date]) -> List[Dict]:
        """Get MEA Speeches & Statements for target dates"""
        return self._get_section('MEA - Speeches & Statements', target_dates)
