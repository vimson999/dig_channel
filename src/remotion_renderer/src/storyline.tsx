import React from 'react';
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import type {ReportVideoPayload, ReportVideoSegment, ReportVideoTimelineSpan} from './types';

export const reportVideoDefaultProps: ReportVideoPayload = {
  taskId: 'demo',
  taskName: '研报视频试验线',
  ratio: '16:9',
  durationSec: 60,
  reports: [],
  segments: [
    {
      id: 'demo-1',
      segmentId: 'S001',
      reportCode: '001',
      reportTitle: '示例研报',
      title: '核心结论',
      text: '这里展示研报观点、证据和风险提示。',
      focusNodeId: 'node-1',
      durationSec: 8,
    },
  ],
  timelineSpans: [{type: 'segment', segmentId: 'demo-1', startSec: 0, endSec: 8}],
  theme: {
    title: '研报核心观点',
    subtitle: 'Remotion experimental pipeline',
    sourceLine: '独立渲染试验线',
  },
  rendererVersion: 'remotion_storyline_v1',
};

const palette = {
  bg: '#06111f',
  bg2: '#0b1c32',
  ink: '#f8fafc',
  muted: '#9db2ce',
  cyan: '#38bdf8',
  green: '#34d399',
  amber: '#facc15',
  rose: '#fb7185',
  panel: 'rgba(9, 22, 42, 0.78)',
  panel2: 'rgba(15, 34, 58, 0.86)',
  border: 'rgba(148, 163, 184, 0.28)',
};

const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

const pickSpan = (payload: ReportVideoPayload, second: number): ReportVideoTimelineSpan => {
  const spans = payload.timelineSpans?.length
    ? payload.timelineSpans
    : payload.segments.map((segment, index) => ({
        type: 'segment',
        segmentId: segment.id,
        startSec: payload.segments.slice(0, index).reduce((sum, item) => sum + item.durationSec, 0),
        endSec: payload.segments.slice(0, index + 1).reduce((sum, item) => sum + item.durationSec, 0),
      }));
  return spans.find((span) => second >= span.startSec && second < span.endSec) || spans[spans.length - 1];
};

const segmentForSpan = (payload: ReportVideoPayload, span: ReportVideoTimelineSpan): ReportVideoSegment => {
  return (
    payload.segments.find((segment) => segment.id === span.segmentId || segment.segmentId === span.segmentId) ||
    payload.segments[0] ||
    reportVideoDefaultProps.segments[0]
  );
};

const trim = (text: string | undefined | null, max: number) => {
  const value = String(text || '').replace(/\s+/g, ' ').trim();
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
};

const splitLines = (text: string | undefined | null, maxLineLength: number, maxLines: number) => {
  const value = String(text || '').replace(/\s+/g, ' ').trim();
  if (!value) return [];
  const lines: string[] = [];
  let cursor = '';
  for (const char of value) {
    cursor += char;
    if (cursor.length >= maxLineLength || /[。；;!?！？]$/.test(cursor)) {
      lines.push(cursor.trim());
      cursor = '';
      if (lines.length >= maxLines) break;
    }
  }
  if (cursor.trim() && lines.length < maxLines) lines.push(cursor.trim());
  return lines.slice(0, maxLines);
};

const useSegmentProgress = (span: ReportVideoTimelineSpan) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const second = frame / fps;
  const duration = Math.max(span.endSec - span.startSec, 0.1);
  return clamp((second - span.startSec) / duration, 0, 1);
};

const Background: React.FC<{progress: number}> = ({progress}) => {
  const slow = Math.sin(progress * Math.PI * 2);
  return (
    <AbsoluteFill
      style={{
        background:
          `radial-gradient(circle at ${30 + slow * 8}% 18%, rgba(56,189,248,.22), transparent 28%),` +
          `radial-gradient(circle at ${72 - slow * 6}% 78%, rgba(52,211,153,.16), transparent 30%),` +
          `linear-gradient(135deg, ${palette.bg}, ${palette.bg2})`,
      }}
    >
      <div
        style={{
          position: 'absolute',
          inset: 64,
          border: `1px solid ${palette.border}`,
          opacity: 0.55,
        }}
      />
      <div
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          top: 0,
          height: 8,
          background: `linear-gradient(90deg, ${palette.cyan}, ${palette.green}, ${palette.amber})`,
        }}
      />
    </AbsoluteFill>
  );
};

