import asyncio
import os
from bilibili_api import user, Credential

async def main():
    cred = Credential(
        sessdata=os.environ.get('BILI_SESSDATA'),
        bili_jct=os.environ.get('BILI_BILI_JCT'),
        buvid3=os.environ.get('BILI_BUVID3'),
        dedeuserid=os.environ.get('BILI_DEDEUSERID')
    )
    u = user.User(uid=int(os.environ.get('BILI_DEDEUSERID', 0)), credential=cred)
    print(f"User methods: {[m for m in dir(u) if not m.startswith('_')]}")

if __name__ == "__main__":
    asyncio.run(main())
