import os
import re
import html
import time
import threading
import requests
import yt_dlp

from flask import Flask, request, jsonify
from concurrent.futures import ThreadPoolExecutor


# =========================================================
# APLICAÇÃO
# =========================================================

app = Flask(__name__)


# =========================================================
# CONFIGURAÇÕES
# =========================================================

TELEGRAM_TOKEN = os.environ.get(
    "TELEGRAM_TOKEN",
    ""
)

API_SECRET = os.environ.get(
    "API_SECRET",
    ""
)

MAX_WORKERS = int(
    os.environ.get(
        "MAX_WORKERS",
        "3"
    )
)


# =========================================================
# FILA DE PROCESSAMENTO
# =========================================================

executor = ThreadPoolExecutor(
    max_workers=MAX_WORKERS
)


# Evita processar o mesmo update do Telegram
# duas vezes caso o webhook seja reenviado.

processed_jobs = {}
processed_jobs_lock = threading.Lock()

JOB_TTL_SECONDS = 3600


# =========================================================
# HEADERS
# =========================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": (
        "pt-BR,pt;q=0.9,"
        "en-US;q=0.8,en;q=0.7"
    ),
    "Accept": (
        "text/html,"
        "application/xhtml+xml,"
        "application/xml;q=0.9,"
        "image/avif,image/webp,"
        "*/*;q=0.8"
    ),
}


# =========================================================
# HOME
# =========================================================

@app.get("/")
def home():

    return jsonify({
        "status": "ok",
        "service": "video-downloader-bot",
        "mode": "async",
        "workers": MAX_WORKERS
    })


# =========================================================
# HEALTH
# =========================================================

@app.get("/health")
def health():

    return jsonify({
        "status": "healthy"
    })


# =========================================================
# LIMPA JOBS ANTIGOS
# =========================================================

def cleanup_processed_jobs():

    agora = time.time()

    with processed_jobs_lock:

        expirados = [
            job_id
            for job_id, timestamp
            in processed_jobs.items()
            if agora - timestamp > JOB_TTL_SECONDS
        ]

        for job_id in expirados:
            processed_jobs.pop(
                job_id,
                None
            )


# =========================================================
# MARCA JOB COMO PROCESSADO
# =========================================================

def register_job(job_id):

    if not job_id:
        return True

    cleanup_processed_jobs()

    with processed_jobs_lock:

        if job_id in processed_jobs:
            return False

        processed_jobs[job_id] = time.time()

    return True


# =========================================================
# VALIDA CHAVE DA API
# =========================================================

def valid_api_secret():

    if not API_SECRET:
        return True

    received_secret = request.headers.get(
        "X-API-KEY",
        ""
    )

    return received_secret == API_SECRET


# =========================================================
# IDENTIFICA SHOPEE
# =========================================================

def is_shopee(url):

    link = url.lower()

    return (
        "shopee." in link
        or "shp.ee" in link
        or "sv.shopee" in link
    )


# =========================================================
# RESOLVE LINK CURTO
# =========================================================

def resolve_url(url):

    response = requests.get(
        url,
        headers=HEADERS,
        allow_redirects=True,
        timeout=30
    )

    response.raise_for_status()

    return response.url


# =========================================================
# LIMPA URL DE MÍDIA
# =========================================================

def clean_media_url(value):

    if not value:
        return None

    value = html.unescape(
        value
    )

    value = value.replace(
        "\\u002F",
        "/"
    )

    value = value.replace(
        "\\/",
        "/"
    )

    value = value.replace(
        "\\u0026",
        "&"
    )

    if value.startswith("//"):
        value = "https:" + value

    return value


# =========================================================
# SHOPEE
# =========================================================

