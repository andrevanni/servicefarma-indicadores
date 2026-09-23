"""
Coleta diária de indicadores do site da Service Farma.

Lê Search Console e (quando existir) GA4, e grava os números em data/latest.json
e em data/historico/AAAA-MM-DD.json.

Autenticação: Workload Identity Federation. O GitHub Actions se identifica ao
Google por token temporário; não existe chave de conta de serviço em lugar nenhum.
Localmente, use `gcloud auth application-default login`.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import google.auth
from googleapiclient.discovery import build

SITE = os.environ.get("GSC_SITE", "sc-domain:servicefarma.far.br")
GA4_PROPERTY = os.environ.get("GA4_PROPERTY_ID", "").strip()
JANELA_DIAS = int(os.environ.get("JANELA_DIAS", "28"))
# O Search Console fecha os dados com ~2 dias de atraso.
ATRASO_GSC = 3
# O GA4 fecha no mesmo dia; usar o atraso do Search Console aqui esconderia
# tres dias de dados sem necessidade.
ATRASO_GA4 = 1

RAIZ = Path(__file__).resolve().parent
DIR_DADOS = RAIZ / "data"
DIR_HIST = DIR_DADOS / "historico"

ESCOPOS = [
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
]


def credenciais():
    try:
        creds, _ = google.auth.default(scopes=ESCOPOS)
    except Exception as erro:
        sys.exit(
            "Não foi possível obter credenciais do Google. No GitHub Actions isso "
            "vem do passo de autenticação; localmente, rode "
            "`gcloud auth application-default login`.\nDetalhe: %s" % erro
        )
    return creds


def periodo(atraso):
    """Janela de JANELA_DIAS terminando `atraso` dias atras."""
    fim = date.today() - timedelta(days=atraso)
    inicio = fim - timedelta(days=JANELA_DIAS - 1)
    return inicio.isoformat(), fim.isoformat()


def consultar_gsc(servico, inicio, fim, dimensoes, limite):
    corpo = {"startDate": inicio, "endDate": fim, "rowLimit": limite}
    if dimensoes:
        corpo["dimensions"] = dimensoes
    resposta = servico.searchanalytics().query(siteUrl=SITE, body=corpo).execute()
    return resposta.get("rows", [])


def coletar_search_console(creds, inicio, fim):
    servico = build("searchconsole", "v1", credentials=creds, cache_discovery=False)

    totais = consultar_gsc(servico, inicio, fim, None, 1)
    t = totais[0] if totais else {}
    resumo = {
        "cliques": int(t.get("clicks", 0)),
        "impressoes": int(t.get("impressions", 0)),
        "ctr": round(t.get("ctr", 0) * 100, 2),
        "posicao_media": round(t.get("position", 0), 1),
    }

    consultas = [
        {
            "termo": r["keys"][0],
            "cliques": int(r.get("clicks", 0)),
            "impressoes": int(r.get("impressions", 0)),
            "ctr": round(r.get("ctr", 0) * 100, 2),
            "posicao": round(r.get("position", 0), 1),
        }
        for r in consultar_gsc(servico, inicio, fim, ["query"], 25)
    ]

    paginas = [
        {
            "url": r["keys"][0],
            "cliques": int(r.get("clicks", 0)),
            "impressoes": int(r.get("impressions", 0)),
            "posicao": round(r.get("position", 0), 1),
        }
        for r in consultar_gsc(servico, inicio, fim, ["page"], 10)
    ]

    dispositivos = [
        {"dispositivo": r["keys"][0], "cliques": int(r.get("clicks", 0))}
        for r in consultar_gsc(servico, inicio, fim, ["device"], 5)
    ]

    return {
        "resumo": resumo,
        "consultas": consultas,
        "paginas": paginas,
        "dispositivos": dispositivos,
    }


def coletar_ga4(creds, inicio, fim):
    """Opcional: só roda quando GA4_PROPERTY_ID estiver configurado."""
    if not GA4_PROPERTY:
        return {"configurado": False}

    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        DateRange,
        Dimension,
        Metric,
        RunReportRequest,
    )

    cliente = BetaAnalyticsDataClient(credentials=creds)
    intervalo = [DateRange(start_date=inicio, end_date=fim)]

    geral = cliente.run_report(
        RunReportRequest(
            property=f"properties/{GA4_PROPERTY}",
            date_ranges=intervalo,
            metrics=[
                Metric(name="sessions"),
                Metric(name="totalUsers"),
                Metric(name="engagementRate"),
                Metric(name="conversions"),
            ],
        )
    )
    linha = geral.rows[0].metric_values if geral.rows else []
    resumo = {
        "sessoes": int(float(linha[0].value)) if linha else 0,
        "usuarios": int(float(linha[1].value)) if linha else 0,
        "taxa_engajamento": round(float(linha[2].value) * 100, 1) if linha else 0.0,
        "conversoes": int(float(linha[3].value)) if linha else 0,
    }

    def por(dimensoes, limite=12):
        r = cliente.run_report(
            RunReportRequest(
                property=f"properties/{GA4_PROPERTY}",
                date_ranges=intervalo,
                dimensions=[Dimension(name=d) for d in dimensoes],
                metrics=[Metric(name="sessions"), Metric(name="conversions")],
                limit=limite,
            )
        )
        return r.rows

    origem = [
        {
            "canal": l.dimension_values[0].value,
            "sessoes": int(float(l.metric_values[0].value)),
            "conversoes": int(float(l.metric_values[1].value)),
        }
        for l in por(["sessionDefaultChannelGroup"], 10)
    ]

    # De onde vem cada visita: separa LinkedIn de Google, organico de pago.
    fontes = [
        {
            "fonte": l.dimension_values[0].value,
            "midia": l.dimension_values[1].value,
            "sessoes": int(float(l.metric_values[0].value)),
            "conversoes": int(float(l.metric_values[1].value)),
        }
        for l in por(["sessionSource", "sessionMedium"], 15)
    ]

    # Le os UTMs: mede post a post, campanha a campanha.
    campanhas = [
        {
            "campanha": l.dimension_values[0].value,
            "sessoes": int(float(l.metric_values[0].value)),
            "conversoes": int(float(l.metric_values[1].value)),
        }
        for l in por(["sessionCampaignName"], 15)
        if l.dimension_values[0].value not in ("(not set)", "(organic)", "(direct)")
    ]

    return {
        "configurado": True,
        "resumo": resumo,
        "origem": origem,
        "fontes": fontes,
        "campanhas": campanhas,
    }


def main():
    creds = credenciais()
    gsc_inicio, gsc_fim = periodo(ATRASO_GSC)
    ga4_inicio, ga4_fim = periodo(ATRASO_GA4)

    dados = {
        "coletado_em": date.today().isoformat(),
        # "periodo" continua sendo o do Search Console, para nao quebrar
        # quem ja le esse campo; o do GA4 vai em "periodo_ga4".
        "periodo": {"inicio": gsc_inicio, "fim": gsc_fim, "dias": JANELA_DIAS},
        "periodo_ga4": {"inicio": ga4_inicio, "fim": ga4_fim, "dias": JANELA_DIAS},
        "site": SITE,
        "search_console": coletar_search_console(creds, gsc_inicio, gsc_fim),
        "ga4": coletar_ga4(creds, ga4_inicio, ga4_fim),
    }

    DIR_HIST.mkdir(parents=True, exist_ok=True)
    (DIR_DADOS / "latest.json").write_text(
        json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (DIR_HIST / f"{dados['coletado_em']}.json").write_text(
        json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    r = dados["search_console"]["resumo"]
    print(
        f"Search Console {gsc_inicio} a {gsc_fim}: {r['cliques']} cliques, "
        f"{r['impressoes']} impressões, posição média {r['posicao_media']}"
    )
    g = dados["ga4"]
    if g.get("configurado"):
        print(
            f"GA4 {ga4_inicio} a {ga4_fim}: {g['resumo']['sessoes']} sessões, "
            f"{g['resumo']['conversoes']} conversões, "
            f"{len(g.get('fontes', []))} fontes, "
            f"{len(g.get('campanhas', []))} campanhas"
        )
    else:
        print("GA4 não configurado (defina GA4_PROPERTY_ID)")


if __name__ == "__main__":
    main()
