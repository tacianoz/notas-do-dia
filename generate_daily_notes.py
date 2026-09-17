#!/usr/bin/env python3
"""
Main script to generate Notas do Dia - India
Uses new scraper architecture
"""
from app.core.scraper_factory import get_mea_scraper, get_pm_scraper
from app.core.date_utils import get_target_dates
from app.services.summarizer import Summarizer
from app.services.html_generator import HTMLGenerator
from app.services.theme_classifier import classify_all
from app.emailer import send_email
from app.logger import logger
from datetime import datetime
import pytz
import os
import glob
import signal


PREVIOUS_SYNTHESES_WINDOW = 3

# PIB + as três seções do MEA. Só serve para a mensagem de erro do guard.
TOTAL_DE_FONTES = 4

# Teto de tempo para uma execução inteira. Uma rodada normal leva ~2min; o pior
# caso com os retries do Selenium não passa de ~6min.
JOB_TIMEOUT_SECONDS = int(os.getenv('JOB_TIMEOUT_SECONDS', 15 * 60))


class JobTimeout(BaseException):
    """Estouro do tempo máximo de execução.

    Herda de BaseException de propósito: os `except Exception` espalhados pelos
    scrapers engoliriam o timeout e o job seguiria como se nada tivesse acontecido.
    """


def _armar_watchdog(seconds: int) -> None:
    """Interrompe a execução se ela passar de `seconds`.

    Sem isso, uma chamada de rede pendurada trava o processo indefinidamente: não
    gera erro, não gera e-mail, e ainda empilha com o disparo do cron do dia
    seguinte. Melhor falhar alto e cedo.
    """
    def _estourou(signum, frame):
        raise JobTimeout(f"Execução passou de {seconds}s sem terminar - abortando")

    signal.signal(signal.SIGALRM, _estourou)
    signal.alarm(seconds)


def _format_dates_header(dates_part: str) -> str:
    """Converte '20260521' ou '20260516_20260517' em DD/MM/YYYY [e DD/MM/YYYY]."""
    parts = dates_part.split('_')
    formatted = []
    for p in parts:
        if len(p) == 8 and p.isdigit():
            formatted.append(f"{p[6:8]}/{p[4:6]}/{p[0:4]}")
        else:
            formatted.append(p)
    return ' e '.join(formatted)


def _load_previous_synthesis(archive_dir: str, current_dates_str: str) -> str | None:
    """Lê as últimas N sínteses anteriores ao dia atual e devolve concatenadas em ordem
    cronológica, com cabeçalho de data por bloco. N = PREVIOUS_SYNTHESES_WINDOW.
    Retorna None se não houver nenhuma — primeira execução é caso esperado."""
    pattern = os.path.join(archive_dir, 'notas_*.synthesis.txt')
    candidates = sorted(glob.glob(pattern))
    selected = []  # tuples (dates_part, content) em ordem do mais recente pro mais antigo
    for path in reversed(candidates):
        if len(selected) >= PREVIOUS_SYNTHESES_WINDOW:
            break
        filename = os.path.basename(path)
        # Extrai a parte de datas: notas_YYYYMMDD[...].synthesis.txt
        dates_part = filename[len('notas_'):-len('.synthesis.txt')]
        if dates_part >= current_dates_str:
            # Mesmo dia ou posterior — ignora (re-execução do mesmo dia)
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                selected.append((dates_part, f.read().strip()))
        except OSError:
            continue
    if not selected:
        return None
    # Ordem cronológica (mais antigo → mais recente)
    selected.reverse()
    blocks = [
        f"[Síntese de {_format_dates_header(dates_part)}]\n{content}"
        for dates_part, content in selected
    ]
    return "\n\n---\n\n".join(blocks)


