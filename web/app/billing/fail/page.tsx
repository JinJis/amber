// BILL-2: 토스 결제창 실패/이탈 착지.
export default function BillingFail() {
  return (
    <main className="signin"><div className="signin-card">
      <div className="signin-brand"><span className="mascot" aria-hidden /><b>ValueGraph</b></div>
      <h1 className="signin-h">결제를 완료하지 못했어요</h1>
      <p className="signin-sub">카드 등록이 중단됐어요. 언제든 설정 → 요금제에서 다시 시도할 수 있어요.</p>
      <a className="btn ghost" href="/">돌아가기</a>
    </div></main>
  );
}
