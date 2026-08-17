import os
import re
import html
import requests
import yt_dlp

from flask import Flask, request, jsonify


# =========================================================
# APP
# =========================================================

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
    )
}


# =========================================================
# HOME
# =========================================================

@app.get("/")
def home():

    return jsonify({
        "status": "ok",
        "service": "video-downloader-bot",
        "version": "3.0",
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
# SHOPEE
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

    value = value.replace(
        "&amp;",
        "&"
    )

    if value.startswith("//"):
        value = "https:" + value

    return value


# =========================================================
# REMOVE DUPLICADOS
# =========================================================

def unique_urls(urls):

    result = []

    seen = set()

    for url in urls:

        if not url:
            continue

        url = clean_media_url(
            url
        )

        if not url:
            continue

        if url in seen:
            continue

        seen.add(
            url
        )

        result.append(
            url
        )

    return result


# =========================================================
# DETECTA POSSÍVEL WATERMARK PELA URL
#
# Isso NÃO remove watermark.
# Só reduz prioridade de variantes identificadas
# explicitamente como watermark/logo/overlay.
# =========================================================

def has_watermark_hint(url):

    lower = str(url).lower()

    patterns = [
        "watermark",
        "overlay",
        "with_logo",
        "with-logo",
        "watermarked",
        "?wm=",
        "&wm=",
        "?watermark=",
        "&watermark=",
    ]

    return any(
        item in lower
        for item in patterns
    )


# =========================================================
# AVALIA UM CANDIDATO SHOPEE
# =========================================================

def probe_candidate(
    media_url,
    referer
):

    result = {
        "url": media_url,
        "valid": False,
        "size": 0,
        "content_type": "",
        "score": 0,
        "watermark_hint": False
    }


    try:

        response = requests.get(
            media_url,

            headers={
                **HEADERS,
                "Referer": referer
            },

            stream=True,

            allow_redirects=True,

            timeout=15
        )


        result[
            "content_type"
        ] = (
            response.headers
            .get(
                "content-type",
                ""
            )
            .lower()
        )


        content_length = (
            response.headers
            .get(
                "content-length"
            )
        )


        if content_length:

            try:

                result["size"] = int(
                    content_length
                )

            except Exception:
                pass


        result[
            "watermark_hint"
        ] = has_watermark_hint(
            media_url
        )


        if (
            response.status_code < 400
            and (
                "video" in result[
                    "content_type"
                ]
                or ".mp4" in media_url.lower()
            )
        ):

            result[
                "valid"
            ] = True


        response.close()


    except Exception as error:

        print(
            "ERRO PROBE:",
            media_url,
            error
        )

        return result


    # -----------------------------------------------------
    # SCORE
    #
    # Maior arquivo tende a ser maior qualidade.
    # -----------------------------------------------------

    score = result[
        "size"
    ]


    # CDN oficial de vídeo da Shopee ganha prioridade.

    if (
        "vod.susercontent.com"
        in media_url.lower()
    ):

        score += (
            500 * 1024 * 1024
        )


    # MP4 ganha prioridade.

    if ".mp4" in media_url.lower():

        score += (
            100 * 1024 * 1024
        )


    # URLs explicitamente indicando watermark
    # perdem bastante prioridade.

    if result[
        "watermark_hint"
    ]:

        score -= (
            1000 * 1024 * 1024
        )


    result[
        "score"
    ] = score


    return result


# =========================================================
# EXTRATOR SHOPEE
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
    # MP4 DIRETO
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

        candidates.extend(
            matches
        )


    # =====================================================
    # CAMPOS JSON
    # =====================================================

    json_patterns = [

        r'"videoUrl"\s*:\s*"([^"]+)"',

        r'"video_url"\s*:\s*"([^"]+)"',

        r'"playUrl"\s*:\s*"([^"]+)"',

        r'"play_url"\s*:\s*"([^"]+)"',

        r'"video"\s*:\s*"([^"]+\.mp4[^"]*)"',

        r'"src"\s*:\s*"(https?:[^"]+\.mp4[^"]*)"',

        r'"url"\s*:\s*"(https?:[^"]+\.mp4[^"]*)"',
    ]


    for pattern in json_patterns:

        matches = re.findall(
            pattern,
            page,
            flags=re.IGNORECASE
        )

        candidates.extend(
            matches
        )


    # =====================================================
    # TAG <VIDEO>
    # =====================================================

    matches = re.findall(

        r'<video[^>]+src=["\']([^"\']+)["\']',

        page,

        flags=re.IGNORECASE

    )


    candidates.extend(
        matches
    )


    # =====================================================
    # TAG <SOURCE>
    # =====================================================

    matches = re.findall(

        r'<source[^>]+src=["\']([^"\']+)["\']',

        page,

        flags=re.IGNORECASE

    )


    candidates.extend(
        matches
    )


    # =====================================================
    # REMOVE DUPLICADOS
    # =====================================================

    candidates = unique_urls(
        candidates
    )


    print(
        "SHOPEE CANDIDATOS:",
        len(candidates)
    )


    # =====================================================
    # AVALIA TODOS
    # =====================================================

    analyzed = []


    for candidate in candidates:

        info = probe_candidate(
            candidate,
            final_url
        )


        if info[
            "valid"
        ]:

            analyzed.append(
                info
            )


    # =====================================================
    # NENHUM MP4
    # =====================================================

    if not analyzed:

        return {

            "success":
                False,

            "platform":
                "Shopee",

            "resolved_url":
                final_url,

            "candidates_found":
                len(
                    candidates
                ),

            "error": (
                "A página foi aberta, "
                "mas não encontrei um MP4 público."
            )
        }


    # =====================================================
    # MELHOR VERSÃO
    # =====================================================

    analyzed.sort(

        key=lambda item:
            item.get(
                "score",
                0
            ),

        reverse=True

    )


    best = analyzed[
        0
    ]


    print(
        "SHOPEE ESCOLHIDO:",
        best["url"]
    )


    print(
        "TAMANHO:",
        best["size"]
    )


    print(
        "WATERMARK HINT:",
        best[
            "watermark_hint"
        ]
    )


    return {

        "success":
            True,

        "platform":
            "Shopee",

        "url":
            best["url"],

        "file_size":
            best["size"],

        "content_type":
            best["content_type"],

        "watermark_hint":
            best[
                "watermark_hint"
            ],

        "resolved_url":
            final_url,

        "candidates_found":
            len(
                analyzed
            )
    }


# =========================================================
# YT-DLP
# =========================================================

def extract_with_ytdlp(url):

    ydl_opts = {

        "quiet":
            True,

        "no_warnings":
            True,

        "skip_download":
            True,

        # Queremos arquivo único MP4,
        # porque o Telegram precisa conseguir
        # acessar diretamente.

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


    if not video_url:

        formats = (
            info.get(
                "formats"
            )
            or []
        )


        candidates = []


        for item in formats:

            media_url = item.get(
                "url"
            )


            if not media_url:
                continue


            vcodec = item.get(
                "vcodec"
            )


            acodec = item.get(
                "acodec"
            )


            if (
                not vcodec
                or vcodec == "none"
            ):
                continue


            candidates.append(
                item
            )


        candidates.sort(

            key=lambda item: (

                1
                if (
                    item.get(
                        "ext"
                    ) == "mp4"
                )
                else 0,

                1
                if (
                    item.get(
                        "acodec"
                    )
                    and
                    item.get(
                        "acodec"
                    ) != "none"
                )
                else 0,

                item.get(
                    "height"
                ) or 0,

                item.get(
                    "tbr"
                ) or 0

            ),

            reverse=True
        )


        if candidates:

            selected = candidates[
                0
            ]


            video_url = selected.get(
                "url"
            )


            height = selected.get(
                "height"
            )


        else:

            height = info.get(
                "height"
            )


    else:

        height = info.get(
            "height"
        )


    if not video_url:

        return {

            "success":
                False,

            "error": (
                "Vídeo identificado, "
                "mas não encontrei uma URL direta."
            )
        }


    return {

        "success":
            True,

        "title":
            info.get(
                "title"
            ),

        "platform":
            (
                info.get(
                    "extractor_key"
                )
                or
                info.get(
                    "extractor"
                )
            ),

        "width":
            info.get(
                "width"
            ),

        "height":
            height,

        "duration":
            info.get(
                "duration"
            ),

        "thumbnail":
            info.get(
                "thumbnail"
            ),

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

            "success":
                False,

            "error":
                "Não autorizado."

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

                "success":
                    False,

                "error":
                    "URL não informada."

            }), 400


        result = extract_video(
            url
        )


        if result.get(
            "success"
        ):

            return jsonify(
                result
            ), 200


        return jsonify(
            result
        ), 422


    except Exception as error:

        print(
            "ERRO /extract:",
            error
        )


        return jsonify({

            "success":
                False,

            "error":
                str(error)

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

        host=
            "0.0.0.0",

        port=
            port,

        threaded=
            True

    )
