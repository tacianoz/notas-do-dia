#!/usr/bin/env bash
#
# Prepara o ambiente e roda o boletim. Pensado para a máquina recém-clonada:
# cria o venv, instala dependências e confere a configuração antes de rodar.
#
#   ./rodar.sh              gera e envia o e-mail
#   ./rodar.sh --simular    gera sem enviar, e mostra onde o HTML foi salvo
#
set -euo pipefail
cd "$(dirname "$0")"

simular=false
[ "${1:-}" = "--simular" ] && simular=true

if [ ! -d venv ]; then
  echo "▸ Criando venv..."
  python3 -m venv venv
fi

# Barato quando já está tudo instalado, e evita o clone quebrar por dependência
# faltando.
echo "▸ Conferindo dependências..."
./venv/bin/pip install --quiet --requirement requirements.txt

if [ ! -f .env ]; then
  cp config/env.example .env
  echo
  echo "✗ Faltava o .env - copiei de config/env.example."
  echo "  Preencha as credenciais e rode de novo."
  exit 1
fi

# O MEA bloqueia IP de datacenter (VPN, cloud) com 403. Melhor avisar aqui do
# que deixar a rodada morrer no meio.
echo "▸ Testando acesso às fontes..."
if ! ./venv/bin/python - <<'PY'
import sys
import requests
from app.infrastructure.scrapers.base_scraper import BaseScraper

sessao = BaseScraper().session
for nome, url in [('MEA', 'https://www.mea.gov.in/'), ('PIB', 'https://www.pib.gov.in/')]:
    try:
        codigo = sessao.get(url, timeout=20).status_code
    except Exception as e:
        print(f"  {nome}: inacessível ({type(e).__name__})")
        sys.exit(1)
    print(f"  {nome}: HTTP {codigo}")
    if codigo == 403:
        print(f"\n  {nome} devolveu 403 - normalmente é VPN ligada ou IP de datacenter.")
        print("  Desligue a VPN e rode de novo.")
        sys.exit(1)
PY
then
  exit 1
fi

if $simular; then
  echo "▸ Modo simulação - nenhum e-mail será enviado"
  ./venv/bin/python - <<'PY'
import sys
from app.core.date_utils import get_target_dates
from generate_daily_notes import generate_daily_notes

datas = get_target_dates()
print(f"Datas-alvo: {datas}")
html = generate_daily_notes(datas)
if not html:
    sys.exit(1)
arquivo = 'logs/arquivo/notas_' + '_'.join(d.strftime('%Y%m%d') for d in sorted(datas)) + '.html'
print(f"\n✅ Gerado sem enviar: {arquivo}")
PY
else
  ./venv/bin/python generate_daily_notes.py
fi
