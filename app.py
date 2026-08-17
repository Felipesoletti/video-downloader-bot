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
        "version": "5.0-diagnostic"
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
# ADICIONA CANDIDATO SEM DUPLICAR
# =========================================================

def add_candidate(
    candidates,
    value,
    source
):

    value = clean_media_url(
        value
    )

    if not value:
        return

    for item in candidates:

        if (
            item["url"] ==
            value
        ):
            return

    candidates.append({
        "url": value,
        "source": source
    })


# =========================================================
# ANALISA CANDIDATO
# =========================================================

def inspect_candidate(
    candidate,
    referer
):

    media_url = candidate[
        "url"
    ]

    result = {
        "url":
            media_url,

        "source":
            candidate.get(
                "source"
            ),

        "valid":
            False,

        "status":
            None,

        "content_type":
            "",

        "content_length":
            0,

        "final_url":
            media_url,

        "watermark_hint":
            False
    }


    lower = (
        media_url.lower()
    )


    watermark_terms = [
        "watermark",
        "watermarked",
        "with_logo",
        "with-logo",
        "overlay",
        "logo",
        "wm="
    ]


    result[
        "watermark_hint"
    ] = any(
        term in lower
        for term in watermark_terms
    )


    try:

        response = requests.get(
            media_url,

            headers={
                **HEADERS,
                "Referer":
                    referer
            },

            stream=True,

            allow_redirects=True,

            timeout=20
        )


        result[
            "status"
        ] = response.status_code


        result[
            "final_url"
        ] = response.url


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

                result[
                    "content_length"
                ] = int(
                    content_length
                )

            except Exception:
                pass


        if (
            response.status_code < 400
            and (
                "video" in result[
                    "content_type"
                ]
                or
                ".mp4" in lower
            )
        ):

            result[
                "valid"
            ] = True


        response.close()


    except Exception as error:

        result[
            "error"
        ] = str(
            error
        )


    return result


# =========================================================
# COLETA TODOS OS CANDIDATOS SHOPEE
# =========================================================

def collect_shopee_candidates(
    url
):

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

    direct_patterns = [
        r'https?://[^"\']+\.mp4[^"\']*',
        r'https?:\\?/\\?/[^"\']+\.mp4[^"\']*',
    ]


    for pattern in direct_patterns:

        matches = re.findall(
            pattern,
            page,
            flags=re.IGNORECASE
        )


        for item in matches:

            add_candidate(
                candidates,
                item,
                "direct_mp4"
            )


    # =====================================================
    # CAMPOS JSON
    # =====================================================

    json_patterns = {

        "videoUrl":
            r'"videoUrl"\s*:\s*"([^"]+)"',

        "video_url":
            r'"video_url"\s*:\s*"([^"]+)"',

        "playUrl":
            r'"playUrl"\s*:\s*"([^"]+)"',

        "play_url":
            r'"play_url"\s*:\s*"([^"]+)"',

        "url":
            r'"url"\s*:\s*"(https?:[^"]+\.mp4[^"]*)"',


        "src":
            r'"src"\s*:\s*"(https?:[^"]+\.mp4[^"]*)"',
    }


    for source_name, pattern in (
        json_patterns.items()
    ):

        matches = re.findall(
            pattern,
            page,
            flags=re.IGNORECASE
        )


        for item in matches:

            add_candidate(
                candidates,
                item,
                source_name
            )


    # =====================================================
    # VIDEO SRC
    # =====================================================

    matches = re.findall(
        r'<video[^>]+src=["\']([^"\']+)["\']',
        page,
        flags=re.IGNORECASE
    )


    for item in matches:

        add_candidate(
            candidates,
            item,
            "video_tag"
        )


    # =====================================================
    # SOURCE SRC
    # =====================================================

    matches = re.findall(
        r'<source[^>]+src=["\']([^"\']+)["\']',
        page,
        flags=re.IGNORECASE
    )


    for item in matches:

        add_candidate(
            candidates,
            item,
            "source_tag"
        )


    analyzed = []


    for candidate in candidates:

        analyzed.append(
            inspect_candidate(
                candidate,
                final_url
            )
        )


    return {
        "resolved_url":
            final_url,

        "page_length":
            len(page),

        "candidates":
            analyzed
    }


