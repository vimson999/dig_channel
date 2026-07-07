import React from 'react';
import {
  AbsoluteFill,
  Easing,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

const bars = [
  {label: 'Alpha', value: 72, color: '#38bdf8'},
  {label: 'Beta', value: 46, color: '#34d399'},
  {label: 'Gamma', value: 88, color: '#facc15'},
  {label: 'Delta', value: 63, color: '#fb7185'},
  {label: 'Omega', value: 95, color: '#a78bfa'},
];

const maxValue = Math.max(...bars.map((bar) => bar.value));
const chartHeight = 560;
const staggerFrames = 8;

export const AnimatedBarChart: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();

  const titleOpacity = interpolate(frame, [0, fps * 0.7], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });

  const sweep = interpolate(frame, [fps * 2.2, fps * 3.5], [-180, 1180], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });

  return (
    <AbsoluteFill
      style={{
        background:
          'radial-gradient(circle at 20% 16%, rgba(56, 189, 248, 0.22), transparent 28%), linear-gradient(135deg, #0b1020 0%, #111827 52%, #172033 100%)',
        color: '#f8fafc',
        fontFamily: 'Inter, Arial, sans-serif',
        padding: 96,
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          opacity: titleOpacity,
        }}
      >
        <div>
          <div style={{fontSize: 28, color: '#94a3b8', fontWeight: 700}}>
            Quarterly index
          </div>
          <div style={{fontSize: 76, lineHeight: 1.05, fontWeight: 900, marginTop: 12}}>
            Animated 5-bar chart
          </div>
        </div>
        <div
          style={{
            fontSize: 28,
            color: '#cbd5e1',
            border: '1px solid rgba(148, 163, 184, 0.32)',
            padding: '18px 24px',
          }}
        >
          2026
        </div>
      </div>

      <div
        style={{
          position: 'absolute',
          left: 150,
          right: 150,
          bottom: 126,
          height: chartHeight + 112,
          borderBottom: '3px solid rgba(226, 232, 240, 0.42)',
          borderLeft: '3px solid rgba(226, 232, 240, 0.22)',
        }}
      >
        {[0, 1, 2, 3].map((line) => (
          <div
            key={line}
            style={{
              position: 'absolute',
              left: 0,
              right: 0,
              bottom: 112 + line * (chartHeight / 3),
              borderTop: '1px solid rgba(148, 163, 184, 0.18)',
            }}
          />
        ))}

        <div
          style={{
            position: 'absolute',
            left: 68,
            right: 68,
            bottom: 0,
            height: chartHeight + 112,
            display: 'flex',
            alignItems: 'flex-end',
            justifyContent: 'space-between',
          }}
        >
          {bars.map((bar, index) => {
            const growth = spring({
              frame,
              fps,
              delay: fps * 0.55 + index * staggerFrames,
              config: {damping: 18, stiffness: 80, mass: 0.85},
            });
            const barHeight = interpolate(growth, [0, 1], [0, (bar.value / maxValue) * chartHeight]);
            const labelOpacity = interpolate(growth, [0.45, 1], [0, 1], {
              extrapolateLeft: 'clamp',
              extrapolateRight: 'clamp',
            });

            return (
              <div
                key={bar.label}
                style={{
                  width: 190,
                  height: chartHeight + 92,
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'flex-end',
                  gap: 18,
                }}
              >
                <div
                  style={{
                    fontSize: 34,
                    fontWeight: 900,
                    color: '#f8fafc',
                    opacity: labelOpacity,
                  }}
                >
                  {Math.round(bar.value * growth)}
                </div>
                <div
                  style={{
                    width: 112,
                    height: barHeight,
                    minHeight: 2,
                    background: `linear-gradient(180deg, ${bar.color}, rgba(15, 23, 42, 0.75))`,
                    boxShadow: `0 0 46px ${bar.color}66`,
                    transformOrigin: 'bottom',
                  }}
                />
                <div
                  style={{
                    height: 42,
                    fontSize: 26,
                    fontWeight: 800,
                    color: '#cbd5e1',
                    opacity: labelOpacity,
                  }}
                >
                  {bar.label}
                </div>
              </div>
            );
          })}
        </div>

        <div
          style={{
            position: 'absolute',
            top: 36,
            left: sweep,
            width: 150,
            height: chartHeight,
            background: 'linear-gradient(90deg, transparent, rgba(255,255,255,0.2), transparent)',
            transform: 'skewX(-14deg)',
          }}
        />
      </div>
    </AbsoluteFill>
  );
};
