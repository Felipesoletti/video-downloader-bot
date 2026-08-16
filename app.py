import os
import re
import json
import html
import requests
from flask import Flask, request, jsonify
import yt_dlp

app = Flask(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
}


@app.get("/")
def home():
    return jsonify({
        "status": "ok",
        "service": "video-downloader-bot"
    })


@app.get("/health")
def health():
    return jsonify({
        "status": "healthy"
    })


def is_shopee(url):
    url = url.lower()

    return (
        "shopee." in url
        or "shp.ee" in url
        or "sv.shopee" in url
    )


def resolve_url(url):
    response = requests.get(
        url,
        headers=HEADERS,
        allow_redirects=True,
        timeout=20
    )

    return response.url


def clean_media_url(value):
    if not value:
        return None

    value = html.unescape(value)

    value = value.replace("\\u002F", "/")
    value = value.replace("\\/", "/")
    value = value.replace("\\u0026", "&")

    if value.startswith("//"):
        value = "https:" + value

    return value


def extract_shopee_video(url):
    final_url = resolve_url(url)

    response = requests.get(
        final_url,
        headers=HEADERS,
        timeout=20
    )

    response.raise_for_status()

    page = response.text


    # ------------------------------------------------
    # 1. Procura URLs MP4 diretas
    # ------------------------------------------------

    patterns = [
        r'https?://[^"\']+\.mp4[^"\']*',
        r'https?:\\?/\\?/[^"\']+\.mp4[^"\']*',
    ]

    candidates = []

    for pattern in patterns:
        matches = re.findall(
            pattern,
            page,
            flags=re.IGNORECASE
        )

        for item in matches:
            media_url = clean_media_url(item)

            if (
                media_url
                and media_url not in candidates
            ):
                candidates.append(media_url)


    # ------------------------------------------------
    # 2. Procura campos JSON comuns
    # ------------------------------------------------

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
            media_url = clean_media_url(item)

            if (
                media_url
                and media_url not in candidates
            ):
                candidates.append(media_url)


    # ------------------------------------------------
    # 3. Procura tags <video>
    # ------------------------------------------------

    video_src_matches = re.findall(
        r'<video[^>]+src=["\']([^"\']+)["\']',
        page,
        flags=re.IGNORECASE
    )

    for item in video_src_matches:
        media_url = clean_media_url(item)

        if (
            media_url
            and media_url not in candidates
        ):
            candidates.append(media_url)


    # ------------------------------------------------
    # 4. Procura <source src="">
    # ------------------------------------------------

    source_matches = re.findall(
        r'<source[^>]+src=["\']([^"\']+)["\']',
        page,
        flags=re.IGNORECASE
    )

    for item in source_matches:
        media_url = clean_media_url(item)

        if (
            media_url
            and media_url not in candidates
        ):
            candidates.append(media_url)


    # ------------------------------------------------
    # 5. Testa os candidatos
    # ------------------------------------------------

    valid_candidates = []

    for media_url in candidates:

        try:
            head = requests.get(
                media_url,
                headers={
                    **HEADERS,
                    "Referer": final_url
                },
                stream=True,
                timeout=10
            )

            content_type = (
                head.headers
                .get("content-type", "")
                .lower()
            )

            if (
                head.status_code < 400
                and (
                    "video" in content_type
                    or ".mp4" in media_url.lower()
                )
            ):
                valid_candidates.append(media_url)

                head.close()

        except Exception:
            pass


    if valid_candidates:

        return {
            "success": True,
            "platform": "Shopee",
            "url": valid_candidates[0],
            "resolved_url": final_url,
            "candidates_found": len(valid_candidates)
        }


    # ------------------------------------------------
    # DEBUG
    # ------------------------------------------------

    return {
        "success": False,
        "platform": "Shopee",
        "resolved_url": final_url,
        "candidates_found": len(candidates),
        "error": (
            "A página foi aberta, mas não encontrei "
            "uma URL MP4 pública no HTML."
        )
    }


def extract_with_ytdlp(url):
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "format": "bestvideo*+bestaudio/best",
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(
            url,
            download=False
        )

        info = ydl.sanitize_info(info)


    video_url = info.get("url")


    if not video_url:
        formats = (
            info.get("requested_formats")
            or info.get("formats")
            or []
        )

        for item in reversed(formats):

            if item.get("url"):
                video_url = item.get("url")
                break


    return {
        "success": True,
        "title": info.get("title"),
        "platform": info.get("extractor_key"),
        "width": info.get("width"),
        "height": info.get("height"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "url": video_url
    }


@app.post("/extract")
def extract():
    try:
        data = request.get_json(
            silent=True
        ) or {}

        url = data.get("url")


        if not url:
            return jsonify({
                "success": False,
                "error": "URL não informada."
            }), 400


        # --------------------------------------------
        # SHOPEE
        # --------------------------------------------

        if is_shopee(url):

            result = extract_shopee_video(url)

            status_code = (
                200
                if result.get("success")
                else 422
            )

            return jsonify(result), status_code


        # --------------------------------------------
        # OUTRAS PLATAFORMAS
        # --------------------------------------------

        result = extract_with_ytdlp(url)

        return jsonify(result)


    except Exception as error:

        return jsonify({
            "success": False,
            "error": str(error)
        }), 500


if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
