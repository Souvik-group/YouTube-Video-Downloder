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

# Store download status for each user session
user_downloads = {}
user_files = {}

def progress_hook(session_id):
    def hook(d):
        if d['status'] == 'downloading':
            downloaded = d.get('downloaded_bytes', 0)
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            
            downloaded_mb = round(downloaded / (1024 * 1024), 2)
            total_mb = round(total / (1024 * 1024), 2) if total > 0 else 0
            percent = round((downloaded / total) * 100, 1) if total > 0 else 0
            
            user_downloads[session_id] = {
                "status": "downloading",
                "message": f"Downloading... {downloaded_mb} MB / {total_mb} MB ({percent}%)",
                "downloaded_mb": downloaded_mb,
                "total_mb": total_mb,
                "percent": percent
            }
        elif d['status'] == 'finished':
            user_downloads[session_id] = {
                "status": "processing",
                "message": "Processing... Almost done!",
                "percent": 100
            }
    return hook

def download_video(url, quality, format_type, session_id):
    try:
        save_path = f"downloads/{session_id}"
        user_downloads[session_id] = {"status": "downloading", "message": "Initializing...", "downloaded_mb": 0, "total_mb": 0, "percent": 0, "filename": ""}
        
        if not os.path.exists(save_path):
            os.makedirs(save_path)
        
        ydl_opts = {
            'outtmpl': f'{save_path}/%(title)s.%(ext)s',
            'progress_hooks': [progress_hook(session_id)],
        }
        
        if format_type == "mp3":
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }]
            })
        else:
            # Enterprise-grade configuration - force high-quality audio merge
            if shutil.which('ffmpeg'):
                ydl_opts.update({
                    'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio',
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
                })
            else:
                # FFmpeg required for high-quality audio
                raise Exception("FFmpeg is required for high-quality MP4 audio. Please install FFmpeg.")
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            ydl.download([url])
            
            # Find the actual downloaded file
            for file in os.listdir(save_path):
                if file.startswith(info['title'][:20]):
                    user_files[session_id] = os.path.join(save_path, file)
                    break
        
        user_downloads[session_id] = {"status": "completed", "message": "Download completed! Click to download to your device.", "percent": 100, "filename": os.path.basename(user_files.get(session_id, ""))}
    except Exception as e:
        user_downloads[session_id] = {"status": "error", "message": f"Download failed: {str(e)}", "percent": 0, "filename": ""}

@app.route('/')
def index():
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def start_download():
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
    
    session_id = session['user_id']
    data = request.json
    url = data.get('url', '').strip()
    quality = data.get('quality', '720p')
    format_type = data.get('format', 'mp4')
    
    if not url:
        return jsonify({"status": "error", "message": "Please enter a YouTube URL"})
    
    # Reset status for this user
    user_downloads[session_id] = {"status": "ready", "message": "Ready to download", "downloaded_mb": 0, "total_mb": 0, "percent": 0, "filename": ""}
    
    thread = threading.Thread(target=download_video, args=(url, quality, format_type, session_id))
    thread.daemon = True
    thread.start()
    
    return jsonify({"status": "started", "message": "Download started"})

@app.route('/download-file')
def download_file():
    if 'user_id' not in session:
        return jsonify({"error": "Session not found"}), 404
    
    session_id = session['user_id']
    file_path = user_files.get(session_id)
    
    if file_path and os.path.exists(file_path):
        return send_file(file_path, as_attachment=True, download_name=os.path.basename(file_path))
    else:
        return jsonify({"error": "File not found"}), 404

@app.route('/status')
def get_status():
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
    
    session_id = session['user_id']
    return jsonify(user_downloads.get(session_id, {"status": "ready", "message": "Ready to download", "downloaded_mb": 0, "total_mb": 0, "percent": 0}))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
