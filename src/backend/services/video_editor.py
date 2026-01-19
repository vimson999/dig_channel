
import os
import logging
from moviepy import VideoFileClip, AudioFileClip, ImageClip, concatenate_videoclips
from werkzeug.utils import secure_filename
import uuid

logger = logging.getLogger(__name__)

class VideoEditorService:
    def __init__(self, upload_folder='data/uploads', output_folder='data/processed'):
        self.upload_folder = upload_folder
        self.output_folder = output_folder
        
        # Ensure directories exist
        os.makedirs(self.upload_folder, exist_ok=True)
        os.makedirs(self.output_folder, exist_ok=True)

    def process_mixed_content(self, video_file, image_file, audio_file, audio_start=7.0, audio_end_offset=0.0, output_filename=None):
        """
        Synthesize a video from Video A + (Image + Audio[start:end-offset]).
        """
        try:
            logger.info(f"Starting video processing: Video={video_file}, Image={image_file}, Audio={audio_file}, Offset={audio_end_offset}")
            
            # 1. Load Video A
            clip_a = VideoFileClip(video_file)
            
            # 2. Load and Clip Audio
            audio = AudioFileClip(audio_file)
            
            # 计算结束时间：总时长 - 尾部偏移
            end_time = audio.duration - audio_end_offset
            
            if end_time <= audio_start:
                raise ValueError(f"Clip range invalid: start({audio_start}s) is greater than or equal to end({end_time}s)")
            
            audio_clipped = audio.subclipped(audio_start, end_time)
            
            # 3. Create Image Clip (Video B)
            # Resize image to match Video A's resolution
            clip_b = (
                ImageClip(image_file)
                .with_duration(audio_clipped.duration)
                .with_audio(audio_clipped)
                .resized(new_size=(clip_a.w, clip_a.h))
            )
            
            # 4. Concatenate A + B
            final_clip = concatenate_videoclips([clip_a, clip_b])
            
            # 5. Export
            if output_filename:
                # Ensure extension is mp4
                if not output_filename.lower().endswith('.mp4'):
                    output_filename += '.mp4'
            else:
                output_filename = f"mixed_video_{uuid.uuid4().hex[:8]}.mp4"
                
            output_path = os.path.join(self.output_folder, output_filename)
            
            logger.info(f"Writing video file to {output_path}...")
            
            # Use 'libx264' codec for compatibility, 'aac' for audio
            final_clip.write_videofile(
                output_path, 
                codec='libx264', 
                audio_codec='aac', 
                temp_audiofile=os.path.join(self.output_folder, 'temp-audio.m4a'),
                remove_temp=True,
                logger=None  # Disable moviepy's internal logger printing to stdout
            )
            
            # Close clips to release resources
            clip_a.close()
            clip_b.close()
            audio.close()
            final_clip.close()
            
            return output_path

        except Exception as e:
            logger.error(f"Video processing failed: {str(e)}", exc_info=True)
            raise e

# Singleton instance
video_editor_service = VideoEditorService()
