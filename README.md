# 📖 leitor-pdf-telegram

Bot + Mini App do Telegram que adiciona um botão **“📖 Abrir no leitor”** embaixo de cada PDF
postado no seu canal. O botão abre um leitor de PDF dentro do próprio Telegram (celular e desktop,
incluindo 64Gram), com rolagem contínua, zoom (botões, pinça e Ctrl+roda), “página X de Y”, ir para
página, busca de texto, download, tema claro/escuro e memória da última página lida.

```
Canal ── post com PDF ──► Bot (long polling) ──► SQLite (id ↔ file_id)
                              │
                              └─► edita o post: botão URL https://t.me/<bot>/<app>?startapp=<id>
Usuário toca no botão ──► Mini App (static/) ──► API FastAPI (/api/pdf/<id>/…)
                                                   │  valida initData (HMAC-SHA256)
                                                   └─► getFile → cache em disco → PDF
```

Tudo roda em **um único processo**: o FastAPI serve o frontend e a API e roda o bot no mesmo event loop.

---

## Estrutura

```
leitor-pdf-telegram/
├── app/
│   ├── __main__.py       # `python -m app` (sobe o uvicorn)
│   ├── main.py           # FastAPI + ciclo de vida do bot (create_app)
│   ├── bot.py            # handlers do Telegram
│   ├── api.py            # rotas /api/... e /health
│   ├── telegram_auth.py  # validação do initData + tokens de download
│   ├── cache.py          # cache LRU em disco
│   ├── db.py             # SQLite
│   ├── ids.py            # geração de IDs curtos
│   └── config.py         # leitura do .env
├── static/               # Mini App (HTML/CSS/JS + PDF.js via CDN)
├── tests/                # pytest
├── .env.example
├── Dockerfile
├── requirements.txt / requirements-dev.txt
└── README.md
```

## Variáveis de ambiente

| Variável | Obrigatória | Descrição |
|---|---|---|
| `BOT_TOKEN` | ✅ | Token do @BotFather |
| `BOT_USERNAME` | ✅ | Username do bot, sem `@` |
| `MINIAPP_SHORT_NAME` | ✅ | Short name do Mini App (`/newapp`) |
| `PUBLIC_URL` | ✅ | URL HTTPS pública do servidor (a mesma do `/newapp`) |
| `CHANNEL_ID` | ✅ | ID do canal (`-100…`) ou `@username` |
| `ALLOWED_USER_IDS` | – | IDs que podem ver os PDFs, separados por vírgula. Vazio = qualquer pessoa com o link. O 1º é o dono, se `OWNER_ID` estiver vazio |
| `OWNER_ID` | – | Seu user ID (recebe avisos e usa comandos de dono) |
| `DB_PATH` | – | Padrão `data/leitor.db` (`/data/leitor.db` no Docker) |
| `CACHE_DIR` | – | Padrão `data/cache` (`/data/cache` no Docker) |
| `CACHE_MAX_MB` | – | Limite do cache em disco (padrão 300) |
| `INIT_DATA_MAX_AGE` | – | Validade do initData em segundos (padrão 86400 = 24 h) |
| `RUN_BOT` | – | `false` sobe só API + frontend (útil para testes) |
| `LOG_LEVEL` / `PORT` | – | Padrões `INFO` / `8000` |

> ⚠️ **Defina `OWNER_ID` (ou `ALLOWED_USER_IDS`)**. Sem um dono, os avisos de erro, o encaminhamento
> de posts antigos e a publicação via privado ficam desativados.

---

## Passo a passo

### 1. Criar o bot no @BotFather

