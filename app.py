import os
import re
import html
import time
import sqlite3
import tempfile
import threading
import requests
import yt_dlp

from flask import Flask, request, jsonify
from concurrent.futures import ThreadPoolExecutor


# =========================================================
# APP
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

MAX_TELEGRAM_FILE_SIZE = (
    49 * 1024 * 1024
)


# =========================================================
# FILA
# =========================================================

executor = ThreadPoolExecutor(
    max_workers=MAX_WORKERS
)


# =========================================================
# BANCO LOCAL DE JOBS
#
# Evita que o mesmo job seja executado várias vezes.
# =========================================================

DB_PATH = "/tmp/video_jobs.sqlite3"

db_lock = threading.Lock()


def init_database():

    with sqlite3.connect(DB_PATH) as conn:

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                status TEXT NOT NULL
            )
            """
        )

        conn.commit()


init_database()


def register_job(job_id):

    if not job_id:
        return True

    with db_lock:

        try:

            with sqlite3.connect(
                DB_PATH
            ) as conn:

                conn.execute(
                    """
                    INSERT INTO jobs (
                        job_id,
                        created_at,
                        status
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        job_id,
                        time.time(),
                        "queued"
                    )
                )

                conn.commit()

                return True

        except sqlite3.IntegrityError:

            print(
                "JOB DUPLICADO IGNORADO:",
                job_id
            )

            return False


