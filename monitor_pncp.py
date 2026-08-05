#!/usr/bin/env python3
"""
Agente de monitoramento de editais de TI no PNCP (Portal Nacional de Contratações Públicas)

O que faz:
1. Consulta a API pública do PNCP (não exige login) por contratações publicadas
   nos últimos N dias, para as modalidades e UFs configuradas.
2. Filtra pelo texto do objeto da contratação usando as palavras-chave de TI
   definidas em KEYWORDS.
3. Compara com um "cache" local (visto.json) para não notificar a mesma
   oportunidade duas vezes.
4. Envia um e-mail com a lista de novidades (ou grava um relatório .txt/.csv
   se você não configurar SMTP).

Como rodar sozinho, sem seu computador ligado: veja o arquivo
.github/workflows/monitor.yml — ele executa este script todo dia via
GitHub Actions gratuito.

Documentação oficial da API: https://pncp.gov.br/api/consulta/swagger-ui/index.html
"""

import os
import json
import html
import smtplib
import time
import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from pathlib import Path

import requests

# =========================== CONFIGURAÇÃO ================================

# Palavras-chave que identificam compras de TI. Ajuste à vontade.
KEYWORDS = [
    "computador", "notebook", "desktop", "microcomputador",
    "impressora", "multifuncional", "scanner",
    "camera", "câmera", "cftv", "videomonitoramento", "vigilância",
    "servidor de rede", "storage", "nobreak", "no-break",
    "switch", "roteador", "rede de dados", "cabeamento estruturado",
    "hardware", "software", "licenca", "licença", "licenciamento",
    "sistema de informacao", "sistema de informação",
    "solucao de tecnologia", "solução de tecnologia",
    "tecnologia da informacao", "tecnologia da informação",
    "antivirus", "antivírus", "firewall", "backup",
    "equipamento de informatica", "equipamento de informática",
]

# Modalidades de contratação do PNCP que valem a pena olhar.
# 6 = Pregão Eletrônico | 8 = Dispensa de Licitação
# (lista completa na documentação do PNCP, pode adicionar outras)
MODALIDADES = {
    6: "Pregão Eletrônico",
    8: "Dispensa de Licitação",
}

# UFs a monitorar. Deixe a lista vazia [] para buscar o Brasil todo
# (mais lento, mais resultados). Ex: ["SP"] só São Paulo.
UFS = ["SP"]

# Quantos dias para trás consultar a cada execução.
DIAS_JANELA = 3

# Onde salvar o histórico de itens já notificados, para não repetir.
CACHE_PATH = Path(__file__).parent / "visto.json"

# --- E-mail (opcional). Se não configurar, o script só salva um .csv local.
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
EMAIL_TO = os.environ.get("EMAIL_TO", "")

BASE_URL = "https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao"

# ============================================================================


def buscar_contratacoes(data_inicial, data_final, modalidade, uf=None):
    """Busca todas as páginas de contratações para uma modalidade/UF/período.

    A API do PNCP retorna 429 (Too Many Requests) se as páginas forem
    consultadas rápido demais. Por isso: pequena pausa entre páginas e
    retry com backoff quando o limite é atingido.
    """
    resultados = []
    pagina = 1
    while True:
        params = {
            "dataInicial": data_inicial,
            "dataFinal": data_final,
            "codigoModalidadeContratacao": modalidade,
            "pagina": pagina,
            "tamanhoPagina": 50,
        }
        if uf:
            params["uf"] = uf

        resp = None
        for tentativa in range(5):
            resp = requests.get(BASE_URL, params=params, timeout=30)
            if resp.status_code == 429:
                time.sleep(3 * (tentativa + 1))
                continue
            break

        if resp.status_code == 204:
            break  # sem resultados
        resp.raise_for_status()
        dados = resp.json()

        itens = dados.get("data", [])
        resultados.extend(itens)

        total_paginas = dados.get("totalPaginas", 1)
        if pagina >= total_paginas:
            break
        pagina += 1
        time.sleep(0.4)  # evita novo 429 na próxima página

    return resultados


def bate_com_palavra_chave(objeto_compra):
    texto = (objeto_compra or "").lower()
    return any(kw.lower() in texto for kw in KEYWORDS)


def carregar_cache():
    if CACHE_PATH.exists():
        return set(json.loads(CACHE_PATH.read_text(encoding="utf-8")))
    return set()


