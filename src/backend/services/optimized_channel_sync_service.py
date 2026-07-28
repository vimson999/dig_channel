"""Optimized channel sync service with simple parallel execution."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any, Dict, Optional

from flask import current_app

from services.channel_service import ChannelService
from services.runtime_guard import release_db_session_quietly
from services.youtube_sync_service import YouTubeSyncService


class OptimizedChannelSyncService:
    """Provide a lightweight parallel channel statistics synchronisation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sync_in_progress: bool = False

    def sync_all_channels_parallel(
        self,
        target_date: Optional[date] = None,
        max_workers: int = 5,
        active_only: bool = True,
    ) -> Dict[str, Any]:
        """Synchronise channel statistics in parallel.

        Args:
            target_date: Optional explicit date to attach to the snapshot.
            max_workers: Size of the thread pool (bounded to 1..10).
            active_only: Whether to filter only active channels.

        Returns:
            Standard response dict used by existing channel sync endpoint.
        """

        with self._lock:
            if self._sync_in_progress:
                return {
                    'success': False,
                    'message': '同步任务正在进行中，请稍后再试',
                    'data': {
                        'total_channels': 0,
                        'success_count': 0,
                        'failed_count': 0,
                        'results': []
                    }
                }
            self._sync_in_progress = True

        try:
            max_workers = max(1, min(int(max_workers or 1), 10))

            channels = ChannelService.get_all_channels(active_only=active_only, order_by_sort=False)
            # ChannelService returns plain dictionaries. Release the scheduler/web
            # session before the thread pool starts remote YouTube requests.
            release_db_session_quietly()
            total_channels = len(channels)

            if total_channels == 0:
                return {
                    'success': True,
                    'message': '没有需要同步的频道',
                    'data': {
                        'total_channels': 0,
                        'success_count': 0,
                        'failed_count': 0,
                        'results': [],
                        'max_workers': max_workers
                    }
                }

            from time import perf_counter

            start_time = perf_counter()

            results = []
            success_count = 0
            failed_count = 0

            app = current_app._get_current_object()

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_channel = {
                    executor.submit(self._sync_single_channel, app, channel['id'], target_date): channel
                    for channel in channels
                }

                for future in as_completed(future_to_channel):
                    channel_info = future_to_channel[future]
                    channel_name = channel_info.get('name') or channel_info.get('channel_name') or '未知频道'

                    try:
                        sync_result = future.result()
                    except Exception as exc:  # pragma: no cover - defensive logging
                        sync_result = {
                            'success': False,
                            'message': str(exc)
                        }

                    if sync_result.get('success'):
                        success_count += 1
                    else:
                        failed_count += 1

                    status_success = bool(sync_result.get('success'))
                    message = sync_result.get('message')
                    if not message:
                        message = '同步成功' if status_success else '同步失败'

                    results.append({
                        'channel_id': channel_info.get('id'),
                        'channel_name': channel_name,
                        'success': status_success,
                        'message': message,
                        'data': sync_result.get('data')
                    })

            duration_seconds = perf_counter() - start_time

            message = (
                f"并行同步完成，共处理 {total_channels} 个频道，"
                f"成功 {success_count} 个，失败 {failed_count} 个。"
            )

            return {
                'success': True,
                'message': message,
                'data': {
                    'total_channels': total_channels,
                    'success_count': success_count,
                    'failed_count': failed_count,
                    'results': results,
                    'max_workers': max_workers,
                    'duration_seconds': round(duration_seconds, 2)
                }
            }

        finally:
            with self._lock:
                self._sync_in_progress = False

    def _sync_single_channel(
        self,
        app,
        channel_id: int,
        target_date: Optional[date]
    ) -> Dict[str, Any]:
        """Run channel sync inside an application context."""

        with app.app_context():
            service = YouTubeSyncService()
            try:
                return service.sync_channel_data(channel_id, target_date)
            finally:
                # Ensure scoped session is cleaned up for the worker thread.
                from models import db
                db.session.remove()


__all__ = ['OptimizedChannelSyncService']
