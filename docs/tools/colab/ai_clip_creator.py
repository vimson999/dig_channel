# -*- coding: utf-8 -*-
"""
AI 驱动的短视频切片与双语字幕生成器

这是一个功能强大的自动化脚本，用于从长视频（如YouTube）中智能提取精华片段，
并将其制作成带有双语字幕的短视频。

核心流程:
1.  **下载视频**: 从指定 URL 下载原始视频。
2.  **生成原文SRT**: 使用 Whisper 模型对视频进行语音转写，生成带精确时间戳的原文 SRT 字幕文件。
3.  **翻译文本**: 调用 DeepSeek API 将原文 SRT 中的所有文本翻译成中文。
4.  **生成翻译SRT**: 将翻译好的中文文本与原文 SRT 的时间戳对齐，生成带时间戳的翻译 SRT 文件。
5.  **合并为双语SRT**: 将原文和翻译的 SRT 文件合并，创建双语字幕文件。
6.  **AI智能切片**: 将原文内容发送给 DeepSeek API，让 AI 分析并推荐最具“爆款”潜力的视频片段，返回其开始和结束时间。
7.  **切割与烧录**: 使用 FFmpeg 根据 AI 给出的时间戳切割视频，并将双语字幕“烧录”（硬编码）到视频画面上，最终输出多个短视频文件。

设计环境: Google Colab
"""

import os
import re
import time
import json
import torch
import ffmpeg
import yt_dlp
import whisper
import moviepy.editor as mp
from openai import OpenAI
from google.colab import drive

##############################################################
# 1. 配置与工具函数 (Utilities & Configuration)
##############################################################

def sanitize_filename(filename: str) -> str:
    """
    清理文件名，移除操作系统不允许的特殊字符，并将空格替换为下划线。
    """
    return re.sub(r'[\/*?:"<>| ]', "_", filename)


def parse_srt_time_to_seconds(srt_time: str) -> float:
    """
    将 SRT 时间格式 'HH:MM:SS,mmm' 转换为总秒数（浮点型）。
    """
    try:
        hhmmss, mmm = srt_time.split(',')
        hh, mm, ss = hhmmss.split(':')
        return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(mmm) / 1000
    except ValueError:
        # 兼容没有毫秒的情况
        hh, mm, ss = srt_time.split(':')
        return int(hh) * 3600 + int(mm) * 60 + int(ss)


def seconds_to_srt_time(seconds: float) -> str:
    """
    将总秒数（浮点型）转换为 SRT 标准时间格式 'HH:MM:SS,mmm'。
    """
    import math
    s_int = int(math.floor(seconds))
    ms = int((seconds - s_int) * 1000)
    hh = s_int // 3600
    s_int %= 3600
    mm = s_int // 60
    ss = s_int % 60
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def parse_srt_blocks(srt_file: str) -> list:
    """
    解析完整的 SRT 文件，将其内容转换为结构化的字典列表。
    每个字典代表一个字幕块。

    Returns:
    [
      { 'index': 1, 'start': 12.345, 'end': 15.678, 'text': '这是字幕内容' },
      ...
    ]
    """
    if not os.path.exists(srt_file):
        print(f"[警告] SRT文件不存在: {srt_file}")
        return []

    with open(srt_file, 'r', encoding='utf-8') as f:
        content = f.read().strip()

    blocks = re.split(r'\n\s*\n', content) # 按空行分割字幕块
    results = []

    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 2:
            continue

        try:
            index = int(lines[0])
            start_str, end_str = [t.strip() for t in lines[1].split('-->')]
            text = "\n".join(lines[2:]).strip()

            results.append({
                'index': index,
                'start': parse_srt_time_to_seconds(start_str),
                'end':   parse_srt_time_to_seconds(end_str),
                'text':  text
            })
        except (ValueError, IndexError) as e:
            print(f"[警告] 解析SRT块失败: {block}, 错误: {e}")
            continue

    return results


