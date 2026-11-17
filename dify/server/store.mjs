import mysql from 'mysql2/promise';
import { readFile } from 'node:fs/promises';

export const initialState = () => ({room: '大厅', inventory: [], turns: 0, hints: 0, status: 'playing'});
export class Conflict extends Error {}
export class Store {
  constructor(config = {}) {
    this.pool = mysql.createPool({host: process.env.SEEKDB_HOST || '127.0.0.1',
      port: Number(process.env.SEEKDB_PORT || 2881), user: process.env.SEEKDB_USER || 'root',
      password: process.env.SEEKDB_PASSWORD || '', connectionLimit: 4, ...config});
  }
  async init() {
    await this.pool.query('CREATE DATABASE IF NOT EXISTS midnight_game');
    await this.pool.query(`CREATE TABLE IF NOT EXISTS midnight_game.scenes (
      room VARCHAR(20) PRIMARY KEY, description TEXT NOT NULL, clue_id VARCHAR(20) NOT NULL,
      clue_text TEXT NOT NULL, fragment VARCHAR(20) NOT NULL, ordinal INT NOT NULL)`);
    await this.pool.query(`CREATE TABLE IF NOT EXISTS midnight_game.players (
      session_id VARCHAR(32) PRIMARY KEY, version INT NOT NULL, state_json TEXT NOT NULL)`);
    await this.pool.query(`CREATE TABLE IF NOT EXISTS midnight_game.history (
      session_id VARCHAR(32) NOT NULL, version INT NOT NULL, command VARCHAR(100) NOT NULL,
      reply TEXT NOT NULL, PRIMARY KEY(session_id, version))`);
    const scenes = JSON.parse(await readFile(new URL('../scenes.json', import.meta.url)));
    for (const s of scenes) await this.pool.execute(
      'INSERT IGNORE INTO midnight_game.scenes VALUES (?, ?, ?, ?, ?, ?)',
      [s.room, s.description, s.clue_id, s.clue_text, s.fragment, s.ordinal]);
  }
  async context(session) {
    await this.pool.execute('INSERT IGNORE INTO midnight_game.players VALUES (?, 0, ?)',
      [session, JSON.stringify(initialState())]);
    const [[player]] = await this.pool.execute('SELECT version, state_json FROM midnight_game.players WHERE session_id=?', [session]);
    const [scenes] = await this.pool.query('SELECT * FROM midnight_game.scenes ORDER BY ordinal');
    return {session_id: session, version: player.version, state: JSON.parse(player.state_json), scenes};
  }
  async commit({session_id, version, state, command, reply}) {
    const c = await this.pool.getConnection();
    try {
      await c.beginTransaction();
      const [result] = await c.execute(
        'UPDATE midnight_game.players SET version=version+1, state_json=? WHERE session_id=? AND version=?',
        [JSON.stringify(state), session_id, version]);
      if (result.affectedRows !== 1) throw new Conflict('进度已更新，请重新运行这条命令。');
      await c.execute('INSERT INTO midnight_game.history VALUES (?, ?, ?, ?)', [session_id, version + 1, command, reply]);
      await c.commit();
      return {saved: true, version: version + 1, state, reply};
    } catch (e) { await c.rollback(); throw e; }
    finally { c.release(); }
  }
  async close() { await this.pool.end(); }
}
