'use client'

import { useEffect, useState } from 'react'
import { formatRelativeTime } from '@/lib/format'

interface ClientDateProps {
  date: Date | string
  // When true, render a relative time ("2h ago") and keep the absolute time in
  // the `title` attribute for hover. Otherwise render the absolute local time.
  relative?: boolean
}

export function ClientDate({ date, relative = false }: ClientDateProps) {
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    // Avoid synchronous state update to prevent cascading renders
    let isActive = true;
    const timerId = setTimeout(() => {
      if (isActive) setMounted(true)
    }, 0);
    return () => {
      isActive = false
      clearTimeout(timerId)
    }
  }, [])

  if (!mounted) {
    // Server/first-paint fallback: keep layout stable but avoid a hydration
    // mismatch from locale/timezone differences.
    return <span className="opacity-0">{new Date(date).toLocaleString('en-US', { timeZone: 'UTC' })}</span>
  }

  const absolute = new Date(date).toLocaleString()

  if (relative) {
    return <span title={absolute}>{formatRelativeTime(date)}</span>
  }

  return <span>{absolute}</span>
}
