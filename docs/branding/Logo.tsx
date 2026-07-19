type LogoProps = {
  variant?: "mark" | "lockup" | "mono";
  size?: number;
  className?: string;
};

function Nugget({ size, mono }: { size: number; mono: boolean }) {
  const h = Math.round((size * 106) / 104);
  return (
    <svg
      width={size}
      height={h}
      viewBox="0 0 104 106"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M52 0C78 0 100 20 102 46c2 30-21 59-50 59C23 105 0 80 2 48 4 21 26 0 52 0Z"
        fill={mono ? "none" : "var(--amber-400, #EF9F27)"}
        stroke={mono ? "currentColor" : "none"}
        strokeWidth={mono ? 3 : 0}
      />
      <rect
        x="24" y="38" width="56" height="5" rx="2.5"
        fill={mono ? "currentColor" : "var(--amber-600, #BA7517)"}
        opacity={mono ? 0.4 : 1}
      />
      <rect
        x="24" y="50" width="34" height="8" rx="4"
        fill={mono ? "currentColor" : "var(--amber-900, #412402)"}
      />
      <rect
        x="24" y="64" width="56" height="5" rx="2.5"
        fill={mono ? "currentColor" : "var(--amber-600, #BA7517)"}
        opacity={mono ? 0.4 : 1}
      />
    </svg>
  );
}

export function Logo({ variant = "lockup", size = 32, className }: LogoProps) {
  const mono = variant === "mono";

  if (variant === "mark" || mono) {
    return (
      <span className={className} role="img" aria-label="Amber">
        <Nugget size={size} mono={mono} />
      </span>
    );
  }

  return (
    <span
      className={className}
      role="img"
      aria-label="Amber"
      style={{ display: "inline-flex", alignItems: "center", gap: size * 0.34 }}
    >
      <Nugget size={size} mono={false} />
      <span
        aria-hidden="true"
        style={{
          fontFamily: "var(--font-display)",
          fontWeight: 500,
          fontSize: size * 1.12,
          letterSpacing: "-0.03em",
          lineHeight: 1,
          color: "var(--text-primary)",
        }}
      >
        amber
      </span>
    </span>
  );
}

export default Logo;
