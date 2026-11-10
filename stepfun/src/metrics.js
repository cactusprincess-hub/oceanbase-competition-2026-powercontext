export function retrievalMetrics(ids, relevant, k = 5) {
  const wanted = new Set(relevant), hits = new Set();
  let dcg = 0;
  ids.slice(0, k).forEach((id, rank) => {
    if (wanted.has(id) && !hits.has(id)) { hits.add(id); dcg += 1 / Math.log2(rank + 2); }
  });
  const ideal = Array.from({ length: Math.min(wanted.size, k) }, (_, i) => 1 / Math.log2(i + 2))
    .reduce((a, b) => a + b, 0);
  return { recall: wanted.size ? hits.size / wanted.size : 0, ndcg: ideal ? dcg / ideal : 0 };
}
export function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  if (!sorted.length) return null;
  const i = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[i] : (sorted[i - 1] + sorted[i]) / 2;
}

export function splitDocument(text, maxCharacters = 650) {
  const paragraphs = text.replace(/\r\n?/g, '\n').split(/\n\s*\n/).filter(p => p.trim());
  const chunks = [];
  for (const p of paragraphs) {
    const points = Array.from(p.trim());
    for (let i = 0; i < points.length; i += maxCharacters) chunks.push(points.slice(i, i + maxCharacters).join(''));
  }
  return chunks;
}
