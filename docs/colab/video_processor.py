# -*- coding: utf-8 -*-
"""
视频处理工具脚本

该脚本集成了一系列功能，用于自动化处理在线视频（如 YouTube），主要流程包括：
1. 下载视频。
2. 使用 Whisper 模型进行语音转文字（转写）。
3. （可选）使用 DeepSeek 模型将文本翻译成中文。
4. 将生成的文本文件保存到 Google Drive。

此脚本设计在 Google Colab 环境中运行，并依赖于 Google Drive 的挂载以及相关的 API 密钥配置。

主要依赖库:
- yt-dlp: 用于下载视频。
- openai-whisper: 用于语音转写。
- moviepy: 用于视频/音频处理。
- openai: 作为 DeepSeek API 的客户端。
- google-colab, google-auth-oauthlib, google-api-python-client: 用于 Google 服务集成。
- pandas: 用于处理来自 Google Sheets 的数据。
"""

import os
import re
import time
import torch
import yt_dlp
import whisper
import moviepy.editor as mp
from openai import OpenAI
from google.colab import drive
import datetime
import pickle
import pandas as pd
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
# from google.auth.transport.requests import Request # 在 authenticate_google_api 中 creds.refresh 需要

# ================================
# 全局配置与常量
# ================================

# Google API 相关设置
# 定义访问 Google Drive 和 Google Sheets 所需的权限范围
SCOPES = ['https://www.googleapis.com/auth/drive',
          'https://www.googleapis.com/auth/spreadsheets']
# 缓存 Google API 凭证的文件名
GOOGLE_DOC_TOKEN = 'token.pickle'
# Google API 客户端密钥文件名
GOOGLE_DOC_CREDS = 'credentials.json'


# ================================
# 核心工具函数
# ================================

def sanitize_filename(filename: str) -> str:
    """
    清理文件名，移除操作系统不允许的特殊字符，并将空格替换为下划线。
    这可以防止在保存文件时因非法字符而导致错误。

    Args:
        filename (str): 原始文件名。

    Returns:
        str: 清理后的安全文件名。
    """
    return re.sub(r'[\/*?:"<>| ]', "_", filename)

# ================================
# 1. 视频下载模块
# ================================

def download_video(url: str, out_dir: str = "./content") -> (str, str):
    """
    使用 yt-dlp 从指定 URL 下载视频。

    Args:
        url (str): 视频的 URL。
        out_dir (str): 视频下载后保存的目录。

    Returns:
        tuple: (下载的视频文件路径, 视频标题)。如果下载失败，则返回 (None, None)。
    """
    os.makedirs(out_dir, exist_ok=True)
    # 使用标题作为文件名模板
    outtmpl = os.path.join(out_dir, "%(title)s.%(ext)s")

    ydl_opts = {
        'outtmpl': outtmpl,
        'format': 'bestvideo+bestaudio/best',  # 下载最佳画质和音质
        'merge_output_format': 'mp4',         # 如果视频和音频是分开的，则合并为 mp4
        'quiet': False,                       # 在控制台打印下载日志
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            downloaded_path = ydl.prepare_filename(info)
            downloaded_title = info.get('title', "downloaded_video")

            if not os.path.exists(downloaded_path):
                print(f"[错误] 视频文件下载后未找到: {downloaded_path}")
                return None, None

        return downloaded_path, downloaded_title
    except Exception as e:
        print(f"[错误] 下载视频时发生异常: {e}")
        return None, None

# ================================
# 2. 语音转写模块
# ================================

def transcribe_video_to_text(video_path: str, audio_path: str = None) -> str:
    """
    使用 OpenAI Whisper 模型将视频文件转写为纯文本。

    Args:
        video_path (str): 本地视频文件的路径。
        audio_path (str, optional): 如果提供，会先将视频中的音频提取到此路径。
                                    这在处理某些视频格式时可能更稳定或高效。

    Returns:
        str: 转写得到的纯文本内容。
    """
    # 如果指定了 audio_path，则先使用 moviepy 提取音频
    if audio_path:
        print(f"[INFO] 正在从视频中提取音频到: {audio_path}")
        try:
            clip = mp.VideoFileClip(video_path)
            clip.audio.write_audiofile(audio_path, codec='aac')
            target_file = audio_path
        except Exception as e:
            print(f"[警告] 提取音频失败: {e}。将直接转写视频文件。")
            target_file = video_path
    else:
        target_file = video_path

    # 自动检测可用的计算设备（优先使用 GPU）
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[INFO] Whisper 使用的设备: {device}")

    # 加载 Whisper 模型（"base" 是一个中等大小的模型）
    model = whisper.load_model("base", device=device)

    print(f"[INFO] 正在转写文件，请稍候... ({target_file})")
    result = model.transcribe(target_file)
    text = result.get("text", "").strip()

    print("[INFO] 转写完成。")
    return text

# ================================
# 3. 文本翻译模块
# ================================

# 初始化 API 客户端
# 注意：这里虽然使用 OpenAI 的库，但通过 base_url 将请求指向了 DeepSeek 的兼容 API 端点。
# 请确保已在环境中设置了 DEEPSEEK_API_KEY。
try:
    client = OpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.lkeap.cloud.tencent.com/v1" # 示例 URL，请替换为你的
    )
