import os
import re
import html
import uuid
import time
import json
import shutil
import subprocess
import threading
import requests
import yt_dlp

from flask import (
    Flask,
    request,
    jsonify,
    send_file,
    abort
)


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

TEMP_DIR = os.environ.get(
    "TEMP_DIR",
    "/tmp/shopee_video"
)

FILE_TTL_SECONDS = int(
    os.environ.get(
        "FILE_TTL_SECONDS",
        "1800"
    )
)

os.makedirs(
    TEMP_DIR,
    exist_ok=True
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
    ),
}


# =========================================================
# ARQUIVOS TEMPORÁRIOS
# =========================================================

media_registry = {}
media_lock = threading.Lock()


def cleanup_temp_files():

    agora = time.time()

    expirados = []


    with media_lock:

        for token, info in list(
            media_registry.items()
        ):

            created_at = info.get(
                "created_at",
                0
            )

            if (
                agora - created_at
                >
                FILE_TTL_SECONDS
            ):

                expirados.append(
                    token
                )


        for token in expirados:

            info = media_registry.pop(
                token,
                None
            )

            if not info:
                continue

            path = info.get(
                "path"
            )

            try:

                if (
                    path
                    and
                    os.path.exists(
                        path
                    )
                ):

                    os.remove(
                        path
                    )

            except Exception:
                pass


# =========================================================
# HOME
# =========================================================