def salvar_cache(vistos):
    CACHE_PATH.write_text(
        json.dumps(sorted(vistos), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def montar_id(item):
    # numeroControlePNCP é o identificador único da contratação no PNCP
    return item.get("numeroControlePNCP") or item.get("processo") or str(item)


def formatar_item(item, modalidade_nome):
    orgao = (item.get("orgaoEntidade") or {}).get("razaoSocial", "Órgão não informado")
    unidade = (item.get("unidadeOrgao") or {})
    municipio = unidade.get("municipioNome", "")
    uf = unidade.get("ufSigla", "")
    objeto = item.get("objetoCompra", "").strip()
    valor = item.get("valorTotalEstimado")
    valor_fmt = f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if valor else "não informado"
    abertura = item.get("dataAberturaProposta", "não informado")
    encerramento = item.get("dataEncerramentoProposta", "não informado")
    link = item.get("linkSistemaOrigem", "")
    numero_controle = item.get("numeroControlePNCP", "")
    link_pncp = f"https://pncp.gov.br/app/editais/{numero_controle}" if numero_controle else ""

    return (
        f"--------------------------------------------------\n"
        f"Modalidade: {modalidade_nome}\n"
        f"Órgão: {orgao}\n"
        f"Local: {municipio}/{uf}\n"
        f"Objeto: {objeto}\n"
        f"Valor estimado: {valor_fmt}\n"
        f"Abertura das propostas: {abertura}\n"
        f"Encerramento das propostas: {encerramento}\n"
        f"Link PNCP: {link_pncp}\n"
        f"Link sistema de origem: {link}\n"
    )


def gerar_relatorio_html(novos, data_execucao):
    """Monta um relatório HTML com um cartão por oportunidade encontrada."""

    def cartao(item, modalidade_nome):
        orgao = html.escape((item.get("orgaoEntidade") or {}).get("razaoSocial", "Órgão não informado"))
        unidade = item.get("unidadeOrgao") or {}
        municipio = html.escape(unidade.get("municipioNome", ""))
        uf = html.escape(unidade.get("ufSigla", ""))
        objeto = html.escape(item.get("objetoCompra", "").strip())
        valor = item.get("valorTotalEstimado")
        valor_fmt = (
            f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            if valor else "não informado"
        )
        abertura = html.escape(str(item.get("dataAberturaProposta", "não informado")))
        encerramento = html.escape(str(item.get("dataEncerramentoProposta", "não informado")))
        link_origem = html.escape(item.get("linkSistemaOrigem", "") or "")
        numero_controle = item.get("numeroControlePNCP", "")
        link_pncp = f"https://pncp.gov.br/app/editais/{numero_controle}" if numero_controle else ""

        botoes = ""
        if link_pncp:
            botoes += f'<a class="btn" href="{html.escape(link_pncp)}" target="_blank">Ver no PNCP</a>'
        if link_origem:
            botoes += f'<a class="btn btn-outline" href="{link_origem}" target="_blank">Sistema de origem</a>'

        return f"""
        <div class="card">
          <div class="card-header">
            <span class="badge">{html.escape(modalidade_nome)}</span>
            <span class="valor">{valor_fmt}</span>
          </div>
          <h3>{objeto}</h3>
          <p class="orgao">{orgao} — {municipio}/{uf}</p>
          <div class="datas">
            <div><span>Abertura</span>{abertura}</div>
            <div><span>Encerramento</span>{encerramento}</div>
          </div>
          <div class="botoes">{botoes}</div>
        </div>
        """

    cartoes_html = "\n".join(cartao(item, nome) for item, nome in novos)

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Editais de TI — {data_execucao}</title>
<style>
  body {{
    font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif;
    background: #0f172a;
    color: #e2e8f0;
    margin: 0;
    padding: 32px 16px;
  }}
  .container {{ max-width: 720px; margin: 0 auto; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  .subtitulo {{ color: #94a3b8; margin-top: 0; margin-bottom: 28px; font-size: 14px; }}
  .card {{
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 16px;
  }}
  .card-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }}
  .badge {{
    background: #2563eb;
    color: white;
    font-size: 12px;
    font-weight: 600;
    padding: 4px 10px;
    border-radius: 999px;
  }}
  .valor {{ color: #4ade80; font-weight: 600; font-size: 14px; }}
  h3 {{ font-size: 16px; line-height: 1.4; margin: 8px 0; color: #f1f5f9; }}
  .orgao {{ color: #94a3b8; font-size: 13px; margin: 4px 0 14px; }}
  .datas {{ display: flex; gap: 24px; font-size: 13px; margin-bottom: 14px; }}
  .datas span {{ display: block; color: #64748b; font-size: 11px; text-transform: uppercase; }}
  .botoes {{ display: flex; gap: 10px; }}
  .btn {{
    background: #2563eb;
    color: white !important;
    text-decoration: none;
    font-size: 13px;
    font-weight: 600;
    padding: 8px 14px;
    border-radius: 8px;
  }}
  .btn-outline {{
    background: transparent;
    border: 1px solid #475569;
    color: #cbd5e1 !important;
  }}
</style>
</head>
<body>
  <div class="container">
    <h1>Editais de TI encontrados no PNCP</h1>
    <p class="subtitulo">Execução de {data_execucao} — {len(novos)} nova(s) oportunidade(s)</p>
    {cartoes_html}
  </div>
</body>
</html>"""


def enviar_email(assunto, corpo_texto, anexo_html=None, anexo_nome=None):
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS and EMAIL_TO):
        print("[aviso] SMTP não configurado — pulando envio de e-mail.")
        return False

    msg = MIMEMultipart()
    msg["From"] = SMTP_USER
    msg["To"] = EMAIL_TO
    msg["Subject"] = assunto
    msg.attach(MIMEText(corpo_texto, "plain", "utf-8"))

    if anexo_html:
        parte = MIMEApplication(anexo_html.encode("utf-8"), _subtype="html")
        parte.add_header("Content-Disposition", "attachment", filename=anexo_nome or "relatorio.html")
        msg.attach(parte)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, EMAIL_TO, msg.as_string())

    return True


def main():
    hoje = datetime.date.today()
    data_final = hoje.strftime("%Y%m%d")
    data_inicial = (hoje - datetime.timedelta(days=DIAS_JANELA)).strftime("%Y%m%d")

    vistos = carregar_cache()
    novos = []
    erros = []

    ufs_para_buscar = UFS if UFS else [None]

    for cod_modalidade, nome_modalidade in MODALIDADES.items():
        for uf in ufs_para_buscar:
            print(f"Consultando {nome_modalidade} | UF={uf or 'BR'} | {data_inicial}-{data_final}")
            try:
                itens = buscar_contratacoes(data_inicial, data_final, cod_modalidade, uf)
            except requests.RequestException as e:
                msg = f"{nome_modalidade} | UF={uf or 'BR'}: {e}"
                print(f"[erro] Falha ao consultar PNCP: {msg}")
                erros.append(msg)
                continue

            for item in itens:
                if not bate_com_palavra_chave(item.get("objetoCompra")):
                    continue
                item_id = montar_id(item)
                if item_id in vistos:
                    continue
                vistos.add(item_id)
                novos.append((item, nome_modalidade))

    if not novos:
        salvar_cache(vistos)
        if erros:
            # Falha real (ex: rate limit da API) — não confundir com "nada encontrado".
            # Sai com erro para o job do GitHub Actions ficar vermelho e não passar
            # em silêncio como se a busca tivesse simplesmente dado zero resultados.
            print("[erro] Execução incompleta, consultas com falha:")
            for msg in erros:
                print(f"  - {msg}")
            raise SystemExit(1)
        print("Nenhuma oportunidade nova de TI encontrada nesta execução.")
        return

    corpo = f"Encontradas {len(novos)} novas oportunidades de TI no PNCP:\n\n"
    corpo += "\n".join(formatar_item(item, nome) for item, nome in novos)
    if erros:
        corpo += "\n\n[aviso] Algumas consultas falharam e podem estar faltando oportunidades:\n"
        corpo += "\n".join(f"- {msg}" for msg in erros)
    corpo += "\n\nO relatório visual completo está em anexo (abra no navegador)."

    print(corpo)

    relatorio_html = gerar_relatorio_html(novos, hoje.strftime("%d/%m/%Y"))
    nome_anexo = f"editais_ti_{hoje.isoformat()}.html"

    enviado = enviar_email(
        f"[PNCP] {len(novos)} nova(s) oportunidade(s) de TI",
        corpo,
        anexo_html=relatorio_html,
        anexo_nome=nome_anexo,
    )
    if not enviado:
        print("[info] configure SMTP_HOST/SMTP_USER/SMTP_PASS/EMAIL_TO para receber por e-mail.")

    # Também salva o relatório localmente, útil se o e-mail não estiver configurado
    relatorio_path = Path(__file__).parent / nome_anexo
    relatorio_path.write_text(relatorio_html, encoding="utf-8")
    print(f"Relatório salvo em {relatorio_path}")

    salvar_cache(vistos)


if __name__ == "__main__":
    main()
