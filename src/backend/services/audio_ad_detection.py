"""Waveform matching for audio review and batch cleaning."""

import subprocess
from pathlib import Path

import numpy as np
from scipy.signal import fftconvolve, find_peaks

DEFAULT_AD_SAMPLE = Path(__file__).resolve().parents[3] / 'data/audio-ad-removal/default-ad-sample.wav'


def pcm(path, rate=4000):
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-ac', '1',
                          '-ar', str(rate), '-f', 'f32le', '-'], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype='<f4').copy()


def correlation_scores(x, y):
    y = y - y.mean()
    norm_y = np.linalg.norm(y)
    if norm_y < 1e-6:
        return np.zeros(len(x) - len(y) + 1)
    cross = fftconvolve(x, y[::-1], mode='valid')
    sums = np.cumsum(np.r_[0., x.astype('f8')])
    squares = np.cumsum(np.r_[0., np.square(x.astype('f8'))])
    n = len(y)
    variance = squares[n:] - squares[:-n] - (sums[n:] - sums[:-n]) ** 2 / n
    # A silent window has no usable correlation. Ignore energy below
    # 1e-8 of the centered sample energy to avoid amplifying FFT roundoff.
    usable = variance > norm_y ** 2 * 1e-8
    variance[~usable] = 1
    np.sqrt(variance, out=variance)
    variance *= norm_y
    np.divide(cross, variance, out=cross, where=usable)
    cross[~usable] = 0
    return np.clip(cross, -1, 1, out=cross)


def matches(source, sample, rate=4000, threshold=0.55):
    """Find full matches and near-complete copies cut off at the end."""
    x, y = pcm(source, rate), pcm(sample, rate)
    if len(y) < rate // 2 or len(x) < len(y):
        return []
    scores = correlation_scores(x, y)
    peaks, _ = find_peaks(scores, height=threshold, distance=max(1, int(len(y) * .8)))
    if scores[0] >= threshold and (not len(peaks) or peaks[0] > len(y) * .8):
        peaks = np.r_[0, peaks]
    last = len(scores) - 1
    if scores[last] >= threshold and (not len(peaks) or last - peaks[-1] > len(y) * .8):
        peaks = np.r_[peaks, last]
    candidates = [(int(p), int(p) + len(y), float(scores[p])) for p in peaks]

    # The final copy can start after the last full window. Locate it using
    # the first 90% of the sample, then verify all audio up to the file end.
    tail_start = last + 1
    prefix_length = (9 * len(y) + 9) // 10
    if len(x) - tail_start >= prefix_length:
        prefix_scores = correlation_scores(x[tail_start:], y[:prefix_length])
        offset = int(np.argmax(prefix_scores))
        tail_threshold = max(threshold, 0.80)
        if prefix_scores[offset] >= tail_threshold:
            start = tail_start + offset
            score = float(correlation_scores(x[start:], y[:len(x) - start])[0])
            if score >= tail_threshold:
                if candidates and start - candidates[-1][0] < len(y) * .8:
                    if score > candidates[-1][2]:
                        candidates[-1] = (start, len(x), score)
                else:
                    candidates.append((start, len(x), score))

    return [{'start': round(start / rate, 3), 'end': round(end / rate, 3),
             'score': round(score, 3), 'confirmed': False} for start, end, score in candidates]
