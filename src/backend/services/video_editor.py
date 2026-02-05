
import os
import logging
from moviepy import VideoFileClip, AudioFileClip, ImageClip, concatenate_videoclips
from werkzeug.utils import secure_filename
import uuid

logger = logging.getLogger(__name__)

class VideoEditorService:
    def __init__(self, upload_folder='data/uploads', output_folder='data/processed'):
        # Convert to absolute paths based on project root
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        self.upload_folder = os.path.join(base_dir, upload_folder)
        self.output_folder = os.path.join(base_dir, output_folder)
        
        # Ensure directories exist
        os.makedirs(self.upload_folder, exist_ok=True)
        os.makedirs(self.output_folder, exist_ok=True)

    def _render_intermediate_clip(self, clip, filename, target_fps=24):
        """Helper to render a clip to a temp file and reload it.
        This forces all transformations (resize, audio sync, etc.) to be 'baked in'.
        """
        temp_path = os.path.join(self.output_folder, filename)
        logger.info(f"Rendering intermediate clip to {temp_path}...")
        
        # Use a faster preset for intermediate files to save time
        clip.write_videofile(
            temp_path,
            fps=target_fps,
            codec='libx264',
            audio_codec='aac',
            preset='ultrafast',
            threads=4,
            logger=None
        )
        return VideoFileClip(temp_path)

    def process_mixed_content(self, video_file, image_file, audio_file, video_c_path=None, audio_c_path=None, audio_start=7.0, audio_end_offset=0.0, output_filename=None):
        """
        Synthesize a video using intermediate rendering for stability.
        """
        clip_a = None
        clip_b = None
        clip_c = None
        
        # Raw resources to close
        raw_resources = []
        # Intermediate clips to close
        intermediate_clips = []
        # Final clip
        final_clip = None
        
        temp_files_to_clean = []

        try:
            logger.info(f"Starting video processing (Intermediate Mode): Video={video_file}")
            
            clips_to_concat = []
            target_w, target_h = 1920, 1080 # Default
            target_fps = 24 # Default

            # 1. Load Video A to get specs (Optional)
            if video_file and os.path.exists(video_file):
                clip_a = VideoFileClip(video_file)
                raw_resources.append(clip_a)
                target_w, target_h = clip_a.w, clip_a.h
                target_fps = getattr(clip_a, 'fps', 24) or 24
                clips_to_concat.append(clip_a)
            else:
                logger.info("No Video A provided, using default 1080p specs.")
            
            # 2. Process Video C (Optional)
            if video_c_path:
                logger.info(f"Processing Video C: {video_c_path}")
                raw_video_c = VideoFileClip(video_c_path)
                raw_resources.append(raw_video_c)
                
                if audio_c_path:
                    # Case A: Both Video and Audio -> Sync duration to audio
                    logger.info(f"Syncing Video C with Audio: {audio_c_path}")
                    audio_c = AudioFileClip(audio_c_path)
                    raw_resources.append(audio_c)
                    
                    target_duration_c = audio_c.duration
                    if raw_video_c.duration < target_duration_c:
                         clip_c_base = raw_video_c.loop(duration=target_duration_c)
                    else:
                         clip_c_base = raw_video_c.subclipped(0, target_duration_c)
                    
                    clip_c_composed = (
                        clip_c_base
                        .with_duration(target_duration_c)
                        .with_audio(audio_c)
                        .resized(new_size=(target_w, target_h))
                        .with_fps(target_fps)
                    )
                else:
                    # Case B: Video only -> Use original video as-is (but resize to match A)
                    logger.info("Using Video C as-is (resizing to match Video A)")
                    clip_c_composed = (
                        raw_video_c
                        .resized(new_size=(target_w, target_h))
                        .with_fps(target_fps)
                    )
                
                # Render C to temp file
                temp_c_name = f"temp_c_{uuid.uuid4().hex[:6]}.mp4"
                clip_c_final = self._render_intermediate_clip(clip_c_composed, temp_c_name, target_fps)
                
                intermediate_clips.append(clip_c_final)
                clips_to_concat.append(clip_c_final)
                temp_files_to_clean.append(os.path.join(self.output_folder, temp_c_name))
                
                clip_c_composed.close()

            # 3. Process Video B (Image + Audio)
            logger.info("Processing Video B...")
            audio_b_raw = AudioFileClip(audio_file)
            raw_resources.append(audio_b_raw)
            
            end_time = audio_b_raw.duration - audio_end_offset
            if end_time <= audio_start:
                 raise ValueError(f"Clip range invalid for Audio B")
                 
            audio_b_clipped = audio_b_raw.subclipped(audio_start, end_time)
            target_duration_b = audio_b_clipped.duration
            
            clip_b_composed = (
                ImageClip(image_file)
                .with_duration(target_duration_b)
                .with_audio(audio_b_clipped)
                .resized(new_size=(target_w, target_h))
                .with_fps(target_fps)
            )
            
            # Render B to temp file
            temp_b_name = f"temp_b_{uuid.uuid4().hex[:6]}.mp4"
            clip_b_final = self._render_intermediate_clip(clip_b_composed, temp_b_name, target_fps)
            
            intermediate_clips.append(clip_b_final)
            clips_to_concat.append(clip_b_final)
            temp_files_to_clean.append(os.path.join(self.output_folder, temp_b_name))
            
            clip_b_composed.close()
            
            # 4. Final Concatenation
            logger.info(f"Concatenating {len(clips_to_concat)} clips...")
            final_clip = concatenate_videoclips(clips_to_concat)
            
            # 5. Export Final
            if output_filename:
                if not output_filename.lower().endswith('.mp4'):
                    output_filename += '.mp4'
            else:
                output_filename = f"mixed_video_{uuid.uuid4().hex[:8]}.mp4"
                
            output_path = os.path.join(self.output_folder, output_filename)
            
            final_clip.write_videofile(
                output_path, 
                codec='libx264', 
                audio_codec='aac', 
                temp_audiofile=os.path.join(self.output_folder, 'temp-audio-final.m4a'),
                remove_temp=True,
                logger=None
            )
            
            return output_path

        except Exception as e:
            logger.error(f"Video processing failed: {str(e)}", exc_info=True)
            raise e
        finally:
            # Clean up EVERYTHING
            if final_clip: final_clip.close()
            
            for clip in intermediate_clips:
                clip.close()
                
            for res in raw_resources:
                if hasattr(res, 'close'): res.close()
            
            # Remove temp files
            for path in temp_files_to_clean:
                if os.path.exists(path):
                    try:
                        os.remove(path)
                        logger.info(f"Deleted temp file: {path}")
                    except Exception as ex:
                        logger.warning(f"Failed to delete temp file {path}: {ex}")

# Singleton instance
video_editor_service = VideoEditorService()
