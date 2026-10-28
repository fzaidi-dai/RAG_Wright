import type { ReactNode } from 'react'

/**
 * One addressable surface of the application: the registry the PRODUCT populates and the shell only reads.
 *
 * One entry carries BOTH the route and the navigation, so the two cannot drift into separate lists that
 * disagree about which pages exist. `nav` is optional: a surface reached from somewhere other than a menu
 * (an account badge, a link handed out after a long-running job) is routed without being advertised.
 */
export type Surface = {
  /** The address. Surfaces are addressable so a reload, a back button or an agent's link returns to it. */
  path: string
  element: ReactNode
  nav?: {
    label: string
    /**
     * `end` so a parent path does not stay highlighted while a child is open. Without it `/intake` matches
     * `/intake/saved` too, and the wrong item stays lit beside the right one.
     */
    end?: boolean
  }
}

/** The navigable subset, in registry order. */
export function navigable(surfaces: Surface[]): Surface[] {
  return surfaces.filter((surface) => surface.nav !== undefined)
}
