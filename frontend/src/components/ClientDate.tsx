'use client'

import { useEffect, useState } from 'react'

export function ClientDate({ date }: { date: Date | string }) {
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
    return <span className="opacity-0">{new Date(date).toLocaleString('en-US', { timeZone: 'UTC' })}</span>
  }

  return <span>{new Date(date).toLocaleString()}</span>
}