1. Abra o [@BotFather](https://t.me/BotFather) e envie `/newbot`.
2. Escolha um nome e um username terminado em `bot` (ex.: `MeuLeitorBot`).
3. Copie o **token** → `BOT_TOKEN`. O username (sem `@`) → `BOT_USERNAME`.

### 2. Criar o Mini App no @BotFather

Você precisa de uma URL HTTPS pública **antes** deste passo (veja o passo 4 para usar o ngrok
localmente, ou o passo 5 para o Railway). Se ainda não tiver, pode cadastrar qualquer URL e trocar
depois com `/myapps` → seu app → *Edit Web App URL*.

1. No @BotFather, envie `/newapp` e escolha o seu bot.
2. Informe título, descrição curta e uma foto 640×360 (obrigatória; qualquer imagem serve).
3. Quando pedir o GIF, envie `/empty`.
4. **Web App URL**: a sua `PUBLIC_URL` (ex.: `https://abc123.ngrok-free.app`).
5. **Short name**: ex.: `leitor` → `MINIAPP_SHORT_NAME`.

O link final do botão fica `https://t.me/MeuLeitorBot/leitor?startapp=<id>`.

### 3. Adicionar o bot ao canal e descobrir os IDs

1. No canal: **Configurações → Administradores → Adicionar administrador** → procure o bot.
2. Ative **“Editar mensagens de outros”** (Editar mensagens). Para o modo “enviar PDF ao bot e ele
   publica”, ative também **“Publicar mensagens”**.
3. **Seu user ID**: abra o seu bot, envie `/start` — ele responde com o seu ID (enquanto você ainda
   não estiver configurado como dono). Alternativa: [@userinfobot](https://t.me/userinfobot).
   Coloque em `OWNER_ID` (e/ou em `ALLOWED_USER_IDS`).
4. **ID do canal**: encaminhe qualquer post do canal para o [@userinfobot](https://t.me/userinfobot)
   (ou [@RawDataBot](https://t.me/RawDataBot)) — o ID começa com `-100`. No Telegram Web, a URL
   `web.telegram.org/a/#-1001234567890` também mostra o ID. Para canal público, pode usar `@username`.
5. **Envie `/start` ao seu bot** pelo seu usuário — sem isso o bot não consegue mandar avisos no privado.

### 4. Rodar localmente com ngrok

Requisitos: Python 3.11+ e [ngrok](https://ngrok.com/download) (conta gratuita).

```bash
cd leitor-pdf-telegram
python -m venv .venv
# Windows: .venv\Scripts\activate   |   Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # Windows: copy .env.example .env
```

Em outro terminal, exponha a porta 8000:

```bash
ngrok http 8000
```

Copie a URL `https://….ngrok-free.app` para `PUBLIC_URL` no `.env` **e** para o Mini App no
@BotFather (`/myapps` → app → *Edit Web App URL*). Preencha o resto do `.env` e rode:

```bash
python -m app
```

Verifique: `http://localhost:8000/health` deve responder `{"status":"ok","bot":"running",...}`.
Agora poste um PDF no canal — em 1–2 segundos o botão aparece.

> A URL gratuita do ngrok muda a cada execução: atualize `PUBLIC_URL` e o @BotFather quando mudar
> (ou use o domínio estático gratuito do ngrok: `ngrok http --url=SEU-DOMINIO.ngrok-free.app 8000`).

Testes:

```bash
pytest
```

### 5. Deploy no Railway (com volume persistente)

O Railway roda o `Dockerfile` do projeto e oferece HTTPS automático. (O plano gratuito é um crédito de
avaliação; depois o plano Hobby custa alguns dólares/mês — este app consome muito pouco.)

1. Suba o projeto para um repositório no GitHub (o `.gitignore` já exclui `.env`, `*.db` e o cache).
2. Em [railway.com](https://railway.com): **New Project → Deploy from GitHub repo** → escolha o repo.
   O Railway detecta o `Dockerfile` sozinho.
3. **Volume**: no serviço, clique com o botão direito → **Attach Volume** (ou Settings → Volumes) e
   monte em **`/data`**. O banco (`/data/leitor.db`) e o cache (`/data/cache`) ficam lá e sobrevivem a
   novos deploys.
4. **Domínio**: Settings → Networking → **Generate Domain** → ex.: `https://leitor.up.railway.app`.
5. **Variables**: adicione `BOT_TOKEN`, `BOT_USERNAME`, `MINIAPP_SHORT_NAME`, `CHANNEL_ID`,
   `OWNER_ID`, `ALLOWED_USER_IDS` (opcional) e `PUBLIC_URL` = o domínio gerado. Não defina `PORT`
   (o Railway define sozinho).
6. No @BotFather: `/myapps` → seu app → *Edit Web App URL* → o domínio do Railway.
7. **Mantenha 1 réplica só** (long polling não pode rodar em duas instâncias com o mesmo token) e
   **pare o servidor local** — senão aparece o erro `Conflict: terminated by other getUpdates request`.

Alternativas com HTTPS: **Fly.io** (`fly launch` + `fly volumes create data` montado em `/data`),
**Render** (plano pago para ter disco persistente) ou qualquer VPS com Docker + Caddy:

```bash
docker build -t leitor-pdf .
docker run -d --name leitor --restart unless-stopped --env-file .env -v leitor-data:/data -p 8000:8000 leitor-pdf
```

### 6. Adicionar o botão a PDFs antigos

O bot só “vê” posts novos. Para posts que já estavam no canal:

1. No canal, **encaminhe** o post com o PDF para o bot (no privado). Pode selecionar vários e
   encaminhar de uma vez.
2. O bot usa a origem do encaminhamento para achar o post original, registra o PDF e edita o post.
3. Ele responde **✅ Botão adicionado** (ou **✅ já tinha o botão**) ou **❌ + o motivo**.

Outros recursos do dono:

- **Enviar um PDF diretamente ao bot** (sem ser encaminhamento) → ele publica no canal já com o botão
  (a legenda é mantida). Requer a permissão “Publicar mensagens”.
- `/status` — total de PDFs e uso do cache.
- `/recentes` — últimos 10 PDFs com links do leitor.
- `/start` e `/ajuda` — instruções (funcionam para todos).

### 7. Solução de problemas

| Sintoma | Causa provável / solução |
|---|---|
| O botão não aparece em posts novos | O bot não é admin do canal, `CHANNEL_ID` está errado ou o servidor não está rodando. Confira `/health` e os logs. Se a edição falhar, o bot avisa você no privado com o motivo. |
| Não recebo os avisos no privado | Envie `/start` ao bot pelo seu usuário e confira `OWNER_ID`. |
| “o bot não tem a permissão Editar mensagens” | Canal → Administradores → bot → ative “Editar mensagens de outros”. |
| PDFs enviados em **álbum** (vários arquivos juntos) não ganham botão | O Telegram não permite botões em mensagens de álbum. Envie cada PDF sozinho (ou mande ao bot no privado para ele publicar). |
| O leitor mostra “Sessão inválida” | O initData expirou (mais de 24 h com o leitor aberto) ou o `BOT_TOKEN` do servidor é de outro bot. Feche e abra o leitor de novo pelo botão. |
| “Sem permissão” | `ALLOWED_USER_IDS` está definido e seu ID não está na lista. |
| “Arquivo grande demais” | A Bot API só permite que bots baixem arquivos de até 20 MB. |
| O botão abre o Telegram, mas dá erro de app não encontrado | `BOT_USERNAME` ou `MINIAPP_SHORT_NAME` não batem com o `/newapp`. |
| O Mini App abre em branco / não carrega | `PUBLIC_URL` e a URL no @BotFather precisam ser iguais e HTTPS. Com ngrok, a URL muda a cada execução. |
| `Conflict: terminated by other getUpdates request` | Duas instâncias rodando com o mesmo token (ex.: local + Railway). Pare uma delas. |
| Banco “zerou” após um deploy | O volume não está montado em `/data`. Os links antigos param de funcionar; encaminhe os posts ao bot de novo para recriá-los. |
| Encaminhei e o bot disse “não veio de um canal” | O post foi encaminhado com “ocultar remetente” ou de outro chat. Encaminhe direto do canal. |
| Editei a legenda do post e o botão sumiu | Encaminhe o post ao bot novamente: ele recoloca o botão. |
| No celular, rolar o PDF fecha o leitor | Atualize o Telegram (o leitor desativa o “arrastar para fechar” a partir da Bot API 7.7). |
| O download não começa | Em clientes antigos, o PDF abre no navegador externo para baixar. Os links de download expiram em 10 minutos. |

---

## Segurança

- Toda chamada à API exige o header `X-Telegram-Init-Data`, validado com HMAC-SHA256 (chave
  `HMAC("WebAppData", BOT_TOKEN)`), com `auth_date` de no máximo 24 h.
- O `BOT_TOKEN` nunca vai para o cliente: o servidor baixa o arquivo via `getFile` e entrega pelo
  próprio domínio. Os logs do `httpx` (que conteriam o token nas URLs) ficam em nível WARNING.
- O botão de download usa um link temporário assinado (10 min), válido só para aquele PDF.
- IDs dos PDFs são aleatórios (12 caracteres, ~72 bits): não dá para “adivinhar” outros PDFs.

## Desenvolvimento

- Rodar só API + frontend, sem bot: `RUN_BOT=false python -m app`.
- Fora do Telegram, o leitor mostra um aviso e permite abrir PDFs locais.
- Testes: `pytest` (initData, IDs, banco, cache, API e funções do bot).
