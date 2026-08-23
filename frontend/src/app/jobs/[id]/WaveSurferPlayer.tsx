'use client'

import React, { useEffect, useRef, useState } from 'react'
import WaveSurfer from 'wavesurfer.js'

interface EventData {
  t: number
  strength: number
  f0: number
  dur: number
}

interface Props {
  audioUrl: string
  percUrl?: string
  instUrl?: string
  events: EventData[]
}

export function WaveSurferPlayer({ audioUrl, percUrl, instUrl, events }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const wavesurfer = useRef<WaveSurfer | null>(null)
  const percAudioRef = useRef<HTMLAudioElement>(null)
  const instAudioRef = useRef<HTMLAudioElement>(null)

  const [isPlaying, setIsPlaying] = useState(false)
  const [isReady, setIsReady] = useState(false)
  const [duration, setDuration] = useState(0)
  const [error, setError] = useState<string | null>(null)

  const [percVolume, setPercVolume] = useState(1)
  const [instVolume, setInstVolume] = useState(1)
  const [percMuted, setPercMuted] = useState(false)
  const [instMuted, setInstMuted] = useState(false)

  useEffect(() => {
    if (!containerRef.current || !percAudioRef.current || !instAudioRef.current) return

    setIsReady(false)
    setError(null)

    const ws = WaveSurfer.create({
      container: containerRef.current,
      waveColor: '#4F4A85',
      progressColor: '#383351',
      url: audioUrl,
    })

    // Only mute the visualization track if we actually have the separate stems to play instead
    if (percUrl && instUrl) {
        ws.setVolume(0)
    }

    ws.on('ready', () => {
      setDuration(ws.getDuration())
      setIsReady(true)
    })

    ws.on('error', (err) => {
      console.error('WaveSurfer failed to load audio', err)
      setError('Unable to load the audio for playback.')
    })

    // Sync HTML audio elements with WaveSurfer
    ws.on('play', () => {
      setIsPlaying(true)
      if (percUrl && instUrl) {
        // Align the stems to the waveform playhead once, right before starting,
        // so we don't have to seek them repeatedly during playback.
        const time = ws.getCurrentTime()
        if (percAudioRef.current) percAudioRef.current.currentTime = time
        if (instAudioRef.current) instAudioRef.current.currentTime = time
        percAudioRef.current?.play().catch(console.error)
        instAudioRef.current?.play().catch(console.error)
      }
    })

    ws.on('pause', () => {
      setIsPlaying(false)
      if (percUrl && instUrl) {
        percAudioRef.current?.pause()
        instAudioRef.current?.pause()
      }
    })

    ws.on('finish', () => {
       // WaveSurfer automatically pauses on finish, but we want to make sure the HTML audio elements stop too
       if (percUrl && instUrl) {
           percAudioRef.current?.pause()
           instAudioRef.current?.pause()
       }
    })

    ws.on('seeking', (time) => {
      if (percAudioRef.current) percAudioRef.current.currentTime = time
      if (instAudioRef.current) instAudioRef.current.currentTime = time
    })

    ws.on('timeupdate', (time) => {
        // The two stems share the browser's audio clock, so once started together
        // they stay in sync with each other. We only nudge them back to the waveform
        // playhead if they drift far enough to be noticeable. Seeking on every small
        // drift re-buffers the <audio> elements and makes the sound choppy, so keep
        // the threshold high and only correct rare, large drifts (e.g. tab throttling).
        const MAX_DRIFT = 0.5
        if (percUrl && instUrl) {
            if (percAudioRef.current && Math.abs(percAudioRef.current.currentTime - time) > MAX_DRIFT) {
                 percAudioRef.current.currentTime = time
            }
            if (instAudioRef.current && Math.abs(instAudioRef.current.currentTime - time) > MAX_DRIFT) {
                 instAudioRef.current.currentTime = time
            }
        }
    })

    wavesurfer.current = ws

    return () => {
      ws.destroy()
    }
  }, [audioUrl, percUrl, instUrl])

  useEffect(() => {
    if (percAudioRef.current) {
      percAudioRef.current.volume = percMuted ? 0 : percVolume
    }
  }, [percVolume, percMuted])

  useEffect(() => {
    if (instAudioRef.current) {
      instAudioRef.current.volume = instMuted ? 0 : instVolume
    }
  }, [instVolume, instMuted])


  const onPlayPause = () => {
    wavesurfer.current?.playPause()
  }

  return (
    <div>
      {/* Hidden audio elements for the separate stems */}
      <audio ref={percAudioRef} src={percUrl} preload="auto" />
      <audio ref={instAudioRef} src={instUrl} preload="auto" />

      <div className="relative w-full mb-6">
        <div ref={containerRef} className="w-full min-h-[80px] border border-white/10 rounded-xl overflow-hidden bg-black/50" />
        {/* Loading state while the waveform and stems are still being fetched/decoded. */}
        {!isReady && !error && (
          <div className="absolute inset-0 flex items-center justify-center gap-3 rounded-xl bg-black/50">
            <span className="h-5 w-5 rounded-full border-2 border-white/20 border-t-indigo-400 animate-spin" />
            <span className="text-sm text-neutral-400">Loading tracks…</span>
          </div>
        )}
        {/* Lightweight overlay markers for detected syllable onsets. */}
        {isReady && duration > 0 && (
          <div className="pointer-events-none absolute inset-0">
            {events.map((ev, i) => (
              <div
                key={i}
                className="absolute top-0 bottom-0 w-px bg-indigo-500/30"
                style={{ left: `${Math.min(100, (ev.t / duration) * 100)}%` }}
              />
            ))}
          </div>
        )}
      </div>

      {error && <p className="text-red-500 mb-4">{error}</p>}

      {/* Only show the dual controls if the separate stems are available */}
      {percUrl && instUrl ? (
      <div className="flex flex-col md:flex-row gap-6 mb-4 items-start md:items-center justify-between">
          <button
            onClick={onPlayPause}
            disabled={!isReady}
            className="w-full md:w-auto flex-shrink-0 bg-indigo-600 hover:bg-indigo-500 text-white font-bold py-3 px-8 rounded-full disabled:opacity-50 transition-colors"
          >
            {isPlaying ? 'Pause' : 'Play'}
          </button>

          <div className="flex-grow flex flex-col sm:flex-row gap-6 w-full">
            {/* Instrumental Controls */}
            <div className="flex-1 bg-white/5 rounded-2xl p-4 flex flex-col gap-2">
                <div className="flex justify-between items-center text-sm font-medium text-white/80 mb-1">
                    <span>Background (Instrumental)</span>
                    <button
                        onClick={() => setInstMuted(!instMuted)}
                        className={`text-xs px-2 py-1 rounded transition-colors ${instMuted ? 'bg-red-500/20 text-red-400' : 'bg-white/10 text-white/60 hover:bg-white/20'}`}
                    >
                        {instMuted ? 'Muted' : 'Mute'}
                    </button>
                </div>
                <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.01"
                    value={instVolume}
                    onChange={(e) => setInstVolume(parseFloat(e.target.value))}
                    disabled={instMuted}
                    className="w-full accent-indigo-500 disabled:opacity-50"
                />
            </div>

            {/* Percussion Controls */}
            <div className="flex-1 bg-white/5 rounded-2xl p-4 flex flex-col gap-2">
                 <div className="flex justify-between items-center text-sm font-medium text-white/80 mb-1">
                    <span>Generated Track (Percussion)</span>
                    <button
                        onClick={() => setPercMuted(!percMuted)}
                        className={`text-xs px-2 py-1 rounded transition-colors ${percMuted ? 'bg-red-500/20 text-red-400' : 'bg-white/10 text-white/60 hover:bg-white/20'}`}
                    >
                        {percMuted ? 'Muted' : 'Mute'}
                    </button>
                </div>
                <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.01"
                    value={percVolume}
                    onChange={(e) => setPercVolume(parseFloat(e.target.value))}
                    disabled={percMuted}
                    className="w-full accent-indigo-500 disabled:opacity-50"
                />
            </div>
          </div>
      </div>
      ) : (
          <button
            onClick={onPlayPause}
            disabled={!isReady}
            className="w-full sm:w-auto flex-shrink-0 bg-indigo-600 hover:bg-indigo-500 text-white font-bold py-3 px-8 rounded-full disabled:opacity-50 transition-colors"
          >
            {isPlaying ? 'Pause' : 'Play'}
          </button>
      )}
    </div>
  )
}
