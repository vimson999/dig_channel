# -*- coding: utf-8 -*-
"""
多功能 YouTube 自动化管理脚本

这是一个高度集成的自动化工具，专为在 Google Colab 环境中管理和发布 YouTube 视频而设计。
它涵盖了从视频下载、内容再处理到多频道、定时发布的完整工作流程。

核心功能模块:
1.  **视频下载 (yt-dlp)**: 从指定 URL 下载视频及其封面。
2.  **音频与字幕提取 (Whisper)**: 从视频中分离音频，并使用 Whisper 模型生成带时间戳的 SRT 字幕文件和纯文本文件。
3.  **视频二次创作**: 
    - 将提取的音频和字幕与一个全新的背景视频（素材视频）合并。
    - 支持添加片头视频，将多个片段拼接成一个完整的最终视频。
4.  **YouTube API 集成**: 
    - **OAuth 2.0 认证**: 实现了完整的 Google 账户认证流程，支持多账号（通过不同的 token 文件）。
    - **视频上传**: 上传视频到指定频道，可设置标题、描述、标签、分类、隐私状态。
    - **定时发布**: 支持将视频设置为在未来的特定时间点自动发布。
    - **播放列表管理**: 自动将上传的视频添加到指定的播放列表。
    - **自定义封面**: 上传并设置视频的自定义封面图。
    - **重复检查**: 提供搜索功能，可在上传前检查 YouTube 上是否已存在同名视频，避免重复发布。
5.  **多频道工作流**: 预设了多个函数（如 `to_jiaonang`, `to_qian`），每个函数代表一个特定频道的完整自动化发布流程，包含独立的视频源、发布计划和频道配置。

"""

# ====================================================================
# 1. 导入依赖库 (Import Libraries)
# ====================================================================
import os
import shutil
import time
import random
from datetime import datetime, timedelta

import torch
import whisper
import yt_dlp
import pytz
import moviepy.editor as mp
from moviepy.editor import (
    VideoFileClip, AudioFileClip, TextClip, 
    CompositeVideoClip, concatenate_videoclips
)
from moviepy.video.fx import all as vfx

# Google Colab and API specific imports
from google.colab import drive
from google.colab import files
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


# ====================================================================
# 2. 视频下载模块 (Video Downloading)
# ====================================================================

