# WSJ output for MP3 video workflow

Reuse the approved 4:3 artwork as the visual base. Render the current plan's title into one canonical WSJ image, and let a manual title edit replace that asset. Expose two preview cards for the single file, including a generation control for existing jobs.

Queue a third publisher child for full-video runs using the canonical image. Fit the 4:3 output through the existing FFmpeg pipeline; keep the original two children and preview-only behavior. Track the WSJ asset used by the video so replacing the asset doesn't expose an outdated video as current.

Verify dynamic image generation, the shared preview URLs, correct export dimensions, and regression tests for existing MP3 workflow behavior.
