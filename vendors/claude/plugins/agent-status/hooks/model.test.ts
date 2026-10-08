import { expect, test } from 'claude-code/testing'

import { colorFor, fit, formatDuration, formatTokens, glyphFor, mergeList } from './model'
import type { AgentStatus } from '../types'

test('formatTokens uses k and M with floored tenths', () => {
  expect(formatTokens(0)).toBe('0')
  expect(formatTokens(999)).toBe('999')
  expect(formatTokens(1000)).toBe('1.0k')
  expect(formatTokens(1234)).toBe('1.2k')
  expect(formatTokens(9999)).toBe('9.9k')
  expect(formatTokens(10_000)).toBe('10k')
  expect(formatTokens(999_999)).toBe('999k')
  expect(formatTokens(1_000_000)).toBe('1.0M')
  expect(formatTokens(2_550_000)).toBe('2.5M')
})

test('formatDuration', () => {
  expect(formatDuration(0)).toBe('0s')
  expect(formatDuration(59_999)).toBe('59s')
  expect(formatDuration(65_000)).toBe('1m05s')
  expect(formatDuration(3_661_000)).toBe('1h01m')
  expect(formatDuration(-5)).toBe('0s')
})

test('fit cuts with an ellipsis and strips control characters', () => {
  expect(fit('abcdef', 6)).toBe('abcdef')
  expect(fit('abcdefg', 6)).toBe('abcde…')
  expect(fit('a\nb\tc', 10)).toBe('a b c')
  expect(fit('abc', 0)).toBe('')
})

test('a status with no glyph or color is an error, not a default', () => {
  expect(() => glyphFor('bogus' as AgentStatus)).toThrow('no glyph')
  expect(() => colorFor('bogus' as AgentStatus)).toThrow('no color')
})

test('mergeList marks an end once and keeps parents', () => {
  const live = [{ id: 'a', type: 'T', description: 'd', status: 'running' as const, parentId: 'p' }]
  const first = mergeList({}, live, 100)
  expect(first.ended).toHaveLength(0)
  expect(first.next['a']?.parentId).toBe('p')

  const done = [{ ...live[0]!, status: 'completed' as const }]
  const second = mergeList(first.next, done, 200)
  expect(second.ended.map(r => r.id)).toEqual(['a'])
  expect(second.next['a']?.endedAt).toBe(200)

  const third = mergeList(second.next, done, 300)
  expect(third.ended).toHaveLength(0)
  expect(third.next['a']?.endedAt).toBe(200)
})