def download_video(url: str, file_name: str = None) -> tuple:
    """
    使用 yt-dlp 从指定 URL 下载视频和封面。

    Args:
        url (str): 视频的 URL。
        file_name (str, optional): 指定的输出文件名（不含扩展名）。如果为 None，则使用视频原标题。

    Returns:
        tuple: (视频文件路径, 视频标题, 封面图路径) 或 (None, None, None) 如果失败。
    """
    output_dir = '/content/'  # Colab 默认工作目录
    outtmpl = os.path.join(output_dir, f'{file_name}.%(ext)s' if file_name else '%(title)s.%(ext)s')

    ydl_opts = {
        'outtmpl': outtmpl,
        'format': 'bestvideo+bestaudio/best',
        'merge_output_format': 'mp4',
        'writethumbnail': True,
        'quiet': False,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
            video_path = ydl.prepare_filename(info)
            video_title = info.get('title')

            if not os.path.exists(video_path):
                print(f"[警告] 视频下载后未在预期路径找到: {video_path}。尝试模糊匹配...")
                # 这是一个健壮性处理，有时文件名可能因特殊字符而与预期不符
                dir_name = os.path.dirname(video_path) or output_dir
                base_name = os.path.splitext(video_title)[0]
                possible_files = [f for f in os.listdir(dir_name) if base_name in f and f.endswith('.mp4')]
                if possible_files:
                    video_path = os.path.join(dir_name, possible_files[0])
                    print(f"[INFO] 成功匹配到实际文件: {video_path}")
                else:
                    print("[错误] 无法找到下载的视频文件。")
                    return None, None, None

            thumbnail_path = os.path.splitext(video_path)[0] + '.jpg'
            if not os.path.exists(thumbnail_path):
                thumbnail_path = None
                print("[警告] 未找到封面图。")
            
            print(f"[INFO] 视频下载成功: {video_path}")
            return video_path, video_title, thumbnail_path

        except Exception as e:
            print(f"[错误] 下载过程中发生异常: {e}")
            return None, None, None


# ====================================================================
# 3. 音频与字幕处理 (Audio & Subtitle Processing)
# ====================================================================

def transcribe_video_and_extract_audio(video_path: str, output_audio_path: str, output_srt_path: str, full_text_path: str) -> str:
    """
    从视频中提取音频，并使用 Whisper 模型生成 SRT 字幕和纯文本文件。

    Args:
        video_path (str): 输入视频的路径。
        output_audio_path (str): 输出音频文件的路径。
        output_srt_path (str): 输出 SRT 字幕文件的路径。
        full_text_path (str): 输出纯文本字幕的路径。

    Returns:
        str: 生成的音频文件路径。
    """
    # 1. 提取音频
    print("[INFO] 正在从视频中提取音频...")
    video = mp.VideoFileClip(video_path)
    video.audio.write_audiofile(output_audio_path, codec='aac')

    # 2. 使用 Whisper 进行转写
    print("[INFO] 正在使用 Whisper 模型进行语音转写...")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = whisper.load_model("base", device=device)
    result = model.transcribe(video_path, fp16=(device=="cuda"))

    # 3. 保存为 SRT 字幕文件
    with open(output_srt_path, 'w', encoding='utf-8') as f:
        for idx, segment in enumerate(result["segments"], start=1):
            start = segment['start']
            end = segment['end']
            start_str = time.strftime("%H:%M:%S", time.gmtime(start)) + f",{int((start % 1) * 1000):03d}"
            end_str = time.strftime("%H:%M:%S", time.gmtime(end)) + f",{int((end % 1) * 1000):03d}"
            f.write(f"{idx}\n{start_str} --> {end_str}\n{segment['text']}\n\n")
    print(f"[INFO] SRT 字幕已保存至: {output_srt_path}")

    # 4. 保存为纯文本文件
    with open(full_text_path, 'w', encoding='utf-8') as f:
        f.write(result['text'])
    print(f"[INFO] 纯文本字幕已保存至: {full_text_path}")

    return output_audio_path

def parse_srt(subtitle_path: str) -> list:
    """解析 SRT 文件，返回包含时间戳和文本的字典列表。"""
    subtitles = []
    with open(subtitle_path, 'r', encoding='utf-8') as f:
        lines = f.read().split('\n\n')
        for block in lines:
            if not block.strip(): continue
            parts = block.strip().split('\n')
            if len(parts) < 3: continue
            try:
                time_line = parts[1]
                start_str, end_str = [t.strip() for t in time_line.split('-->')]
                text = " ".join(parts[2:])
                subtitles.append({
                    'start': convert_time_to_seconds(start_str),
                    'end': convert_time_to_seconds(end_str),
                    'text': text
                })
            except (ValueError, IndexError) as e:
                print(f"[警告] 解析 SRT 块失败: {parts}, 错误: {e}")
    return subtitles

def convert_time_to_seconds(time_str: str) -> float:
    """将 'hh:mm:ss,SSS' 格式的时间字符串转换为秒。"""
    h, m, s_ms = time_str.split(':')
    s, ms = s_ms.split(',')
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


# ====================================================================
# 4. 视频合成与处理 (Video Composition & Processing)
# ====================================================================

def add_audio_subtitles_to_video(video_path: str, audio_path: str, subtitle_path: str, output_path: str):
    """
    将指定的音频和字幕合并到一个背景视频上。
    """
    print("[INFO] 开始将音频和字幕合成到背景视频...")
    video_clip = VideoFileClip(video_path)
    audio_clip = AudioFileClip(audio_path)

    # 统一视频尺寸为 720p
    video_clip = video_clip.resize(newsize=(1280, 720))

    # 确保视频时长与音频时长匹配
    if audio_clip.duration > video_clip.duration:
        video_clip = video_clip.fx(vfx.loop, duration=audio_clip.duration)
    else:
        video_clip = video_clip.subclip(0, audio_clip.duration)

    final_video = video_clip.set_audio(audio_clip)

    # 创建字幕图层
    subtitles = parse_srt(subtitle_path)
    subtitle_clips = []
    for sub in subtitles:
        clip = TextClip(
            sub['text'],
            fontsize=40, color='#ffde00', font='/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
            size=(int(video_clip.w * 0.8), None), method='caption', align='center'
        ).set_position(('center', 'center')).set_start(sub['start']).set_end(sub['end'])
        subtitle_clips.append(clip)

    # 合成最终视频
    final_video_with_subtitles = CompositeVideoClip([final_video] + subtitle_clips)
    final_video_with_subtitles.write_videofile(
        output_path, codec='libx264', audio_codec='aac', 
        fps=24, threads=8, preset='fast'
    )
    print(f"[INFO] 合成视频已保存至: {output_path}")

def merge_videos(video_paths: list, output_path: str):
    """
    将多个视频文件按顺序拼接成一个视频。
    """
    print("[INFO] 正在合并多个视频片段...")
    clips = [VideoFileClip(p).resize((1280, 720)).set_fps(24) for p in video_paths]
    final_clip = concatenate_videoclips(clips)
    final_clip.write_videofile(output_path, codec='libx264', audio_codec='aac', fps=24, preset='fast')
    print(f"[INFO] 合并后的视频已保存至: {output_path}")


# ====================================================================
# 5. YouTube API 交互模块 (YouTube API Interaction)
# ====================================================================

def load_driver():
    """检查并挂载 Google Drive。"""
    mount_path = "/content/drive"
    if not os.path.exists(mount_path) or not os.listdir(mount_path):
        print("[INFO] 正在挂载 Google Drive...")
        drive.mount(mount_path, force_remount=False)
    else:
        print("[INFO] Google Drive 已挂载。")

def get_authenticated_service(token_file: str, secret_file: str):
    """通过 OAuth 2.0 认证并返回 YouTube API 服务对象。"""
    SCOPES = ['https://www.googleapis.com/auth/youtube.upload', 'https://www.googleapis.com/auth/youtube.force-ssl']
    credentials = None
    load_driver()

    if os.path.exists(token_file):
        credentials = Credentials.from_authorized_user_file(token_file, SCOPES)

    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            if not os.path.exists(secret_file):
                raise FileNotFoundError(f"[错误] 客户端密钥文件不存在: {secret_file}")
            flow = InstalledAppFlow.from_client_secrets_file(secret_file, SCOPES)
            credentials = flow.run_console()
        with open(token_file, "w") as token:
            token.write(credentials.to_json())
    
    return build("youtube", "v3", credentials=credentials)

def upload_video_to_youtube(**kwargs):
    """
    上传视频到 YouTube，支持定时发布、添加播放列表和自定义封面。
    """
    print("[INFO] 准备上传视频到 YouTube...")
    youtube = get_authenticated_service(kwargs['token_file'], kwargs['secret_file'])

    request_body = {
        "snippet": {
            "title": kwargs['title'], "description": kwargs['description'],
            "tags": kwargs['tags'], "categoryId": kwargs['category_id']
        },
        "status": {"privacyStatus": kwargs['privacy'], "madeForKids": False}
    }
    if kwargs.get('publish_at') and kwargs['privacy'] in ["private", "unlisted"]:
        request_body["status"]["publishAt"] = kwargs['publish_at']

    media = MediaFileUpload(kwargs['file_path'], mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=request_body, media_body=media)
    
    # 带重试逻辑的上传
    response = None
    retry = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                print(f"上传进度: {int(status.progress() * 100)}%")
        except Exception as e:
            if retry < 3:
                retry += 1
                print(f"[警告] 上传中断，正在重试 ({retry}/3)... 错误: {e}")
                time.sleep(5)
            else:
                print("[错误] 上传失败，已达最大重试次数。")
                return

    video_id = response.get('id')
    print(f"[成功] 视频已上传: https://www.youtube.com/watch?v={video_id}")

    if kwargs.get('playlist'):
        youtube.playlistItems().insert(part="snippet", body={
            "snippet": {"playlistId": kwargs['playlist'], "resourceId": {"kind": "youtube#video", "videoId": video_id}}
        }).execute()
        print(f"[INFO] 视频已添加到播放列表。")

    if kwargs.get('thumbnail_path'):
        youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(kwargs['thumbnail_path'])).execute()
        print("[INFO] 自定义封面已设置。")

