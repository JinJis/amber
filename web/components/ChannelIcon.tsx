// Brand icons for the notification channels (Telegram · Slack · KakaoTalk · Gmail), rendered as
// inline SVG so they sit cleanly next to the channel label in chips / badges / previews. Sized in
// `em` so they track the surrounding text. Replaces the old single-char placeholders (✈ # K ✉).

import type { ChannelKind } from "@/lib/alerts";

function Svg({ children, viewBox = "0 0 24 24", title }: { children: React.ReactNode; viewBox?: string; title: string }) {
  return (
    <svg className="chan-svg" viewBox={viewBox} width="1em" height="1em" role="img" aria-label={title}
      style={{ display: "inline-block", verticalAlign: "-0.14em", flex: "none" }}>
      {children}
    </svg>
  );
}

export function ChannelIcon({ kind }: { kind: ChannelKind }) {
  switch (kind) {
    case "telegram":
      return (
        <Svg title="Telegram">
          <circle cx="12" cy="12" r="12" fill="#229ED9" />
          <path fill="#fff" d="M17.6 7.3l-2 9.4c-.15.66-.54.82-1.1.51l-3-2.2-1.45 1.4c-.16.16-.3.3-.6.3l.2-3.05 5.5-4.97c.24-.2-.05-.32-.37-.12l-6.8 4.28-2.93-.92c-.64-.2-.65-.64.13-.95l11.46-4.42c.53-.2 1 .12.83.92z" />
        </Svg>
      );
    case "slack":
      return (
        <Svg title="Slack" viewBox="0 0 122.8 122.8">
          <path fill="#E01E5A" d="M25.8 77.6c0 7.1-5.8 12.9-12.9 12.9S0 84.7 0 77.6s5.8-12.9 12.9-12.9h12.9v12.9zm6.5 0c0-7.1 5.8-12.9 12.9-12.9s12.9 5.8 12.9 12.9v32.3c0 7.1-5.8 12.9-12.9 12.9s-12.9-5.8-12.9-12.9V77.6z" />
          <path fill="#36C5F0" d="M45.2 25.8c-7.1 0-12.9-5.8-12.9-12.9S38.1 0 45.2 0s12.9 5.8 12.9 12.9v12.9H45.2zm0 6.5c7.1 0 12.9 5.8 12.9 12.9s-5.8 12.9-12.9 12.9H12.9C5.8 58.1 0 52.3 0 45.2s5.8-12.9 12.9-12.9h32.3z" />
          <path fill="#2EB67D" d="M97 45.2c0-7.1 5.8-12.9 12.9-12.9s12.9 5.8 12.9 12.9-5.8 12.9-12.9 12.9H97V45.2zm-6.5 0c0 7.1-5.8 12.9-12.9 12.9s-12.9-5.8-12.9-12.9V12.9C64.7 5.8 70.5 0 77.6 0s12.9 5.8 12.9 12.9v32.3z" />
          <path fill="#ECB22E" d="M77.6 97c7.1 0 12.9 5.8 12.9 12.9s-5.8 12.9-12.9 12.9-12.9-5.8-12.9-12.9V97h12.9zm0-6.5c-7.1 0-12.9-5.8-12.9-12.9s5.8-12.9 12.9-12.9h32.3c7.1 0 12.9 5.8 12.9 12.9s-5.8 12.9-12.9 12.9H77.6z" />
        </Svg>
      );
    case "kakao":
      return (
        <Svg title="카카오톡">
          <rect width="24" height="24" rx="6" fill="#FEE500" />
          <path fill="#371D1E" d="M12 6.1c-3.43 0-6.2 2.2-6.2 4.9 0 1.74 1.13 3.27 2.84 4.16-.13.46-.68 2.35-.7 2.5-.03.18.07.18.14.13.06-.04 2.2-1.5 2.55-1.74.45.07.9.1 1.37.1 3.43 0 6.2-2.2 6.2-4.91S15.43 6.1 12 6.1z" />
        </Svg>
      );
    case "email":
    default:
      return (
        <Svg title="Gmail" viewBox="0 0 52 40">
          <path fill="#4285F4" d="M3.6 40h8.7V19.1L0 9.8v26.6C0 38.4 1.62 40 3.6 40z" />
          <path fill="#34A853" d="M39.7 40h8.7c2 0 3.6-1.62 3.6-3.6V9.8L39.7 19.1V40z" />
          <path fill="#FBBC04" d="M39.7 3.6v15.5L52 9.8V5.5c0-4.04-4.6-6.34-7.84-3.91L39.7 3.6z" />
          <path fill="#EA4335" d="M12.3 19.1V3.6L26 13.9l13.7-10.3v15.5L26 29.4z" />
          <path fill="#C5221F" d="M0 5.5v4.3l12.3 9.3V3.6L7.84 1.59C4.6-.84 0 1.46 0 5.5z" />
        </Svg>
      );
  }
}
