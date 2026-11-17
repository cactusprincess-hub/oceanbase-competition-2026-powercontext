import { Store } from './store.mjs';
import { createApp } from './app.mjs';
const store = new Store();
for (let attempt = 0; ; attempt++) {
  try { await store.init(); break; }
  catch (e) { if (attempt >= 89) throw e; await new Promise(resolve => setTimeout(resolve, 2000)); }
}
const server = createApp(store, process.env.GAME_TOKEN).listen(Number(process.env.PORT || 3100), '0.0.0.0', () => console.log('Midnight game data service ready'));
async function stop() { server.close(async () => { await store.close(); process.exit(0); }); }
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
