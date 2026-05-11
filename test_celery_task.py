import sys
import os
sys.path.append('src/backend')
from celery_config import celery_app

res = celery_app.AsyncResult('22f0aa7f-c637-4aac-ae2e-8e3e27ed5542')
print(f"Task status: {res.status}")
print(f"Task result: {res.result}")