except Exception as e:
    client = None
    print(f"[警告] 初始化 DeepSeek 客户端失败: {e}")


def translate_text_deepseek(content: str, target_language="zh") -> str:
    """
    调用 DeepSeek API 将文本翻译成指定语言。

    Args:
        content (str): 需要翻译的原始文本。
        target_language (str): 目标语言代码（例如 "zh" 表示中文）。

    Returns:
        str: 翻译后的文本。如果翻译失败，则返回原始文本。
    """
    if not client:
        print("[警告] DeepSeek 客户端未初始化，跳过翻译。")
        return content
        
    try:
        print("[INFO] 正在调用 DeepSeek API 进行翻译...")
        response = client.chat.completions.create(
            model="deepseek-v3",  # 指定使用的模型
            messages=[
                {"role": "system", "content": "You are a professional translator."},
                {"role": "user", "content": f"Translate the following text into {target_language}: {content}"}
            ],
            stream=False
        )
        translated_text = response.choices[0].message.content.strip()
        print("[INFO] 翻译完成。")
        return translated_text
    except Exception as e:
        print(f"[警告] DeepSeek 翻译出错: {e}")
        return content # 出错时返回原文

# ================================
# 4. Google 服务集成模块
# ================================

def load_driver():
    """
    检查并挂载 Google Drive 到 Colab 环境。
    """
    mount_path = "/content/drive"
    try:
        if not os.path.exists(mount_path) or len(os.listdir(mount_path)) == 0:
            print("Google Drive 未挂载，正在尝试挂载...")
            drive.mount(mount_path, force_remount=False)
            print("Google Drive 挂载成功。")
        else:
            print("Google Drive 已挂载。")
    except Exception as e:
        print(f"[错误] 挂载 Google Drive 失败: {e}")
        return

# --- 以下是与 Google Sheets 和 Drive API 交互的函数 ---
# --- 目前在主逻辑中被注释掉了，但提供了完整的自动化框架 ---

def authenticate_google_api():
    """
    通过 OAuth 2.0 对用户进行身份验证，并缓存凭证。
    如果存在 token.pickle 文件，则直接加载；否则，通过浏览器引导用户授权。
    """
    creds = None
    if os.path.exists(GOOGLE_DOC_TOKEN):
        with open(GOOGLE_DOC_TOKEN, 'rb') as token:
            creds = pickle.load(token)

    # 如果凭证无效或已过期，则刷新或重新获取
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # from google.auth.transport.requests import Request
            # creds.refresh(Request()) # 如果需要自动刷新，取消此行注释
            pass
        else:
            # 需要 credentials.json 文件来进行首次授权
            if not os.path.exists(GOOGLE_DOC_CREDS):
                print(f"[错误] 缺少 Google API 凭证文件: {GOOGLE_DOC_CREDS}")
                return None
            flow = InstalledAppFlow.from_client_secrets_file(GOOGLE_DOC_CREDS, SCOPES)
            creds = flow.run_local_server(port=0)

        # 保存凭证以备下次使用
        with open(GOOGLE_DOC_TOKEN, 'wb') as token:
            pickle.dump(creds, token)

    return creds

