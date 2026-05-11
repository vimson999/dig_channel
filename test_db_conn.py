import psycopg2
import os

url = os.environ.get("DATABASE_URL", "postgresql://cappa_rw:RWcappaDb!!!2025@101.35.56.140:5432/show_ytb_data")
try:
    print(f"Connecting to {url}...")
    conn = psycopg2.connect(url, connect_timeout=5)
    print("Connection successful!")
    conn.close()
except Exception as e:
    print(f"Connection failed: {e}")
