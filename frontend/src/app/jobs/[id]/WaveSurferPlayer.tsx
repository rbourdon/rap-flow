'use client'

import React, { useCallback, useEffect, useRef, useState } from 'react'
import WaveSurfer from 'wavesurfer.js'
import RegionsPlugin, { type Region } from 'wavesurfer.js/dist/plugins/regions.esm.js'
import { focusRing } from '@/lib/ui'

interface EventData {
  t: number
  strength: number
  f0: number
  dur: number
  // Optional drum classification. When present, markers are colour-coded.
  drum?: string
  type?: string
}

interface Props {
  // Audio loaded into WaveSurfer for the waveform/seek UI only (muted).
  waveformUrl: string
  // Streaming URLs for the two stems (Opus preferred, WAV fallback). When both
  // are present the sample-accurate Web Audio engine is used.
  percUrl?: string
  instUrl?: string
  events: EventData[]
}

const DRUM_COLORS: Record<string, string> = {
  kick: '#f59e0b',
  snare: '#22d3ee',
  hat: '#a78bfa',
}
const DEFAULT_MARKER_COLOR = 'rgba(129, 140, 248, 0.5)' // indigo-400/50

function drumOf(ev: EventData): string | null {
  const d = (ev.drum || ev.type || '').toLowerCase()
  if (d in DRUM_COLORS) return d
  return null
}