const Header: React.FC<{payload: ReportVideoPayload; second: number}> = ({payload, second}) => {
  return (
    <div
      style={{
        position: 'absolute',
        left: 86,
        right: 86,
        top: 54,
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        color: palette.ink,
      }}
    >
      <div>
        <div style={{fontSize: 24, letterSpacing: 1.8, color: palette.cyan, fontWeight: 800}}>
          REPORT STORYLINE
        </div>
        <div style={{fontSize: 46, fontWeight: 900, marginTop: 8}}>
          {trim(payload.theme.title || payload.taskName, 34)}
        </div>
      </div>
      <div style={{textAlign: 'right', color: palette.muted, fontSize: 24, lineHeight: 1.35}}>
        <div>{payload.ratio} · {payload.rendererVersion}</div>
        <div>{Math.floor(second)}s / {Math.ceil(payload.durationSec)}s</div>
      </div>
    </div>
  );
};

const EvidenceStack: React.FC<{payload: ReportVideoPayload; active: ReportVideoSegment}> = ({payload, active}) => {
  const reports = payload.reports.length ? payload.reports : [{id: 'fallback', code: active.reportCode, title: active.reportTitle, keyPoints: []}];
  return (
    <div
      style={{
        position: 'absolute',
        left: 86,
        bottom: 92,
        width: 470,
        display: 'flex',
        flexDirection: 'column',
        gap: 18,
      }}
    >
      {reports.slice(0, 4).map((report, index) => {
        const isActive = report.code === active.reportCode;
        return (
          <div
            key={report.id}
            style={{
              padding: '24px 26px',
              background: isActive ? 'rgba(14, 165, 233, 0.2)' : palette.panel,
              border: `1px solid ${isActive ? palette.cyan : palette.border}`,
              color: palette.ink,
              transform: `translateX(${isActive ? 18 : 0}px)`,
            }}
          >
            <div style={{display: 'flex', justifyContent: 'space-between', color: isActive ? palette.cyan : palette.muted, fontSize: 22, fontWeight: 800}}>
              <span>{report.code}</span>
              <span>{report.institution || '机构研报'}</span>
            </div>
            <div style={{fontSize: 28, fontWeight: 850, marginTop: 12, lineHeight: 1.2}}>
              {trim(report.title, 24)}
            </div>
            <div style={{fontSize: 20, color: palette.muted, marginTop: 10}}>
              {(report.keyPoints || [])[0] || (index === 0 ? payload.theme.sourceLine : '')}
            </div>
          </div>
        );
      })}
    </div>
  );
};

const KnowledgePath: React.FC<{payload: ReportVideoPayload; active: ReportVideoSegment; progress: number}> = ({payload, active, progress}) => {
  const nodes = payload.segments.slice(0, 8);
  const activeIndex = Math.max(0, nodes.findIndex((segment) => segment.id === active.id));
  const reveal = Math.ceil(interpolate(progress, [0, 1], [Math.max(1, activeIndex), nodes.length], {extrapolateRight: 'clamp'}));
  return (
    <svg
      style={{position: 'absolute', right: 92, top: 250, width: 720, height: 510, overflow: 'visible'}}
      viewBox="0 0 720 510"
    >
      {nodes.slice(0, reveal).map((node, index) => {
        if (index === 0) return null;
        const prevX = 90 + ((index - 1) % 4) * 170;
        const prevY = 82 + Math.floor((index - 1) / 4) * 190;
        const x = 90 + (index % 4) * 170;
        const y = 82 + Math.floor(index / 4) * 190;
        return <line key={`line-${node.id}`} x1={prevX} y1={prevY} x2={x} y2={y} stroke="rgba(148,163,184,.35)" strokeWidth={3} />;
      })}
      {nodes.slice(0, reveal).map((node, index) => {
        const x = 90 + (index % 4) * 170;
        const y = 82 + Math.floor(index / 4) * 190;
        const isActive = index === activeIndex;
        const radius = isActive ? 45 : 34;
        return (
          <g key={node.id} transform={`translate(${x} ${y})`}>
            <circle r={radius + 14} fill={isActive ? 'rgba(56,189,248,.14)' : 'rgba(15,23,42,.35)'} />
            <circle r={radius} fill={isActive ? palette.cyan : '#10213a'} stroke={isActive ? '#e0f2fe' : 'rgba(148,163,184,.5)'} strokeWidth={3} />
            <text textAnchor="middle" y={7} fill={isActive ? '#06111f' : palette.ink} fontSize={22} fontWeight={900}>
              {String(index + 1).padStart(2, '0')}
            </text>
            <text textAnchor="middle" y={radius + 34} fill={isActive ? palette.ink : palette.muted} fontSize={18} fontWeight={700}>
              {trim(node.title, 10)}
            </text>
          </g>
        );
      })}
    </svg>
  );
};

