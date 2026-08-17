import { useCallback, useState } from 'react'

export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'tradingapp-theme'

/** Stored preference, falling back to light. */
export function readStoredTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark') return stored
  } catch {
    // localStorage can throw in restricted contexts; the default is fine.
  }
  return 'light'
}

export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute('data-theme', theme)
}

function persistTheme(theme: Theme): void {
  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    // Preference just won't persist; not worth failing over.
  }
}

/**
 * Read a CSS custom property off the root element.
 *
 * Lightweight Charts is canvas-based, so it cannot inherit CSS. Pulling the
 * values from the same custom properties the stylesheet defines keeps one
 * source of truth instead of a second hardcoded palette that drifts.
 */
export function cssVar(name: string, fallback = ''): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name)
  return value.trim() || fallback
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(readStoredTheme)

  const toggle = useCallback(() => {
    const next: Theme = theme === 'light' ? 'dark' : 'light'

    // The DOM attribute is set here, synchronously in the event handler, and
    // deliberately NOT in an effect.
    //
    // React runs child effects before parent effects. If this lived in an
    // effect on the component that owns the theme state, any child reading the
    // CSS variables — the chart does, because canvas cannot inherit CSS —
    // would run first and read the *previous* theme's colours, leaving a white
    // chart on a dark page. Setting the attribute before the re-render means
    // every effect that follows sees the correct values.
    applyTheme(next)
    persistTheme(next)
    setTheme(next)
  }, [theme])

  return [theme, toggle]
}