def extract_shopee_video(url):

    final_url = resolve_url(
        url
    )

    response = requests.get(
        final_url,
        headers=HEADERS,
        timeout=30
    )

    response.raise_for_status()

    page = response.text

    candidates = []


    # -----------------------------------------------------
    # URLs MP4
    # -----------------------------------------------------

    patterns = [
        r'https?://[^"\']+\.mp4[^"\']*',
        r'https?:\\?/\\?/[^"\']+\.mp4[^"\']*',
    ]

    for pattern in patterns:

        matches = re.findall(
            pattern,
            page,
            flags=re.IGNORECASE
        )

        for item in matches:

            media_url = clean_media_url(
                item
            )

            if (
                media_url
                and media_url not in candidates
            ):
                candidates.append(
                    media_url
                )


    # -----------------------------------------------------
    # CAMPOS JSON
    # -----------------------------------------------------

    json_patterns = [
        r'"videoUrl"\s*:\s*"([^"]+)"',
        r'"video_url"\s*:\s*"([^"]+)"',
        r'"playUrl"\s*:\s*"([^"]+)"',
        r'"play_url"\s*:\s*"([^"]+)"',
        r'"url"\s*:\s*"(https?:[^"]+\.mp4[^"]*)"',
        r'"src"\s*:\s*"(https?:[^"]+\.mp4[^"]*)"',
    ]

    for pattern in json_patterns:

        matches = re.findall(
            pattern,
            page,
            flags=re.IGNORECASE
        )

        for item in matches:

            media_url = clean_media_url(
                item
            )

            if (
                media_url
                and media_url not in candidates
            ):
                candidates.append(
                    media_url
                )


    # -----------------------------------------------------
    # TAG VIDEO
    # -----------------------------------------------------

    video_matches = re.findall(
        r'<video[^>]+src=["\']([^"\']+)["\']',
        page,
        flags=re.IGNORECASE
    )

    for item in video_matches:

        media_url = clean_media_url(
            item
        )

        if (
            media_url
            and media_url not in candidates
        ):
            candidates.append(
                media_url
            )


    # -----------------------------------------------------
    # TAG SOURCE
    # -----------------------------------------------------

    source_matches = re.findall(
        r'<source[^>]+src=["\']([^"\']+)["\']',
        page,
        flags=re.IGNORECASE
    )

    for item in source_matches:

        media_url = clean_media_url(
            item
        )

        if (
            media_url
            and media_url not in candidates
        ):
            candidates.append(
                media_url
            )


    # -----------------------------------------------------
    # VALIDA CANDIDATOS
    # -----------------------------------------------------

    valid_candidates = []

    for media_url in candidates:

        try:

            media_response = requests.get(
                media_url,
                headers={
                    **HEADERS,
                    "Referer": final_url
                },
                stream=True,
                timeout=15
            )

            content_type = (
                media_response.headers
                .get(
                    "content-type",
                    ""
                )
                .lower()
            )

            if (
                media_response.status_code < 400
                and (
                    "video" in content_type
                    or ".mp4" in media_url.lower()
                )
            ):

                valid_candidates.append(
                    media_url
                )

            media_response.close()

        except Exception as error:

            print(
                "Erro validando candidato:",
                error
            )


    if valid_candidates:

        return {
            "success": True,
            "platform": "Shopee",
            "url": valid_candidates[0],
            "resolved_url": final_url,
            "candidates_found": len(
                valid_candidates
            )
        }


    return {
        "success": False,
        "platform": "Shopee",
        "resolved_url": final_url,
        "candidates_found": len(
            candidates
        ),
        "error": (
            "A página foi aberta, "
            "mas não encontrei uma URL "
            "MP4 pública."
        )
    }


# =========================================================
# YT-DLP
# =========================================================

def extract_with_ytdlp(url):

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,

        # Prioriza arquivo único para conseguirmos
        # devolver uma URL direta.
        "format": (
            "best[ext=mp4]/"
            "best"
        ),
    }


    with yt_dlp.YoutubeDL(
        ydl_opts
    ) as ydl:

        info = ydl.extract_info(
            url,
            download=False
        )

        info = ydl.sanitize_info(
            info
        )


    video_url = info.get(
        "url"
    )


    # -----------------------------------------------------
    # FALLBACK PARA FORMATS
    # -----------------------------------------------------

    if not video_url:

        formats = (
            info.get("formats")
            or []
        )

        formatos_validos = []

        for item in formats:

            if not item.get("url"):
                continue

            vcodec = item.get(
                "vcodec"
            )

            if (
                vcodec
                and vcodec != "none"
            ):
                formatos_validos.append(
                    item
                )


        formatos_validos.sort(
            key=lambda x: (
                x.get("height") or 0,
                x.get("tbr") or 0
            ),
            reverse=True
        )


        if formatos_validos:

            video_url = (
                formatos_validos[0]
                .get("url")
            )


    if not video_url:

        return {
            "success": False,
            "error": (
                "O vídeo foi identificado, "
                "mas não encontrei uma URL "
                "de mídia."
            )
        }


    return {
        "success": True,
        "title": info.get(
            "title"
        ),
        "platform": (
            info.get("extractor_key")
            or info.get("extractor")
        ),
        "width": info.get(
            "width"
        ),
        "height": info.get(
            "height"
        ),
        "duration": info.get(
            "duration"
        ),
        "thumbnail": info.get(
            "thumbnail"
        ),
        "url": video_url
    }


# =========================================================
# EXTRATOR PRINCIPAL
# =========================================================

def extract_video(url):

    if is_shopee(url):
        return extract_shopee_video(
            url
        )

    return extract_with_ytdlp(
        url
    )


# =========================================================
# ENVIA MENSAGEM PARA TELEGRAM
# =========================================================

def send_telegram_message(
    chat_id,
    thread_id,
    text,
    disable_preview=True
):

    if not TELEGRAM_TOKEN:

        print(
            "TELEGRAM_TOKEN não configurado."
        )

        return False


    endpoint = (
        "https://api.telegram.org/bot"
        + TELEGRAM_TOKEN
        + "/sendMessage"
    )


    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": (
            disable_preview
        )
    }


    if thread_id:

        try:
            payload[
                "message_thread_id"
            ] = int(thread_id)

        except Exception:
            pass


    try:

        response = requests.post(
            endpoint,
            json=payload,
            timeout=20
        )


        print(
            "TELEGRAM HTTP:",
            response.status_code
        )


        print(
            "TELEGRAM:",
            response.text
        )


        return response.ok


    except Exception as error:

        print(
            "ERRO TELEGRAM:",
            error
        )

        return False


