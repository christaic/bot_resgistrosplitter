import os
import io
import json
import uuid
import logging
from datetime import datetime
from pytz import timezone
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, filters
)
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from telegram.error import BadRequest



# ================== CONFIGURACIÓN ==================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
NOMBRE_CARPETA_DRIVE = "REPORTE_SPLITTERS_SGA"
SHARED_DRIVE_ID = "0AGwYd9KBTiYXUk9PVA"

SHEET_ID = "1Er9RvzWsC3nfVPUDRLo2bY0HUyuOw9davySdVT_ymUQ"

USUARIOS_DEV = {7175478712,798153777}
GRUPO_SUPERVISION_ID = [-4949670947]

CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")

# ================== GOOGLE SHEETS ==================

try:
    # 🔹 Intenta cargar desde variable de entorno
    if os.getenv("GOOGLE_CREDENTIALS_JSON"):
        creds_dict = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))
        print("✅ Credenciales cargadas desde variable de entorno.")
    else:
        # 🔹 Si no existe, intenta cargar desde archivo local (modo desarrollo)
        with open("credentials.json", "r") as f:
            creds_dict = json.load(f)
        print("✅ Credenciales cargadas desde archivo local.")
except Exception as e:
    raise RuntimeError(f"❌ No se pudo cargar las credenciales: {e}")

scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
gc = gspread.authorize(creds)
drive_service = build("drive", "v3", credentials=creds)

# ================== SHEET ==================
sh = gc.open_by_key(SHEET_ID)
worksheet = sh.sheet1

ENCABEZADOS = [
    "FECHA", "HORA", "USER_ID", "ID_REGISTRO",
    "TICKET", "DNI", "NOMBRE",
    "LAT_CLIENTE", "LNG_CLIENTE","TIPO_CTO",
    "CODIGO_CTO", "LAT_CTO", "LNG_CTO",
    "FOTO_CTO", "SPLITTER", "PUERTO", "FOTO_SPLITTER"
]

if not worksheet.get_all_values():
    worksheet.append_row(ENCABEZADOS)

# ================== LOGGING ==================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================== PASOS ==================
PASOS = {
    "TICKET": {
        "tipo": "texto",
        "mensaje": "🎫 Ingrese el número de TICKET:"
    },
    "DNI": {
        "tipo": "texto",
        "mensaje": "🪪 Ingrese el DNI del cliente:"
    },
    "NOMBRE": {
        "tipo": "texto",
        "mensaje": "👤 Ingrese el nombre del cliente:"
    },
    "UBICACION_CLIENTE": {
        "tipo": "ubicacion",
        "mensaje": "📍 Envíe la ubicación del cliente:",
        "lat_key": "LAT_CLIENTE",
        "lng_key": "LNG_CLIENTE"
    },
    "TIPO_CAJA": {
        "tipo": "boton",
        "mensaje": "🟠 Seleccione el tipo de caja que está registrando:",
    },
    "CODIGO_CTO": {
        "tipo": "texto",
        "mensaje": "🏷 Ingrese el código de la CTO/NAP:"
    },
    "UBICACION_CTO": {
        "tipo": "ubicacion",
        "mensaje": "📍 Envíe la ubicación de la CTO/NAP:",
        "lat_key": "LAT_CTO",
        "lng_key": "LNG_CTO"
    },
    "FOTO_CTO": {
        "tipo": "foto",
        "mensaje": "📸 Envíe la foto de la CTO o NAP:"
    },
    "USO_SPLITTER": {
        "tipo": "boton",
        "mensaje": "✏️ Confirme el uso de splitter, porfavor:"
    },
    "PUERTO": {
        "tipo": "texto",
        "mensaje": "🔢 Ingrese el puerto donde se usó el splitter:"
    },
    "FOTO_SPLITTER": {
        "tipo": "foto",
        "mensaje": "📸 Envíe la foto de CTO con splitter donde se vea el puerto:"
    },
}

PASOS_LISTA = list(PASOS.keys())

