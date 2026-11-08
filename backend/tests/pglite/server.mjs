// Throwaway in-memory Postgres (PGlite = Postgres compiled to WebAssembly) with pgvector,
// served over TCP so the backend's psycopg code can be tested against real SQL.
// Started automatically by tests/conftest.py after `npm install` in this folder.
import { PGlite } from '@electric-sql/pglite'
import { PGLiteSocketServer } from '@electric-sql/pglite-socket'
import { vector } from '@electric-sql/pglite-pgvector'

const port = Number(process.argv[2] || 54329)
const db = await PGlite.create({ extensions: { vector } })
const server = new PGLiteSocketServer({ db, port, host: '127.0.0.1', maxConnections: 8 })
await server.start()
console.log(`PGLITE_READY ${port}`)

const shutdown = async () => {
  await server.stop()
  await db.close()
  process.exit(0)
}
process.on('SIGTERM', shutdown)
process.on('SIGINT', shutdown)