# =========================================================
# ESCOLHE MELHOR CANDIDATO
# =========================================================

def choose_best_candidate(
    candidates
):

    valid = [
        item
        for item in candidates
        if item.get(
            "valid"
        )
    ]


    if not valid:

        return None


    # -----------------------------------------------------
    # PRIMEIRO TENTA CANDIDATOS SEM INDÍCIO DE WATERMARK
    # -----------------------------------------------------

    clean_candidates = [
        item
        for item in valid
        if not item.get(
            "watermark_hint"
        )
    ]


    pool = (
        clean_candidates
        if clean_candidates
        else valid
    )


    # -----------------------------------------------------
    # MAIOR ARQUIVO PRIMEIRO
    # -----------------------------------------------------

    pool.sort(

        key=lambda item:
            item.get(
                "content_length",
                0
            ),

        reverse=True

    )


    return pool[0]


# =========================================================
# EXTRATOR SHOPEE
# =========================================================

def extract_shopee_video(
    url
):

    diagnostic = (
        collect_shopee_candidates(
            url
        )
    )


    best = (
        choose_best_candidate(
            diagnostic[
                "candidates"
            ]
        )
    )


    if not best:

        return {

            "success":
                False,

            "platform":
                "Shopee",

            "resolved_url":
                diagnostic[
                    "resolved_url"
                ],

            "candidates":
                diagnostic[
                    "candidates"
                ],

            "error":
                (
                    "Não encontrei "
                    "MP4 público válido."
                )
        }


    return {

        "success":
            True,

        "platform":
            "Shopee",

        "url":
            best[
                "url"
            ],

        "selected_source":
            best.get(
                "source"
            ),

        "selected_size":
            best.get(
                "content_length"
            ),

        "watermark_hint":
            best.get(
                "watermark_hint"
            ),

        "resolved_url":
            diagnostic[
                "resolved_url"
            ],

        "candidates_found":
            len(
                diagnostic[
                    "candidates"
                ]
            )
    }


# =========================================================
# YT-DLP
# =========================================================

def extract_with_ytdlp(
    url
):

    ydl_opts = {

        "quiet":
            True,

        "no_warnings":
            True,

        "skip_download":
            True,

        "format": (
            "best[ext=mp4]"
            "[vcodec!=none]"
            "[acodec!=none]/"
            "best[ext=mp4]/"
            "best"
        )
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

        return {

            "success":
                False,

            "error":
                "URL de vídeo não encontrada."
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
            info.get(
                "height"
            ),

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
# PRINCIPAL
# =========================================================

def extract_video(
    url
):

    if is_shopee(
        url
    ):

        return (
            extract_shopee_video(
                url
            )
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


        return jsonify(
            result
        ), (
            200
            if result.get(
                "success"
            )
            else 422
        )


    except Exception as error:

        return jsonify({

            "success":
                False,

            "error":
                str(error)

        }), 500


# =========================================================
# /DIAGNOSTIC
#
# ESTE É O ENDPOINT QUE VAMOS USAR AGORA
# PARA DESCOBRIR A VERSÃO LIMPA.
# =========================================================

@app.post("/diagnostic")
def diagnostic():

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


        if not is_shopee(
            url
        ):

            return jsonify({

                "success":
                    False,

                "error":
                    "Diagnostic é somente Shopee."

            }), 400


        result = (
            collect_shopee_candidates(
                url
            )
        )


        return jsonify({

            "success":
                True,

            "resolved_url":
                result[
                    "resolved_url"
                ],

            "page_length":
                result[
                    "page_length"
                ],

            "candidates":
                result[
                    "candidates"
                ]

        })


    except Exception as error:

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
        host="0.0.0.0",
        port=port,
        threaded=True
    )
