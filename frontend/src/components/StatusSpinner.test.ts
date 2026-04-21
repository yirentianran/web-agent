import { describe, it, expect } from 'vitest'
import { formatElapsed } from './StatusSpinner'

describe('formatElapsed', () => {
  it('shows seconds only for small values', () => {
    expect(formatElapsed(0)).toBe('0秒')
    expect(formatElapsed(5)).toBe('5秒')
    expect(formatElapsed(59)).toBe('59秒')
  })

  it('shows minutes and seconds for values over 60s', () => {
    expect(formatElapsed(65)).toBe('1分5秒')
    expect(formatElapsed(90)).toBe('1分30秒')
    expect(formatElapsed(120)).toBe('2分')
    expect(formatElapsed(3599)).toBe('59分59秒')
  })

  it('shows hours, minutes and seconds for values over 3600s', () => {
    expect(formatElapsed(3600)).toBe('1时')
    expect(formatElapsed(3661)).toBe('1时1分1秒')
    expect(formatElapsed(7200)).toBe('2时')
    expect(formatElapsed(3665)).toBe('1时1分5秒')
  })

  it('omits zero-value units', () => {
    expect(formatElapsed(3600)).toBe('1时')
    expect(formatElapsed(60)).toBe('1分')
    expect(formatElapsed(0)).toBe('0秒')
  })
})
