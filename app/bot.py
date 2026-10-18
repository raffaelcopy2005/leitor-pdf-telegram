"""Bot do Telegram: adiciona o botão "📖 Abrir no leitor" aos PDFs do canal."""

from __future__ import annotations

import html
import logging
from typing import Any

from telegram import (
    BotCommand,
    Document,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageOriginChannel,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from .config import Settings
from .db import Database, PdfRecord

logger = logging.getLogger(__name__)

BUTTON_TEXT = "📖 Abrir no leitor"
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # limite do getFile da Bot API
DEFAULT_FILE_NAME = "documento.pdf"


# --- Funções puras (testáveis sem rede) ------------------------------------


def is_pdf(document: Document | None) -> bool:
    if document is None:
        return False
    if (document.mime_type or "").lower() == "application/pdf":
        return True
    return (document.file_name or "").lower().endswith(".pdf")


def build_markup(existing: InlineKeyboardMarkup | None, url: str, link_prefix: str) -> InlineKeyboardMarkup | None:
    """Preserva os botões existentes e acrescenta o do leitor.

    Retorna None se a mensagem já tiver um botão do leitor (nada a fazer).
    """
    rows: list[list[InlineKeyboardButton]] = []
    if existing is not None:
        for row in existing.inline_keyboard:
            for button in row:
                if button.url and button.url.startswith(link_prefix):
                    return None
            rows.append(list(row))
    rows.append([InlineKeyboardButton(BUTTON_TEXT, url=url)])
    return InlineKeyboardMarkup(rows)


def explain_error(exc: TelegramError) -> str:
    """Traduz erros comuns da Bot API em explicações amigáveis."""
    msg = str(exc).lower()
    if "not enough rights" in msg or "chat_admin_required" in msg or "have no rights" in msg:
        return "o bot não tem a permissão “Editar mensagens” no canal. Abra o canal → Administradores → bot e ative-a."
    if "message can't be edited" in msg:
        return (
            "o Telegram não permite editar essa mensagem (por exemplo, PDFs enviados em álbum/grupo "
            "de arquivos não aceitam botões). Envie o PDF sozinho."
        )
    if "message to edit not found" in msg or "message_id_invalid" in msg:
        return "a mensagem original não foi encontrada (pode ter sido apagada)."
    if "chat not found" in msg:
        return "o canal não foi encontrado. Verifique o CHANNEL_ID e se o bot é administrador do canal."
    if "button_url_invalid" in msg or ("url" in msg and "invalid" in msg):
        return "o link do botão foi recusado. Verifique BOT_USERNAME e MINIAPP_SHORT_NAME no .env."
    if isinstance(exc, Forbidden):
        return "o bot foi removido do canal ou não tem acesso a ele."
    return f"erro do Telegram: {exc}"


def _settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.application.bot_data["settings"]


def _db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["db"]


def _register(db: Database, document: Document, chat_id: int | None, message_id: int | None) -> tuple[PdfRecord, bool]:
    return db.register(
        file_id=document.file_id,
        file_unique_id=document.file_unique_id,
        file_name=document.file_name or DEFAULT_FILE_NAME,
        file_size=document.file_size,
        chat_id=chat_id,
        message_id=message_id,
    )


def _too_big(document: Document) -> bool:
    return document.file_size is not None and document.file_size > MAX_DOWNLOAD_BYTES


async def _notify_owner(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    owner = _settings(context).effective_owner_id
    if owner is None:
        logger.warning("Sem OWNER_ID/ALLOWED_USER_IDS: não há a quem avisar. Mensagem: %s", text)
        return
    try:
        await context.bot.send_message(owner, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except TelegramError as exc:
        logger.error("Não consegui avisar o dono (%s). Ele já deu /start no bot? Erro: %s", owner, exc)


async def _attach_button(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int | str,
    message_id: int,
    existing: InlineKeyboardMarkup | None,
    record: PdfRecord,
) -> bool:
    """Edita o post para incluir o botão. Retorna False se o botão já existia."""
    settings = _settings(context)
    markup = build_markup(existing, settings.miniapp_link(record.id), settings.miniapp_link_prefix)
    if markup is None:
        return False
    try:
        await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=markup)
    except BadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return False
        raise
    return True


# --- Handlers ---------------------------------------------------------------


async def on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    post = update.channel_post
    if post is None or post.document is None:
        return
    settings = _settings(context)
    if not settings.is_target_channel(post.chat.id, post.chat.username):
        logger.debug("Ignorando post de outro canal: %s", post.chat.id)
        return
    if not is_pdf(post.document):
        logger.debug("Ignorando documento que não é PDF: %s", post.document.mime_type)
        return

    name = html.escape(post.document.file_name or DEFAULT_FILE_NAME)
    if _too_big(post.document):
        await _notify_owner(
            context,
            f"⚠️ O PDF <b>{name}</b> tem mais de 20 MB. A Bot API não permite baixar arquivos "
            "desse tamanho, então o botão do leitor não foi adicionado.",
        )
        return

    record, created = _register(_db(context), post.document, post.chat.id, post.message_id)
    try:
        added = await _attach_button(context, post.chat.id, post.message_id, post.reply_markup, record)
        logger.info("Post %s/%s: botão %s (id=%s, novo=%s)", post.chat.id, post.message_id,
                    "adicionado" if added else "já existia", record.id, created)
    except TelegramError as exc:
        reason = explain_error(exc)
        logger.error("Falha ao editar post %s/%s: %s", post.chat.id, post.message_id, exc)
        await _notify_owner(
            context,
            f"❌ Não consegui adicionar o botão ao PDF <b>{name}</b>: {html.escape(reason)}\n\n"
            f"Link direto para o leitor (funciona mesmo assim):\n{settings.miniapp_link(record.id)}",
        )


async def on_private_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or message.document is None:
        return
    settings = _settings(context)
    if not settings.is_owner(user.id if user else None):
        await message.reply_text("🔒 Apenas o dono do bot pode registrar ou publicar PDFs.")
        return
    if not is_pdf(message.document):
        await message.reply_text("❌ Esse arquivo não é um PDF.")
        return
    if _too_big(message.document):
        await message.reply_text("❌ PDFs acima de 20 MB não podem ser lidos pelo bot (limite da Bot API).")
        return

    if message.forward_origin is not None:
        await _handle_forward(message, context)
    else:
        await _publish_to_channel(message, context)


async def _handle_forward(message: Message, context: ContextTypes.DEFAULT_TYPE) -> None:
    """PDF antigo encaminhado do canal: registra e edita o post original."""
    origin = message.forward_origin
    if not isinstance(origin, MessageOriginChannel):
        await message.reply_text(
            "❌ Esse encaminhamento não veio de um canal. Encaminhe o post diretamente do canal "
            "(sem “ocultar remetente”)."
        )
        return

    record, _ = _register(_db(context), message.document, origin.chat.id, origin.message_id)
    try:
        added = await _attach_button(context, origin.chat.id, origin.message_id, message.reply_markup, record)
    except TelegramError as exc:
        logger.error("Falha ao editar post antigo %s/%s: %s", origin.chat.id, origin.message_id, exc)
        await message.reply_text(
            f"❌ Não consegui editar o post: {explain_error(exc)}\n\nLink do leitor: {_settings(context).miniapp_link(record.id)}"
        )
        return
    if added:
        await message.reply_text(f"✅ Botão adicionado ao post original.\n{_settings(context).miniapp_link(record.id)}")
    else:
        await message.reply_text("✅ Esse post já tinha o botão do leitor.")


async def _publish_to_channel(message: Message, context: ContextTypes.DEFAULT_TYPE) -> None:
    """PDF enviado direto ao bot: publica no canal já com o botão."""
    settings = _settings(context)
    db = _db(context)
    record, created = _register(db, message.document, None, None)
    markup = build_markup(None, settings.miniapp_link(record.id), settings.miniapp_link_prefix)
    try:
        sent = await context.bot.send_document(
            chat_id=settings.channel_chat_id,
            document=message.document.file_id,
            caption=message.caption,
            caption_entities=message.caption_entities,
            reply_markup=markup,
        )
    except TelegramError as exc:
        logger.error("Falha ao publicar no canal: %s", exc)
        if "not enough rights" in str(exc).lower():
            reason = "o bot não tem permissão “Publicar mensagens” no canal."
        else:
            reason = explain_error(exc)
        await message.reply_text(f"❌ Não consegui publicar no canal: {reason}")
        return
    if created or record.message_id is None:
        db.update_location(record.id, sent.chat.id, sent.message_id)
    await message.reply_text("✅ PDF publicado no canal com o botão do leitor.")


async def on_private_forward_without_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is not None and _settings(context).is_owner(update.effective_user.id if update.effective_user else None):
        await message.reply_text("❌ Esse post não contém um PDF.")


HELP_TEXT = (
    "📖 <b>Leitor de PDF</b>\n\n"
    "Todo PDF publicado no canal ganha automaticamente o botão <b>“📖 Abrir no leitor”</b>, "
    "que abre o arquivo aqui dentro do Telegram (com zoom, busca, ir para página e download).\n\n"
    "No leitor você também pode abrir um PDF do seu aparelho pelo ícone de pasta."
)

OWNER_HELP = (
    "\n\n<b>Comandos do dono</b>\n"
    "• <b>Encaminhe</b> para mim um post antigo do canal com PDF → eu adiciono o botão ao post original.\n"
    "• <b>Envie</b> um PDF diretamente para mim → eu publico no canal já com o botão.\n"
    "• /status — quantidade de PDFs registrados e uso do cache.\n"
    "• /recentes — últimos PDFs registrados com seus links.\n"
    "• /ajuda — esta mensagem."
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = HELP_TEXT
    if _settings(context).is_owner(user.id if user else None):
        text += OWNER_HELP
    elif user is not None:
        text += f"\n\nSeu ID de usuário: <code>{user.id}</code>"
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def _require_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if _settings(context).is_owner(user.id if user else None):
        return True
    await update.effective_message.reply_text("🔒 Comando disponível apenas para o dono do bot.")
    return False


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_owner(update, context):
        return
    cache = context.application.bot_data.get("cache")
    cache_mb = cache.total_size() / 1024 / 1024 if cache is not None else 0.0
    settings = _settings(context)
    await update.effective_message.reply_text(
        f"📊 PDFs registrados: <b>{_db(context).count()}</b>\n"
        f"💾 Cache: {cache_mb:.1f} MB de {settings.cache_max_bytes / 1024 / 1024:.0f} MB\n"
        f"📢 Canal: <code>{html.escape(settings.channel_id)}</code>\n"
        f"🔐 Acesso: {'restrito a ' + str(len(settings.allowed_user_ids)) + ' usuário(s)' if settings.allowed_user_ids else 'qualquer pessoa com o link'}",
        parse_mode=ParseMode.HTML,
    )


async def cmd_recent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_owner(update, context):
        return
    records = _db(context).recent(10)
    if not records:
        await update.effective_message.reply_text("Nenhum PDF registrado ainda.")
        return
    settings = _settings(context)
    lines = [
        f"• <a href=\"{settings.miniapp_link(r.id)}\">{html.escape(r.file_name)}</a>" for r in records
    ]
    await update.effective_message.reply_text(
        "🗂 <b>Últimos PDFs</b>\n" + "\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Erro não tratado no bot", exc_info=context.error)


async def _post_init(application: Application) -> None:
    try:
        await application.bot.set_my_commands(
            [BotCommand("start", "Como usar o leitor"), BotCommand("ajuda", "Ajuda")]
        )
    except TelegramError as exc:
        logger.warning("Não consegui registrar os comandos: %s", exc)


def build_application(settings: Settings, db: Database, extra_data: dict[str, Any] | None = None) -> Application:
    application = ApplicationBuilder().token(settings.bot_token).post_init(_post_init).build()
    application.bot_data["settings"] = settings
    application.bot_data["db"] = db
    if extra_data:
        application.bot_data.update(extra_data)

    private = filters.ChatType.PRIVATE
    application.add_handler(CommandHandler(["start", "ajuda", "help"], cmd_start, filters=private))
    application.add_handler(CommandHandler("status", cmd_status, filters=private))
    application.add_handler(CommandHandler("recentes", cmd_recent, filters=private))
    application.add_handler(
        MessageHandler(filters.UpdateType.CHANNEL_POST & filters.Document.ALL, on_channel_post)
    )
    application.add_handler(MessageHandler(private & filters.Document.ALL, on_private_document))
    application.add_handler(
        MessageHandler(private & filters.FORWARDED & ~filters.Document.ALL, on_private_forward_without_pdf)
    )
    application.add_error_handler(on_error)
    return application
