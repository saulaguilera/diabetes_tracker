// theme.js — paleta de marca Orbit + tokens de tema (portado de cm-core.jsx)

export const SANS = '"Outfit", -apple-system, system-ui, sans-serif'

// Paleta: cyan / teal / violet sobre navy profundo. Una clave por "guía".
export const PAL = {
  pancreas:    { key: '#38BDF8', mid: '#2A8FD0', deep: '#16456A', rgb: '56,189,248',  soft: 'rgba(56,189,248,0.55)'  },
  glucosa:     { key: '#22D3EE', mid: '#0E9AB5', deep: '#0A4A5A', rgb: '34,211,238',  soft: 'rgba(34,211,238,0.55)'  },
  insulina:    { key: '#14B8A6', mid: '#0E8C7E', deep: '#0A4A44', rgb: '20,184,166',  soft: 'rgba(20,184,166,0.55)'  },
  ritmo:       { key: '#8B5CF6', mid: '#6D44C8', deep: '#382264', rgb: '139,92,246',  soft: 'rgba(139,92,246,0.55)'  },
  metabolismo: { key: '#7C6CF6', mid: '#5B4BD0', deep: '#2C2566', rgb: '124,108,246', soft: 'rgba(124,108,246,0.55)' },
}

// makeTheme(dark) → tokens de color para tema oscuro (default) o claro.
export function makeTheme(dark = true) {
  if (dark === false) {
    return {
      dark: false,
      bg: 'radial-gradient(120% 80% at 50% -10%, #EAF2FB 0%, #DCE6F2 45%, #D0DCEC 100%)',
      ink: '#0B1324', inkSoft: 'rgba(11,19,36,0.62)', inkFaint: 'rgba(11,19,36,0.36)',
      surface: 'rgba(255,255,255,0.6)', surfaceStrong: 'rgba(255,255,255,0.82)',
      border: 'rgba(11,19,36,0.10)', borderStrong: 'rgba(11,19,36,0.16)',
      accent: '#0E9AB5',
    }
  }
  return {
    dark: true,
    bg: 'radial-gradient(125% 90% at 50% -8%, #16243F 0%, #0B1324 46%, #060B18 100%)',
    ink: '#EAF2F8', inkSoft: 'rgba(234,242,248,0.60)', inkFaint: 'rgba(234,242,248,0.34)',
    surface: 'rgba(255,255,255,0.045)', surfaceStrong: 'rgba(255,255,255,0.085)',
    border: 'rgba(255,255,255,0.085)', borderStrong: 'rgba(255,255,255,0.16)',
    accent: '#22D3EE',
  }
}
