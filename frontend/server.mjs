import express from 'express'
import http from 'node:http'
import { fileURLToPath } from 'node:url'

const app = express()
app.disable('x-powered-by')
const origin = new URL(process.env.GENERATOR_URL ?? 'http://127.0.0.1:8000')
if (origin.protocol !== 'http:' || origin.pathname !== '/' || origin.search || origin.hash || origin.username || origin.password) {
  throw new Error('GENERATOR_URL must be an HTTP origin')
}
const maxRequest = 82 * 1024 ** 2
const jobPath = /^\/jobs\/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}(?:\/download)?$/
const responseHeaders = ['content-type', 'content-length', 'content-disposition', 'accept-ranges', 'content-range']

function error(res, status, code, message) {
  res.set('Cache-Control', 'no-store').status(status).json({ error: { code, message } })
}

app.use('/api', (req, res) => {
  const path = req.path
  if (path !== '/health' && path !== '/jobs' && !jobPath.test(path)) {
    return error(res, 404, 'not_found', 'API route not found.')
  }
  const allowed = path === '/jobs' ? ['GET', 'POST'] : ['GET']
  if (!allowed.includes(req.method)) {
    res.set('Allow', allowed.join(', '))
    return error(res, 405, 'method_not_allowed', 'Method not allowed.')
  }

  const upload = req.method === 'POST'
  const download = path.endsWith('/download')
  const length = req.headers['content-length']
  if (upload && length && Number(length) > maxRequest) {
    res.set('Connection', 'close')
    return error(res, 413, 'upload_too_large', 'The request must be at most 82 MiB.')
  }

  const headers = {}
  if (upload && req.headers['content-type']) headers['content-type'] = req.headers['content-type']
  if (upload && length) headers['content-length'] = length
  if (download && req.headers.range) headers.range = req.headers.range

  const upstream = http.request(new URL(path, origin), { method: req.method, headers, agent: false })
  let stopped = false
  let reply
  const timer = setTimeout(() => stop(504, 'upstream_timeout', 'The generation service took too long to respond.'),
    upload ? 120_000 : 5_000)

  function stop(status, code, message) {
    if (stopped) return
    stopped = true
    clearTimeout(timer)
    if (status && !res.headersSent && !res.destroyed) {
      for (const name of responseHeaders) res.removeHeader(name)
      if (upload && !req.complete) res.set('Connection', 'close')
      error(res, status, code, message)
    } else if (status && !res.destroyed) {
      res.destroy()
    }
    upstream.destroy()
    reply?.destroy()
    req.resume()
  }

  upstream.on('error', () => stop(502, 'upstream_unavailable', 'The generation service is unavailable.'))
  res.on('close', () => stop())
  upstream.on('response', incoming => {
    if (stopped) {
      incoming.destroy()
      return
    }
    reply = incoming
    clearTimeout(timer)
    res.status(incoming.statusCode ?? 502)
    for (const name of responseHeaders) {
      if (incoming.headers[name] !== undefined) res.setHeader(name, incoming.headers[name])
    }
    res.setHeader('Cache-Control', 'no-store')
    if (download) incoming.setTimeout(120_000, () => stop(504, 'upstream_timeout', 'The download timed out.'))
    incoming.on('error', () => stop(502, 'upstream_unavailable', 'The generation response was interrupted.'))
    incoming.pipe(res)
  })

  if (!upload) {
    upstream.end()
    return
  }
  let received = 0
  req.on('data', chunk => {
    if (stopped) return
    received += chunk.length
    if (received > maxRequest) {
      stop(413, 'upload_too_large', 'The request must be at most 82 MiB.')
      return
    }
    if (!upstream.write(chunk)) req.pause()
  })
  upstream.on('drain', () => req.resume())
  req.on('end', () => { if (!stopped) upstream.end() })
  req.on('error', () => stop())
})

app.use(express.static(fileURLToPath(new URL('./dist/', import.meta.url))))
app.listen(Number(process.env.PORT ?? 8080), process.env.HOST ?? '127.0.0.1')
