export default function QwenIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 36 36"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
    >
      <defs>
        <linearGradient id="qg" x1="0" y1="0" x2="36" y2="36" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#FF7B2C" />
          <stop offset="100%" stopColor="#E0401A" />
        </linearGradient>
      </defs>
      <rect width="36" height="36" rx="9" fill="url(#qg)" />
      {/* Circle */}
      <circle cx="17" cy="17" r="8" stroke="white" strokeWidth="2.8" fill="none" />
      {/* Tail of Q */}
      <line x1="22.5" y1="22.5" x2="27" y2="27" stroke="white" strokeWidth="2.8" strokeLinecap="round" />
      {/* Small inner dot */}
      <circle cx="17" cy="17" r="2" fill="white" opacity="0.35" />
    </svg>
  );
}
