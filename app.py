import os
from flask import Flask, request, jsonify
import yt_dlp

app = Flask(__name__)


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


@app.post("/extract")
def extract():
    try:
        data = request.get_json(silent=True) or {}
        url = data.get("url")

        if not url:
            return jsonify({
                "success": False,
                "error": "URL não informada."
            }), 400

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
            requested_formats = info.get(
                "requested_formats"
            ) or []

            for item in requested_formats:
                if item.get("url"):
                    video_url = item.get("url")
                    break

        return jsonify({
            "success": True,
            "title": info.get("title"),
            "platform": info.get("extractor_key"),
            "width": info.get("width"),
            "height": info.get("height"),
            "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail"),
            "url": video_url
        })

    except Exception as error:
        return jsonify({
            "success": False,
            "error": str(error)
        }), 500


if __name__ == "__main__":
    port = int(
        os.environ.get("PORT", 10000)
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