def get_sheet_data(spreadsheet_id, range_name, creds):
    """获取 Google Sheets 中的数据并返回一个 pandas DataFrame。"""
    try:
        service = build('sheets', 'v4', credentials=creds)
        sheet = service.spreadsheets()
        result = sheet.values().get(spreadsheetId=spreadsheet_id, range=range_name).execute()
        values = result.get('values', [])

        if not values:
            print('Google Sheet 中未找到数据。')
            return None
        else:
            # 假设第一行是列名
            return pd.DataFrame(values[1:], columns=values[0])
    except HttpError as error:
        print(f'访问 Google Sheet 时发生错误: {error}')
        return None

# def update_sheet_data(spreadsheet_id, range_name, values, creds):
#     """更新 Google Sheets 中的数据。"""
#     try:
#         service = build('sheets', 'v4', credentials=creds)
#         body = {'values': values}
#         result = service.spreadsheets().values().update(
#             spreadsheetId=spreadsheet_id, range=range_name,
#             body=body, valueInputOption="RAW"
#         ).execute()
#         print(f"成功更新 {result.get('updatedCells')} 个单元格。")
#     except HttpError as error:
#         print(f'更新 Google Sheet 时发生错误: {error}')

# ================================
# 主执行逻辑
# ================================

def process_video_pipeline(video_url, enable_translation=True):
    """
    执行完整的视频处理流程。

    Args:
        video_url (str): 要处理的视频的 URL。
        enable_translation (bool): 是否执行翻译步骤。
    """
    start_time = time.time()
    print(f"--- 开始处理视频: {video_url} ---")

    # 1. 下载视频
    downloaded_path, downloaded_title = download_video(video_url)
    if not downloaded_path:
        print("[错误] 下载视频失败，处理流程终止。")
        return

    # 2. 清理文件名并重命名
    safe_title = sanitize_filename(downloaded_title)
    video_dir = os.path.dirname(downloaded_path)
    safe_video_path = os.path.join(video_dir, f"{safe_title}.mp4")
    if downloaded_path != safe_video_path:
        if not os.path.exists(safe_video_path):
            os.rename(downloaded_path, safe_video_path)
        else:
            # 如果目标文件已存在，则直接使用，避免重复下载
            print(f"[INFO] 清理后的文件已存在: {safe_video_path}")

    # 3. 转写视频获取纯文本
    audio_path = os.path.join(video_dir, f"{safe_title}.aac")
    raw_text = transcribe_video_to_text(safe_video_path, audio_path=audio_path)
    if not raw_text:
        print("[警告] 转写结果为空，后续步骤可能无意义。")

    # 4. 挂载 Google Drive 并保存原始文本
    load_driver()
    # 定义在 Google Drive 中的输出目录
    out_dir = '/content/drive/My Drive/video_outputs/scripts'
    os.makedirs(out_dir, exist_ok=True)
    
    today_date = datetime.datetime.now().strftime('%Y-%m-%d')
    raw_txt_filename = f"{today_date}_{safe_title}_original.txt"
    raw_txt_path = os.path.join(out_dir, raw_txt_filename)

    with open(raw_txt_path, 'w', encoding='utf-8') as f:
        f.write(raw_text)
    print(f"[INFO] 原始文本已保存到 Google Drive: {raw_txt_path}")

    # 5. (可选) 翻译并保存
    if enable_translation:
        translated_text = translate_text_deepseek(raw_text, target_language="zh")
        
        translated_txt_filename = f"{today_date}_{safe_title}_translated.txt"
        translated_txt_path = os.path.join(out_dir, translated_txt_filename)
        
        with open(translated_txt_path, 'w', encoding='utf-8') as f:
            f.write(translated_text)
        print(f"[INFO] 翻译文本已保存到 Google Drive: {translated_txt_path}")

    # 6. 结束流程
    end_time = time.time()
    print(f"--- 任务完成，总用时: {end_time - start_time:.2f} 秒 ---")


# ================================
# 脚本入口点
# ================================

if __name__ == '__main__':
    # --- 用户配置区域 ---
    # 设置为 True 以启用翻译功能，设置为 False 则只进行转写
    TRANSLATE_ENABLED = False
    # 输入你想要处理的视频 URL
    VIDEO_URL = "https://www.youtube.com/watch?v=OAg0PU47d3o"
    
    # --- 执行主程序 ---
    process_video_pipeline(VIDEO_URL, enable_translation=TRANSLATE_ENABLED)

