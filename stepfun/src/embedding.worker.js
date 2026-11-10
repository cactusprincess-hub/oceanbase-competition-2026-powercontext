import { pipeline, env } from '@huggingface/transformers';
import { MODEL } from '../shared/model.mjs';

env.allowRemoteModels = false;
env.allowLocalModels = true;
env.localModelPath = '/models/';
env.useBrowserCache = true;
env.backends.onnx.wasm.wasmPaths = '/runtime/';
env.backends.onnx.wasm.numThreads = self.crossOriginIsolated ? Math.min(navigator.hardwareConcurrency || 1, 4) : 1;
let extractor, loadedKey, current;
let pending = Promise.resolve();

async function prepare(settings, id) {
  let device = 'wasm', dtype = settings.dtype === 'fp32' ? 'fp32' : 'q8';
  let reason = settings.backend === 'wasm' ? '手动选择 WASM，用于降级验证或基线测试' : '';
  if (settings.backend !== 'wasm') {
    try {
      const adapter = settings.simulateNoGPU ? null : await navigator.gpu?.requestAdapter();
      if (adapter?.features.has('shader-f16')) {
        device = 'webgpu'; dtype = 'fp16';
      } else { reason = (settings.simulateNoGPU ? '诊断：模拟无 GPU 适配器；' : '') + 'WebGPU 或 shader-f16 不可用，自动降级 WASM / INT8'; }
    } catch (error) { reason = `WebGPU 适配器不可用，自动降级 WASM：${error.message}`; }
  }
  const key = `${device}/${dtype}`;
  if (loadedKey === key) return { ...current, loadMs: 0, reused: true };
  if (extractor) await extractor.dispose();
  extractor = undefined; loadedKey = undefined;
  const start = performance.now();
  const load = (device, dtype) => pipeline('feature-extraction', MODEL.id, { device, dtype,
    progress_callback: event => self.postMessage({ id, type: 'progress', event }) });
  try {
    extractor = await load(device, dtype);
  } catch (error) {
    if (device !== 'webgpu') throw error;
    reason = `WebGPU 初始化失败，自动降级 WASM / INT8：${error.message}`;
    device = 'wasm'; dtype = 'q8'; extractor = await load(device, dtype);
  }
  loadedKey = `${device}/${dtype}`;
  current = { backend: device, dtype, reason, threads: device === 'wasm' ? env.backends.onnx.wasm.numThreads : null };
  return { ...current, loadMs: performance.now() - start, reused: false };
}

self.onmessage = ({ data }) => {
  pending = pending.catch(() => {}).then(async () => {
    const { id, settings, texts, batchSize = 4 } = data;
    try {
      const runtime = await prepare(settings, id);
      const vectors = [], batches = [];
      for (let offset = 0; offset < texts.length; offset += batchSize) {
        const start = performance.now();
        const output = await extractor(texts.slice(offset, offset + batchSize), {
          pooling: MODEL.pooling, normalize: MODEL.normalize, truncation: true, max_length: MODEL.maxLength });
        vectors.push(...output.tolist());
        batches.push(performance.now() - start);
        self.postMessage({ id, type: 'inference-progress', done: vectors.length, total: texts.length, runtime });
      }
      self.postMessage({ id, type: 'result', runtime, vectors, inferenceMs: batches.reduce((a, b) => a + b, 0), batches });
    } catch (error) { self.postMessage({ id, type: 'error', error: error.message || String(error) }); }
  });
};