# ================== ETIQUETAS LIMPIAS ==================
ETIQUETAS = {
    "TICKET": "🎫 Ticket",
    "DNI": "🪪 DNI",
    "NOMBRE": "👤 Nombre del Cliente",
    "UBICACION_CLIENTE": "📍 Ubicación Cliente",
    "TIPO_CAJA": "🟠 Tipo de Caja",   # 👈 NUEVA ETIQUETA AÑADIDA
    "CODIGO_CTO": "🏷 Código CTO/NAP",
    "UBICACION_CTO": "📍 Ubicación CTO/NAP",
    "FOTO_CTO": "📸 Foto CTO/NAP",
    "SPLITTER": "🔌 Uso de Splitter",
    "PUERTO": "🔢 Puerto",
    "FOTO_SPLITTER": "📸 Foto Splitter"
}


# ========= CREAR CARPETA ========

def get_or_create_folder(nombre, parent_id=None):
    """Busca o crea carpeta en Drive (unidad compartida incluida)."""
    query = f"name='{nombre}' and mimeType='application/vnd.google-apps.folder'"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    results = drive_service.files().list(
        q=query,
        spaces="drive",
        fields="files(id, name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]

    # Crear si no existe
    metadata = {
        "name": nombre,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id] if parent_id else [],
    }
    folder = drive_service.files().create(
        body=metadata,
        fields="id",
        supportsAllDrives=True
    ).execute()
    return folder["id"]


# ================== UTILS ==================
def get_fecha_hora():
    lima = timezone("America/Lima")
    now = datetime.now(lima)
    return now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S")


def upload_to_drive(file_bytes, filename, mime_type="image/jpeg"):
    """Sube un archivo a la carpeta IMAGENES_SPLITTERS en el Drive compartido y devuelve el link público."""
    media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime_type, resumable=True)
    file_metadata = {"name": filename, "parents": [CARPETA_IMAGENES_ID]}
    
    file = drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id",
        supportsAllDrives=True
    ).execute()

    file_id = file.get("id")

    # Dar permisos de lectura pública
    drive_service.permissions().create(
        fileId=file_id,
        body={"role": "reader", "type": "anyone"},
        supportsAllDrives=True
    ).execute()

    return f"https://drive.google.com/uc?id={file_id}"

# ========= CREAR CARPETAS EN DRIVE =========
CARPETA_BASE_ID = get_or_create_folder("REPORTE_SPLITTERS_SGA", parent_id=SHARED_DRIVE_ID)
CARPETA_IMAGENES_ID = get_or_create_folder("IMAGENES_SPLITTERS", parent_id=CARPETA_BASE_ID)

# =================== NUEVO START ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id  # 👈 aquí definimos chat_id
    # 🚫 Ignorar si es el grupo de supervisión
    if chat_id in GRUPO_SUPERVISION_ID:
        return ConversationHandler.END

    registro = context.user_data.get("registro", {})

    if registro.get("ACTIVO", False):
        # ⚠️ Registro activo → comportarse como /registro
        paso_actual = registro.get("PASO_ACTUAL", "TICKET")
        await update.message.reply_text(
            f"⚠️ Ya tienes un registro en curso.\n\n"
            f"📌 Estás en el paso: *{ETIQUETAS.get(paso_actual, paso_actual)}*.\n\n"
            f"👉 Responde lo solicitado o usa /cancel para anular tu registro.",
            parse_mode="Markdown"
        )
        return paso_actual

    # ✅ Si no hay registro activo, mostrar instrucciones
    instrucciones = (
        "👋 *Bienvenido al Bot para Registro de Splitters*\n\n"
        "👉 Usa /registro para iniciar un nuevo registro.\n"
        "👉 Usa /cancel para cancelar un registro en curso.\n\n"
        "‼️ Importante: si ya tienes un registro activo, no podrás iniciar otro."
    )
    await update.message.reply_text(instrucciones, parse_mode="Markdown")
    return "TICKET"  # o paso_actual si lo quieres dinámico

# ================== REGISTRO ==================
async def registro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # 🚫 Ignorar si es el grupo de supervisión
    if chat_id in GRUPO_SUPERVISION_ID:
        return ConversationHandler.END

    # 🚫 Si ya tiene un registro activo
    if "registro" in context.user_data and context.user_data["registro"].get("ACTIVO", False):
        registro = context.user_data["registro"]
        paso_actual = registro.get("PASO_ACTUAL", "TICKET")
        await update.message.reply_text(
            f"⚠️ Ya tienes un registro en curso.\n\n"
            f"📌 Estás en el paso: *{ETIQUETAS.get(paso_actual, paso_actual)}*.\n\n"
            f"👉 Responde lo solicitado o usa /cancel para anular tu registro.",
            parse_mode="Markdown"
        )
        return paso_actual

    # ✅ Crear nuevo registro
    context.user_data["registro"] = {
        "USER_ID": user_id,
        "ID_REGISTRO": str(uuid.uuid4())[:8],
        "ACTIVO": True,
        "PASO_ACTUAL": "TICKET"  # 👈 añadimos esto para que /start sepa en qué paso estamos
    }
    await update.message.reply_text(PASOS["TICKET"]["mensaje"])
    return "TICKET"

