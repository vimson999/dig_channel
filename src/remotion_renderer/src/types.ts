export type ReportVideoSegment = {
  id: string;
  segmentId: string;
  reportCode: string;
  reportTitle: string;
  title: string;
  text: string;
  focusNodeId?: string | null;
  durationSec: number;
};

export type ReportVideoReport = {
  id: string;
  code: string;
  title: string;
  institution?: string | null;
  coreView?: string | null;
  keyPoints: string[];
};

export type ReportVideoTimelineSpan = {
  type: string;
  segmentId?: string | null;
  startSec: number;
  endSec: number;
  text?: string;
};

export type ReportVideoPayload = {
  taskId: string;
  taskName: string;
  ratio: '16:9' | '9:16';
  durationSec: number;
  reports: ReportVideoReport[];
  segments: ReportVideoSegment[];
  timelineSpans: ReportVideoTimelineSpan[];
  theme: {
    title: string;
    subtitle?: string;
    sourceLine?: string;
  };
  rendererVersion: string;
};
