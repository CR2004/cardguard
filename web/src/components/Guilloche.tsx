// Security-printing rosettes (hypotrochoids), the kind engraved on banknotes and cheques. Used as
// the Policy Gate's seal and, faintly, on private-data surfaces. Deterministic: no randomness.

function hypotrochoid(cx: number, cy: number, R: number, r: number, d: number, scale: number, steps = 1400): string {
  const k = (R - r) / r;
  // the curve closes after r / gcd(R, r) turns
  const gcd = (a: number, b: number): number => (b === 0 ? a : gcd(b, a % b));
  const turns = r / gcd(R, r);
  const total = Math.PI * 2 * turns;
  let path = '';
  for (let i = 0; i <= steps; i++) {
    const t = (i / steps) * total;
    const x = cx + scale * ((R - r) * Math.cos(t) + d * Math.cos(k * t));
    const y = cy + scale * ((R - r) * Math.sin(t) - d * Math.sin(k * t));
    path += `${i === 0 ? 'M' : 'L'}${x.toFixed(2)} ${y.toFixed(2)}`;
  }
  return `${path}Z`;
}

const CACHE = new Map<string, string[]>();

export function rosettes(size: number): string[] {
  const key = String(size);
  const hit = CACHE.get(key);
  if (hit) return hit;
  const c = size / 2;
  const paths = [
    hypotrochoid(c, c, 96, 36, 40, size / 240),
    hypotrochoid(c, c, 96, 36, 52, size / 262),
    hypotrochoid(c, c, 105, 35, 28, size / 250),
    hypotrochoid(c, c, 80, 25, 30, size / 330),
  ];
  CACHE.set(key, paths);
  return paths;
}

interface Props {
  size: number;
  colors?: string[];
  strokeWidth?: number;
  className?: string;
}

export function Guilloche({ size, colors = ['#cdbb93', '#8e7c55', '#cdbb93', '#6f7b9a'], strokeWidth = 0.55, className }: Props) {
  const paths = rosettes(size);
  return (
    <svg className={className} width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden focusable="false">
      {paths.map((d, i) => (
        <path key={i} d={d} fill="none" stroke={colors[i % colors.length]} strokeWidth={strokeWidth} opacity={0.85 - i * 0.12} />
      ))}
    </svg>
  );
}
