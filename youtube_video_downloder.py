from flask import Flask, render_template, request, jsonify, send_file, session
import yt_dlp
import shutil
import os
import threading
import time
import uuid
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'youtube_downloader_secret_key_2024'

# Per-session, per-download tracking
# Structure: user_downloads[session_id][download_id] = {...}
user_downloads = {}
user_files = {}


def ensure_session():
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
    return session['user_id']


def progress_hook(session_id, download_id):
    def hook(d):
        # Init dicts if missing
        if session_id not in user_downloads:
            user_downloads[session_id] = {}
        if download_id not in user_downloads[session_id]:
            user_downloads[session_id][download_id] = {}

        if d['status'] == 'downloading':
            downloaded = d.get('downloaded_bytes', 0) or 0
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0

            downloaded_mb = round(downloaded / (1024 * 1024), 2)
            total_mb = round(total / (1024 * 1024), 2) if total > 0 else 0
            percent = round((downloaded / total) * 100, 1) if total > 0 else 0

            user_downloads[session_id][download_id] = {
                "status": "downloading",
                "message": f"Downloading... {downloaded_mb} MB / {total_mb} MB ({percent}%)",
                "downloaded_mb": downloaded_mb,
                "total_mb": total_mb,
                "percent": percent,
                "filename": d.get('filename', '')
            }

        elif d['status'] == 'finished':
            # finished but still processing (postprocess)
            user_downloads[session_id][download_id] = {
                "status": "processing",
                "message": "Processing... Almost done!",
                "percent": 100,
                "filename": d.get('filename', '')
            }

    return hook


def build_mp3_opts(quality):
    """
    quality: string like '320', '256', '192', etc.
    yt_dlp audio-quality:
      - 0..9 for VBR (0 best)
      - or '128K', '192K' etc for CBR. [web:27]
    Yahan simple VBR index map use kar rahe hain.
    """
    vbr_map = {
        '320': '0',  # best
        '256': '2',
        '192': '4',
        '128': '6',
        '96': '8'
    }
    audio_quality = vbr_map.get(str(quality), '4')
    return {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': audio_quality,
        }]
    }


def build_mp4_opts(quality):
    """
    MP4 download + merge with FFmpeg. [web:26]
    """
    if not shutil.which('ffmpeg'):
        raise RuntimeError("FFmpeg is required for MP4 downloads. Install FFmpeg and restart the server.")

    if quality == "best":
        format_str = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio'
    elif quality == "4k":
        format_str = 'bestvideo[height<=2160][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=2160]+bestaudio'
    elif quality == "2k":
        format_str = 'bestvideo[height<=1440][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1440]+bestaudio'
    elif quality == "1080p":
        format_str = 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio'
    elif quality == "720p":
        format_str = 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio'
    elif quality == "480p":
        format_str = 'bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=480]+bestaudio'
    elif quality == "360p":
        format_str = 'bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=360]+bestaudio'
    else:
        format_str = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio'

    return {
        'format': format_str,
        'merge_output_format': 'mp4',
        'postprocessors': [{
            'key': 'FFmpegVideoConvertor',
            'preferedformat': 'mp4'
        }],
        'postprocessor_args': [
            '-c:a', 'aac',
            '-b:a', '192k'
        ],
        'prefer_ffmpeg': True
    }


def download_video(url, quality, format_type, session_id, download_id):
    try:
        save_path = os.path.join("downloads", session_id)
        os.makedirs(save_path, exist_ok=True)

        # Base opts + progress hook
        outtmpl = os.path.join(save_path, f"{download_id}-%(title)s.%(ext)s")
        ydl_opts = {
            'outtmpl': outtmpl,
            'progress_hooks': [progress_hook(session_id, download_id)],
            'noplaylist': True,
        }

        # Format-specific config
        if format_type == "mp3":
            ydl_opts.update(build_mp3_opts(quality))
        else:
            ydl_opts.update(build_mp4_opts(quality))

        # Initial status
        if session_id not in user_downloads:
            user_downloads[session_id] = {}
        user_downloads[session_id][download_id] = {
            "status": "downloading",
            "message": "Initializing...",
            "downloaded_mb": 0,
            "total_mb": 0,
            "percent": 0,
            "filename": ""
        }

        # Download
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # yt_dlp filename helper [web:21][web:25]
            final_filename = ydl.prepare_filename(info)
            if format_type == "mp3":
                # Postprocessor may change extension; mp3 usually final
                base, _ = os.path.splitext(final_filename)
                final_filename = base + ".mp3"

        # Save final path for this download
        if session_id not in user_files:
            user_files[session_id] = {}
        user_files[session_id][download_id] = final_filename

        # Update status
        user_downloads[session_id][download_id] = {
            "status": "completed",
            "message": "Download completed! Click to save to your device.",
            "percent": 100,
            "filename": os.path.basename(final_filename)
        }

    except Exception as e:
        # Log full error server side
        print("Download error:", repr(e))
        if session_id not in user_downloads:
            user_downloads[session_id] = {}
        user_downloads[session_id][download_id] = {
            "status": "error",
            "message": "Download failed. Please try again later.",
            "percent": 0,
            "filename": ""
        }


@app.route('/')
def index():
    ensure_session()
    return render_template('index.html')


@app.route('/download', methods=['POST'])
def start_download():
    session_id = ensure_session()
    data = request.get_json(force=True) or {}
    url = (data.get('url') or '').strip()
    quality = data.get('quality', '720p')
    format_type = data.get('format', 'mp4')

    if not url:
        return jsonify({"status": "error", "message": "Please enter a YouTube URL"}), 400

    download_id = str(uuid.uuid4())

    # Initialize dicts
    user_downloads.setdefault(session_id, {})
    user_files.setdefault(session_id, {})

    user_downloads[session_id][download_id] = {
        "status": "ready",
        "message": "Ready to download",
        "downloaded_mb": 0,
        "total_mb": 0,
        "percent": 0,
        "filename": ""
    }

    thread = threading.Thread(
        target=download_video,
        args=(url, quality, format_type, session_id, download_id),
        daemon=True
    )
    thread.start()

    # return download_id so frontend can poll status
    return jsonify({
        "status": "started",
        "message": "Download started",
        "download_id": download_id
    })


@app.route('/download-file/<download_id>')
def download_file(download_id):
    session_id = ensure_session()

    file_map = user_files.get(session_id, {})
    file_path = file_map.get(download_id)

    if not file_path or not os.path.exists(file_path):
        return jsonify({"error": "File not found"}), 404

    return send_file(
        file_path,
        as_attachment=True,
        download_name=os.path.basename(file_path)
    )


@app.route('/status/<download_id>')
def get_status(download_id):
    session_id = ensure_session()
    status_map = user_downloads.get(session_id, {})
    status = status_map.get(download_id, {
        "status": "ready",
        "message": "Ready to download",
        "downloaded_mb": 0,
        "total_mb": 0,
        "percent": 0,
        "filename": ""
    })
    return jsonify(status)


if __name__ == '__main__':
    # debug True sirf development ke liye
    app.run(debug=True, host='0.0.0.0', port=5000)
