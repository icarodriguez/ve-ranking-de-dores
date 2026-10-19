# VE • Ranking de dores

Automação Python para identificar uma dor principal e um desejo por reunião do Programa Veterinário Empresário e alimentar duas planilhas Google Sheets.

## Estado do projeto

A operação atual usa conectores Gmail/Drive no Codex e `connector_bridge.py` para registrar gravações com proteção contra duplicação. A frequência autorizada é diária às 07:00, America/Sao_Paulo.

Publicar este repositório não instala nem agenda a automação. Não há workflow de produção no GitHub Actions. Credenciais, transcrições, recibos e dados dos clientes não são versionados.

O modo independente (`automacao.py`) é uma implementação anterior, ainda não ativada com credenciais reais: seu `--daemon` roda às segundas-feiras; sua triagem ainda exclui onboarding e exige transcrição completa. Ele também não percorre automaticamente a pasta fixa de Meet. A rotina por conectores segue o escopo ampliado do RUNBOOK. Não tratar os dois modos como equivalentes.

## Regras da operação por conectores

- Diagnósticos comerciais, hotseats e diagnósticos/acompanhamentos de mentorados são elegíveis quando contêm relatos reais.
- Reuniões internas, TheVetPlan, chats de bot e vídeos sem texto ficam fora.
- Resumos podem servir como evidência, identificados como resumo; paráfrases não são citações literais.
- Uma dor por reunião; resumo, transcrição, vídeo e retomada da mesma call não geram ocorrências adicionais.
- A: até R$250 mil/mês. B: acima disso. Receita desconhecida vai para A com `nao_informado` preservado.
- Dores equivalentes reutilizam o nome e incrementam Score. Desejos são agregados sem repetição.
- Novas dores aguardam Revisar; revisão com evidência permite promoção para Dores.
- Nunca apagar, reordenar ou zerar Dores. Preservar Autoridade, Método, CTA e Status.

## Arquivos

| Arquivo | Responsabilidade |
| --- | --- |
| `automacao.py` | Normalização, extração API, estado transacional, gravação e agendador legado |
| `gmail_source.py` | Busca paginada no Gmail e leitura de documentos/anexos |
| `connector_bridge.py` | Preparação, reconciliação, confirmação e revisão via conectores |
| `prompt_*.txt` | Prompts de extração por tipo de fonte |
| `tests/` | Testes locais com dados fictícios |
| `RUNBOOK.md` | Procedimento atual por conectores |

## Instalação

Python 3.11+ em macOS/Linux (`fcntl` é usado para bloqueio local).

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Preencher o `.env` somente no ambiente privado: chave OpenAI, modelo compatível com a implementação, OAuth Google, conta Gmail e IDs das duas planilhas existentes. Habilitar Gmail, Drive, Docs e Sheets APIs. Os escopos são `gmail.readonly`, `drive.readonly`, `documents.readonly` e `spreadsheets`, sob `https://www.googleapis.com/auth/`. Não copiar tokens internos de plugins.

```sh
.venv/bin/python automacao.py --check-config
# Execução única do modo independente legado, após validar seu escopo:
.venv/bin/python automacao.py
```

O modo independente usa temperatura 0 e JSON estruturado. A interpretação por conectores usa o modelo da tarefa no Codex. A configuração de exemplo não contém credenciais nem IDs reais.

## Migrar sem duplicar scores

Antes de executar uma cópia contra as planilhas existentes, transferir `processados.json` por canal privado, junto com a configuração. Não iniciar com estado vazio contra planilhas já alimentadas. Preservar qualquer `pending` e reconciliá-lo antes de preparar outra call. Não executar duas cópias ou modos simultaneamente. O arquivo de estado precisa de armazenamento persistente e backup privado.

O bloqueio local não protege duas máquinas distintas. Edições manuais concorrentes no Score podem gerar conflito; nesse caso a rotina interrompe a gravação.

## Planilhas

Cada planilha tem Dores, Top 10 e Revisar, com as colunas Categoria, Dor, Desejo, Score, Autoridade, Método, CTA e Status. Estados: Ativa, Em produção e Coberta.

`Top 10!A2` usa `=SORT(Dores!A2:H200;4;FALSE)`. A fórmula ordena todo o intervalo; as primeiras dez linhas são as prioridades. Acima de 200 linhas é necessário ampliar o intervalo. Não recriar planilhas existentes nem usar setup para substituir o design.

## Testes

```sh
python3 -m unittest discover -s tests -v
```

Cobrem normalização, idempotência, conflitos, recuperação de gravações, preservação editorial, revisão, roteamento e agendador legado. São testes locais; não comprovam disponibilidade ou permissões das APIs reais.
