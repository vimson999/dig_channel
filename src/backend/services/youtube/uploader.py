import os
import logging
import pickle
from typing import List, Optional
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from services.config_service import ConfigService
from config import get_config

logger = logging.getLogger("youtube")

class YouTubeUploader:
    """YouTube video uploader service using Google Data API v3."""

    SCOPES = ['https://www.googleapis.com/auth/youtube.upload']
    API_SERVICE_NAME = 'youtube'
    API_VERSION = 'v3'

    def __init__(self, client_secrets_file: str = None, token_pickle_file: str = None):
        """
        Initialize the uploader with OAuth2 credentials.
        """
        self.client_secrets_file = client_secrets_file or ConfigService.get('oauth_config_file', 'youtube_auth', 'client_secrets.json')
        self.token_pickle_file = token_pickle_file or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'youtube_token.pickle'
        )
        self.credentials = self._get_authenticated_service()

    def _get_authenticated_service(self):
        """Authenticate and return the YouTube service object."""
        creds = None
        # Load existing credentials
        if os.path.exists(self.token_pickle_file):
            with open(self.token_pickle_file, 'rb') as token:
                creds = pickle.load(token)
        
        # If no valid credentials, let user log in
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not self.client_secrets_file or not os.path.exists(self.client_secrets_file):
                    logger.error("Client secrets file not found.")
                    return None
                
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.client_secrets_file, self.SCOPES)
                creds = flow.run_local_server(port=0)
            
            # Save credentials for next run
            with open(self.token_pickle_file, 'wb') as token:
                pickle.dump(creds, token)
        
        return build(self.API_SERVICE_NAME, self.API_VERSION, credentials=creds)

    def upload_video(
        self,
        video_path: str,
        title: str,
        description: str = "",
        tags: List[str] = None,
        category_id: str = "22",  # 22 = People & Blogs
        privacy_status: str = "private",
        thumbnail_path: Optional[str] = None
    ):
        """
        Uploads a video to YouTube.
        
        Args:
            video_path: Path to the video file.
            title: Video title.
            description: Video description.
            tags: List of tags.
            category_id: YouTube category ID (string).
            privacy_status: 'public', 'private', or 'unlisted'.
            thumbnail_path: Path to the custom thumbnail image.
        
        Returns:
            Uploaded video resource dict.
        """
        if not self.credentials:
            logger.error("Authentication failed. Cannot upload.")
            raise Exception("Authentication failed.")
        
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        body = {
            'snippet': {
                'title': title,
                'description': description,
                'tags': tags or [],
                'categoryId': category_id
            },
            'status': {
                'privacyStatus': privacy_status,
                'selfDeclaredMadeForKids': False, 
            }
        }

        # MediaFileUpload for resumable uploads
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True)

        logger.info(f"Uploading '{title}' to YouTube...")
        
        request = self.credentials.videos().insert(
            part=','.join(body.keys()),
            body=body,
            media_body=media
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info(f"Upload progress: {int(status.progress() * 100)}%")

        video_id = response.get('id')
        logger.info(f"Upload Complete! Video ID: {video_id}")

        # Set Thumbnail if provided
        if thumbnail_path and os.path.exists(thumbnail_path) and video_id:
            self._set_thumbnail(video_id, thumbnail_path)

        return response

    def _set_thumbnail(self, video_id: str, thumbnail_path: str):
        """Uploads a custom thumbnail for a video."""
        logger.info(f"Setting thumbnail for video {video_id}...")
        try:
            self.credentials.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumbnail_path)
            ).execute()
            logger.info("Thumbnail set successfully.")
        except Exception as e:
            logger.error(f"Failed to set thumbnail: {e}")

