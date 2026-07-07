import React from 'react';
import {Composition} from 'remotion';
import {AnimatedBarChart} from './animated-bar-chart';
import {ReportVideoStoryline, reportVideoDefaultProps} from './storyline';
import type {ReportVideoPayload} from './types';

const fps = 30;

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="ReportVideoStoryline"
        component={ReportVideoStoryline}
        fps={fps}
        width={1920}
        height={1080}
        durationInFrames={fps * 60}
        defaultProps={reportVideoDefaultProps}
        calculateMetadata={({props}) => {
          const payload = props as ReportVideoPayload;
          const isPortrait = payload.ratio === '9:16';
          const durationSec = Math.max(3, Math.min(payload.durationSec || 60, 60 * 60));
          return {
            width: isPortrait ? 1080 : 1920,
            height: isPortrait ? 1920 : 1080,
            durationInFrames: Math.max(1, Math.ceil(durationSec * fps)),
          };
        }}
      />
      <Composition
        id="AnimatedBarChart"
        component={AnimatedBarChart}
        fps={fps}
        width={1920}
        height={1080}
        durationInFrames={fps * 5}
      />
    </>
  );
};
