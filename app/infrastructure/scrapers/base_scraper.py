"""
Base scraper class with common functionality for all scrapers
"""
import requests
from bs4 import BeautifulSoup
from datetime import datetime, date
from typing import List, Dict, Optional
from app.logger import logger
from app.core.date_utils import parse_date_string
import time
import random
import re


class BaseScraper:
    """Base class for all scrapers with common functionality"""
    
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
        'DNT': '1',
        'Referer': 'https://www.pib.gov.in/'
    }
    
    def __init__(self):
        self.retry_attempts = 3
        self.retry_delays = [1, 2, 4]  # segundos
        self.timeout = 30
        # Sessão persistente: mantém cookies entre as chamadas (priming da home +
        # endpoints AJAX). WAFs costumam setar um cookie na primeira visita e exigi-lo
        # nas requisições seguintes; reusar a sessão satisfaz isso.
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self.session.max_redirects = 5
        # Motivo da última falha de fetch_page, para o chamador poder descrever
        # por que a fonte não respondeu.
        self.ultimo_erro: Optional[str] = None
        # Fontes que não puderam ser consultadas nesta execução.
        self.falhas_de_fonte: List[str] = []

    def registrar_falha_de_fonte(self, fonte: str) -> None:
        """Marca uma fonte como não consultada nesta execução.

        Uma listagem que não carrega e uma listagem sem publicações na data são
        indistinguíveis pela lista vazia que as duas devolvem - e a diferença
        importa: a segunda é um dia fraco, a primeira é um boletim capenga.
        """
        motivo = self.ultimo_erro or 'motivo desconhecido'
        self.falhas_de_fonte.append(f"{fonte} ({motivo})")
        logger.error(f"❌ Fonte não consultada: {fonte} - {motivo}")
    
    def get_selenium_options(self):
        """Retorna opções configuradas para Selenium Chrome"""
        from selenium.webdriver.chrome.options import Options
        import os

        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument(f"--user-agent={self.HEADERS['User-Agent']}")

        # Anti-detection options
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option('excludeSwitches', ['enable-automation'])
        chrome_options.add_experimental_option('useAutomationExtension', False)

        return chrome_options
    
    def fetch_page(self, url: str, retry: bool = True, headers: Optional[Dict] = None) -> Optional[str]:
        """
        Busca uma página web com retry logic e delay aleatório.

        `headers` permite sobrescrever/complementar headers por requisição
        (ex.: Referer da seção e X-Requested-With para chamadas AJAX).
        """
        attempt = 0
        while attempt < self.retry_attempts:
            try:
                # Adicionar delay aleatório para evitar bloqueio
                if attempt > 0:
                    delay = self.retry_delays[min(attempt - 1, len(self.retry_delays) - 1)]
                    time.sleep(delay)
                else:
                    time.sleep(random.uniform(1, 3))

                resp = self.session.get(
                    url,
                    timeout=self.timeout,
                    allow_redirects=True,
                    headers=headers,
                )
                resp.raise_for_status()
                self.ultimo_erro = None
                return resp.text

            except requests.exceptions.Timeout:
                attempt += 1
                logger.warning(f"Timeout ao acessar {url} (tentativa {attempt}/{self.retry_attempts})")
                if attempt >= self.retry_attempts:
                    logger.error(f"Falha ao acessar {url} após {self.retry_attempts} tentativas (timeout)")
                    self.ultimo_erro = f"timeout após {self.retry_attempts} tentativas"
                    return None

            except requests.exceptions.HTTPError as e:
                status = e.response.status_code
                if status == 404:
                    logger.error(f"404 - Página não encontrada: {url}")
                    self.ultimo_erro = "404 - página não encontrada"
                    return None
                # 5xx e bloqueios transitórios (403 Forbidden / 429 Too Many Requests)
                # são reprocessados com backoff — o WAF do MEA costuma bloquear de
                # forma intermitente, então uma nova tentativa geralmente passa.
                elif status >= 500 or status in (403, 429):
                    attempt += 1
                    logger.warning(f"Erro {status} ao acessar {url} (tentativa {attempt}/{self.retry_attempts})")
                    if attempt >= self.retry_attempts:
                        logger.error(f"Falha ao acessar {url} após {self.retry_attempts} tentativas (erro {status})")
                        self.ultimo_erro = f"HTTP {status} após {self.retry_attempts} tentativas"
                        return None
                else:
                    logger.error(f"Erro HTTP {status} ao acessar {url}: {e}")
                    self.ultimo_erro = f"HTTP {status}"
                    return None

            except requests.exceptions.TooManyRedirects as e:
                logger.warning(f"Muitos redirects ao acessar {url}: {e}")
                # Tentar sem seguir redirects para ver a resposta final
                try:
                    resp = self.session.get(url, timeout=self.timeout, allow_redirects=False)
                    logger.info(f"URL final após redirects: {resp.headers.get('Location', url)}")
                except:
                    pass
                self.ultimo_erro = "muitos redirects"
                return None
            except Exception as e:
                attempt += 1
                error_str = str(e)
                # Verificar se é erro de redirects
                if "redirect" in error_str.lower() or "Exceeded" in error_str:
                    logger.warning(f"Problema de redirects ao acessar {url}: {e}")
                    self.ultimo_erro = "problema de redirects"
                    return None
                logger.warning(f"Erro ao acessar {url} (tentativa {attempt}/{self.retry_attempts}): {e}")
                if attempt >= self.retry_attempts:
                    logger.error(f"Falha ao acessar {url} após {self.retry_attempts} tentativas: {e}")
                    self.ultimo_erro = f"{type(e).__name__}: {e}"
                    return None
        
        return None
    
    def parse_date_string(self, date_str: str) -> Optional[date]:
        """Wrapper para parse_date_string do date_utils"""
        return parse_date_string(date_str)
    
    def extract_content(self, url: str) -> str:
        """
        Extrai o conteúdo completo de uma página
        Usa múltiplos seletores com fallback
        """
        html = self.fetch_page(url)
        if not html:
            return ''
        
        soup = BeautifulSoup(html, 'html.parser')
        
        # Verificar se é uma URL do PIB (Prime Minister)
        if 'pib.gov.in' in url:
            # Para URLs do PIB, procurar por frames/iframes primeiro
            iframes = soup.find_all('iframe')
            frames = soup.find_all('frame')
            
            all_frames = iframes + frames
            
            for frame in all_frames:
                src = frame.get('src')
                if src:
                    # Construir URL completa do frame
                    if src.startswith('/'):
                        frame_url = 'https://www.pib.gov.in' + src
                    elif src.startswith('http'):
                        frame_url = src
                    else:
                        frame_url = 'https://www.pib.gov.in/' + src
                    
                    # Acessar o conteúdo do frame
                    frame_html = self.fetch_page(frame_url)
                    if frame_html:
                        frame_soup = BeautifulSoup(frame_html, 'html.parser')
                        
                        # Extrair conteúdo do frame
                        content_selectors = [
                            'div.content',
                            'div.main-content',
                            'div.article-content',
                            'div.post-content',
                            'article',
                            'div[class*="content"]',
                            'body'
                        ]
                        
                        for selector in content_selectors:
                            content_elem = frame_soup.select_one(selector)
                            if content_elem:
                                content_text = content_elem.get_text(separator='\n', strip=True)
                                if len(content_text) > 100:
                                    return content_text
                        
                        # Se não encontrou com seletores, pegar todo o texto
                        all_text = frame_soup.get_text(separator='\n', strip=True)
                        if len(all_text) > 100:
                            return all_text
            
            # Se não encontrou frames, tentar extrair informações básicas
            title = ''
            date_str = ''
            
            # Procurar por título
            title_selectors = [
                'h1', 'h2', 'h3',
                'div[class*="title"]',
                'span[class*="title"]'
            ]
            
            for selector in title_selectors:
                title_elem = soup.select_one(selector)
                if title_elem:
                    title = title_elem.get_text(strip=True)
                    if title and len(title) > 10:
                        break
            
            # Procurar por data
            date_selectors = [
                'span[class*="date"]',
                'div[class*="date"]',
                'span[class*="time"]',
                'div[class*="time"]'
            ]
            
            for selector in date_selectors:
                date_elem = soup.select_one(selector)
                if date_elem:
                    date_str = date_elem.get_text(strip=True)
                    if date_str and len(date_str) > 5:
                        break
            
            # Se encontrou título, retornar informações básicas
            if title:
                content = f"Prime Minister's Office Release\n\nTitle: {title}\n"
                if date_str:
                    content += f"Date: {date_str}\n"
                content += f"\nThis is an official release from the Prime Minister's Office of India. "
                content += "The full detailed content is available on the official PIB website but requires JavaScript to access."
                return content
            else:
                return "Prime Minister's Office Release - This is an official communication from the Prime Minister's Office of India. The full content requires JavaScript and is available on the official PIB website."
        
        # Para outras URLs (MEA), usar seletores de conteúdo
        content_selectors = [
            'div.content',
            'div.col-md-9',
            'div.main-content',
            'div.article-content',
            'div.post-content',
            'article',
            'div[class*="content"]'
        ]
        
        main = None
        for selector in content_selectors:
            main = soup.select_one(selector)
            if main:
                break
        
        if not main:
            # Fallback: pegar o body inteiro
            main = soup.find('body')
        
        if not main:
            return ''
        
        # Limpar conteúdo: remover scripts, styles, nav, footer
        for unwanted in main(['script', 'style', 'nav', 'header', 'footer']):
            unwanted.decompose()
        
        content_text = main.get_text(separator='\n', strip=True)

        return content_text

