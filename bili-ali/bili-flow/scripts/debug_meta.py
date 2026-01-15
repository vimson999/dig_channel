from bilibili_api.video_uploader import VideoMeta

try:
    print("Testing VideoMeta with cover=None...")
    meta = VideoMeta(title="test", desc="test", tid=1, tags=["test"], cover=None)
    print(f"Has cover attribute: {hasattr(meta, 'cover')}")
    if hasattr(meta, 'cover'):
        print(f"Cover value: {meta.cover}")
except Exception as e:
    print(f"Error initializing VideoMeta: {e}")

try:
    print("\nTesting VideoMeta with cover='path'...")
    meta2 = VideoMeta(title="test", desc="test", tid=1, tags=["test"], cover="test.jpg")
    print(f"Has cover attribute: {hasattr(meta2, 'cover')}")
except Exception as e:
    print(f"Error initializing VideoMeta: {e}")