def update_job_status(
    job_id,
    status
):

    if not job_id:
        return

    with db_lock:

        try:

            with sqlite3.connect(
                DB_PATH
            ) as conn:

                conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?
                    WHERE job_id = ?
                    """,
                    (
                        status,
                        job_id
                    )
                )

                conn.commit()

        except Exception as error:

            print(
                "ERRO STATUS JOB:",
                error
            )


# =========================================================
# HEADERS
# =========================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
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
        "workers": MAX_WORKERS,
        "telegram_video": True
    })


@app.get("/health")
def health():

    return jsonify({
        "status": "healthy"
    })


# =========================================================
# SEGURANÇA
# =========================================================

def valid_api_secret():

    if not API_SECRET:
        return True

    received = request.headers.get(
        "X-API-KEY",
        ""
    )

    return received == API_SECRET


# =========================================================
# SHOPEE
# =========================================================

def is_shopee(url):

    link = str(url).lower()

    return (
        "shopee." in link
        or "shp.ee" in link
        or "sv.shopee" in link
    )


def resolve_url(url):

    response = requests.get(
        url,
        headers=HEADERS,
        allow_redirects=True,
        timeout=30
    )

    response.raise_for_status()

    return response.url


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
    # MP4 DIRETO
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
    # VIDEO SRC
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
    # SOURCE SRC
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
    # VALIDA
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
                media_response
                .headers
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
                "ERRO VALIDANDO MP4:",
                error
            )


    if valid_candidates:

        return {
            "success": True,
            "platform": "Shopee",
            "url": valid_candidates[0],
            "resolved_url": final_url,
            "candidates_found":
                len(valid_candidates)
        }


    return {
        "success": False,
        "platform": "Shopee",
        "resolved_url": final_url,
        "candidates_found":
            len(candidates),

        "error": (
            "Não encontrei uma URL "
            "MP4 pública."
        )
    }


# =========================================================
# OUTRAS PLATAFORMAS
# =========================================================

def extract_with_ytdlp(url):

    ydl_opts = {

        "quiet": True,

        "no_warnings": True,

        "skip_download": True,

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


    if not video_url:

        formats = (
            info.get("formats")
            or []
        )


        valid_formats = []


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

                valid_formats.append(
                    item
                )


        valid_formats.sort(

            key=lambda x: (
                x.get("height") or 0,
                x.get("tbr") or 0
            ),

            reverse=True
        )


        if valid_formats:

            video_url = (
                valid_formats[0]
                .get("url")
            )


    if not video_url:

        return {
            "success": False,
            "error": (
                "Vídeo encontrado, "
                "mas não encontrei a mídia."
            )
        }


    return {
        "success": True,

        "title":
            info.get("title"),

        "platform":
            info.get("extractor_key")
            or info.get("extractor"),

        "width":
            info.get("width"),

        "height":
            info.get("height"),

        "duration":
            info.get("duration"),

        "thumbnail":
            info.get("thumbnail"),

        "url":
            video_url
    }


def extract_video(url):

    if is_shopee(url):

        return extract_shopee_video(
            url
        )

    return extract_with_ytdlp(
        url
    )


# =========================================================
# TELEGRAM MESSAGE
# =========================================================

def send_telegram_message(
    chat_id,
    thread_id,
    text,
    disable_preview=True
):

    if not TELEGRAM_TOKEN:
        return False


    endpoint = (
        "https://api.telegram.org/bot"
        + TELEGRAM_TOKEN
        + "/sendMessage"
    )


    payload = {

        "chat_id":
            chat_id,

        "text":
            text,

        "disable_web_page_preview":
            disable_preview
    }


    if thread_id:

        payload[
            "message_thread_id"
        ] = int(thread_id)


    try:

        response = requests.post(
            endpoint,
            json=payload,
            timeout=30
        )


        print(
            "SEND MESSAGE:",
            response.status_code,
            response.text
        )


        return response.ok


    except Exception as error:

        print(
            "ERRO SEND MESSAGE:",
            error
        )

        return False


# =========================================================
# BAIXA O MP4 TEMPORARIAMENTE
# =========================================================

def download_video_file(
    video_url
):

    temp_file = tempfile.NamedTemporaryFile(
        suffix=".mp4",
        delete=False
    )

    path = temp_file.name

    temp_file.close()


    total = 0


    try:

        with requests.get(
            video_url,
            headers=HEADERS,
            stream=True,
            timeout=60
        ) as response:

            response.raise_for_status()


            content_length = response.headers.get(
                "content-length"
            )


            if content_length:

                size = int(
                    content_length
                )

                if (
                    size >
                    MAX_TELEGRAM_FILE_SIZE
                ):

                    os.unlink(
                        path
                    )

                    return {
                        "success": False,
                        "too_large": True,
                        "size": size
                    }


            with open(
                path,
                "wb"
            ) as file:

                for chunk in response.iter_content(
                    chunk_size=1024 * 1024
                ):

                    if not chunk:
                        continue


                    total += len(
                        chunk
                    )


                    if (
                        total >
                        MAX_TELEGRAM_FILE_SIZE
                    ):

                        file.close()

                        os.unlink(
                            path
                        )

                        return {
                            "success": False,
                            "too_large": True,
                            "size": total
                        }


                    file.write(
                        chunk
                    )


        return {
            "success": True,
            "path": path,
            "size": total
        }


    except Exception as error:

        try:

            if os.path.exists(
                path
            ):
                os.unlink(
                    path
                )

        except Exception:
            pass


        return {
            "success": False,
            "error": str(error)
        }


# =========================================================
# ENVIA MP4 DIRETO AO TELEGRAM
# =========================================================

def send_telegram_video(
    chat_id,
    thread_id,
    file_path,
    caption
):

    endpoint = (
        "https://api.telegram.org/bot"
        + TELEGRAM_TOKEN
        + "/sendVideo"
    )


    data = {

        "chat_id":
            str(chat_id),

        "caption":
            caption,

        "supports_streaming":
            "true"
    }


    if thread_id:

        data[
            "message_thread_id"
        ] = str(thread_id)


    try:

        with open(
            file_path,
            "rb"
        ) as video_file:

            files = {

                "video": (
                    "video.mp4",
                    video_file,
                    "video/mp4"
                )

            }


            response = requests.post(
                endpoint,
                data=data,
                files=files,
                timeout=180
            )


        print(
            "SEND VIDEO:",
            response.status_code,
            response.text
        )


        return response.ok


    except Exception as error:

        print(
            "ERRO SEND VIDEO:",
            error
        )

        return False


# =========================================================
# FALLBACK COMO ARQUIVO
# =========================================================

def send_telegram_document(
    chat_id,
    thread_id,
    file_path,
    caption
):

    endpoint = (
        "https://api.telegram.org/bot"
        + TELEGRAM_TOKEN
        + "/sendDocument"
    )


    data = {

        "chat_id":
            str(chat_id),

        "caption":
            caption
    }


    if thread_id:

        data[
            "message_thread_id"
        ] = str(thread_id)


    try:

        with open(
            file_path,
            "rb"
        ) as video_file:

            files = {

                "document": (
                    "video.mp4",
                    video_file,
                    "video/mp4"
                )

            }


            response = requests.post(
                endpoint,
                data=data,
                files=files,
                timeout=180
            )


        print(
            "SEND DOCUMENT:",
            response.status_code,
            response.text
        )


        return response.ok


    except Exception as error:

        print(
            "ERRO DOCUMENT:",
            error
        )

        return False


# =========================================================
# DURAÇÃO
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


    minutes = (
        seconds // 60
    )

    remaining = (
        seconds % 60
    )


    return (
        f"{minutes}:"
        f"{remaining:02d}"
    )


# =========================================================
# JOB
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

    job_id = str(
        data.get(
            "job_id",
            ""
        )
    )

    platform_received = data.get(
        "platform",
        "Vídeo"
    )


    update_job_status(
        job_id,
        "processing"
    )


    print(
        "PROCESSANDO JOB:",
        job_id,
        url
    )


    result = None

    last_error = None


    # -----------------------------------------------------
    # TENTA EXTRAÇÃO ATÉ 3 VEZES
    # -----------------------------------------------------

    for attempt in range(
        1,
        4
    ):

        try:

            print(
                f"EXTRAÇÃO {attempt}/3"
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


        if attempt < 3:

            time.sleep(
                3
            )


    # -----------------------------------------------------
    # NÃO ENCONTROU
    # -----------------------------------------------------

    if (
        not result
        or not result.get("success")
        or not result.get("url")
    ):

        update_job_status(
            job_id,
            "failed"
        )


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


    platform = (
        result.get("platform")
        or platform_received
    )


    caption = (
        "✅ Vídeo pronto!\n\n"
        f"📱 Plataforma: {platform}"
    )


    title = result.get(
        "title"
    )


    if title:

        caption += (
            "\n\n🎬 "
            + str(title)[:500]
        )


    height = result.get(
        "height"
    )


    if height:

        caption += (
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

        caption += (
            "\n\n⏱ Duração: "
            + duration
        )


    # -----------------------------------------------------
    # BAIXA MP4 PARA O RENDER
    # -----------------------------------------------------

    downloaded = download_video_file(
        result["url"]
    )


    # -----------------------------------------------------
    # ARQUIVO MUITO GRANDE
    # -----------------------------------------------------

    if (
        not downloaded.get(
            "success"
        )
    ):

        update_job_status(
            job_id,
            "fallback"
        )


        # SOMENTE UM LINK.
        send_telegram_message(
            chat_id,
            thread_id,

            "✅ Vídeo encontrado!\n\n"

            f"📱 Plataforma: {platform}\n\n"

            "📥 O arquivo é grande demais para "
            "eu enviar diretamente pelo Telegram.\n\n"

            + result["url"]
        )


        return


    file_path = downloaded[
        "path"
    ]


    try:

        # -------------------------------------------------
        # PRIMEIRA OPÇÃO: VÍDEO NORMAL
        # -------------------------------------------------

        sent = send_telegram_video(
            chat_id,
            thread_id,
            file_path,
            caption
        )


        # -------------------------------------------------
        # SEGUNDA OPÇÃO: ARQUIVO MP4
        # -------------------------------------------------

        if not sent:

            sent = send_telegram_document(
                chat_id,
                thread_id,
                file_path,
                caption
            )


        # -------------------------------------------------
        # ÚLTIMO FALLBACK: UM ÚNICO LINK
        # -------------------------------------------------

        if not sent:

            send_telegram_message(
                chat_id,
                thread_id,

                "✅ Vídeo encontrado!\n\n"

                f"📱 Plataforma: {platform}\n\n"

                "📥 Não consegui anexar o MP4. "
                "Use este link:\n\n"

                + result["url"]
            )


            update_job_status(
                job_id,
                "fallback"
            )


        else:

            update_job_status(
                job_id,
                "completed"
            )


    finally:

        try:

            if os.path.exists(
                file_path
            ):

                os.unlink(
                    file_path
                )

        except Exception:
            pass


    print(
        "JOB FINALIZADO:",
        job_id
    )


# =========================================================
# ENQUEUE
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


    if not job_id:

        return jsonify({
            "success": False,
            "error": "job_id não informado."
        }), 400


    # -----------------------------------------------------
    # DUPLICADO NÃO É PROCESSADO NOVAMENTE
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


    executor.submit(
        process_job,
        data.copy()
    )


    return jsonify({
        "success": True,
        "queued": True,
        "job_id": job_id
    }), 202


# =========================================================
# TESTE SINCRONO
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
                "error": "URL não informada."
            }), 400


        result = extract_video(
            url
        )


        return jsonify(
            result
        ), (
            200
            if result.get("success")
            else 422
        )


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