def extract_raw_subtitle_text(srt_path: str) -> str:
    """
    从 SRT 文件中仅提取所有字幕的纯文本内容，用于后续的整体翻译。
    """
    segments = parse_srt_blocks(srt_path)
    return "\n".join([seg["text"] for seg in segments])


##############################################################
# 2. 下载视频 (Video Download)
##############################################################

def download_video(url: str, out_dir="/content") -> (str, str, str):
    """
    使用 yt-dlp 下载视频。

    Returns:
        tuple: (视频文件路径, 视频标题, 缩略图路径或None)
    """
    os.makedirs(out_dir, exist_ok=True)
    outtmpl = os.path.join(out_dir, "%(title)s.%(ext)s")

    ydl_opts = {
        'outtmpl': outtmpl,
        'format': 'bestvideo+bestaudio/best',
        'merge_output_format': 'mp4',
        'writethumbnail': True, # 请求下载缩略图
        'quiet': False,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
            downloaded_file_path = ydl.prepare_filename(info)
            downloaded_title = info.get('title', "downloaded_video")
            # yt-dlp 会将缩略图与视频文件保存在一起，扩展名通常是 .jpg 或 .webp
            thumbnail_file = os.path.splitext(downloaded_file_path)[0] + '.jpg'
            if not os.path.exists(thumbnail_file):
                 thumbnail_file = os.path.splitext(downloaded_file_path)[0] + '.webp'
                 if not os.path.exists(thumbnail_file):
                    thumbnail_file = None

            if not os.path.exists(downloaded_file_path):
                print(f"[错误] 视频下载后未找到: {downloaded_file_path}")
                return None, None, None

            return downloaded_file_path, downloaded_title, thumbnail_file
        except Exception as e:
            print(f"[错误] 下载视频时发生异常: {e}")
            return None, None, None


##############################################################
# 3. Whisper 转写 -> 生成原文 SRT
##############################################################

def transcribe_video_to_srt(video_path: str, srt_path: str, audio_path: str = None):
    """
    使用 Whisper 模型转写视频，并直接输出带时间戳的 SRT 文件。
    """
    if audio_path and not os.path.exists(audio_path):
        try:
            print(f"[INFO] 正在提取音频到: {audio_path}")
            video = mp.VideoFileClip(video_path)
            video.audio.write_audiofile(audio_path, codec='aac')
            target_file = audio_path
        except Exception as e:
            print(f"[警告] 提取音频失败: {e}。将直接转写原视频。")
            target_file = video_path
    else:
        target_file = video_path if not audio_path else audio_path

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[INFO] Whisper 使用设备: {device}")
    model = whisper.load_model("base", device=device)

    print(f"[INFO] Whisper 正在转写，这可能需要很长时间...")
    result = model.transcribe(target_file, verbose=True) # verbose=True 显示进度

    # 将 Whisper 的结果格式化并写入 SRT 文件
    with open(srt_path, 'w', encoding='utf-8') as f:
        for i, seg in enumerate(result["segments"], start=1):
            f.write(f"{i}\n")
            f.write(f"{seconds_to_srt_time(seg['start'])} --> {seconds_to_srt_time(seg['end'])}\n")
            f.write(f"{seg['text'].strip()}\n\n")

    print(f"已生成原文SRT: {srt_path}")


##############################################################
# 4. DeepSeek 翻译 (返回纯文本)
##############################################################

# 初始化API客户端
try:
    client = OpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com"
    )
except Exception:
    client = None
    print("[警告] 未能初始化 DeepSeek API 客户端。请检查 DEEPSEEK_API_KEY 环境变量。")