def search_youtube_videos(token_file: str, secret_file: str, query: str, max_results=5) -> list:
    """根据关键词在 YouTube 上搜索视频，用于检查重复。"""
    youtube = get_authenticated_service(token_file, secret_file)
    response = youtube.search().list(part="snippet", q=query, type="video", maxResults=max_results).execute()
    return [{
        "video_id": item["id"]["videoId"],
        "title": item["snippet"]["title"],
        "url": f"https://www.youtube.com/watch?v={item['id']['videoId']}"
    } for item in response.get("items", [])]


# ====================================================================
# 6. 主流程与多频道工作流 (Main & Multi-Channel Workflows)
# ====================================================================

def process_and_upload_video(**kwargs):
    """
    集成的总流程函数，根据参数决定执行简单上传还是复杂的二次创作流程。
    """
    start_time = time.time()
    video_url = kwargs.get('video_url')
    
    if not kwargs.get('downloaded_path'):
        downloaded_path, downloaded_title, thumbnail_file = download_video(video_url, kwargs.get('fileName'))
        if not downloaded_path: return
        kwargs['downloaded_path'] = downloaded_path
        kwargs['title'] = downloaded_title
        kwargs['thumbnail_path'] = thumbnail_file

    if kwargs.get('need_trans', False):
        title = kwargs['title']
        output_final_video = f'/content/{title}_final.mp4'
        if not os.path.exists(output_final_video):
            audio_path = f'/content/{title}.aac'
            srt_path = f'/content/{title}.srt'
            txt_path = f'/content/{title}.txt'
            transcribe_video_and_extract_audio(kwargs['downloaded_path'], audio_path, srt_path, txt_path)

            driver_videos = ["driver.mp4", "driver1.mp4", "driver2.mp4"]
            driver_video_path = f"/content/drive/My Drive/video_bus/{random.choice(driver_videos)}"
            temp_output_video = f'/content/temp_{title}.mp4'
            add_audio_subtitles_to_video(driver_video_path, audio_path, srt_path, temp_output_video)

            intro_video_path = '/content/drive/My Drive/video_bus/pt1.mp4'
            merge_videos([intro_video_path, temp_output_video], output_final_video)
            files.download(txt_path) # 下载纯文本字幕供本地查看
        kwargs['file_path'] = output_final_video
    else:
        kwargs['file_path'] = kwargs['downloaded_path']

    upload_video_to_youtube(**kwargs)
    print(f"--- 流程结束，总用时: {time.time() - start_time:.2f} 秒 ---")


