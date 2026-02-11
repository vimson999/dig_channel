import httplib2
import os
import sys

def test_proxy():
    proxy_url = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy') or \
                os.environ.get('HTTP_PROXY') or os.environ.get('http_proxy')
    
    print(f"Testing with proxy: {proxy_url}")
    
    if not proxy_url:
        print("No proxy defined in environment.")
        proxy_url = "http://127.0.0.1:7890"
        print(f"Falling back to default for test: {proxy_url}")

    import urllib.parse
    parsed = urllib.parse.urlparse(proxy_url)
    
    try:
        proxy_info = httplib2.ProxyInfo(
            proxy_type=3, # HTTP
            proxy_host=parsed.hostname,
            proxy_port=parsed.port
        )
        
        h = httplib2.Http(proxy_info=proxy_info, timeout=10)
        
        print(f"Attempting to fetch google.com via {parsed.hostname}:{parsed.port}...")
        resp, content = h.request("https://www.google.com")
        print(f"Success! Status: {resp.status}")
    except Exception as e:
        print(f"Failed with httplib2: {e}")
        
    # Also test with requests since we know it works elsewhere
    try:
        import requests
        print("\nAttempting to fetch google.com via requests...")
        proxies = { 'http': proxy_url, 'https': proxy_url }
        resp = requests.get("https://www.google.com", proxies=proxies, timeout=10)
        print(f"Success with requests! Status: {resp.status_code}")
    except Exception as e:
        print(f"Failed with requests: {e}")

if __name__ == "__main__":
    test_proxy()