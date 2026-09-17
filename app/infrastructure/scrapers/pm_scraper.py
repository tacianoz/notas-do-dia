"""
Prime Minister Scraper for PM Office Releases
"""
from bs4 import BeautifulSoup
from datetime import datetime, date
from typing import List, Dict, Optional
from app.logger import logger
from app.config import PM_RELEASES_URL, SELENIUM_WAIT_TIME
from app.infrastructure.scrapers.base_scraper import BaseScraper
from urllib.parse import urlencode
import pytz
import time
import re


# (connect, read) em segundos para o download do ChromeDriver. O read timeout é o
# que importa: sem ele um socket que morre no meio do download deixa o processo
# pendurado indefinidamente.
DRIVER_DOWNLOAD_TIMEOUT = (10, 60)


def _chromedriver_manager_com_timeout():
    """ChromeDriverManager cujo download tem timeout.

    O WDMHttpClient padrão do webdriver-manager chama requests.get() sem timeout.
    Se a conexão morre no meio do caminho — VPN caindo, por exemplo — o socket fica
    ESTABLISHED sem nunca receber dado nem RST, e o read bloqueia pra sempre. Com
    timeout isso vira exceção, que o retry de _fetch_with_selenium trata.
    """
    from webdriver_manager.chrome import ChromeDriverManager
    from webdriver_manager.core.download_manager import WDMDownloadManager
    from webdriver_manager.core.http import WDMHttpClient

    class _HttpClientComTimeout(WDMHttpClient):
        def get(self, url, **kwargs):
            kwargs.setdefault('timeout', DRIVER_DOWNLOAD_TIMEOUT)
            return super().get(url, **kwargs)

    # O mesmo http client atende à consulta de versão e ao download do binário.
    return ChromeDriverManager(download_manager=WDMDownloadManager(_HttpClientComTimeout()))