# --- 以下是为不同 YouTube 频道定制的自动化工作流 ---

def to_jiaonang():
    """“胶囊”频道的工作流：从B站下载视频并定时发布。"""
    print("--- 启动 [胶囊] 频道工作流 ---")
    videos = [{
        "url": "https://www.bilibili.com/video/BV1zBCAYdEEu/",
        "publish_time": (datetime.now() + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
    }]
    token_file = '/content/drive/My Drive/video_bus/jiaonang_token.json'
    secret_file = '/content/drive/My Drive/video_bus/jiaonang_client_secrets.json'
    
    for video in videos:
        process_and_upload_video(
            video_url=video["url"], playlist='PLaYXTQ6nlsIdly8WOyOC6LK1bMznvUsty',
            tags=["财经", "金融", "投资"], category_id='27', 
            token_file=token_file, secret_file=secret_file,
            privacy='private', publish_at=video["publish_time"]
        )

def to_qian():
    """“钱”频道的工作流：批量处理预设的URL列表，并按指定时间定时发布。"""
    print("--- 启动 [钱] 频道工作流 ---")
    video_data = [{
        "url": "https://www.bilibili.com/video/BV1dRrLY1EMm/", "publish_at": "2025-01-07T15:00:00Z"
    }]
    token_file = '/content/drive/My Drive/video_bus/qian_token.json'
    secret_file = '/content/drive/My Drive/video_bus/jiaonang_client_secrets.json'

    for video in video_data:
        process_and_upload_video(
            video_url=video["url"], playlist=None, 
            tags=["财经", "金融", "投资"], category_id='27', 
            token_file=token_file, secret_file=secret_file,
            privacy='private', publish_at=video["publish_at"]
        )

def to_jiefu():
    """“解夫”频道工作流：上传前先搜索YouTube，避免重复发布。"""
    print("--- 启动 [解夫] 频道工作流 ---")
    videos = [{
        "url": "https://www.bilibili.com/video/BV1qyrpYiEpH/",
        "query": "围攻张颂文！无凭被造谣，出轨又家暴，哭鸡又鸟嚎，都拿他起号！张颂文到底得罪谁了？",
        "publish_time": (datetime.now() + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
    }]
    token_file = '/content/drive/My Drive/video_bus/jiefu_token.json'
    secret_file = '/content/drive/My Drive/video_bus/jiaonang_client_secrets.json'

    for video in videos:
        results = search_youtube_videos(token_file, secret_file, video["query"])
        if any(result["title"].strip() == video["query"].strip() for result in results):
            print(f"[INFO] 视频已存在，跳过上传: {video['query']}")
        else:
            print(f"[INFO] 未找到匹配视频，准备上传: {video['url']}")
            process_and_upload_video(
                video_url=video["url"], playlist='PL4AZuASMnsbF5V9zaXDOixrBbvFVFRvus',
                tags=["娱乐", "明星", "八卦"], category_id='24',
                token_file=token_file, secret_file=secret_file,
                privacy='private', publish_at=video["publish_time"]
            )

def to_pet500():
    """“宠物500”频道工作流：从Google Drive文件夹读取视频文件并定时发布。"""
    print("--- 启动 [宠物500] 频道工作流 ---")
    video_folder = "/content/drive/My Drive/video_bus/pet500_videos"
    processed_folder = "/content/drive/My Drive/video_bus/pet500_processed"
    os.makedirs(processed_folder, exist_ok=True)

    video_files = [os.path.join(video_folder, f) for f in os.listdir(video_folder) if f.endswith(".mp4")]
    if not video_files: return print("[错误] 宠物视频文件夹为空。")

    # 设置发布时间为美国东部时间的明天9点、17点和后天20点
    us_eastern = pytz.timezone('US/Eastern')
    tomorrow = datetime.now(us_eastern) + timedelta(days=1)
    publish_times = [
        tomorrow.replace(hour=9, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%S%z"),
        tomorrow.replace(hour=17, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%S%z"),
        (tomorrow + timedelta(days=1)).replace(hour=20, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%S%z")
    ]

    token_file = "/content/drive/My Drive/video_bus/pet500_token.json"
    secret_file = "/content/drive/My Drive/video_bus/jiaonang_client_secrets.json"

    for i, file_path in enumerate(video_files[:3]):
        upload_video_to_youtube(
            file_path=file_path, title=os.path.splitext(os.path.basename(file_path))[0],
            description="Enjoy this adorable pet video! #Pets #Animals", category_id='15',
            tags=["Pets", "Dogs", "Cats"], playlist='PLwZBUzMzH00FBMDVoeJXPJZlTyPi2y64s',
            token_file=token_file, secret_file=secret_file,
            privacy='private', publish_at=publish_times[i]
        )
        shutil.move(file_path, processed_folder)


# ====================================================================
# 7. 脚本执行入口 (Execution Entry Point)
# ====================================================================

if __name__ == "__main__":
    print("=================================================")
    print("YouTube 自动化管理器已启动")
    print(f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=================================================")
    
    # --- 选择要执行的工作流 ---
    # --- 取消下面一行的注释以运行特定频道的任务 ---
    
    # to_jiaonang()
    to_qian()
    # to_jiefu()
    # to_pet500()

    print("\n[INFO] 所有选定任务已执行完毕。")
