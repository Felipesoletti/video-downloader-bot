import os
import re
import html
import requests
import yt_dlp

from flask import Flask, request, jsonify


app = Flask(__name__)


# =========================================================
# CONFIGURAÇÕES
# =========================================================

API_SECRET = os.environ.get(
    "API_SECRET",
    ""
)


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": (
        "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
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
        "version": "4.0",
        "mode": "extract-only"
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
# IDENTIFICA SHOPEE
# =========================================================

def is_shopee(url):

    link = str(url).lower()

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
# LIMPA URL
# =========================================================

def clean_media_url(value):

    if not value:
        return None

    value = html.unescape(value)

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
# ADICIONA CANDIDATO SEM REPETIR
# =========================================================

def add_candidate(
    candidates,
    value
):

    value = clean_media_url(
        value
    )

    if not value:
        return

    if value in candidates:
        return

    candidates.append(
        value
    )


# =========================================================
# SHOPEE
#
# IMPORTANTE:
# Voltamos ao método original que funcionou:
#
# 1. encontra os MP4
# 2. mantém a ordem original da página
# 3. valida
# 4. usa o PRIMEIRO válido
#
# Não fazemos ranking que possa trocar a variante.
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


    # =====================================================
    # 1. MP4 DIRETO
    # =====================================================

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

            add_candidate(
                candidates,
                item
            )


    # =====================================================
    # 2. CAMPOS JSON
    # =====================================================

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

            add_candidate(
                candidates,
                item
            )


    # =====================================================
    # 3. TAG VIDEO
    # =====================================================

    matches = re.findall(
        r'<video[^>]+src=["\']([^"\']+)["\']',
        page,
        flags=re.IGNORECASE
    )


    for item in matches:

        add_candidate(
            candidates,
            item
        )


    # =====================================================
    # 4. TAG SOURCE
    # =====================================================

    matches = re.findall(
        r'<source[^>]+src=["\']([^"\']+)["\']',
        page,
        flags=re.IGNORECASE
    )


    for item in matches:

        add_candidate(
            candidates,
            item
        )


    print(
        "SHOPEE - CANDIDATOS:",
        len(candidates)
    )


    # =====================================================
    # 5. TESTA NA ORDEM
    # =====================================================

    for index, media_url in enumerate(
        candidates
    ):

        try:

            media_response = requests.get(
                media_url,

                headers={
                    **HEADERS,
                    "Referer": final_url
                },

                stream=True,

                allow_redirects=True,

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


            status = (
                media_response.status_code
            )


            media_response.close()


            if (
                status < 400
                and (
                    "video" in content_type
                    or ".mp4" in media_url.lower()
                )
            ):

                print(
                    "SHOPEE - MP4 ESCOLHIDO:",
                    index,
                    media_url
                )


                return {
                    "success": True,
                    "platform": "Shopee",
                    "url": media_url,
                    "resolved_url": final_url,
                    "candidate_index": index,
                    "candidates_found": len(
                        candidates
                    )
                }


        except Exception as error:

            print(
                "ERRO CANDIDATO:",
                index,
                error
            )


    return {
        "success": False,
        "platform": "Shopee",
        "resolved_url": final_url,
        "candidates_found": len(
            candidates
        ),
        "error": (
            "A página foi aberta, "
            "mas não encontrei um MP4 público válido."
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

        # Prefere MP4 único com áudio + vídeo.

        "format": (
            "best[ext=mp4][vcodec!=none][acodec!=none]/"
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


    selected_height = info.get(
        "height"
    )


    # =====================================================
    # FALLBACK
    # =====================================================

    if not video_url:

        formats = (
            info.get("formats")
            or []
        )


        valid_formats = []


        for item in formats:

            media_url = item.get(
                "url"
            )


            if not media_url:
                continue


            vcodec = item.get(
                "vcodec"
            )


            if (
                not vcodec
                or vcodec == "none"
            ):
                continue


            valid_formats.append(
                item
            )


        valid_formats.sort(

            key=lambda item: (

                1
                if item.get("ext") == "mp4"
                else 0,

                1
                if (
                    item.get("acodec")
                    and
                    item.get("acodec") != "none"
                )
                else 0,

                item.get("height") or 0,

                item.get("tbr") or 0

            ),

            reverse=True
        )


        if valid_formats:

            selected = (
                valid_formats[0]
            )


            video_url = selected.get(
                "url"
            )


            selected_height = selected.get(
                "height"
            )


    if not video_url:

        return {
            "success": False,
            "error": (
                "Vídeo identificado, "
                "mas não encontrei uma URL direta."
            )
        }


    return {

        "success": True,

        "title":
            info.get("title"),

        "platform":
            (
                info.get("extractor_key")
                or
                info.get("extractor")
            ),

        "width":
            info.get("width"),

        "height":
            selected_height,

        "duration":
            info.get("duration"),

        "thumbnail":
            info.get("thumbnail"),

        "url":
            video_url
    }


# =========================================================
# EXTRATOR PRINCIPAL
# =========================================================

def extract_video(url):

    if is_shopee(
        url
    ):

        return extract_shopee_video(
            url
        )


    return extract_with_ytdlp(
        url
    )


# =========================================================
# /EXTRACT
# =========================================================

@app.post("/extract")
def extract():

    if not valid_api_secret():

        return jsonify({
            "success": False,
            "error": "Não autorizado."
        }), 401


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


        print(
            "EXTRAINDO:",
            url
        )


        result = extract_video(
            url
        )


        status = (
            200
            if result.get("success")
            else 422
        )


        return jsonify(
            result
        ), status


    except Exception as error:

        print(
            "ERRO /extract:",
            error
        )


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
