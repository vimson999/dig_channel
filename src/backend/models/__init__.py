# 数据模型包初始化文件
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

from .user import User
from .channel import Channel
from .channel_data import ChannelData
from .video import Video, VideoData
from .favorite import FavoriteVideo
from .indie_navigator import OpportunitySignal, UserOpportunityInteraction, CollectionKeyword, CollectionJob
from .task import PublicationTask
from .content_pipeline_asset import ContentPipelineAsset
from .workflow_notification import (
    WorkflowNotificationDelivery,
    WorkflowNotificationProviderState,
)
from .report_visual_plan import ReportVisualPlanOutput, ReportVisualPlanSegment
from .report_stick_figure_asset import ReportStickFigureAudioTrack, ReportStickFigureSceneAsset
from .report_pdf_evidence import (
    ReportPdfEvidenceClaim,
    ReportPdfEvidenceOutput,
    ReportVisualPlanSegmentEvidence,
)
from .download_task import DownloadTask
from .system_config import SystemConfig
from .platform_credential import PlatformCredentialAccount, PlatformCredentialBinding
from .zsxq_history_api_client import ZsxqHistoryApiClient
from .external_document import ExternalDocument, ZsxqTopicExternalDocument
from .bilibili_intelligence import (
    BilibiliChargingSnapshot,
    BilibiliCompetitor,
    BilibiliCompetitorVideo,
)
from .bilibili_publish import PublicationTaskBilibiliUpload
from .tts import TTSArticle, TTSSegment
from .zsxq_sync import (
    ZsxqAttachment,
    ZsxqBrowserSubmission,
    ZsxqSyncCursor,
    ZsxqSyncRun,
    ZsxqSyncRunDownload,
    ZsxqTopic,
    ZsxqTopicComment,
    ZsxqTopicFavorite,
)
from .zsxq_workflow import ZsxqWorkflowRun
from .zsxq_analysis import (
    ZsxqAnalysisItem,
    ZsxqAnalysisKeyword,
    ZsxqAnalysisRun,
    ZsxqAnalysisTopic,
)
from .report_video import (
    ReportVideoTask,
    ReportVideoReport,
    ReportVideoAnalysis,
    ReportVideoSegment,
    ReportVideoMindmap,
    ReportVideoAudio,
    ReportVideoOutput,
)

__all__ = [
    'db', 
    'User', 
    'Channel', 
    'ChannelData', 
    'Video', 
    'VideoData', 
    'FavoriteVideo', 
    'OpportunitySignal',
    'PublicationTask',
    'ContentPipelineAsset',
    'WorkflowNotificationDelivery',
    'WorkflowNotificationProviderState',
    'ReportVisualPlanOutput',
    'ReportVisualPlanSegment',
    'ReportStickFigureSceneAsset',
    'ReportStickFigureAudioTrack',
    'ReportPdfEvidenceOutput',
    'ReportPdfEvidenceClaim',
    'ReportVisualPlanSegmentEvidence',
    'DownloadTask',
    'SystemConfig',
    'PlatformCredentialAccount',
    'PlatformCredentialBinding',
    'ZsxqHistoryApiClient',
    'ExternalDocument',
    'ZsxqTopicExternalDocument',
    'BilibiliCompetitor',
    'BilibiliCompetitorVideo',
    'BilibiliChargingSnapshot',
    'PublicationTaskBilibiliUpload',
    'TTSArticle',
    'TTSSegment',
    'ZsxqSyncRun',
    'ZsxqSyncRunDownload',
    'ZsxqTopic',
    'ZsxqTopicFavorite',
    'ZsxqTopicComment',
    'ZsxqAttachment',
    'ZsxqBrowserSubmission',
    'ZsxqSyncCursor',
    'ZsxqWorkflowRun',
    'ZsxqAnalysisRun',
    'ZsxqAnalysisItem',
    'ZsxqAnalysisTopic',
    'ZsxqAnalysisKeyword',
    'ReportVideoTask',
    'ReportVideoReport',
    'ReportVideoAnalysis',
    'ReportVideoSegment',
    'ReportVideoMindmap',
    'ReportVideoAudio',
    'ReportVideoOutput'
]