export function WaveSurferPlayer({ waveformUrl, percUrl, instUrl, events }: Props) {
  const hasStems = Boolean(percUrl && instUrl)

  const containerRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const wavesurfer = useRef<WaveSurfer | null>(null)
  const regionsRef = useRef<RegionsPlugin | null>(null)
  const loopRegionRef = useRef<Region | null>(null)

  // --- Web Audio engine refs ---
  const ctxRef = useRef<AudioContext | null>(null)
  const percBufferRef = useRef<AudioBuffer | null>(null)
  const instBufferRef = useRef<AudioBuffer | null>(null)
  const percGainRef = useRef<GainNode | null>(null)
  const instGainRef = useRef<GainNode | null>(null)
  const percSourceRef = useRef<AudioBufferSourceNode | null>(null)
  const instSourceRef = useRef<AudioBufferSourceNode | null>(null)
  const offsetRef = useRef(0) // playhead (s) captured when paused / at last start
  const startCtxTimeRef = useRef(0) // ctx.currentTime when playback started
  const playingRef = useRef(false)
  const rafRef = useRef<number | null>(null)
  const handleFinishRef = useRef<() => void>(() => {})
  const tickRef = useRef<() => void>(() => {})

  const [isPlaying, setIsPlaying] = useState(false)
  const [isReady, setIsReady] = useState(false)
  const [duration, setDuration] = useState(0)
  const [currentTime, setCurrentTime] = useState(0)
  const [error, setError] = useState<string | null>(null)

  const [percVolume, setPercVolume] = useState(1)
  const [instVolume, setInstVolume] = useState(1)
  const [percMuted, setPercMuted] = useState(false)
  const [instMuted, setInstMuted] = useState(false)
  const [percSolo, setPercSolo] = useState(false)
  const [instSolo, setInstSolo] = useState(false)
  const [loopEnabled, setLoopEnabled] = useState(false)

  // Keep loop settings in refs so the rAF loop / scheduling read fresh values.
  const loopEnabledRef = useRef(false)
  useEffect(() => { loopEnabledRef.current = loopEnabled }, [loopEnabled])

  // ----- Gain (volume/mute/solo) -----
  const applyGains = useCallback(() => {
    const soloActive = percSolo || instSolo
    const percAudible = !percMuted && (!soloActive || percSolo)
    const instAudible = !instMuted && (!soloActive || instSolo)
    if (percGainRef.current) percGainRef.current.gain.value = percAudible ? percVolume : 0
    if (instGainRef.current) instGainRef.current.gain.value = instAudible ? instVolume : 0
  }, [percVolume, instVolume, percMuted, instMuted, percSolo, instSolo])

  useEffect(() => { applyGains() }, [applyGains])

  const loopBounds = useCallback((): [number, number] | null => {
    const r = loopRegionRef.current
    if (loopEnabledRef.current && r && r.end > r.start) return [r.start, r.end]
    return null
  }, [])

  const playheadNow = useCallback((): number => {
    const ctx = ctxRef.current
    if (!ctx || !playingRef.current) return offsetRef.current
    let pos = offsetRef.current + (ctx.currentTime - startCtxTimeRef.current)
    const bounds = loopBounds()
    if (bounds) {
      const [ls, le] = bounds
      const span = le - ls
      if (span > 0 && pos > le) pos = ls + ((pos - ls) % span)
    }
    return pos
  }, [loopBounds])

  const stopSources = useCallback(() => {
    for (const ref of [percSourceRef, instSourceRef]) {
      const src = ref.current
      if (src) {
        try { src.onended = null; src.stop() } catch { /* already stopped */ }
        try { src.disconnect() } catch { /* noop */ }
      }
      ref.current = null
    }
  }, [])

  const startSources = useCallback((fromOffset: number) => {
    const ctx = ctxRef.current
    const percBuf = percBufferRef.current
    const instBuf = instBufferRef.current
    if (!ctx || !percBuf || !instBuf || !percGainRef.current || !instGainRef.current) return

    stopSources()

    const bounds = loopBounds()
    const total = Math.max(percBuf.duration, instBuf.duration)
    let offset = fromOffset
    if (bounds) {
      const [ls, le] = bounds
      if (offset < ls || offset >= le) offset = ls
    } else if (offset >= total) {
      offset = 0
    }

    const mkSource = (buf: AudioBuffer, gain: GainNode) => {
      const src = ctx.createBufferSource()
      src.buffer = buf
      if (bounds) {
        src.loop = true
        src.loopStart = bounds[0]
        src.loopEnd = bounds[1]
      }
      src.connect(gain)
      return src
    }

    const percSrc = mkSource(percBuf, percGainRef.current)
    const instSrc = mkSource(instBuf, instGainRef.current)
    percSourceRef.current = percSrc
    instSourceRef.current = instSrc

    // Schedule both on the SAME clock time so they are sample-accurate and can
    // never drift relative to each other.
    const when = ctx.currentTime
    percSrc.start(when, offset)
    instSrc.start(when, offset)
    offsetRef.current = offset
    startCtxTimeRef.current = when

    if (!bounds) {
      // Auto-stop when the longer stem ends (only when not looping).
      percSrc.onended = () => {
        if (playingRef.current && !loopBounds()) handleFinishRef.current()
      }
    }
  }, [loopBounds, stopSources])

  const tick = useCallback(() => {
    const t = playheadNow()
    setCurrentTime(t)
    if (wavesurfer.current && duration > 0) {
      // Move the visual cursor without emitting user-interaction events.
      try { wavesurfer.current.setTime(t) } catch { /* noop */ }
    }
    rafRef.current = requestAnimationFrame(() => tickRef.current())
  }, [playheadNow, duration])
  useEffect(() => { tickRef.current = tick }, [tick])

  const startRaf = useCallback(() => {
    if (rafRef.current == null) rafRef.current = requestAnimationFrame(() => tickRef.current())
  }, [])

  const stopRaf = useCallback(() => {
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
  }, [])

  const pause = useCallback(() => {
    if (!playingRef.current) return
    const pos = playheadNow()
    stopSources()
    offsetRef.current = pos
    playingRef.current = false
    setIsPlaying(false)
    stopRaf()
    setCurrentTime(pos)
  }, [playheadNow, stopSources, stopRaf])

  const handleFinish = useCallback(() => {
    stopSources()
    offsetRef.current = 0
    playingRef.current = false
    setIsPlaying(false)
    stopRaf()
    setCurrentTime(0)
    if (wavesurfer.current) {
      try { wavesurfer.current.setTime(0) } catch { /* noop */ }
    }
  }, [stopSources, stopRaf])
  useEffect(() => { handleFinishRef.current = handleFinish }, [handleFinish])

  const play = useCallback(async () => {
    const ctx = ctxRef.current
    if (!ctx) return
    if (ctx.state === 'suspended') await ctx.resume()
    applyGains()
    startSources(offsetRef.current)
    playingRef.current = true
    setIsPlaying(true)
    startRaf()
  }, [applyGains, startSources, startRaf])

  const togglePlay = useCallback(() => {
    if (!isReady) return
    if (!hasStems) {
      wavesurfer.current?.playPause()
      return
    }
    if (playingRef.current) pause()
    else play()
  }, [isReady, hasStems, pause, play])

  const seekTo = useCallback((t: number) => {
    const clamped = Math.max(0, Math.min(t, duration || t))
    offsetRef.current = clamped
    setCurrentTime(clamped)
    if (playingRef.current) {
      // Reschedule both stems at the new offset on the shared clock.
      startSources(clamped)
    }
  }, [duration, startSources])

  // ----- Set up WaveSurfer (+ regions) for the visual/seek UI -----
  useEffect(() => {
    if (!containerRef.current) return
    setIsReady(false)
    setError(null)

    const regions = RegionsPlugin.create()
    regionsRef.current = regions

    const ws = WaveSurfer.create({
      container: containerRef.current,
      waveColor: '#4F4A85',
      progressColor: '#6366f1',
      cursorColor: '#a5b4fc',
      url: waveformUrl,
      plugins: [regions],
      // When we have stems, WaveSurfer is visual-only; mute its own media.
      ...(hasStems ? {} : {}),
    })

    ws.on('ready', () => {
      const d = ws.getDuration()
      setDuration(d)
      if (hasStems) {
        ws.setMuted(true)
        // Add a single draggable/resizable loop region (first ~2s or 25%).
        const end = Math.min(d, Math.max(2, d * 0.25))
        const region = regions.addRegion({
          start: 0,
          end,
          color: 'rgba(99, 102, 241, 0.15)',
          drag: true,
          resize: true,
        })
        loopRegionRef.current = region
        // Readiness on the stems path is driven by the decode effect.
      } else {
        setIsReady(true)
      }
    })

    ws.on('error', (err) => {
      console.error('WaveSurfer failed to load audio', err)
      setError('Unable to load the audio for playback.')
    })

    // Keep a single loop region: discard any extra user-created ones.
    regions.on('region-created', (region) => {
      if (loopRegionRef.current && region !== loopRegionRef.current) {
        region.remove()
      }
    })

    if (hasStems) {
      // User seeking on the waveform updates the Web Audio playhead.
      ws.on('interaction', (newTime: number) => seekTo(newTime))
    } else {
      ws.on('play', () => setIsPlaying(true))
      ws.on('pause', () => setIsPlaying(false))
      ws.on('finish', () => setIsPlaying(false))
      ws.on('timeupdate', (t: number) => setCurrentTime(t))
    }

    wavesurfer.current = ws

    return () => {
      ws.destroy()
      wavesurfer.current = null
      regionsRef.current = null
      loopRegionRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [waveformUrl, hasStems])

  // ----- Decode the two stems into AudioBuffers (Web Audio path) -----
  useEffect(() => {
    if (!hasStems || !percUrl || !instUrl) return
    let cancelled = false
    const ctx = new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)()
    ctxRef.current = ctx
    const percGain = ctx.createGain()
    const instGain = ctx.createGain()
    percGain.connect(ctx.destination)
    instGain.connect(ctx.destination)
    percGainRef.current = percGain
    instGainRef.current = instGain

    const load = async (url: string) => {
      const res = await fetch(url)
      if (!res.ok) throw new Error(`Failed to fetch stem (${res.status})`)
      const buf = await res.arrayBuffer()
      return await ctx.decodeAudioData(buf)
    }

    Promise.all([load(percUrl), load(instUrl)])
      .then(([percBuf, instBuf]) => {
        if (cancelled) return
        percBufferRef.current = percBuf
        instBufferRef.current = instBuf
        setDuration((d) => d || Math.max(percBuf.duration, instBuf.duration))
        applyGains()
        setIsReady(true)
      })
      .catch((err) => {
        if (cancelled) return
        console.error('Failed to decode stems', err)
        setError('Unable to load the audio for playback.')
      })

    return () => {
      cancelled = true
      stopRaf()
      stopSources()
      percBufferRef.current = null
      instBufferRef.current = null
      ctx.close().catch(() => {})
      ctxRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasStems, percUrl, instUrl])

  // ----- Onset markers on a single canvas overlay -----
  const drawMarkers = useCallback(() => {
    const canvas = canvasRef.current
    const container = containerRef.current
    if (!canvas || !container || duration <= 0) return
    const rect = container.getBoundingClientRect()
    const dpr = window.devicePixelRatio || 1
    canvas.width = Math.max(1, Math.floor(rect.width * dpr))
    canvas.height = Math.max(1, Math.floor(rect.height * dpr))
    canvas.style.width = `${rect.width}px`
    canvas.style.height = `${rect.height}px`
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    ctx.scale(dpr, dpr)
    for (const ev of events) {
      const x = Math.min(rect.width, (ev.t / duration) * rect.width)
      const drum = drumOf(ev)
      ctx.fillStyle = drum ? DRUM_COLORS[drum] : DEFAULT_MARKER_COLOR
      ctx.fillRect(x, 0, 1, rect.height)
    }
  }, [events, duration])

  useEffect(() => {
    drawMarkers()
    const container = containerRef.current
    if (!container) return
    const ro = new ResizeObserver(() => drawMarkers())
    ro.observe(container)
    return () => ro.disconnect()
  }, [drawMarkers])

  const drumsPresent = events.some((ev) => drumOf(ev) != null)

  const onWaveformKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === ' ' || e.key === 'Spacebar') {
      e.preventDefault()
      togglePlay()
    }
  }

  const fmt = (s: number) => {
    if (!Number.isFinite(s)) return '0:00'
    const m = Math.floor(s / 60)
    const r = Math.floor(s % 60)
    return `${m}:${r.toString().padStart(2, '0')}`
  }

  return (
    <div>
      <div className="relative w-full mb-6">
        <div
          ref={containerRef}
          role="group"
          aria-label="Audio waveform. Press space to play or pause."
          tabIndex={0}
          onKeyDown={onWaveformKeyDown}
          className={`relative w-full min-h-[80px] border border-white/10 rounded-xl overflow-hidden bg-black/50 ${focusRing}`}
        />
        <canvas ref={canvasRef} className="pointer-events-none absolute inset-0 h-full w-full" />
        {!isReady && !error && (
          <div className="absolute inset-0 flex items-center justify-center gap-3 rounded-xl bg-black/50">
            <span className="h-5 w-5 rounded-full border-2 border-white/20 border-t-indigo-400 motion-safe:animate-spin" />
            <span className="text-sm text-neutral-400">Loading tracks…</span>
          </div>
        )}
      </div>

      {drumsPresent && (
        <div className="flex items-center gap-4 mb-4 text-xs text-neutral-400">
          {Object.entries(DRUM_COLORS).map(([name, color]) => (
            <span key={name} className="inline-flex items-center gap-1.5">
              <span className="inline-block w-3 h-1 rounded-sm" style={{ backgroundColor: color }} />
              <span className="capitalize">{name}</span>
            </span>
          ))}
        </div>
      )}

      {error && <p className="text-red-400 mb-4" role="alert">{error}</p>}

      <div className="flex flex-wrap items-center gap-3 mb-4">
        <button
          onClick={togglePlay}
          disabled={!isReady}
          className={`bg-indigo-600 hover:bg-indigo-500 text-white font-bold py-3 px-8 rounded-lg disabled:opacity-50 transition-colors ${focusRing}`}
        >
          {isPlaying ? 'Pause' : 'Play'}
        </button>
        <span className="text-sm text-neutral-400 tabular-nums">
          {fmt(currentTime)} / {fmt(duration)}
        </span>
        {hasStems && (
          <button
            onClick={() => setLoopEnabled((v) => !v)}
            aria-pressed={loopEnabled}
            className={`ml-auto text-sm px-3 py-2 rounded-lg border transition-colors ${focusRing} ${
              loopEnabled
                ? 'bg-indigo-500/20 text-indigo-300 border-indigo-500/40'
                : 'bg-white/5 text-white/70 border-white/10 hover:bg-white/10'
            }`}
          >
            Loop {loopEnabled ? 'on' : 'off'}
          </button>
        )}
      </div>

      {hasStems && (
        <div className="flex flex-col sm:flex-row gap-6 w-full">
          <StemControls
            label="Background (Instrumental)"
            ariaLabel="Instrumental volume"
            volume={instVolume}
            setVolume={setInstVolume}
            muted={instMuted}
            setMuted={setInstMuted}
            solo={instSolo}
            setSolo={setInstSolo}
          />
          <StemControls
            label="Generated Track (Percussion)"
            ariaLabel="Percussion volume"
            volume={percVolume}
            setVolume={setPercVolume}
            muted={percMuted}
            setMuted={setPercMuted}
            solo={percSolo}
            setSolo={setPercSolo}
          />
        </div>
      )}
    </div>
  )
}

