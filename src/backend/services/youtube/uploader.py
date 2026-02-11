import os
import logging
import pickle
import socket
import time
import httplib2
from typing import List, Optional
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request, AuthorizedSession
from googleapiclient.errors import HttpError
from services.config_service import ConfigService
from config import get_config

# 设置全局超时时间 (Requests 也会使用)
socket.setdefaulttimeout(300)

logger = logging.getLogger("youtube")

class RequestsTransportShim:
    """
    A shim that makes a requests.Session look like httplib2.Http.
    Required because googleapiclient expects httplib2-style request() signature.
    """
    def __init__(self, session):
        self.session = session
    
    def request(self, uri, method="GET", body=None, headers=None, **kwargs):
        # Remove httplib2 specific args
        kwargs.pop('redirections', None)
        kwargs.pop('connection_type', None)
        
        # requests.Session.request uses 'data' instead of 'body'
        response = self.session.request(
            method, uri, data=body, headers=headers, timeout=300, **kwargs
        )
        
        # googleapiclient expects a tuple of (response_headers, content)
        res_headers = httplib2.Response(response.headers)
        res_headers.status = response.status_code
        res_headers.reason = response.reason
        
        return res_headers, response.content

class YouTubeUploader:
    """YouTube video uploader service using Google Data API v3 with Requests transport."""

    SCOPES = ['https://www.googleapis.com/auth/youtube.upload']
    API_SERVICE_NAME = 'youtube'
    API_VERSION = 'v3'

    def __init__(self, client_secrets_file: str = None, token_pickle_file: str = None):
        """
        Initialize the uploader with OAuth2 credentials and Requests-based transport.
        """
        self.client_secrets_file = client_secrets_file or ConfigService.get('oauth_config_file', 'youtube_auth', 'client_secrets.json')
        self.token_pickle_file = token_pickle_file or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'youtube_token.pickle'
        )
        self.credentials = self._get_credentials()
        
        # 使用 AuthorizedSession (基于 requests) 并注入代理
        self.authed_session = AuthorizedSession(self.credentials)
        proxy_url = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy') or \
                    os.environ.get('HTTP_PROXY') or os.environ.get('http_proxy')
        if proxy_url:
            self.authed_session.proxies = {'http': proxy_url, 'https': proxy_url}
            logger.info(f"YouTube Uploader: Using Requests transport with proxy: {proxy_url}")
        
        # 通过 Shim 构建服务
        self.transport = RequestsTransportShim(self.authed_session)
        self.service = build(self.API_SERVICE_NAME, self.API_VERSION, 
                            http=self.transport, cache_discovery=False)

    def _get_credentials(self):
        """获取并刷新 OAuth2 凭据"""
        creds = None
        if os.path.exists(self.token_pickle_file):
            with open(self.token_pickle_file, 'rb') as token:
                creds = pickle.load(token)
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    # 刷新时也走代理
                    creds.refresh(Request(session=self._get_simple_session()))
                except Exception as e:
                    logger.error(f"Token refresh failed: {e}")
                    creds = None
            
            if not creds or not creds.valid:
                if not self.client_secrets_file or not os.path.exists(self.client_secrets_file):
                    logger.error(f"Client secrets file not found: {self.client_secrets_file}")
                    raise Exception("YouTube API credentials missing")
                
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.client_secrets_file, self.SCOPES)
                creds = flow.run_local_server(port=0)
            
            with open(self.token_pickle_file, 'wb') as token:
                pickle.dump(creds, token)
        
        return creds

    def _get_simple_session(self):
        """刷新 Token 用的代理 Session"""
        import requests
        session = requests.Session()
        proxy_url = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy') or \
                    os.environ.get('HTTP_PROXY') or os.environ.get('http_proxy')
        if proxy_url:
            session.proxies = {'http': proxy_url, 'https': proxy_url}
        return session

    def upload_video(
        self,
        video_path: str,
        title: str,
        description: str = "",
        tags: List[str] = None,
        category_id: str = "22",
        privacy_status: str = "private",
        thumbnail_path: Optional[str] = None
    ):
        """
        Uploads a video to YouTube.
        """
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        # YouTube limits
        if len(title) > 100: title = title[:97] + "..."
        if description and len(description) > 5000: description = description[:4997] + "..."

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

        # 使用 512KB 分片
        media = MediaFileUpload(video_path, chunksize=512*1024, resumable=True)

        logger.info(f"Uploading '{title}' to YouTube (Requests Transport)...")
        
        request = self.service.videos().insert(
            part=','.join(body.keys()),
            body=body,
            media_body=media
        )

        response = None
        error_count = 0
        max_errors = 5

        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    logger.info(f"Upload progress: {int(status.progress() * 100)}%")
                error_count = 0
            except Exception as e:
                error_count += 1
                logger.warning(f"Error during upload (attempt {error_count}/{max_errors}): {e}")
                if error_count > max_errors:
                    raise
                time.sleep(error_count * 2)

        video_id = response.get('id')
        logger.info(f"Upload Complete! Video ID: {video_id}")

        if thumbnail_path and os.path.exists(thumbnail_path) and video_id:
            self._set_thumbnail(video_id, thumbnail_path)

        return response

    def _set_thumbnail(self, video_id: str, thumbnail_path: str):
        """Uploads a custom thumbnail for a video."""
        logger.info(f"Setting thumbnail for video {video_id}...")
        try:
            self.service.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumbnail_path)
            ).execute()
            logger.info("Thumbnail set successfully.")
        except Exception as e:
            logger.error(f"Failed to set thumbnail: {e}")
