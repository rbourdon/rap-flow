import { describe, expect, it } from 'vitest'
import { formatBytes, isAudioFile, isValidSourceUrl } from './upload'

describe('isValidSourceUrl', () => {
  it.each([
    'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
    'https://youtu.be/dQw4w9WgXcQ',
    'https://m.youtube.com/watch?v=dQw4w9WgXcQ',
    'https://music.youtube.com/watch?v=dQw4w9WgXcQ',
    'https://soundcloud.com/artist/track',
    '  https://youtu.be/dQw4w9WgXcQ  ',
  ])('accepts %s', (url) => {
    expect(isValidSourceUrl(url)).toBe(true)
  })

  it.each([
    '',
    'not a url',
    'ftp://youtube.com/watch?v=x',
    'https://example.com/watch?v=x',
    'https://youtube.com.evil.com/watch?v=x',
    'https://notyoutube.com/watch?v=x',
    'javascript:alert(1)',
  ])('rejects %j', (url) => {
    expect(isValidSourceUrl(url)).toBe(false)
  })
})

describe('isAudioFile', () => {
  const file = (name: string, type = '') => new File([''], name, { type })

  it('trusts the MIME type when present', () => {
    expect(isAudioFile(file('x.bin', 'audio/mpeg'))).toBe(true)
    expect(isAudioFile(file('song.mp3', 'video/mp4'))).toBe(false)
  })

  it('falls back to the extension without a MIME type', () => {
    expect(isAudioFile(file('Song.FLAC'))).toBe(true)
    expect(isAudioFile(file('notes.txt'))).toBe(false)
  })
})

describe('formatBytes', () => {
  it('picks a sensible unit', () => {
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(2048)).toBe('2 KB')
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB')
  })
})