def generate_daily_notes(target_dates=None):
    """
    Generate Notas do Dia for target dates

    Args:
        target_dates: List of dates to scrape. If None, uses get_target_dates()

    Returns:
        HTML string with the email body, or None if error
    """
    logger.info("=== GERANDO NOTAS DO DIA - INDIA ===")

    try:
        if target_dates is None:
            target_dates = get_target_dates()
        logger.info(f"Buscando documentos para datas: {target_dates}")

        mea_scraper = get_mea_scraper()
        pm_scraper = get_pm_scraper()

        all_docs = []

        logger.info("Buscando Prime Minister Releases...")
        all_docs.extend(pm_scraper.get_pm_releases(target_dates))

        logger.info("Buscando MEA Press Releases...")
        all_docs.extend(mea_scraper.get_press_releases(target_dates))

        logger.info("Buscando MEA Speeches & Statements...")
        all_docs.extend(mea_scraper.get_speeches_statements(target_dates))

        logger.info("Buscando MEA Media Briefings...")
        all_docs.extend(mea_scraper.get_media_briefings(target_dates))

        # Guard: uma fonte que não respondeu produz edição capenga em silêncio.
        # Foi exatamente assim que uma rodada saiu verde com o MEA inteiro
        # bloqueado por WAF, sobrando só o PIB. Melhor não publicar nada do que
        # publicar um boletim ao qual falta a fonte principal.
        falhas = pm_scraper.falhas_de_fonte + mea_scraper.falhas_de_fonte
        if falhas:
            logger.error(
                "Abortando: {} de {} fontes não responderam - {}".format(
                    len(falhas), TOTAL_DE_FONTES, '; '.join(falhas)
                )
            )
            logger.error(
                "Nenhum e-mail foi enviado. Rode de novo quando as fontes voltarem "
                "(de rede bloqueada - VPN, IP de datacenter - isso não resolve sozinho)."
            )
            return None

        if not all_docs:
            logger.info("Nenhum documento encontrado para as datas especificadas.")
            return None

        logger.info(f"Total de documentos encontrados: {len(all_docs)}")

        # Generate summaries (adds 'summary' key to each doc)
        summarizer = Summarizer()
        summarizer.compile_report(all_docs)

        # Classify documents by theme
        logger.info("Classificando documentos por tema...")
        classify_all(all_docs)
        brasil_count = sum(1 for d in all_docs if d.get('brasil'))
        if brasil_count:
            logger.info(f"Documentos relacionados ao Brasil: {brasil_count}")

        # Pega a síntese mais recente do arquivo (de um dia anterior) pra dar
        # continuidade ao modelo.
        archive_dir = os.path.join(os.getcwd(), 'logs', 'arquivo')
        os.makedirs(archive_dir, exist_ok=True)
        dates_str = '_'.join(d.strftime('%Y%m%d') for d in sorted(target_dates))
        previous_synthesis = _load_previous_synthesis(archive_dir, dates_str)
        if previous_synthesis:
            logger.info("Síntese anterior carregada como contexto de continuidade")

        # Generate daily synthesis in diplomatic Portuguese
        logger.info("Gerando síntese do dia...")
        synthesis = summarizer.generate_daily_synthesis(
            all_docs, target_dates, previous_synthesis=previous_synthesis
        )

        # Generate HTML email body
        logger.info("Gerando HTML...")
        html_generator = HTMLGenerator()
        html = html_generator.generate(all_docs, target_dates, synthesis)

        # Save to archive (HTML + raw synthesis text para continuidade futura)
        archive_path = os.path.join(archive_dir, f'notas_{dates_str}.html')
        with open(archive_path, 'w', encoding='utf-8') as f:
            f.write(html)
        logger.info(f"Edição salva no arquivo: {archive_path}")

        if synthesis:
            synthesis_path = os.path.join(archive_dir, f'notas_{dates_str}.synthesis.txt')
            with open(synthesis_path, 'w', encoding='utf-8') as f:
                f.write(synthesis)

        return html

    except Exception as e:
        logger.error(f"Erro ao gerar Notas do Dia: {e}")
        import traceback
        traceback.print_exc()
        return None


def generate_and_send():
    """Generate Notas do Dia and send via email"""
    from app.config import TIMEZONE
    import pytz

    tz = pytz.timezone(TIMEZONE)
    today = datetime.now(tz).date()

    if today.weekday() == 6:  # Domingo
        logger.info("Domingo detectado - pulando geração (roda apenas de segunda a sábado)")
        return False

    target_dates = get_target_dates()
    html_body = generate_daily_notes(target_dates)

    if not html_body:
        logger.error("Não foi possível gerar o HTML")
        return False

    try:
        if len(target_dates) > 1:
            date_str = " e ".join([d.strftime('%d/%m/%Y') for d in sorted(target_dates)])
        else:
            publication_date = target_dates[0] if target_dates else datetime.now().date()
            date_str = publication_date.strftime('%d/%m/%Y')

        send_email(
            subject=f"Notas do dia - {date_str}",
            body=f"Notas do Dia - {date_str}",
            html_body=html_body,
        )

        logger.info("E-mail enviado com sucesso!")
        return True

    except Exception as e:
        logger.error(f"Erro ao enviar e-mail: {e}")
        return False


if __name__ == "__main__":
    _armar_watchdog(JOB_TIMEOUT_SECONDS)
    try:
        success = generate_and_send()
    except JobTimeout as e:
        logger.error(f"⏱️ {e}")
        success = False
    finally:
        signal.alarm(0)

    if success:
        print("\n✅ Notas do Dia gerado e enviado com sucesso!")
    else:
        print("\n❌ Erro no processo. Verifique os logs.")