# ================== CALLBACK PARA GESTIONAR CHOQUE ==================
async def registro_activo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "CONTINUAR_REGISTRO":
        registro = context.user_data.get("registro", {})
        paso_actual = registro.get("PASO_ACTUAL", "TICKET")
        await query.edit_message_text(f"✅ Continuando desde {paso_actual}...")
        await context.bot.send_message(query.message.chat.id, PASOS[paso_actual]["mensaje"])
        return paso_actual

    elif query.data == "CANCELAR_REGISTRO":
        context.user_data.pop("registro", None)
        await query.edit_message_text("❌ Registro anterior cancelado. Inicia uno nuevo con /start")
        return ConversationHandler.END


# ================== HANDLER GENÉRICO ==================
async def manejar_paso(update: Update, context: ContextTypes.DEFAULT_TYPE, paso: str):
    chat_id = update.effective_chat.id
    if chat_id in GRUPO_SUPERVISION_ID:
        return ConversationHandler.END  # ❌ Ignorar todo en el grupo supervisión

    paso_cfg = PASOS[paso]
    registro = context.user_data["registro"]

    # --- Validación según tipo ---
    if paso_cfg["tipo"] == "texto":
        if not update.message.text:
            await update.message.reply_text("⚠️ Solo se acepta texto.")
            return paso
        registro[paso] = update.message.text

    elif paso_cfg["tipo"] == "ubicacion":
        if not update.message.location:
            await update.message.reply_text("⚠️ Debe enviar una ubicación válida.")
            return paso
        registro[paso_cfg["lat_key"]] = update.message.location.latitude
        registro[paso_cfg["lng_key"]] = update.message.location.longitude

    elif paso_cfg["tipo"] == "foto":
        if not update.message.photo:
            await update.message.reply_text("⚠️ Debe enviar una foto.")
            return paso
        photo = update.message.photo[-1]
        file = await photo.get_file()
        file_bytes = await file.download_as_bytearray()
        link = upload_to_drive(file_bytes, f"{paso}_{registro['ID_REGISTRO']}.jpg")
        registro[paso] = link

    # ==================================================
    # 🔹 Caso especial: corrección desde RESUMEN FINAL
    # ==================================================
    if registro.get("CORRIGIENDO") == paso:
        registro.pop("CORRIGIENDO")
        if registro.get("DESDE_RESUMEN", False):
            registro.pop("DESDE_RESUMEN")
            registro["PASO_ACTUAL"] = "RESUMEN_FINAL"
            logger.info(f"✏️ Corrección de {paso} hecha desde RESUMEN FINAL.")
            return await mostrar_resumen_final(update, context)

    # ==================================================
    # 🔹 Flujo normal (NO corrección desde resumen)
    # ==================================================
    keyboard = [
        [InlineKeyboardButton("✅ Confirmar", callback_data=f"CONFIRMAR_{paso}"),
         InlineKeyboardButton("✏️ Corregir", callback_data=f"CORREGIR_{paso}")]
    ]

    valor_visible = registro.get(paso, "")
    if paso_cfg["tipo"] == "foto":
        valor_visible = "📸 Foto recibida correctamente"
    
    etiqueta = ETIQUETAS.get(paso, paso)
    await update.message.reply_text(
        f"📌 Has registrado {etiqueta}: {valor_visible}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    registro["PASO_ACTUAL"] = paso
    logger.info(f"📌 Paso actual actualizado a: {paso}")
    return "CONFIRMAR"

# ================== CALLBACKS ==================

async def tipo_caja_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guarda el tipo de caja (CTO o NAP) seleccionado por el técnico"""
    query = update.callback_query
    await query.answer()

    tipo = "CTO" if query.data == "TIPO_CTO" else "NAP"
    registro = context.user_data["registro"]
    registro["TIPO_CAJA"] = tipo

    # ✅ Borramos mensaje anterior y mostramos confirmación con botones
    keyboard = [
        [
            InlineKeyboardButton("✅ Confirmar", callback_data=f"CONFIRMAR_TIPO_CAJA"),
            InlineKeyboardButton("✏️ Corregir", callback_data=f"CORREGIR_TIPO_CAJA"),
        ]
    ]

    texto_confirmacion = f"🟠 Has seleccionado: *{tipo}*"
    await query.edit_message_text(
        texto_confirmacion,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    registro["PASO_ACTUAL"] = "TIPO_CAJA"
    return "CONFIRMAR"


# ================== CONFIRMAR CALLBACK ==================
async def confirmar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    paso = query.data.replace("CONFIRMAR_", "")
    registro = context.user_data["registro"]
    valor = registro.get(paso, "")

    etiqueta = ETIQUETAS.get(paso, paso)
    await query.answer("⏳ Procesando...")

    # ==========================================
    # 🔹 MOSTRAR CONFIRMACIÓN SEGÚN TIPO DE PASO
    # ==========================================

    if paso.startswith("FOTO_"):
        try:
            await query.edit_message_text(f"✅ {etiqueta} confirmado correctamente.")
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise

    elif paso.startswith("UBICACION_"):
        lat = registro.get("LAT_CLIENTE" if paso == "UBICACION_CLIENTE" else "LAT_CTO")
        lng = registro.get("LNG_CLIENTE" if paso == "UBICACION_CLIENTE" else "LNG_CTO")
        try:
            await query.edit_message_text(f"✅ {etiqueta} confirmado: ({lat}, {lng})")
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise

    elif paso == "TIPO_CAJA":
        tipo = registro.get("TIPO_CAJA", "")
        try:
            await query.edit_message_text(f"✅ Tipo de caja confirmado: *{tipo}*", parse_mode="Markdown")
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise

    else:
        try:
            await query.edit_message_text(f"✅ {etiqueta} confirmado: {valor}")
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise

    # ======================================================
    # 🔹 SI LA CONFIRMACIÓN VIENE DESDE EL RESUMEN FINAL
    # ======================================================
    if registro.get("DESDE_RESUMEN", False):
        registro["CORRIGIENDO_ULTIMO"] = paso  # 👈 Campo corregido para resaltarlo
        registro.pop("DESDE_RESUMEN", None)
        registro.pop("CORRIGIENDO", None)
        registro["PASO_ACTUAL"] = "RESUMEN_FINAL"

        # ✏️ Mensaje informativo opcional
        await context.bot.send_message(
            chat_id=query.message.chat.id,
            text="✏️ Campo actualizado correctamente, mostrando resumen actualizado..."
        )

        return await mostrar_resumen_final(update, context)

    # ======================================================
    # 🔹 FLUJO NORMAL (NO DESDE RESUMEN)
    # ======================================================
    idx = PASOS_LISTA.index(paso)
    if idx + 1 < len(PASOS_LISTA):
        siguiente = PASOS_LISTA[idx + 1]
        registro["PASO_ACTUAL"] = siguiente

        if PASOS[siguiente]["tipo"] == "boton":
            if siguiente == "TIPO_CAJA":
                keyboard = [
                    [
                        InlineKeyboardButton("🟦 CTO", callback_data="TIPO_CTO"),
                        InlineKeyboardButton("🟩 NAP", callback_data="TIPO_NAP"),
                    ]
                ]
                await context.bot.send_message(
                    query.message.chat.id,
                    PASOS[siguiente]["mensaje"],
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return "TIPO_CAJA"

            elif siguiente == "USO_SPLITTER":
                keyboard = [[InlineKeyboardButton("✅ Confirmar", callback_data="SPLITTER_SI")]]
                await context.bot.send_message(
                    query.message.chat.id,
                    PASOS[siguiente]["mensaje"],
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return "USO_SPLITTER"

        # Si el siguiente paso es texto, ubicación o foto
        else:
            await context.bot.send_message(query.message.chat.id, PASOS[siguiente]["mensaje"])
            return siguiente

    # Si no hay más pasos → mostrar resumen final
    else:
        return await mostrar_resumen_final(update, context)


async def corregir_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in GRUPO_SUPERVISION_ID:
        return ConversationHandler.END  # ❌ Ignorar en grupo supervisión

    query = update.callback_query
    paso = query.data.replace("CORREGIR_", "")
    await query.answer("✏️ Corrigiendo...")

    # Guardamos el paso que quiere corregir
    context.user_data["registro"]["CORRIGIENDO"] = paso

    # 🔹 Si es el tipo de caja → mostrar nuevamente botonera CTO/NAP
    if paso == "TIPO_CAJA":
        keyboard = [
            [
                InlineKeyboardButton("🟦 CTO", callback_data="TIPO_CTO"),
                InlineKeyboardButton("🟩 NAP", callback_data="TIPO_NAP"),
            ]
        ]
        await query.edit_message_text(
            "🟠 Seleccione nuevamente el tipo de caja:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return "TIPO_CAJA"

    # 🔹 Para los demás pasos → mensaje estándar
    mensaje = PASOS[paso]["mensaje"] if paso in PASOS else f"✏️ Ingresa el valor para {paso}:"
    await query.edit_message_text(mensaje)

    return paso


async def uso_splitter_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in GRUPO_SUPERVISION_ID:
        return ConversationHandler.END  # ❌ Ignorar en grupo supervisión

    query = update.callback_query
    await query.answer("⏳ Procesando...")

    decision = "SI" if query.data == "SPLITTER_SI" else "NO"
    context.user_data["registro"]["SPLITTER"] = decision

    # Editar el mensaje con respuesta clara y sin botones
    texto = f"🔌 ¿Se confirmo el uso de splitter?: {'✅ Sí' if decision == 'SI' else '❌ No'}"
    await query.edit_message_text(texto)

    if decision == "SI":
        # ✅ Guardamos el paso actual en el registro
        context.user_data["registro"]["PASO_ACTUAL"] = "PUERTO"
        await context.bot.send_message(query.message.chat.id, PASOS["PUERTO"]["mensaje"])
        return "PUERTO"
    else:
        # ⚠️ Si no hay splitter, guardamos el estado como RESUMEN
        context.user_data["registro"]["PASO_ACTUAL"] = "RESUMEN_FINAL"
        return await mostrar_resumen_registro(update, context)

# ================== GUARDAR EN SHEETS ==================
async def guardar_registro(update, context):
    data = context.user_data["registro"]
    fecha, hora = get_fecha_hora()
    data["FECHA"] = fecha
    data["HORA"] = hora

    fila = [
        data.get("FECHA", ""), data.get("HORA", ""), data.get("USER_ID", ""), data.get("ID_REGISTRO", ""),
        data.get("TICKET", ""), data.get("DNI", ""), data.get("NOMBRE", ""),
        data.get("LAT_CLIENTE", ""), data.get("LNG_CLIENTE", ""),
        data.get("TIPO_CAJA", ""),  # 👈 Nuevo valor
        data.get("CODIGO_CTO", ""), data.get("LAT_CTO", ""), data.get("LNG_CTO", ""),
        data.get("FOTO_CTO", ""),  # Link Drive
        data.get("SPLITTER", "NO"),
        data.get("PUERTO", ""),
        data.get("FOTO_SPLITTER", "")
    ]
    worksheet.append_row(fila)

    # ✅ Resumen limpio
    resumen_final = f"✅ *Registro guardado exitosamente*\n\n"
    resumen_final += f"{ETIQUETAS['NOMBRE']}: {data.get('NOMBRE','')}\n"
    resumen_final += f"{ETIQUETAS['TICKET']}: {data.get('TICKET','')}\n"
    resumen_final += f"{ETIQUETAS['DNI']}: {data.get('DNI','')}\n"
    # Coordenadas cliente
    if data.get("LAT_CLIENTE") and data.get("LNG_CLIENTE"):
        resumen_final += f"{ETIQUETAS['UBICACION_CLIENTE']}: ({data['LAT_CLIENTE']}, {data['LNG_CLIENTE']})\n"
    # CTO
    resumen_final += f"{ETIQUETAS['CODIGO_CTO']}: {data.get('CODIGO_CTO','')}\n"
    if data.get("LAT_CTO") and data.get("LNG_CTO"):
        resumen_final += f"{ETIQUETAS['UBICACION_CTO']}: ({data['LAT_CTO']}, {data['LNG_CTO']})\n"
    # Splitter
    resumen_final += f"{ETIQUETAS['SPLITTER']}: {data.get('SPLITTER','NO')} | {ETIQUETAS['PUERTO']}: {data.get('PUERTO','-')}\n"
    # Fotos
    fotos_txt = []
    if data.get("FOTO_CTO"):
        fotos_txt.append("CTO")
    if data.get("FOTO_SPLITTER"):
        fotos_txt.append("Splitter")
    if fotos_txt:
        resumen_final += f"📸 Fotos: {', '.join(fotos_txt)} guardadas correctamente\n"
    # 👤 Enviar al técnico
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=resumen_final,
        parse_mode="Markdown"
    )

    # 💬 Mensaje adicional nuevo registro
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            "💬 Recuerda que para iniciar un nuevo registro debes escribir /start.\n\n"
            "🤖 Solo así podre ayudarte. 💪"
        ),
        parse_mode="Markdown"
    )

    # 📢 Enviar también al grupo de supervisión
    for grupo_id in GRUPO_SUPERVISION_ID:
        try:
            await context.bot.send_message(chat_id=grupo_id, text=resumen_final, parse_mode="Markdown")

            if data.get("FOTO_CTO"):
                await context.bot.send_photo(chat_id=grupo_id, photo=data["FOTO_CTO"], caption="📸 CTO")
            if data.get("FOTO_SPLITTER"):
                await context.bot.send_photo(chat_id=grupo_id, photo=data["FOTO_SPLITTER"], caption="📸 Splitter")

        except Exception as e:
            logger.error(f"❌ Error enviando al grupo {grupo_id}: {e}")

    # Limpiar completamente el registro al guardar
    context.user_data.pop("registro", None)
    return ConversationHandler.END

# ================== MOSTRAR RESUMEN FINAL ==================
async def mostrar_resumen_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el resumen final del registro con opciones Guardar/Corregir/Cancelar"""
    registro = context.user_data.get("registro", {})
    paso_corregido = registro.get("CORRIGIENDO_ULTIMO", None)  # 👈 Campo corregido recientemente

    # Texto base del resumen
    resumen = (
        f"📋 *Resumen del registro*\n\n"
        f"🎫 Ticket: {registro.get('TICKET','')}\n"
        f"🪪 DNI: {registro.get('DNI','')}\n"
        f"👤 Cliente: {registro.get('NOMBRE','')}\n"
        f"📍 Cliente: ({registro.get('LAT_CLIENTE','')}, {registro.get('LNG_CLIENTE','')})\n"
        f"🟠 Tipo de caja: {registro.get('TIPO_CAJA','-')}\n"
        f"🏷 CTO: {registro.get('CODIGO_CTO','')}\n"
        f"📍 CTO: ({registro.get('LAT_CTO','')}, {registro.get('LNG_CTO','')})\n"
        f"🔌 Splitter: {registro.get('SPLITTER','NO')} | Puerto: {registro.get('PUERTO','-')}\n"
        f"📸 Fotos: {'✅' if registro.get('FOTO_CTO') else '❌'} CTO / "
        f"{'✅' if registro.get('FOTO_SPLITTER') else '❌'} Splitter"
    )

    # ✏️ Si viene de corrección, añadir aviso visual arriba del resumen
    if paso_corregido:
        etiqueta = ETIQUETAS.get(paso_corregido, paso_corregido)
        resumen = f"✏️ *{etiqueta} actualizado correctamente.*\n\n" + resumen
        registro.pop("CORRIGIENDO_ULTIMO", None)

    # Botones de acción
    keyboard = [
        [InlineKeyboardButton("✅ Guardar Registro", callback_data="FINAL_GUARDAR")],
        [InlineKeyboardButton("✏️ Corregir", callback_data="FINAL_CORREGIR")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="FINAL_CANCELAR")]
    ]

    # Mostrar resumen reemplazando el mensaje anterior
    if update.callback_query:
        query = update.callback_query
        await query.edit_message_text(
            resumen,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            resumen,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    return "RESUMEN_FINAL"


# ================== CALLBACK FINAL ==================
async def resumen_final_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    accion = query.data

    if accion == "FINAL_GUARDAR":
        await query.answer("⏳ Guardando registro...")
        await query.edit_message_text("✅ Registro guardado, generando resumen final...")
        return await guardar_registro(update, context)

    elif accion == "FINAL_CORREGIR":
        await query.answer("✏️ Selecciona qué campo corregir")

        # Botonera con todos los campos corregibles, incluyendo tipo de caja
        keyboard = [
            [InlineKeyboardButton("🎫 Ticket", callback_data="CORREGIR_TICKET"),
             InlineKeyboardButton("🪪 DNI", callback_data="CORREGIR_DNI")],
            [InlineKeyboardButton("👤 Nombre", callback_data="CORREGIR_NOMBRE"),
             InlineKeyboardButton("📍 Cliente", callback_data="CORREGIR_UBICACION_CLIENTE")],
            [InlineKeyboardButton("🟠 Tipo de caja", callback_data="CORREGIR_TIPO_CAJA"),
             InlineKeyboardButton("🏷 CTO/NAP", callback_data="CORREGIR_CODIGO_CTO")],
            [InlineKeyboardButton("📍 Ubicación CTO", callback_data="CORREGIR_UBICACION_CTO"),
             InlineKeyboardButton("📸 Foto CTO", callback_data="CORREGIR_FOTO_CTO")],
            [InlineKeyboardButton("🔌 Puerto", callback_data="CORREGIR_PUERTO"),
             InlineKeyboardButton("📸 Foto Splitter", callback_data="CORREGIR_FOTO_SPLITTER")],
        ]

        await query.edit_message_text(
            "✏️ Selecciona el campo que deseas corregir:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return "CORREGIR_CAMPO"

    elif accion == "FINAL_CANCELAR":
        await query.answer("❌ Registro cancelado")
        await query.edit_message_text("❌ Registro cancelado por el usuario.")
        return ConversationHandler.END



# ================== CALLBACK DE CORRECCIÓN DE CAMPO ==================
async def corregir_campo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cuando selecciona qué campo corregir desde el resumen final"""
    query = update.callback_query
    paso = query.data.replace("CORREGIR_", "")  # ej. CORREGIR_DNI → "DNI"

    await query.answer("✏️ Corrigiendo...")

    # Guardamos el campo que se está corrigiendo
    context.user_data["registro"]["CORRIGIENDO"] = paso
    context.user_data["registro"]["DESDE_RESUMEN"] = True  # 👈 Marca que la corrección viene desde el resumen final

    # 🔹 Caso especial: Tipo de caja (CTO o NAP)
    if paso == "TIPO_CAJA":
        keyboard = [
            [
                InlineKeyboardButton("🟦 CTO", callback_data="TIPO_CTO"),
                InlineKeyboardButton("🟩 NAP", callback_data="TIPO_NAP"),
            ]
        ]
        await query.edit_message_text(
            "📦 Seleccione nuevamente el tipo de caja:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return "TIPO_CAJA"

    # 🔹 Para los demás pasos → mostrar su mensaje habitual
    if paso in PASOS:
        mensaje = PASOS[paso]["mensaje"]
    else:
        mensaje = f"✏️ Ingresa el valor para {ETIQUETAS.get(paso, paso)}:"

    # 🔹 Actualizar el mensaje del resumen final → pedir el nuevo valor
    try:
        await query.edit_message_text(mensaje)
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            raise

    # 👉 Retorna el paso para que el manejador correcto capture la respuesta
    return paso


# ================== CANCEL ==================
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("registro", None)  # ✅ Limpia cualquier registro activo
    await update.message.reply_text("❌ Registro cancelado.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

#============= RESUMEN REGISTRO ==============

async def mostrar_resumen_registro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data["registro"]

    resumen = (
        f"📋 *Resumen del registro*\n\n"
        f"👤 Cliente: {data.get('NOMBRE','')}\n"
        f"🎫 Ticket: {data.get('TICKET','')}\n"
        f"🪪 DNI: {data.get('DNI','')}\n"
        f"📍 Cliente: {data.get('LAT_CLIENTE','')}, {data.get('LNG_CLIENTE','')}\n"
        f"🟠 Tipo de Caja: {data.get('TIPO_CAJA','-')}\n"  # 👈 NUEVA LÍNEA AÑADIDA
        f"🏷 CTO/NAP: {data.get('CODIGO_CTO','')}\n"
        f"📍 CTO/NAP: {data.get('LAT_CTO','')}, {data.get('LNG_CTO','')}\n"
        f"🔌 Splitter: {data.get('SPLITTER','NO')} | Puerto: {data.get('PUERTO','-')}\n"
        f"📸 Fotos registradas correctamente."
    )

    # Botonera final
    keyboard = [
        [InlineKeyboardButton("✅ Guardar registro", callback_data="FINAL_GUARDAR")],
        [InlineKeyboardButton("✏️ Corregir dato", callback_data="FINAL_CORREGIR")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="FINAL_CANCELAR")]
    ]

    # 👉 Igual: editar mensaje si existe callback_query
    if update.callback_query:
        await update.callback_query.edit_message_text(
            resumen,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text(
            resumen,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    return "RESUMEN_FINAL"

# ================== MAIN ==================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("registro", registro)
        ],
        states={

            # ====== PASO 1: TICKET ======
            "TICKET": [
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: manejar_paso(u, c, "TICKET")),
                CommandHandler("start", start),
                CommandHandler("registro", registro),
            ],

            # ====== PASO 2: DNI ======
            "DNI": [
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: manejar_paso(u, c, "DNI")),
                CommandHandler("start", start),
                CommandHandler("registro", registro),
            ],

            # ====== PASO 3: NOMBRE ======
            "NOMBRE": [
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: manejar_paso(u, c, "NOMBRE")),
                CommandHandler("start", start),
                CommandHandler("registro", registro),
            ],

            # ====== PASO 4: UBICACIÓN CLIENTE ======
            "UBICACION_CLIENTE": [
                MessageHandler(filters.LOCATION, lambda u, c: manejar_paso(u, c, "UBICACION_CLIENTE")),
                CommandHandler("start", start),
                CommandHandler("registro", registro),
            ],

            # ====== PASO 5: TIPO DE CAJA (CTO/NAP) ======
            "TIPO_CAJA": [
                CallbackQueryHandler(tipo_caja_callback, pattern="^(TIPO_CTO|TIPO_NAP)$"),
                CommandHandler("start", start),
                CommandHandler("registro", registro),
            ],

            # ====== PASO 6: CÓDIGO CTO/NAP ======
            "CODIGO_CTO": [
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: manejar_paso(u, c, "CODIGO_CTO")),
                CommandHandler("start", start),
                CommandHandler("registro", registro),
            ],

            # ====== PASO 7: UBICACIÓN CTO ======
            "UBICACION_CTO": [
                MessageHandler(filters.LOCATION, lambda u, c: manejar_paso(u, c, "UBICACION_CTO")),
                CommandHandler("start", start),
                CommandHandler("registro", registro),
            ],

            # ====== PASO 8: FOTO CTO ======
            "FOTO_CTO": [
                MessageHandler(filters.PHOTO, lambda u, c: manejar_paso(u, c, "FOTO_CTO")),
                CommandHandler("start", start),
                CommandHandler("registro", registro),
            ],

            # ====== PASO 9: USO DE SPLITTER ======
            "USO_SPLITTER": [
                CallbackQueryHandler(uso_splitter_callback, pattern="^(SPLITTER_SI)$"),
                CommandHandler("start", start),
                CommandHandler("registro", registro),
            ],

            # ====== PASO 10: PUERTO ======
            "PUERTO": [
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: manejar_paso(u, c, "PUERTO")),
                CommandHandler("start", start),
                CommandHandler("registro", registro),
            ],

            # ====== PASO 11: FOTO SPLITTER ======
            "FOTO_SPLITTER": [
                MessageHandler(filters.PHOTO, lambda u, c: manejar_paso(u, c, "FOTO_SPLITTER")),
                CommandHandler("start", start),
                CommandHandler("registro", registro),
            ],

            # ====== PASO DE CONFIRMACIÓN GENERAL ======
            "CONFIRMAR": [
                CallbackQueryHandler(confirmar_callback, pattern="^CONFIRMAR_.*$"),
                CallbackQueryHandler(corregir_callback, pattern="^CORREGIR_.*$"),
                CommandHandler("start", start),
                CommandHandler("registro", registro),
            ],

            # ====== RESUMEN FINAL ======
            "RESUMEN_FINAL": [
                CallbackQueryHandler(resumen_final_callback, pattern="^FINAL_.*$"),
                CommandHandler("start", start),
                CommandHandler("registro", registro),
            ],

            # ====== CORRECCIÓN DESDE RESUMEN ======
            "CORREGIR_CAMPO": [
                CallbackQueryHandler(corregir_campo_callback, pattern="^CORREGIR_.*$"),
                CommandHandler("start", start),
                CommandHandler("registro", registro),
            ],
        },

        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    logger.info("🤖 Bot iniciado y escuchando...")
    app.run_polling()


if __name__ == "__main__":
    main()