const MainCopy: React.FC<{segment: ReportVideoSegment; progress: number}> = ({segment, progress}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const entrance = spring({frame, fps, config: {damping: 18, stiffness: 90}});
  const lines = splitLines(segment.text, 33, 5);
  return (
    <div
      style={{
        position: 'absolute',
        left: 610,
        top: 260,
        width: 700,
        color: palette.ink,
        transform: `translateY(${interpolate(entrance, [0, 1], [28, 0])}px)`,
        opacity: entrance,
      }}
    >
      <div style={{display: 'flex', alignItems: 'center', gap: 14, color: palette.green, fontSize: 24, fontWeight: 850}}>
        <span>{segment.reportCode}</span>
        <span style={{width: 86, height: 2, background: palette.green}} />
        <span>{segment.segmentId}</span>
      </div>
      <div style={{fontSize: 62, fontWeight: 950, lineHeight: 1.08, marginTop: 22}}>
        {trim(segment.title || '核心观点', 24)}
      </div>
      <div style={{marginTop: 34, display: 'flex', flexDirection: 'column', gap: 16}}>
        {lines.map((line, index) => (
          <div
            key={line}
            style={{
              fontSize: 32,
              lineHeight: 1.38,
              color: index === 0 ? palette.ink : '#cbd5e1',
              opacity: interpolate(progress, [index * 0.12, index * 0.12 + 0.12], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}),
            }}
          >
            {line}
          </div>
        ))}
      </div>
    </div>
  );
};

const Footer: React.FC<{payload: ReportVideoPayload; progress: number}> = ({payload, progress}) => (
  <div
    style={{
      position: 'absolute',
      left: 610,
      right: 86,
      bottom: 90,
      height: 76,
      color: palette.muted,
      fontSize: 22,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      borderTop: `1px solid ${palette.border}`,
    }}
  >
    <span>{trim(payload.theme.sourceLine || payload.theme.subtitle || payload.taskName, 42)}</span>
    <span style={{width: 260, height: 6, background: 'rgba(148,163,184,.25)'}}>
      <span style={{display: 'block', width: `${progress * 100}%`, height: '100%', background: palette.amber}} />
    </span>
  </div>
);

export const ReportVideoStoryline: React.FC<ReportVideoPayload> = (payload) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const second = frame / fps;
  const safePayload = {...reportVideoDefaultProps, ...payload};
  const span = pickSpan(safePayload, second);
  const segment = segmentForSpan(safePayload, span);
  const segmentProgress = useSegmentProgress(span);
  const totalProgress = clamp(second / Math.max(safePayload.durationSec, 1), 0, 1);

  return (
    <AbsoluteFill style={{fontFamily: 'Inter, PingFang SC, Hiragino Sans GB, Arial, sans-serif'}}>
      <Background progress={totalProgress} />
      <Header payload={safePayload} second={second} />
      <EvidenceStack payload={safePayload} active={segment} />
      <KnowledgePath payload={safePayload} active={segment} progress={totalProgress} />
      <MainCopy segment={segment} progress={segmentProgress} />
      <Footer payload={safePayload} progress={totalProgress} />
      <div
        style={{
          position: 'absolute',
          right: 92,
          bottom: 88,
          padding: '18px 22px',
          color: palette.ink,
          background: palette.panel2,
          border: `1px solid ${palette.border}`,
          fontSize: 22,
          fontWeight: 850,
        }}
      >
        Remotion Lab
      </div>
    </AbsoluteFill>
  );
};