def translate_text_deepseek(content: str, target_language="zh") -> str:
    """
    调用 DeepSeek API 翻译大段文本。
    """
    if not client:
        return "[错误] DeepSeek 客户端未初始化，无法翻译。"

    try:
        print("[INFO] 正在调用 DeepSeek API 进行翻译...")
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "You are a professional translator."},
                {"role": "user", "content": f"Translate the following text into {target_language}. Maintain the line breaks from the original text:\n\n{content}"}
            ],
            stream=False
        )
        translated_text = response.choices[0].message.content.strip()
        print("[INFO] 翻译完成。")
        return translated_text
    except Exception as e:
        print(f"[警告] DeepSeek 翻译出错: {e}")
        return content # 出错时返回原文


##############################################################
# 5. 对齐翻译文本 -> 生成翻译 SRT
##############################################################

def create_translated_srt_with_timestamps(original_srt_path:
str,
                                          translated_text: str,
                                          output_srt_path: str):
    """
    将一个纯文本的翻译结果与原始SRT文件的时间戳进行对齐，生成新的翻译SRT文件。
    这是一个简化的对齐方法，它假设翻译后的文本行数与原文的字幕块数相同。
    """
    o_segs = parse_srt_blocks(original_srt_path)
    if not o_segs:
        print("[错误] 原文SRT为空或无法解析，无法生成翻译SRT。")
        return

    # 将翻译好的大段文本按换行符切分成行
    translated_lines = [line.strip() for line in translated_text.split('\n') if line.strip()]

    print(f"原文SRT段数: {len(o_segs)}, 翻译文本行数: {len(translated_lines)}")

    with open(output_srt_path, 'w', encoding='utf-8') as f:
        for i, seg in enumerate(o_segs):
            # 如果翻译的行数少于原文的段数，用占位符填充
            text_line = translated_lines[i] if i < len(translated_lines) else "(翻译缺失)"
            f.write(f"{seg['index']}\n")
            f.write(f"{seconds_to_srt_time(seg['start'])} --> {seconds_to_srt_time(seg['end'])}\n")
            f.write(text_line + "\n\n")

    print(f"已生成带时间戳的翻译SRT: {output_srt_path}")


##############################################################
# 6. 合并 -> 双语SRT
##############################################################

def merge_subtitles(original_srt_path, translated_srt_path, bilingual_srt_path):
    """
    将原文和翻译两个SRT文件合并成一个双语SRT文件。
    字幕格式为：
    原文
    译文
    """
    o_segs = parse_srt_blocks(original_srt_path)
    t_segs = parse_srt_blocks(translated_srt_path)
    
    if not o_segs or not t_segs:
        print("[警告] 原文或翻译SRT为空，无法合并。")
        return

    length = min(len(o_segs), len(t_segs))
    if len(o_segs) != len(t_segs):
        print(f"[警告] 原文和翻译SRT段数不匹配({len(o_segs)} vs {len(t_segs)})，将按最短长度合并。")

    with open(bilingual_srt_path, 'w', encoding='utf-8') as f:
        for i in range(length):
            # 拼接原文和译文，中间用换行符隔开
            combined_text = o_segs[i]['text'] + "\n" + t_segs[i]['text']
            f.write(f"{o_segs[i]['index']}\n")
            f.write(f"{seconds_to_srt_time(o_segs[i]['start'])} --> {seconds_to_srt_time(o_segs[i]['end'])}\n")
            f.write(combined_text + "\n\n")

    print(f"已生成双语字幕文件: {bilingual_srt_path}")


##############################################################
# 7. 获取 AI 分段时间戳 (AI-Powered Clip Selection)
##############################################################

