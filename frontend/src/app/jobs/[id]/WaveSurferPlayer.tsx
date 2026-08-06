'use client'

import React, { useEffect, useRef, useState } from 'react'
import WaveSurfer from 'wavesurfer.js'
import RegionsPlugin from 'wavesurfer.js/dist/plugins/regions.esm.js'

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

  useEffect(() => {
    if (!containerRef.current) return

    const ws = WaveSurfer.create({
      container: containerRef.current,
      waveColor: '#4F4A85',
      progressColor: '#383351',
      url: audioUrl,
    })

    // Using a separate rendering strategy for large number of events
    // RegionsPlugin can be slow with thousands of regions.
    // We might just use simple div markers overlaid if events > 500, but for now just use Regions Plugin.
    const wsRegions = ws.registerPlugin(RegionsPlugin.create())

    ws.on('ready', () => {
      // Add markers
      events.forEach(ev => {
        wsRegions.addRegion({
          start: ev.t,
          content: '',
          color: 'rgba(255, 0, 0, 0.5)',
          drag: false,
          resize: false,
        })
      })
    })

    ws.on('play', () => setIsPlaying(true))
    ws.on('pause', () => setIsPlaying(false))

    wavesurfer.current = ws

    return () => {
      ws.destroy()
    }
  }, [audioUrl, events])

  const onPlayPause = () => {
    wavesurfer.current?.playPause()
  }

  return (
    <div>
      <div ref={containerRef} className="w-full mb-4 border rounded" />
      <button
        onClick={onPlayPause}
        className="bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded"
      >
        {isPlaying ? 'Pause' : 'Play'}
      </button>
    </div>
  )
}
