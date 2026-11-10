import test from 'node:test';
import assert from 'node:assert/strict';
import { retrievalMetrics, median, splitDocument } from '../src/metrics.js';
import { validateVector } from '../shared/model.mjs';

test('Recall and nDCG use all relevant documents and discount lower ranks', () => {
  assert.deepEqual(retrievalMetrics(['a', 'b'], ['a', 'b']), { recall: 1, ndcg: 1 });
  const m = retrievalMetrics(['x', 'a'], ['a', 'b']);
  assert.equal(m.recall, .5);
  assert.ok(Math.abs(m.ndcg - (1 / Math.log2(3)) / (1 + 1 / Math.log2(3))) < 1e-12);
  assert.equal(retrievalMetrics(['a', 'a'], ['a', 'b']).recall, .5);
  assert.equal(median([9, 1, 3, 5]), 4);
});

test('chunking keeps astral Unicode characters intact', () => {
  assert.deepEqual(splitDocument('文😀字\n\nnext', 2), ['文😀', '字', 'ne', 'xt']);
});

test('rejects malformed and unnormalized browser vectors', () => {
  assert.throws(() => validateVector([1, 2]));
  assert.throws(() => validateVector(Array(384).fill(NaN)));
  assert.throws(() => validateVector(Array(384).fill(0)));
  const unit = Array(384).fill(0); unit[0] = 1;
  assert.equal(validateVector(unit), unit);
});