def get_split_timestamps_deepseek(subtitles: list, max_clip_duration=300) -> list:
    """
    调用 DeepSeek API，发送字幕内容，让 AI 分析并返回推荐的剪辑时间点。
    """
    if not subtitles:
        return []

    # 格式化字幕内容，以便AI更好地理解
    formatted_text = "\n".join([
        f"[{seg['start']:.2f}s - {seg['end']:.2f}s]: {seg['text']}"
        for seg in subtitles if seg.get('text')
    ])

    # 提示工程（Prompt Engineering）: 这是指导AI完成任务的关键
    system_message = (
        "你是资深的内容运营和短视频流量专家。"
        "我会给你一段带时间戳的视频字幕。你的任务是：分析内容，找出其中最具吸引力、最可能成为爆款的片段。"
        f"请确保每个推荐的片段时长在60秒到{max_clip_duration}秒之间。"
        "返回一个JSON列表，其中每个对象包含'start'和'end'两个键，值为浮点型的秒数。例如：[{'start': 120.5, 'end': 185.2}]。"
        "除了这个JSON列表，不要返回任何其他解释或文字。"
        f"以下是字幕内容：\n\n{formatted_text}"
    )

    try:
        print("[INFO] 正在请求 DeepSeek 分析最佳剪辑点...")
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": system_message}],
            stream=False,
            temperature=0.2, # 较低的温度使输出更稳定
        )
        resp_content = response.choices[0].message.content.strip()
        print(f"DeepSeek 返回内容: {resp_content}")

        # 从返回内容中提取JSON部分
        json_match = re.search(r'\[(.*?)\]', resp_content, re.DOTALL)
        if not json_match:
            print("[警告] DeepSeek 返回的内容中未找到有效的JSON列表。")
            return []
        
        timestamps = json.loads(json_match.group(0))
        print(f"[INFO] 成功解析出 {len(timestamps)} 个推荐剪辑点。")
        return timestamps

    except Exception as e:
        print(f"[警告] AI切分时出错: {e}")
        return []


##############################################################
# 8. 切分视频并烧录字幕 (Video Clipping & Hardcoding)
##############################################################

def write_srt_file(subs: list, output_path: str):
    """一个辅助函数，用于将字幕块列表写回SRT文件。"""
    with open(output_path, 'w', encoding='utf-8') as f:
        for i, s in enumerate(subs, start=1):
            f.write(f"{i}\n")
            f.write(f"{seconds_to_srt_time(s['start'])} --> {seconds_to_srt_time(s['end'])}\n")
            f.write(s['text'] + "\n\n")

def split_video_by_timestamps_with_subtitles(video_path, timestamps, bilingual_srt_path, output_dir="/content/short_videos"):
    """
    根据给定的时间戳列表，使用 FFmpeg 切割视频，并将双语字幕硬编码到视频上。
    """
    os.makedirs(output_dir, exist_ok=True)
    all_subs = parse_srt_blocks(bilingual_srt_path)

    for i, seg in enumerate(timestamps):
        start_sec = float(seg["start"])
        end_sec   = float(seg["end"])
        clip_path = os.path.join(output_dir, f"clip_{i + 1}_bilingual.mp4")

        # 1. 为当前切片创建一个临时的、时间轴重置的SRT文件
        part_subs = []
        for sub in all_subs:
            # 检查字幕时间与切片时间是否有交集
            if sub['end'] > start_sec and sub['start'] < end_sec:
                # 将字幕的绝对时间映射到切片的相对时间（从0开始）
                new_start = max(0, sub['start'] - start_sec)
                new_end   = min(end_sec - start_sec, sub['end'] - start_sec)
                part_subs.append({'start': new_start, 'end': new_end, 'text': sub['text']})
        
        part_srt_path = os.path.join(output_dir, f"clip_{i+1}_temp.srt")
        write_srt_file(part_subs, part_srt_path)

        # 2. 使用 FFmpeg 进行切割和烧录
        print(f"正在处理切片 {i+1}/{len(timestamps)}: {start_sec:.2f}s - {end_sec:.2f}s -> {clip_path}")
        try:
            # 构建 FFmpeg 的视频滤镜 (vf)
            # force_style 内的参数用于精细控制字幕样式
            subtitle_style = "force_style='Fontname=Arial,Fontsize=16,PrimaryColour=&H00FFFFFF,SecondaryColour=&H000000FF,OutlineColour=&H00000000,BackColour=&H80000000,Bold=-1,Outline=1,Shadow=0.5,Alignment=2,MarginV=25'"
            vf_filter = f"subtitles='{part_srt_path}':{subtitle_style}"

            (
                ffmpeg
                .input(video_path, ss=start_sec, to=end_sec)
                .output(clip_path, vf=vf_filter, vcodec="libx264", acodec="aac", preset="fast")
                .run(overwrite_output=True, quiet=True)
            )
            print(f"已生成短视频: {clip_path}")
            os.remove(part_srt_path) # 删除临时SRT文件
        except ffmpeg.Error as e:
            print(f"[错误] FFmpeg 处理切片 {i+1} 失败。")
            print("FFmpeg Stderr:", e.stderr.decode("utf-8"))


