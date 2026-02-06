import os
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# If modifying these SCOPES, delete the file youtube_token.pickle.
SCOPES = ['https://www.googleapis.com/auth/youtube.upload']

def main():
    """
    Shows basic usage of the YouTube Data API.
    Generates youtube_token.pickle for the uploader service.
    """
    creds = None
    token_path = 'src/backend/services/youtube/youtube_token.pickle'
    
    # Check if we have a config file
    config_path = os.getenv('GOOGLE_OAUTH_CONFIG_FILE', 'client_secrets.json')
    
    if not os.path.exists(config_path):
        print(f"❌ Error: {config_path} not found.")
        print("Please place your client_secrets.json from Google Cloud Console in the project root.")
        return

    # The file youtube_token.pickle stores the user's access and refresh tokens
    if os.path.exists(token_path):
        with open(token_path, 'rb') as token:
            creds = pickle.load(token)
            
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(config_path, SCOPES)
            creds = flow.run_local_server(port=8080)
            
        # Save the credentials for the next run
        os.makedirs(os.path.dirname(token_path), exist_ok=True)
        with open(token_path, 'wb') as token:
            pickle.dump(creds, token)
        print(f"✅ Success! Token saved to {token_path}")

if __name__ == '__main__':
    main()
