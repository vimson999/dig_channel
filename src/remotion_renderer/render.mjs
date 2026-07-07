import path from 'node:path';
import process from 'node:process';
import {bundle} from '@remotion/bundler';
import {getCompositions, renderMedia} from '@remotion/renderer';

const [, , payloadPathArg, outputPathArg] = process.argv;

if (!payloadPathArg || !outputPathArg) {
  console.error('Usage: npm run render -- <payload.json> <output.mp4>');
  process.exit(2);
}

const payloadPath = path.resolve(payloadPathArg);
const outputPath = path.resolve(outputPathArg);
const entryPoint = path.join(process.cwd(), 'src', 'index.ts');
const payload = (await import(`file://${payloadPath}`, {with: {type: 'json'}})).default;

const serveUrl = await bundle({
  entryPoint,
  webpackOverride: (config) => config,
});

const compositions = await getCompositions(serveUrl, {
  inputProps: payload,
  chromiumOptions: {
    gl: 'angle',
  },
});
const composition = compositions.find((item) => item.id === 'ReportVideoStoryline');

if (!composition) {
  throw new Error('Composition ReportVideoStoryline not found');
}

await renderMedia({
  composition,
  serveUrl,
  codec: 'h264',
  outputLocation: outputPath,
  inputProps: payload,
  chromiumOptions: {
    gl: 'angle',
  },
});

console.log(JSON.stringify({
  ok: true,
  output: outputPath,
  durationInFrames: composition.durationInFrames,
  width: composition.width,
  height: composition.height,
}));