class PMScraper(BaseScraper):
    """Scraper for Prime Minister Releases"""
    
    def __init__(self):
        super().__init__()
        from app.config import TIMEZONE
        self.timezone = TIMEZONE
    
    def should_use_month_selection(self) -> bool:
        """Verifica se deve usar seleção de mês (apenas no primeiro dia do mês)"""
        tz = pytz.timezone(self.timezone)
        today = datetime.now(tz).date()
        return today.day == 1
    
    def _parse_pm_documents(self, html: str, target_dates: List[date]) -> List[Dict]:
        """Parse Prime Minister documents from HTML"""
        soup = BeautifulSoup(html, 'html.parser')
        docs = []
        seen_links = set()  # Para evitar duplicatas
        
        # Verificar se a página tem conteúdo relevante
        if 'Access Denied' in html or 'access denied' in html.lower():
            logger.error("Página retornou 'Access Denied'")
            return []
        
        # Buscar todos os <li> na página (método simples que funciona)
        list_items = soup.find_all('li')
        
        logger.info(f"Encontrados {len(list_items)} itens <li> para processar")
        
        for item in list_items:
            # Procurar por link dentro do <li>
            link_elem = item.find('a')
            if not link_elem:
                continue
            
            # Extrair título e link primeiro
            title = link_elem.get_text(strip=True)
            link = link_elem.get('href')
            
            # Corrigir URL se necessário
            if link and not link.startswith('http'):
                if link.startswith('/'):
                    link = 'https://www.pib.gov.in' + link
                else:
                    link = 'https://www.pib.gov.in/' + link

            # Garantir que a URL está em inglês (lang=1)
            if link and 'pib.gov.in' in link:
                if 'lang=' not in link:
                    link = link + ('&' if '?' in link else '?') + 'lang=1'
                else:
                    link = re.sub(r'lang=\d+', 'lang=1', link)
            
            if not title or not link:
                continue
                
            # Extrair data - método que funciona: procurar "Posted on:" no texto
            doc_date = None
            item_text = item.get_text(strip=True)
            
            # Método que funciona: procurar "Posted on:" no texto do item
            if 'Posted on:' in item_text:
                # Extrair a parte após "Posted on:"
                date_part = item_text.split('Posted on:')[1].strip()
                # Pegar apenas a data (pode ter mais texto depois)
                date_match = re.search(r'(\d{1,2}\s+\w+\s+\d{4})', date_part)
                if date_match:
                    date_str = date_match.group(1)
                    doc_date = self.parse_date_string(date_str)
                    logger.debug(f"Data extraída: {date_str} -> {doc_date}")
            
            # Fallback: tentar outros métodos apenas se o primeiro não funcionar
            if not doc_date:
                # Tentar span com publishdatesmall (estrutura antiga)
                date_span = item.find('span', class_='publishdatesmall')
                if date_span:
                    date_text = date_span.get_text(strip=True)
                    if 'Posted on:' in date_text:
                        date_part = date_text.split('Posted on:')[1].strip()
                        doc_date = self.parse_date_string(date_part)
                        logger.debug(f"Data encontrada em publishdatesmall: {date_part} -> {doc_date}")
            
            if not doc_date:
                # Tentar span com fa-calendar (estrutura nova)
                calendar_span = item.find('span', class_='fa fa-calendar')
                if calendar_span:
                    parent_p = calendar_span.find_parent('p')
                    if parent_p:
                        date_text = parent_p.get_text(strip=True)
                        date_text = date_text.replace('fa-calendar', '').strip()
                        doc_date = self.parse_date_string(date_text)
                        logger.debug(f"Data encontrada em fa-calendar: {date_text} -> {doc_date}")
            
            if title and link and doc_date:
                # Filtrar por datas alvo
                if doc_date in target_dates:
                    # Verificar se já existe um documento com o mesmo link (evitar duplicatas)
                    if link not in seen_links:
                        seen_links.add(link)
                        logger.info(f"✅ Documento encontrado: {title[:50]}... - {doc_date}")
                        docs.append({
                            'tipo': 'Prime Minister Releases',
                            'title': title,
                            'link': link,
                            'date': doc_date
                        })
                    else:
                        logger.debug(f"Documento duplicado ignorado: {title[:50]}... - {link}")
                else:
                    logger.debug(f"Documento fora do range: {title[:50]}... - {doc_date} (procurando: {target_dates})")
            elif title and link:
                # Só alerta se tinha "Posted on:" (era pra ser um release mas falhou extração).
                # Sem "Posted on:" é item de sidebar/menu (ministérios, Tenders, Home, etc) - ignora.
                if 'Posted on:' in item_text:
                    logger.warning(f"⚠️ Release com data não-parseável: {title[:50]}...")
                else:
                    logger.debug(f"Item de navegação ignorado: {title[:50]}...")
        
        logger.info(f"Total de documentos PM encontrados para {target_dates}: {len(docs)}")
        return docs

    def _extract_content_with_driver(self, driver, url: str) -> str:
        """Extrai conteúdo de uma página PIB usando driver Selenium já aberto"""
        from selenium.webdriver.common.by import By

        try:
            # Garantir que URL está em inglês
            if 'lang=' not in url:
                url = url + ('&' if '?' in url else '?') + 'lang=1'
            else:
                url = re.sub(r'lang=\d+', 'lang=1', url)

            logger.debug(f"Extraindo conteúdo de: {url}")
            driver.get(url)
            time.sleep(3)

            content_text = ""

            # PIB usa iframes para o conteúdo do artigo - tentar acessar o iframe
            try:
                iframes = driver.find_elements(By.TAG_NAME, "iframe")
                for iframe in iframes:
                    src = iframe.get_attribute("src") or ""
                    if "PressReleasePage" in src or "PRID" in src:
                        logger.debug(f"Encontrado iframe com src: {src}")
                        driver.switch_to.frame(iframe)
                        time.sleep(2)

                        # Extrair conteúdo do iframe
                        body = driver.find_element(By.TAG_NAME, "body")
                        content_text = body.text.strip()
                        logger.debug(f"Conteúdo do iframe: {len(content_text)} chars")

                        # Voltar ao contexto principal
                        driver.switch_to.default_content()
                        break
            except Exception as e:
                logger.debug(f"Erro ao acessar iframe: {e}")
                driver.switch_to.default_content()

            # Se não encontrou no iframe, tentar pegar do meta description
            if not content_text or len(content_text) < 200:
                try:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(driver.page_source, 'html.parser')
                    og_desc = soup.find('meta', property='og:description')
                    if og_desc and og_desc.get('content'):
                        content_text = og_desc.get('content')
                        logger.debug(f"Usando og:description: {len(content_text)} chars")
                except:
                    pass

            # Fallback: pegar texto do body
            if not content_text or len(content_text) < 100:
                try:
                    body = driver.find_element(By.TAG_NAME, "body")
                    content_text = body.text.strip()
                except:
                    pass

            if content_text and len(content_text) > 100:
                return content_text

            logger.warning(f"Conteúdo muito curto para: {url}")
            return ""

        except Exception as e:
            logger.error(f"Erro ao extrair conteúdo de {url}: {e}")
            return ""

    def _build_iframe_url(self, detail_url: str) -> str:
        """Transforma URL de detalhe (PressReleseDetail.aspx) na URL do iframe (PressReleasePage.aspx)
        que contém o conteúdo do release sem precisar de JavaScript."""
        iframe_url = re.sub(r'PressReleseDetail\.aspx', 'PressReleasePage.aspx', detail_url)
        if 'lang=' not in iframe_url:
            iframe_url = iframe_url + ('&' if '?' in iframe_url else '?') + 'lang=1'
        else:
            iframe_url = re.sub(r'lang=\d+', 'lang=1', iframe_url)
        return iframe_url

    def _extract_content_via_http(self, detail_url: str) -> str:
        """Extrai o conteúdo de um release PIB via HTTP direto (sem Selenium).
        Busca o iframe PressReleasePage.aspx que contém o texto completo."""
        iframe_url = self._build_iframe_url(detail_url)
        html = self.fetch_page(iframe_url)
        if html:
            soup = BeautifulSoup(html, 'html.parser')
            for tag in soup(['script', 'style', 'nav', 'header', 'footer']):
                tag.decompose()
            text = soup.get_text(separator=' ', strip=True)
            if text and len(text) > 100:
                return text

        # Fallback: og:description da página de detalhe
        detail_html = self.fetch_page(detail_url)
        if detail_html:
            soup = BeautifulSoup(detail_html, 'html.parser')
            og = soup.find('meta', property='og:description')
            if og and og.get('content'):
                return og.get('content')
        return ""

    def _fetch_via_http(self, target_dates: List[date]) -> Optional[List[Dict]]:
        """Busca PM releases via HTTP direto.

        O endpoint PMContents.aspx já devolve todo o mês corrente sem precisar de postback.
        Retorna None se a página não carregou ou veio com estrutura inesperada (sinaliza fallback)."""
        logger.info("Tentando buscar PM releases via HTTP direto...")
        html = self.fetch_page(PM_RELEASES_URL)
        if not html:
            logger.warning("HTTP direto falhou - nenhum HTML retornado")
            return None
        if 'Posted on:' not in html:
            logger.warning("HTTP direto retornou página sem 'Posted on:' - estrutura inesperada")
            return None

        docs = self._parse_pm_documents(html, target_dates)
        logger.info(f"HTTP direto encontrou {len(docs)} documentos para {target_dates}")

        for i, doc in enumerate(docs):
            logger.info(f"Extraindo conteúdo via HTTP [{i+1}/{len(docs)}]: {doc['title'][:50]}...")
            content = self._extract_content_via_http(doc['link'])
            doc['content'] = content if content else "Content not available for this document."

        return docs

    def _fetch_with_selenium(self, target_dates: List[date]) -> List[Dict]:
        """Fetch PM documents via Selenium, com retries em caso de falha."""
        max_attempts = 3
        retry_wait = 15
        for attempt in range(1, max_attempts + 1):
            try:
                return self._fetch_with_selenium_attempt(target_dates)
            except Exception as e:
                if attempt < max_attempts:
                    logger.warning(
                        f"Selenium falhou (tentativa {attempt}/{max_attempts}): {e}. "
                        f"Tentando novamente em {retry_wait}s..."
                    )
                    time.sleep(retry_wait)
                else:
                    logger.error(f"Selenium falhou após {max_attempts} tentativas: {e}")
                    return []
        return []

    def _fetch_with_selenium_attempt(self, target_dates: List[date]) -> List[Dict]:
        """Uma tentativa de Selenium: busca documentos do PM com seleção de mês."""
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import Select
        
        if not target_dates:
            return []
        
        # Pegar o mês da primeira data alvo (implementação simples que funcionava)
        target_month = target_dates[0].month
        
        logger.info(f"Usando Selenium para buscar documentos do mês {target_month}")
        
        driver = None
        try:
            # Configurar Chrome (igual à implementação que funcionava)
            chrome_options = self.get_selenium_options()
            # Estratégia "eager": retornar quando DOMContentLoaded disparar, sem esperar
            # analytics/scripts terceiros que travavam o load até estourar o timeout.
            chrome_options.page_load_strategy = 'eager'
            
            # Configurar ChromeDriver - tentar usar o do sistema primeiro
            from selenium.webdriver.chrome.service import Service
            import os
            
            chromedriver_path = '/usr/bin/chromedriver'
            logger.info(f"Verificando ChromeDriver em: {chromedriver_path}")
            logger.info(f"ChromeDriver existe: {os.path.exists(chromedriver_path)}")
            
            driver = None
            if os.path.exists(chromedriver_path):
                logger.info(f"✅ Usando ChromeDriver do sistema: {chromedriver_path}")
                try:
                    service = Service(chromedriver_path)
                    driver = webdriver.Chrome(service=service, options=chrome_options)
                    logger.info("✅ ChromeDriver inicializado com sucesso!")
                except Exception as e:
                    logger.warning(f"⚠️ Erro ao inicializar ChromeDriver do sistema: {e}")
                    driver = None
            
            if not driver:
                logger.info("Tentando usar webdriver-manager...")
                # Fallback: usar webdriver-manager
                try:
                    logger.info("Usando webdriver-manager para baixar ChromeDriver")
                    service = Service(_chromedriver_manager_com_timeout().install())
                    driver = webdriver.Chrome(service=service, options=chrome_options)
                    logger.info("✅ ChromeDriver inicializado via webdriver-manager!")
                except Exception as e:
                    logger.warning(f"⚠️ Erro ao usar webdriver-manager: {e}")
                    # Último fallback: tentar sem service (pode funcionar se estiver no PATH)
                    logger.info("Tentando usar ChromeDriver do PATH...")
                    try:
                        driver = webdriver.Chrome(options=chrome_options)
                        logger.info("✅ ChromeDriver inicializado do PATH!")
                    except Exception as e:
                        logger.error(f"❌ Erro ao inicializar ChromeDriver: {e}")
                        raise Exception(f"Não foi possível inicializar ChromeDriver: {e}")
            
            # Configurar timeouts
            driver.set_page_load_timeout(30)
            driver.set_script_timeout(30)

            # Remover propriedade webdriver para evitar detecção
            driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': '''
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    })
                '''
            })

            # Acessar URL com parâmetros lang=1&reg=3 diretamente (usando URL encoding correto)
            base_url = 'https://www.pib.gov.in/PMContents/PMContents.aspx'
            params = {'menuid': '1', 'lang': '1', 'reg': '3'}
            url = f"{base_url}?{urlencode(params)}"
            logger.info(f"Acessando: {url}")

            logger.info("Carregando página...")
            driver.get(url)
            logger.info(f"Página carregada. Título: {driver.title}")

            # Verificar se Access Denied
            if 'Access Denied' in driver.title or 'Access Denied' in driver.page_source:
                logger.error("Bloqueado pelo site - Access Denied")
                return []

            # Aguardar a página carregar
            time.sleep(2)

            wait = WebDriverWait(driver, 15)

            def _find_month_dropdown(d):
                """Tenta encontrar o select de mês por ID, name ou CSS."""
                try:
                    return d.find_element(By.ID, "ContentPlaceHolder1_ddlMonth")
                except Exception:
                    pass
                try:
                    return d.find_element(By.NAME, "ddlMonth")
                except Exception:
                    pass
                for sel in ["select[id*='ddlMonth']", "select[name='ddlMonth']"]:
                    try:
                        return d.find_element(By.CSS_SELECTOR, sel)
                    except Exception:
                        pass
                return None

            logger.info("Buscando dropdown de mês...")
            month_dropdown = None
            try:
                month_dropdown = wait.until(
                    EC.presence_of_element_located((By.ID, "ContentPlaceHolder1_ddlMonth"))
                )
                logger.info("Dropdown de mês encontrado por ID.")
            except Exception:
                month_dropdown = _find_month_dropdown(driver)
                if month_dropdown:
                    logger.info("Dropdown de mês encontrado por fallback (name/CSS).")

            # Se não encontrou no frame principal, tentar dentro de iframes
            if not month_dropdown:
                for iframe in driver.find_elements(By.TAG_NAME, "iframe"):
                    src = (iframe.get_attribute("src") or "").lower()
                    if "pib.gov.in" in src or "pmcontents" in src or not src.strip():
                        in_iframe = False
                        try:
                            driver.switch_to.frame(iframe)
                            in_iframe = True
                            logger.info(f"Tentando dropdown dentro de iframe: {src[:80]}...")
                            month_dropdown = _find_month_dropdown(driver)
                            if month_dropdown:
                                logger.info("Dropdown de mês encontrado dentro de iframe.")
                                break
                        except Exception as e:
                            logger.debug(f"Erro ao buscar em iframe: {e}")
                        finally:
                            if in_iframe and not month_dropdown:
                                driver.switch_to.default_content()
                    if month_dropdown:
                        break

            if not month_dropdown:
                logger.warning(
                    "Dropdown de mês não encontrado. O seletor do site PIB pode ter mudado."
                )
                if os.getenv("PIB_SCRAPER_DEBUG", "").lower() in ("1", "true", "yes"):
                    log_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "logs")
                    os.makedirs(log_dir, exist_ok=True)
                    path = os.path.join(
                        log_dir,
                        f"pib_pm_page_fail_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html",
                    )
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(driver.page_source)
                    logger.info(f"HTML da página salvo em {path} para diagnóstico.")
                return []

            # Selecionar o mês correto
            logger.info(f"Selecionando mês: {target_month}")
            select = Select(month_dropdown)
            select.select_by_value(str(target_month))

            logger.info(f"Mês {target_month} selecionado")

            # Aguardar a página atualizar após seleção do mês
            time.sleep(2)
            
            # Pegar o HTML da página
            html = driver.page_source
            
            logger.info(f"HTML obtido, tamanho: {len(html)} caracteres")
            
            # Verificar se a página carregou corretamente
            if 'Access Denied' in html or 'access denied' in html.lower():
                logger.error("Página retornou 'Access Denied'")
                return []
            
            # Verificar se há conteúdo relevante
            if 'Posted on:' not in html:
                logger.warning("HTML não contém 'Posted on:' - pode ter estrutura diferente")
            
            # Fazer parsing dos documentos
            docs = self._parse_pm_documents(html, target_dates)

            logger.info(f"Selenium encontrou {len(docs)} documentos para {target_dates}")

            # Extrair conteúdo de cada documento com o driver ainda aberto
            if docs:
                logger.info(f"Extraindo conteúdo de {len(docs)} documentos...")
                for i, doc in enumerate(docs):
                    logger.info(f"Extraindo conteúdo [{i+1}/{len(docs)}]: {doc['title'][:50]}...")
                    content = self._extract_content_with_driver(driver, doc['link'])
                    doc['content'] = content if content else "Content not available for this document."

            return docs
                
        except Exception as e:
            import traceback
            logger.debug(f"Traceback completo: {traceback.format_exc()}")
            raise
        finally:
            if driver:
                try:
                    driver.quit()
                except:
                    pass
    
    def get_pm_releases(self, target_dates: List[date]) -> List[Dict]:
        """Get Prime Minister Releases for target dates.

        Estratégia em camadas:
        1. HTTP direto quando todas as datas-alvo são do mês corrente (caso comum).
           O endpoint PMContents.aspx já mostra o mês inteiro sem precisar de postback.
        2. Selenium como fallback: para troca de mês (rollover) ou se HTTP falhar.
        """
        logger.info(f"Buscando Prime Minister Releases para datas: {target_dates}")

        tz = pytz.timezone(self.timezone)
        current_month = datetime.now(tz).month
        all_current_month = (
            bool(target_dates) and all(d.month == current_month for d in target_dates)
        )

        if all_current_month:
            try:
                docs = self._fetch_via_http(target_dates)
                if docs is not None:
                    logger.info(f"✅ HTTP direto bem-sucedido: {len(docs)} Prime Minister Releases")
                    return docs
            except Exception as e:
                logger.warning(f"HTTP direto lançou exceção, tentando Selenium: {e}")
        else:
            logger.info("Datas-alvo fora do mês atual - pulando HTTP direto, usando Selenium")

        logger.info("Usando Selenium como fallback para PM scraper")
        try:
            docs = self._fetch_with_selenium(target_dates)
        except Exception as e:
            logger.error(f"Erro ao usar Selenium para PM scraper: {e}")
            docs = []

        logger.info(f"Encontrados {len(docs)} Prime Minister Releases")
        return docs