interface StemControlsProps {
  label: string
  ariaLabel: string
  volume: number
  setVolume: (v: number) => void
  muted: boolean
  setMuted: (v: boolean) => void
  solo: boolean
  setSolo: (v: boolean) => void
}

function StemControls({ label, ariaLabel, volume, setVolume, muted, setMuted, solo, setSolo }: StemControlsProps) {
  return (
    <div className="flex-1 bg-white/5 rounded-2xl p-4 flex flex-col gap-2">
      <div className="flex justify-between items-center text-sm font-medium text-white/80 mb-1">
        <span>{label}</span>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setSolo(!solo)}
            aria-pressed={solo}
            className={`text-xs px-2 py-1 rounded transition-colors ${focusRing} ${
              solo ? 'bg-indigo-500/20 text-indigo-300' : 'bg-white/10 text-white/60 hover:bg-white/20'
            }`}
          >
            Solo
          </button>
          <button
            onClick={() => setMuted(!muted)}
            aria-pressed={muted}
            className={`text-xs px-2 py-1 rounded transition-colors ${focusRing} ${
              muted ? 'bg-red-500/20 text-red-400' : 'bg-white/10 text-white/60 hover:bg-white/20'
            }`}
          >
            {muted ? 'Muted' : 'Mute'}
          </button>
        </div>
      </div>
      <input
        type="range"
        min="0"
        max="1"
        step="0.01"
        value={volume}
        onChange={(e) => setVolume(parseFloat(e.target.value))}
        disabled={muted}
        aria-label={ariaLabel}
        className={`w-full accent-indigo-500 disabled:opacity-50 ${focusRing}`}
      />
    </div>
  )
}
