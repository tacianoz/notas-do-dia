<div align="center">

# Notas do Dia

**Boletim diário automatizado da diplomacia indiana**

Coleta os comunicados oficiais do governo da Índia, escreve uma síntese editorial do dia com IA e envia por e-mail antes do expediente.

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)
![Claude](https://img.shields.io/badge/Claude-Opus%204.7-D97757?style=flat-square)
![Gemini](https://img.shields.io/badge/Gemini-2.5%20Flash-4285F4?style=flat-square&logo=google&logoColor=white)
![Schedule](https://img.shields.io/badge/Cron-06h%20IST%20%C2%B7%20Seg–S%C3%A1b-2ea44f?style=flat-square)

</div>

---

## Visão geral

Todos os dias úteis às 6h (horário de Nova Délhi), o sistema coleta os comunicados publicados pelo Gabinete do Primeiro-Ministro (PIB) e pelo Ministério das Relações Exteriores (MEA) da Índia, gera resumos individuais com Gemini, classifica por tema, escreve uma síntese narrativa do dia com Claude Opus 4.7 em português diplomático, monta um e-mail HTML com a identidade visual da Embaixada do Brasil em Nova Délhi e envia para a lista de destinatários.

> Nenhuma intervenção manual no ciclo. O resultado chega na caixa de entrada antes do expediente.

---

## Recursos

### Coleta

- **PM Releases** (PIB) — HTTP direto para o mês corrente; Selenium com `pageLoadStrategy=eager` como fallback automático para virada de mês ou falha de rede
- **MEA Press Releases**
- **MEA Speeches & Statements**
- **MEA Media Briefings**

### Inteligência

- **Resumos individuais** com Google Gemini 2.5 Flash (50–60 palavras, em inglês)
- **Síntese editorial do dia** com Claude Opus 4.7 — parágrafo único, português PT-BR, registro de revista internacional (The Economist, Financial Times)
- **Contexto de continuidade** — as 3 sínteses anteriores entram no prompt em ordem cronológica, permitindo que ciclos diplomáticos (visitas de estado, cúpulas) tenham fio narrativo entre boletins
- **Classificação temática automatizada** — agricultura, defesa, energia, comércio, BRICS, América Latina, Brasil, ciência e tecnologia, cooperação sul-sul, entre outros

### Entrega

- E-mail HTML com identidade visual da Embaixada
- Síntese narrativa no topo, abaixo as notícias agrupadas por seção
- Nomes próprios em negrito, tags temáticas coloridas
- Destaque visual para documentos que mencionam o Brasil
- Suporte a destinatários principais (`EMAIL_TO`) e em cópia (`EMAIL_CC`)

### Arquivo

- Cada edição salva em `logs/arquivo/notas_YYYYMMDD.html`
- Síntese salva em `notas_YYYYMMDD.synthesis.txt` para servir de contexto aos próximos boletins
- Endpoint `GET /arquivo` lista edições anteriores

### Calendário

| Dia              | Comportamento                            |
| ---------------- | ---------------------------------------- |
| Segunda-feira    | Notas do fim de semana (sábado e domingo) |
| Terça a sábado   | Notas do dia anterior                    |
| Domingo          | Sem execução                             |

---

## Instalação

```bash
git clone https://github.com/tacianoz/notas-do-dia.git
cd notas-do-dia
./rodar.sh --simular
```

O `rodar.sh` cria o venv, instala as dependências e copia o `.env` na primeira
execução — preencha as credenciais e rode de novo. Para fazer os passos à mão:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config/env.example .env
```

Edite o `.env` com suas credenciais:

| Variável                          | Descrição                                                  |
| --------------------------------- | ---------------------------------------------------------- |
| `SUMMARIZER_PROVIDER`             | `gemini`                                                   |
| `GOOGLE_API_KEY`                  | Chave da API do Gemini (resumos e classificação)           |
| `ANTHROPIC_API_KEY`               | Chave da API do Claude (síntese do dia)                    |
| `EMAIL_USER` / `EMAIL_PASSWORD`   | Gmail e senha de aplicativo                                |
| `EMAIL_FROM` / `EMAIL_TO`         | Remetente e destinatários principais (separados por vírgula) |
| `EMAIL_CC`                        | Destinatários em cópia (opcional)                          |

---

## Uso

### Linha de comando

```bash
./rodar.sh              # gera e envia por e-mail
./rodar.sh --simular    # gera sem enviar, salva em logs/arquivo/
```

Gera o boletim do dia útil mais recente. Antes de começar, o script confere o
acesso às fontes e avisa se elas estiverem bloqueadas — ver *Fontes bloqueadas*
abaixo. Para rodar sem o wrapper: `python generate_daily_notes.py`.

### Fontes bloqueadas

O site do MEA responde **403** para IPs de datacenter — o que inclui VPNs
comerciais e runners de CI. Na prática o app precisa rodar de uma conexão
residencial; tentativas via GitHub Actions falham em todas as seções do MEA,
enquanto o PIB passa normalmente.

Quando alguma fonte não responde, a execução **aborta sem enviar e-mail**, em
vez de publicar uma edição à qual falta a fonte principal. O log diz quais
fontes caíram e por quê:

```
Abortando: 3 de 4 fontes não responderam - MEA - Press Releases (HTTP 403 ...)
Nenhum e-mail foi enviado.
```

Uma seção que carrega e volta com zero publicações **não** é falha: é um dia sem
notícia, e o boletim sai normalmente.

### API web

```bash
python main.py
```

| Endpoint                  | Descrição                                            |
| ------------------------- | ---------------------------------------------------- |
| `GET /health`             | Status do serviço                                    |
| `POST /generate`          | Gerar boletim do dia                                 |
| `POST /generate/custom`   | Data específica (body: `{"date": "YYYY-MM-DD"}`)     |
| `GET /arquivo`            | Listar edições arquivadas                            |

### Agendamento via cron

```cron
0 6 * * 1-6 cd /caminho/notas-do-dia && ./rodar.sh >> logs/cron.log 2>&1
```

Execução agendada em nuvem (GitHub Actions e afins) não funciona por causa do
bloqueio do MEA descrito acima.

---

## Arquitetura

```
notas-do-dia/
├── app/
│   ├── infrastructure/scrapers/   # PM (PIB) e MEA
│   ├── services/                  # Sumarização, classificação, HTML
│   ├── core/                      # Utilitários (datas, factory)
│   ├── config.py                  # Configuração via .env
│   ├── emailer.py                 # Envio SMTP + CC
│   └── logger.py                  # Loguru com rotação semanal
├── generate_daily_notes.py        # Orquestração e entrada do cron
├── app_web.py                     # API HTTP opcional
├── main.py                        # Entrypoint Flask
└── deploy/Dockerfile
```

---

## Stack

| Camada         | Tecnologia                          |
| -------------- | ----------------------------------- |
| Linguagem      | Python 3.11+                        |
| Scraping       | Selenium, BeautifulSoup, Requests   |
| IA — Resumo    | Google Gemini 2.5 Flash             |
| IA — Síntese   | Anthropic Claude Opus 4.7           |
| API web        | Flask                               |
| E-mail         | SMTP (Gmail) + MIME                 |
| Logging        | Loguru (rotação semanal)            |
| Deploy         | Docker                              |

---

<div align="center">
<sub>Embaixada do Brasil em Nova Délhi</sub>
</div>
