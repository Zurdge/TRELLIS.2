import { useCallback, useEffect, useRef } from 'react'

export function usePolling<T>(
  read: (signal: AbortSignal) => Promise<T>,
  receive: (value: T) => number,
  fail: () => number,
) {
  const callbacks = useRef({ receive, fail })
  callbacks.current = { receive, fail }
  const control = useRef({ refresh: () => {} })

  useEffect(() => {
    let request: AbortController | undefined
    let timer: ReturnType<typeof setTimeout>
    let stopped = false

    async function poll() {
      clearTimeout(timer)
      request?.abort()
      const active = new AbortController()
      request = active
      const signal = AbortSignal.any([active.signal, AbortSignal.timeout(6500)])
      let delay = 5000
      try {
        const value = await read(signal)
        if (!stopped && request === active) delay = callbacks.current.receive(value)
      } catch {
        if (!stopped && request === active) delay = callbacks.current.fail()
      } finally {
        if (!stopped && request === active) timer = setTimeout(poll, delay)
      }
    }
    control.current.refresh = () => { void poll() }
    void poll()
    return () => {
      stopped = true
      request?.abort()
      clearTimeout(timer)
    }
  }, [read])

  return useCallback(() => control.current.refresh(), [])
}
