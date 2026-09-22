# Indicadores do site — Service Farma

Coleta diária dos números do Search Console e do GA4, gravados em `data/latest.json`
e no histórico em `data/historico/`. Roda sozinho no GitHub Actions, todo dia às 6h
(horário de São Paulo).

## Como a autenticação funciona

Não existe chave de conta de serviço neste projeto. O GitHub Actions se identifica
ao Google por **Workload Identity Federation**: a cada execução o GitHub emite um
token temporário, e o Google aceita esse token apenas se ele vier deste repositório.
Nada para guardar, nada para rotacionar, nada que possa vazar.

| Peça | Valor |
|---|---|
| Projeto Google Cloud | `servicefarma-indicadores` |
| Conta de serviço | `coleta-indicadores@servicefarma-indicadores.iam.gserviceaccount.com` |
| Provedor de identidade | `projects/352367922162/locations/global/workloadIdentityPools/github/providers/github` |
| Repositório autorizado | `andrevanni/servicefarma-indicadores` |

Só este repositório consegue assumir essa conta de serviço, e ela só tem permissão
de **leitura** no Search Console e no GA4.

## Arquivos

| Arquivo | Para que serve |
|---|---|
| `coletar.py` | O script de coleta |
| `requirements.txt` | Bibliotecas do Google |
| `.github/workflows/coleta.yml` | Agendamento diário |
| `data/latest.json` | Última leitura |
| `data/historico/AAAA-MM-DD.json` | Uma foto por dia, para comparar evolução |

## O que ainda falta configurar

1. **Search Console** → Configurações → Usuários e permissões → adicionar
   `coleta-indicadores@servicefarma-indicadores.iam.gserviceaccount.com`
   com permissão **Restrito**
2. **GA4** (quando a propriedade existir) → Administrador → Gerenciamento de acesso →
   adicionar o mesmo e-mail como **Leitor**
3. **GitHub** → Settings → Secrets and variables → Actions → aba **Variables** →
   criar `GA4_PROPERTY_ID` com o ID numérico da propriedade

Sem o passo 3 o script roda normalmente e o bloco `ga4` sai como
`{"configurado": false}`.

## Rodar na mão

```bash
pip install -r requirements.txt
gcloud auth application-default login
export GSC_SITE="sc-domain:servicefarma.far.br"
python coletar.py
```

## Observações

- O Search Console fecha os dados com cerca de dois dias de atraso; o script coleta
  a janela de 28 dias terminando três dias atrás
- O repositório é público e guarda apenas números agregados: nenhuma credencial,
  nenhum dado de visitante
