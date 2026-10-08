// Pure text helpers: no engine calls, so tests and hooks share them.

// Token count as 950, 12.3k, 84k, 1.2M. Integer math, no float rounding drift.
export const kfmt = (n: number): string => {
  const v = Math.max(0, Math.round(n))
  if (v < 1000) return String(v)
  if (v < 100_000) {
    const tenths = Math.round(v / 100)
    const whole = Math.floor(tenths / 10)
    const frac = tenths % 10
    return `${whole}${frac === 0 ? '' : `.${frac}`}k`
  }
  if (Math.round(v / 1000) < 1000) return `${Math.round(v / 1000)}k`
  const tenths = Math.round(v / 100_000)
  return `${Math.floor(tenths / 10)}.${tenths % 10}M`
}

// 1234567 -> 1,234,567
export const grouped = (n: number): string =>
  String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ',')

// Engine cost in US dollars -> "$1.23", rounded to whole cents.
export const usd = (amount: number): string => {
  const cents = Math.round(amount * 100)
  return `$${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, '0')}`
}

// Share of `whole` as a percent with one decimal: "12.5%".
export const pct1 = (part: number, whole: number): string => {
  if (whole <= 0) return '0.0%'
  const tenths = Math.round((part * 1000) / whole)
  return `${Math.floor(tenths / 10)}.${tenths % 10}%`
}

export const cell = (text: string): string => text.replace(/\|/g, '\\|')