##############################################################
# 9. 主流程 (Main Workflow)
##############################################################

def main_pipeline():
    """执行整个自动化流程的主函数"""
    start_time = time.time()
    try:
        # --- 用户配置 ---
        VIDEO_URL = "https://www.youtube.com/watch?v=2FMu4NjoR9U"
        # AI推荐剪辑时，单个片段的最大时长（秒）
        MAX_CLIP_DURATION = 120
        # 最多生成多少个短视频
        MAX_CLIPS_TO_GENERATE = 2
        # 工作目录
        WORK_DIR = "/content"

        # 1. 下载视频
        downloaded_path, downloaded_title, _ = download_video(VIDEO_URL, out_dir=WORK_DIR)
        if not downloaded_path: return

        safe_title = sanitize_filename(downloaded_title)
        safe_video_path = os.path.join(WORK_DIR, f"{safe_title}.mp4")
        if not os.path.exists(safe_video_path):
            os.rename(downloaded_path, safe_video_path)

        # 2. 生成原文 SRT
        original_srt = os.path.join(WORK_DIR, f"{safe_title}_original.srt")
        audio_path   = os.path.join(WORK_DIR, f"{safe_title}.aac")
        transcribe_video_to_srt(safe_video_path, original_srt, audio_path)

        # 3. 翻译
        raw_text = extract_raw_subtitle_text(original_srt)
        translated_text = translate_text_deepseek(raw_text, target_language="zh")
        
        # 4. 生成翻译 SRT
        translated_srt = os.path.join(WORK_DIR, f"{safe_title}_translated.srt")
        create_translated_srt_with_timestamps(original_srt, translated_text, translated_srt)

        # 5. 合并为双语 SRT
        bilingual_srt = os.path.join(WORK_DIR, f"{safe_title}_bilingual.srt")
        merge_subtitles(original_srt, translated_srt, bilingual_srt)
        if not os.path.exists(bilingual_srt):
            print("[错误] 双语字幕生成失败，流程终止。")
            return

        # 6. AI 获取切片时间戳
        srt_segments = parse_srt_blocks(original_srt)
        timestamps = get_split_timestamps_deepseek(srt_segments, max_clip_duration=MAX_CLIP_DURATION)
        if not timestamps:
            print("[警告] AI未能返回有效切分点，流程终止。")
            return
        
        # 7. 切分视频并烧录字幕
        split_video_by_timestamps_with_subtitles(
            video_path=safe_video_path,
            timestamps=timestamps[:MAX_CLIPS_TO_GENERATE], # 只取前N个
            bilingual_srt_path=bilingual_srt,
            output_dir=os.path.join(WORK_DIR, "short_videos")
        )

    except Exception as e:
        print(f"[严重错误] 主流程发生未捕获异常: {e}")
    finally:
        print(f"--- 总流程结束，总用时: {time.time() - start_time:.2f} 秒 ---")


if __name__ == "__main__":
    # (可选) 挂载 Google Drive 以便持久化存储
    # load_driver()
    main_pipeline()
