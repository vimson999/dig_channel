import sys
import os
sys.path.append('src/backend')
from celery_config import celery_app

res = celery_app.AsyncResult('3779e44b-06c6-4893-a79e-a74b2c6be09b')
print(f"Celery ID: 3779e44b-06c6-4893-a79e-a74b2c6be09b")
print(f"Status: {res.status}")
print(f"Result/Error: {res.result}")
