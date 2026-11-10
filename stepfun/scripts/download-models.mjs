import { mkdir, readFile, stat, writeFile } from 'node:fs/promises';
import { createReadStream } from 'node:fs';
import { createHash } from 'node:crypto';
import { promisify } from 'node:util';
import { execFile } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { MODEL } from '../shared/model.mjs';

const exec = promisify(execFile);
const root = fileURLToPath(new URL('../models/', import.meta.url));
const files = ['config.json', 'tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json',
  'onnx/model.onnx', 'onnx/model_quantized.onnx', 'onnx/model_fp16.onnx'];
const records = [];
for (const name of files) {
  const destination = path.join(root, MODEL.id, name);
  await mkdir(path.dirname(destination), { recursive: true });
  const url = `https://huggingface.co/${MODEL.id}/resolve/${MODEL.revision}/${name}`;
  // curl uses the OS certificate store and follows Hugging Face's file redirects.
  console.log(`Downloading ${name}`);
  const start = performance.now();
  await exec('curl', ['--fail', '--location', '--retry', '3', '--max-time', '600',
    '--silent', '--show-error', '--output', destination, url], { maxBuffer: 1024 * 1024 });
  const downloadMs = performance.now() - start;
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(destination)) hash.update(chunk);
  records.push({ name, bytes: (await stat(destination)).size, downloadMs, sha256: hash.digest('hex') });
}
const fp32 = records.find(f => f.name === 'onnx/model.onnx').bytes;
const q8 = records.find(f => f.name === 'onnx/model_quantized.onnx').bytes;
const fp16 = records.find(f => f.name === 'onnx/model_fp16.onnx').bytes;
const sharedBytes = records.filter(f => !f.name.startsWith('onnx/')).reduce((sum, f) => sum + f.bytes, 0);
const manifest = { model: MODEL, downloadedAt: new Date().toISOString(), files: records,
  downloadTotalMs: records.reduce((sum, f) => sum + f.downloadMs, 0),
  weightBytes: { fp32, q8, fp16 }, weightCompression: { q8: fp32 / q8, fp16: fp32 / fp16 },
  sharedBytes, bundleBytes: { fp32: fp32 + sharedBytes, q8: q8 + sharedBytes, fp16: fp16 + sharedBytes },
  bundleCompression: { q8: (fp32 + sharedBytes) / (q8 + sharedBytes), fp16: (fp32 + sharedBytes) / (fp16 + sharedBytes) } };
await writeFile(path.join(root, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
await mkdir(new URL('../results/', import.meta.url), { recursive: true });
await writeFile(new URL('../results/model-manifest.json', import.meta.url), JSON.stringify(manifest, null, 2) + '\n');
console.log(JSON.stringify(manifest, null, 2));
