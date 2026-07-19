"use client";

import { signIn } from "next-auth/react";
import { Logo } from "./Logo";

// GUEST-2: 게스트 체험 한도 도달 시의 가입 월. 핵심 락인: (1) 지금까지의 대화는 서버(게스트
// 세션)에 있고 가입 직후 claim으로 그대로 이어진다(GUEST-3), (2) 아직 못 물어본 마지막 질문은
// callbackUrl(/?q=)로 로그인 왕복을 살아남아 컴포저에 프리필된다(AUTH-3).
export default function GuestWall({ message, pending, providers }: {
  message: string;
  pending?: string;
  providers?: { google: boolean; kakao: boolean; dev?: boolean };
}) {
  const cb = pending ? `/?q=${encodeURIComponent(pending)}` : "/";
  const anySocial = providers?.google || providers?.kakao;
  return (
    <div className="guest-wall" role="dialog" aria-modal="true" aria-label="가입 안내">
      <div className="gw-card">
        <div className="signin-brand"><Logo size={24} /></div>
        <h2 className="gw-h">{message}</h2>
        <p className="gw-sub">지금까지 나눈 대화는 가입하면 그대로 이어져요. 3초면 돼요.</p>
        <div className="gw-actions">
          {providers?.google && (
            <button className="sso sso-google" type="button" onClick={() => void signIn("google", { callbackUrl: cb })}>
              <span className="sso-ic">G</span>Google로 계속하기
            </button>
          )}
          {providers?.kakao && (
            <button className="sso sso-kakao" type="button" onClick={() => void signIn("kakao", { callbackUrl: cb })}>
              <span className="sso-ic" aria-hidden>💬</span>카카오로 계속하기
            </button>
          )}
          {!anySocial && (
            <a className="sso" href={`/api/auth/signin?callbackUrl=${encodeURIComponent(cb)}`}>로그인하고 이어가기</a>
          )}
        </div>
        <p className="signin-legal mono">계속하면 서비스 약관과 개인정보 처리방침에 동의하게 돼요.</p>
      </div>
    </div>
  );
}
