import psycopg2
import os

url = os.environ.get("DATABASE_URL")
if not url:
    raise SystemExit("DATABASE_URL is required")

try:
    print("Connecting to DATABASE_URL...")
    conn = psycopg2.connect(url, connect_timeout=5)
    print("Connection successful!")
    conn.close()
except Exception as e:
    print(f"Connection failed: {e}")
