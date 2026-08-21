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
  events: EventData[]
}

export function WaveSurferPlayer({ audioUrl, events }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const wavesurfer = useRef<WaveSurfer | null>(null)
  const [isPlaying, setIsPlaying] = useState(false)
  const [isReady, setIsReady] = useState(false)
  const [duration, setDuration] = useState(0)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!containerRef.current) return

    setIsReady(false)
    setError(null)

    const ws = WaveSurfer.create({
      container: containerRef.current,
      waveColor: '#4F4A85',
      progressColor: '#383351',
      url: audioUrl,
    })

    ws.on('ready', () => {
      setDuration(ws.getDuration())
      setIsReady(true)
    })

    ws.on('error', (err) => {
      console.error('WaveSurfer failed to load audio', err)
      setError('Unable to load the audio for playback.')
    })

    ws.on('play', () => setIsPlaying(true))
    ws.on('pause', () => setIsPlaying(false))

    wavesurfer.current = ws

    return () => {
      ws.destroy()
    }
  }, [audioUrl])

  const onPlayPause = () => {
    wavesurfer.current?.playPause()
  }

  return (
    <div>
      <div className="relative w-full mb-4">
        <div ref={containerRef} className="w-full border rounded" />
        {/* Lightweight overlay markers for detected syllable onsets. Using plain
            divs (instead of e.g. wavesurfer's RegionsPlugin) keeps this responsive
            even when there are thousands of events for a long track. */}
        {isReady && duration > 0 && (
          <div className="pointer-events-none absolute inset-0">
            {events.map((ev, i) => (
              <div
                key={i}
                className="absolute top-0 bottom-0 w-px bg-red-500/50"
                style={{ left: `${Math.min(100, (ev.t / duration) * 100)}%` }}
              />
            ))}
          </div>
        )}
      </div>
      {error && <p className="text-red-500 mb-4">{error}</p>}
      <button
        onClick={onPlayPause}
        disabled={!isReady}
        className="bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded disabled:opacity-50"
      >
        {isPlaying ? 'Pause' : 'Play'}
      </button>
    </div>
  )
}
