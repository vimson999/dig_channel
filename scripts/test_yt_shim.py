import os
import sys
import logging
import httplib2
from dotenv import load_dotenv
from googleapiclient.discovery import build
from google.auth.transport.requests import AuthorizedSession

# Load environment
load_dotenv()

# Set paths
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(project_root, 'src', 'backend'))

logging.basicConfig(level=logging.INFO)

class RequestsTransportShim:
    def __init__(self, session):
        self.session = session
    
    def request(self, uri, method="GET", body=None, headers=None, **kwargs):
        kwargs.pop('redirections', None)
        kwargs.pop('connection_type', None)
        
        response = self.session.request(
            method, uri, data=body, headers=headers, **kwargs
        )
        
        res_headers = httplib2.Response(response.headers)
        res_headers.status = response.status_code
        res_headers.reason = response.reason
        
        return res_headers, response.content

def test_shim():
    from services.youtube.uploader import YouTubeUploader
    
    print("\n" + "="*50)
    print("Testing YouTube with Requests Shim")
    print("="*50)
    
    try:
        uploader = YouTubeUploader()
        creds = uploader._get_credentials()
        print("Credentials obtained.")
        
        session = AuthorizedSession(creds)
        proxy_url = os.environ.get('HTTPS_PROXY') or os.environ.get('HTTP_PROXY')
        if proxy_url:
            session.proxies = {'http': proxy_url, 'https': proxy_url}
            print(f"Proxy: {proxy_url}")
        
        transport = RequestsTransportShim(session)
        service = build('youtube', 'v3', http=transport, cache_discovery=False)
        print("Service built.")
        
        print("Testing API call...")
        channels_response = service.channels().list(
            part='snippet',
            mine=True
        ).execute()
        
        if 'items' in channels_response:
            print(f"SUCCESS! Channel: {channels_response['items'][0]['snippet']['title']}")
        else:
            print("Success but no items.")
            
    except Exception as e:
        print(f"Failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_shim()