@app.get("/")
def home():

    cleanup_temp_files()

    return jsonify({
        "status": "ok",
        "service": "video-downloader-bot",
        "version": "6.0-hls",
        "ffmpeg": (
            shutil.which("ffmpeg")
            is not None
        )
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

    link = str(
        url
    ).lower()

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
        str(value)
    )


    substitutions = {
        "\\u002F": "/",
        "\\u002f": "/",
        "\\/": "/",
        "\\u0026": "&",
        "\\u003D": "=",
        "\\u003d": "=",
        "\\u003F": "?",
        "\\u003f": "?",
        "\\u0025": "%",
        "&amp;": "&"
    }


    for old, new in substitutions.items():

        value = value.replace(
            old,
            new
        )


    if value.startswith("//"):

        value = (
            "https:"
            + value
        )


    return value


# =========================================================
# ADICIONA CANDIDATO
# =========================================================

def add_candidate(
    collection,
    value,
    source,
    media_type
):

    value = clean_media_url(
        value
    )


    if not value:
        return


    for item in collection:

        if (
            item["url"]
            ==
            value
        ):

            return


    collection.append({
        "url": value,
        "source": source,
        "type": media_type
    })


# =========================================================
# PROCURA URLs RECURSIVAMENTE EM JSON
# =========================================================

def walk_json(
    obj,
    candidates,
    path="root"
):

    if isinstance(
        obj,
        dict
    ):

        for key, value in obj.items():

            child_path = (
                path
                + "."
                + str(key)
            )


            if isinstance(
                value,
                str
            ):

                cleaned = clean_media_url(
                    value
                )


                if cleaned:

                    lower = cleaned.lower()


                    if ".m3u8" in lower:

                        add_candidate(
                            candidates,
                            cleaned,
                            child_path,
                            "hls"
                        )


                    elif ".mp4" in lower:

                        add_candidate(
                            candidates,
                            cleaned,
                            child_path,
                            "mp4"
                        )


            else:

                walk_json(
                    value,
                    candidates,
                    child_path
                )


    elif isinstance(
        obj,
        list
    ):

        for index, item in enumerate(
            obj
        ):

            walk_json(
                item,
                candidates,
                (
                    path
                    + "["
                    + str(index)
                    + "]"
                )
            )


# =========================================================
# COLETA FONTES SHOPEE
# =========================================================

def collect_shopee_sources(url):

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
    # HLS .M3U8
    # =====================================================

    hls_patterns = [

        r'https?://[^"\'\s<>]+\.m3u8[^"\'\s<>]*',

        r'https?:\\?/\\?/[^"\'\s<>]+\.m3u8[^"\'\s<>]*',

        r'"(?:hls|hlsUrl|hls_url|playlist|manifest|playUrl|play_url)"\s*:\s*"([^"]+)"',
    ]


    for pattern in hls_patterns:

        matches = re.findall(
            pattern,
            page,
            flags=re.IGNORECASE
        )


        for item in matches:

            cleaned = clean_media_url(
                item
            )


            if (
                cleaned
                and ".m3u8"
                in cleaned.lower()
            ):

                add_candidate(
                    candidates,
                    cleaned,
                    "html_hls",
                    "hls"
                )


    # =====================================================
    # MP4
    # =====================================================

    mp4_patterns = [

        r'https?://[^"\'\s<>]+\.mp4[^"\'\s<>]*',

        r'https?:\\?/\\?/[^"\'\s<>]+\.mp4[^"\'\s<>]*',

        r'"(?:videoUrl|video_url|playUrl|play_url|src|url)"\s*:\s*"([^"]+\.mp4[^"]*)"',
    ]


    for pattern in mp4_patterns:

        matches = re.findall(
            pattern,
            page,
            flags=re.IGNORECASE
        )


        for item in matches:

            add_candidate(
                candidates,
                item,
                "html_mp4",
                "mp4"
            )


    # =====================================================
    # TENTA EXTRAIR JSONS DO HTML
    # =====================================================

    script_matches = re.findall(
        r'<script[^>]*>(.*?)</script>',
        page,
        flags=(
            re.IGNORECASE
            |
            re.DOTALL
        )
    )


    for script in script_matches:

        stripped = script.strip()


        if not stripped:
            continue


        # -------------------------------------------------
        # JSON PURO
        # -------------------------------------------------

        if (
            stripped.startswith("{")
            or
            stripped.startswith("[")
        ):

            try:

                obj = json.loads(
                    stripped
                )


                walk_json(
                    obj,
                    candidates
                )

            except Exception:
                pass


        # -------------------------------------------------
        # MESMO QUE NÃO SEJA JSON VÁLIDO,
        # PROCURA URLs DENTRO DO SCRIPT
        # -------------------------------------------------

        for match in re.findall(
            r'https?:\\?/\\?/[^"\'\s]+',
            stripped,
            flags=re.IGNORECASE
        ):

            cleaned = clean_media_url(
                match
            )


            if not cleaned:
                continue


            lower = cleaned.lower()


            if ".m3u8" in lower:

                add_candidate(
                    candidates,
                    cleaned,
                    "script_raw",
                    "hls"
                )


            elif ".mp4" in lower:

                add_candidate(
                    candidates,
                    cleaned,
                    "script_raw",
                    "mp4"
                )


    return {
        "resolved_url": final_url,
        "page_length": len(page),
        "candidates": candidates
    }


# =========================================================
# TESTA MP4
# =========================================================

def inspect_mp4(
    media_url,
    referer
):

    result = {
        "url": media_url,
        "valid": False,
        "size": 0,
        "content_type": ""
    }


    try:

        response = requests.get(
            media_url,

            headers={
                **HEADERS,
                "Referer":
                    referer
            },

            stream=True,

            timeout=20,

            allow_redirects=True
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

                result[
                    "size"
                ] = int(
                    content_length
                )

            except Exception:
                pass


        if (
            response.status_code
            <
            400
            and
            (
                "video"
                in
                result[
                    "content_type"
                ]
                or
                ".mp4"
                in
                media_url.lower()
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
# TESTA HLS
# =========================================================

def inspect_hls(
    hls_url,
    referer
):

    result = {
        "url": hls_url,
        "valid": False,
        "content_type": "",
        "playlist_size": 0,
        "master": False
    }


    try:

        response = requests.get(
            hls_url,

            headers={
                **HEADERS,
                "Referer":
                    referer
            },

            timeout=20,

            allow_redirects=True
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


        body = response.text


        result[
            "playlist_size"
        ] = len(
            body
        )


        if (
            response.status_code
            <
            400
            and
            "#EXTM3U"
            in
            body
        ):

            result[
                "valid"
            ] = True


            if (
                "#EXT-X-STREAM-INF"
                in
                body
            ):

                result[
                    "master"
                ] = True


    except Exception as error:

        result[
            "error"
        ] = str(
            error
        )


    return result


# =========================================================
# REMUX HLS PARA MP4
#
# NÃO REENCODA.
# =========================================================

def remux_hls_to_mp4(
    hls_url,
    referer
):

    if not shutil.which(
        "ffmpeg"
    ):

        return {
            "success": False,
            "error": (
                "FFmpeg não instalado."
            )
        }


    token = uuid.uuid4().hex


    output_path = os.path.join(
        TEMP_DIR,
        token + ".mp4"
    )


    headers_string = (
        "User-Agent: "
        + HEADERS["User-Agent"]
        + "\r\n"
        + "Referer: "
        + referer
        + "\r\n"
    )


    command = [

        "ffmpeg",

        "-y",

        "-loglevel",
        "error",

        "-headers",
        headers_string,

        "-i",
        hls_url,

        "-map",
        "0:v:0?",

        "-map",
        "0:a:0?",

        "-c",
        "copy",

        "-movflags",
        "+faststart",

        output_path
    ]


    print(
        "FFMPEG:",
        " ".join(command)
    )


    try:

        result = subprocess.run(
            command,

            stdout=subprocess.PIPE,

            stderr=subprocess.PIPE,

            timeout=180
        )


    except subprocess.TimeoutExpired:

        return {
            "success": False,
            "error": (
                "FFmpeg excedeu o tempo limite."
            )
        }


    if (
        result.returncode != 0
    ):

        return {
            "success": False,
            "error": (
                result.stderr
                .decode(
                    "utf-8",
                    errors="ignore"
                )
            )
        }


    if not os.path.exists(
        output_path
    ):

        return {
            "success": False,
            "error": (
                "FFmpeg não gerou o arquivo."
            )
        }


    size = os.path.getsize(
        output_path
    )


    if size <= 0:

        return {
            "success": False,
            "error": (
                "Arquivo gerado está vazio."
            )
        }


    with media_lock:

        media_registry[
            token
        ] = {
            "path":
                output_path,

            "created_at":
                time.time()
        }


    return {
        "success": True,
        "token": token,
        "size": size
    }


# =========================================================
# SHOPEE - MELHOR FONTE
# =========================================================

def extract_shopee_video(
    url
):

    data = collect_shopee_sources(
        url
    )


    final_url = data[
        "resolved_url"
    ]


    candidates = data[
        "candidates"
    ]


    print(
        "TOTAL CANDIDATOS:",
        len(candidates)
    )


    # =====================================================
    # 1. PRIORIZA HLS
    # =====================================================

    valid_hls = []


    for item in candidates:

        if (
            item.get("type")
            !=
            "hls"
        ):

            continue


        inspected = inspect_hls(
            item["url"],
            final_url
        )


        if inspected.get(
            "valid"
        ):

            inspected[
                "source"
            ] = item.get(
                "source"
            )


            valid_hls.append(
                inspected
            )


    # =====================================================
    # SE ACHOU HLS, TENTA GERAR MP4
    # =====================================================

    for hls in valid_hls:

        result = remux_hls_to_mp4(
            hls["url"],
            final_url
        )


        if result.get(
            "success"
        ):

            token = result[
                "token"
            ]


            public_url = (
                request.host_url
                .rstrip("/")
                +
                "/media/"
                +
                token
            )


            return {
                "success":
                    True,

                "platform":
                    "Shopee",

                "source_type":
                    "hls",

                "source":
                    hls.get(
                        "source"
                    ),

                "url":
                    public_url,

                "original_stream":
                    hls["url"],

                "file_size":
                    result[
                        "size"
                    ],

                "resolved_url":
                    final_url,

                "hls_found":
                    len(
                        valid_hls
                    )
            }


    # =====================================================
    # 2. FALLBACK MP4
    #
    # Só usa se não existir HLS utilizável.
    # =====================================================

    valid_mp4 = []


    for item in candidates:

        if (
            item.get("type")
            !=
            "mp4"
        ):

            continue


        inspected = inspect_mp4(
            item["url"],
            final_url
        )


        if inspected.get(
            "valid"
        ):

            inspected[
                "source"
            ] = item.get(
                "source"
            )


            valid_mp4.append(
                inspected
            )


    if valid_mp4:

        # maior arquivo primeiro

        valid_mp4.sort(

            key=lambda item:
                item.get(
                    "size",
                    0
                ),

            reverse=True
        )


        best = valid_mp4[
            0
        ]


        return {
            "success":
                True,

            "platform":
                "Shopee",

            "source_type":
                "mp4",

            "url":
                best[
                    "url"
                ],

            "file_size":
                best.get(
                    "size"
                ),

            "resolved_url":
                final_url,

            "hls_found":
                0,

            "mp4_found":
                len(
                    valid_mp4
                )
        }


    return {
        "success":
            False,

        "platform":
            "Shopee",

        "resolved_url":
            final_url,

        "error": (
            "Não encontrei "
            "stream HLS ou MP4 válido."
        )
    }


# =========================================================
# OUTRAS PLATAFORMAS
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
            "success": False,
            "error": (
                "URL direta não encontrada."
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

        return extract_shopee_video(
            url
        )


    return extract_with_ytdlp(
        url
    )


# =========================================================
# EXTRACT
# =========================================================

@app.post("/extract")
def extract():

    cleanup_temp_files()


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

        print(
            "ERRO:",
            error
        )


        return jsonify({
            "success":
                False,

            "error":
                str(error)
        }), 500


# =========================================================
# DIAGNÓSTICO
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


        result = collect_shopee_sources(
            url
        )


        analyzed = []


        for item in result[
            "candidates"
        ]:

            if (
                item["type"]
                ==
                "hls"
            ):

                detail = inspect_hls(
                    item["url"],
                    result[
                        "resolved_url"
                    ]
                )

            else:

                detail = inspect_mp4(
                    item["url"],
                    result[
                        "resolved_url"
                    ]
                )


            detail[
                "source"
            ] = item.get(
                "source"
            )


            detail[
                "type"
            ] = item.get(
                "type"
            )


            analyzed.append(
                detail
            )


        return jsonify({
            "success":
                True,

            "resolved_url":
                result[
                    "resolved_url"
                ],

            "candidates":
                analyzed
        })


    except Exception as error:

        return jsonify({
            "success":
                False,

            "error":
                str(error)
        }), 500


# =========================================================
# ENTREGA MP4 TEMPORÁRIO
# =========================================================

@app.get("/media/<token>")
def media(token):

    cleanup_temp_files()


    with media_lock:

        info = media_registry.get(
            token
        )


    if not info:

        abort(
            404
        )


    path = info.get(
        "path"
    )


    if (
        not path
        or
        not os.path.exists(
            path
        )
    ):

        abort(
            404
        )


    return send_file(
        path,
        mimetype="video/mp4",
        as_attachment=False,
        download_name="video.mp4"
    )


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
