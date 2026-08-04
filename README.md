# Agente de monitoramento de editais de TI no PNCP

Este agente consulta diariamente a API pública do [PNCP](https://pncp.gov.br)
(Portal Nacional de Contratações Públicas) por editais de **Pregão Eletrônico**
e **Dispensa de Licitação**, filtra pelo objeto da compra usando palavras-chave
de tecnologia (computador, impressora, câmera, hardware, software etc.) e te
avisa por e-mail quando encontra algo novo — com um **relatório visual em HTML**
anexado (um cartão por oportunidade, com órgão, valor, prazos e link direto
para o edital). Veja `exemplo_relatorio.html` para um preview de como fica.

## Opção 1 — Rodar sozinho, sem depender do seu PC (recomendado)

1. Crie um repositório novo e **privado** no GitHub e suba estes arquivos
   (`monitor_pncp.py`, `.github/workflows/monitor.yml`, este README).
2. No GitHub: vá em **Settings → Secrets and variables → Actions** e crie 3
   segredos:
   - `SMTP_USER`: seu e-mail (ex: Gmail)
   - `SMTP_PASS`: uma **senha de app** do Gmail (não a senha normal — gere em
     https://myaccount.google.com/apppasswords)
   - `EMAIL_TO`: para qual e-mail enviar os avisos (pode ser o mesmo)
3. Pronto. O workflow roda sozinho todo dia às 08h (horário de Brasília).
   Para testar na hora, vá na aba **Actions** do repositório → selecione
   "Monitor de editais PNCP - TI" → **Run workflow**.

Se usar outro provedor de e-mail (não Gmail), troque `SMTP_HOST`/`SMTP_PORT`
no arquivo `monitor.yml`.

## Opção 2 — Rodar no seu computador

```bash
pip install requests
export SMTP_HOST=smtp.gmail.com
export SMTP_PORT=587
export SMTP_USER="seuemail@gmail.com"
export SMTP_PASS="sua-senha-de-app"
export EMAIL_TO="seuemail@gmail.com"
python monitor_pncp.py
```

Sem configurar as variáveis de e-mail, o script funciona do mesmo jeito, só
que em vez de mandar e-mail ele salva um arquivo `relatorio_AAAA-MM-DD.txt`
na pasta com os resultados. Nesse caso, para rodar todo dia sozinho, agende
com `cron` (Linux/Mac) ou o Agendador de Tarefas do Windows.

## Ajustando o que você quer receber

Abra `monitor_pncp.py` e edite no topo do arquivo:

- `KEYWORDS`: as palavras-chave de TI (já vem com computador, impressora,
  câmera, hardware, software etc. — adicione ou remova à vontade)
- `UFS`: lista de estados (ex: `["SP"]`). Deixe `[]` para o Brasil inteiro
- `MODALIDADES`: hoje monitora Pregão Eletrônico e Dispensa de Licitação
- `DIAS_JANELA`: quantos dias para trás olhar a cada execução (padrão: 3)

## Limitações importantes

- A API pública do PNCP cobre os órgãos que já usam a Lei 14.133/2021.
  Prefeituras pequenas que ainda usam a Lei 8.666/93 podem não aparecer —
  para essas, vale complementar monitorando o portal de transparência
  específico da prefeitura.
- O filtro é por palavra-chave no texto do objeto da compra. Alguns editais
  descrevem o item de forma genérica ("materiais de expediente e
  informática", por exemplo) e podem escapar do filtro — vale revisar o
  relatório completo de vez em quando, sem só confiar 100% no filtro.
- Este script só *avisa*. A participação na disputa (cadastro no sistema do
  órgão, envio de proposta) continua manual.