# =========================================================
# FORMATA DURAÇÃO
# =========================================================

def format_duration(seconds):

    if not seconds:
        return None

    try:

        seconds = int(
            float(seconds)
        )

    except Exception:

        return None


    minutes = seconds // 60

    remaining = seconds % 60


    return (
        f"{minutes}:"
        f"{remaining:02d}"
    )


# =========================================================
# PROCESSAMENTO EM BACKGROUND
# =========================================================

def process_job(data):

    url = data.get(
        "url"
    )

    chat_id = data.get(
        "chat_id"
    )

    thread_id = data.get(
        "thread_id"
    )

    platform_received = data.get(
        "platform",
        "Vídeo"
    )


    print(
        "INICIANDO JOB:",
        url
    )


    # -----------------------------------------------------
    # TENTA ATÉ 3 VEZES
    # -----------------------------------------------------

    result = None

    last_error = None


    for attempt in range(
        1,
        4
    ):

        try:

            print(
                f"Tentativa {attempt}/3"
            )


            result = extract_video(
                url
            )


            if (
                result
                and result.get("success")
                and result.get("url")
            ):
                break


            last_error = (
                result.get("error")
                if result
                else "Erro desconhecido."
            )


        except Exception as error:

            last_error = str(
                error
            )

            print(
                "ERRO EXTRAÇÃO:",
                last_error
            )


        if attempt < 3:

            time.sleep(
                4
            )


    # -----------------------------------------------------
    # FALHOU
    # -----------------------------------------------------

    if (
        not result
        or not result.get("success")
        or not result.get("url")
    ):

        send_telegram_message(
            chat_id,
            thread_id,

            "❌ Não consegui localizar o vídeo.\n\n"
            + (
                last_error
                or "Erro desconhecido."
            )
        )

        return


    # -----------------------------------------------------
    # SUCESSO
    # -----------------------------------------------------

    platform = (
        result.get("platform")
        or platform_received
    )


    message = (
        "✅ Vídeo encontrado!\n\n"
        f"📱 Plataforma: {platform}"
    )


    title = result.get(
        "title"
    )

    if title:

        message += (
            "\n\n🎬 "
            + str(title)
        )


    height = result.get(
        "height"
    )

    if height:

        message += (
            "\n\n📺 Qualidade: "
            + str(height)
            + "p"
        )


    duration = format_duration(
        result.get(
            "duration"
        )
    )

    if duration:

        message += (
            "\n\n⏱ Duração: "
            + duration
        )


    message += (
        "\n\n📥 BAIXAR VÍDEO:\n"
        + result["url"]
    )


    send_telegram_message(
        chat_id,
        thread_id,
        message
    )


    print(
        "JOB FINALIZADO:",
        url
    )


# =========================================================
# ENDPOINT ASSÍNCRONO
#
# Apps Script chama esse endpoint.
# Ele coloca o vídeo na fila e responde IMEDIATAMENTE.
# =========================================================

@app.post("/enqueue")
def enqueue():

    if not valid_api_secret():

        return jsonify({
            "success": False,
            "error": "Não autorizado."
        }), 401


    data = (
        request.get_json(
            silent=True
        )
        or {}
    )


    url = data.get(
        "url"
    )

    chat_id = data.get(
        "chat_id"
    )

    thread_id = data.get(
        "thread_id"
    )

    job_id = str(
        data.get(
            "job_id",
            ""
        )
    )


    if not url:

        return jsonify({
            "success": False,
            "error": "URL não informada."
        }), 400


    if not chat_id:

        return jsonify({
            "success": False,
            "error": "chat_id não informado."
        }), 400


    # -----------------------------------------------------
    # EVITA JOB DUPLICADO
    # -----------------------------------------------------

    if not register_job(
        job_id
    ):

        return jsonify({
            "success": True,
            "queued": False,
            "duplicate": True,
            "job_id": job_id
        }), 200


    # -----------------------------------------------------
    # COLOCA NA FILA
    # -----------------------------------------------------

    executor.submit(
        process_job,
        data.copy()
    )


    # IMPORTANTE:
    # não espera o vídeo ser processado.

    return jsonify({
        "success": True,
        "queued": True,
        "job_id": job_id,
        "message": (
            "Vídeo adicionado "
            "à fila."
        )
    }), 202


# =========================================================
# EXTRACT SINCRONO
#
# Mantemos para teste manual.
# =========================================================

@app.post("/extract")
def extract():

    try:

        data = (
            request.get_json(
                silent=True
            )
            or {}
        )

        url = data.get(
            "url"
        )


        if not url:

            return jsonify({
                "success": False,
                "error": (
                    "URL não informada."
                )
            }), 400


        result = extract_video(
            url
        )


        status_code = (
            200
            if result.get("success")
            else 422
        )


        return jsonify(
            result
        ), status_code


    except Exception as error:

        return jsonify({
            "success": False,
            "error": str(error)
        }), 500


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )


    app.run(
        host="0.0.0.0",
        port=port,
        threaded=True
    )
