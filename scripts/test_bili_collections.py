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
    try:
        channels = await u.get_channels()
        print(f"Found {len(channels)} channels.")
        for c in channels:
            print(f"Meta: {c.meta}")
    except Exception as e:
        print(f"get_channels failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())