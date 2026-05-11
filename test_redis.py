import redis
import os
from dotenv import load_dotenv

load_dotenv()
r14 = redis.from_url(os.environ.get('REDIS_URL'))
r15 = redis.from_url(os.environ.get('CELERY_BROKER_URL'))

print("DB 14 keys:", r14.keys('*'))
print("DB 15 keys:", r15.keys('*'))
