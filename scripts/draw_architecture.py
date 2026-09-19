"""krx-stock-scoring 아키텍처 SVG 생성기 (PLAN §2)."""
from html import escape

W, H = 1740, 1060
FONT = "'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif"
MONO = "Menlo,'SF Mono',Consolas,monospace"
INK, SUB, MUTED = "#0F172A", "#475569", "#64748B"
out: list[str] = []


def t(x, y, s, size=13, fill=INK, weight=400, anchor="start", mono=False, italic=False):
    fam = MONO if mono else FONT
    st = ' font-style="italic"' if italic else ""
    out.append(f'<text x="{x}" y="{y}" font-family="{fam}" font-size="{size}" fill="{fill}" '
               f'font-weight="{weight}" text-anchor="{anchor}"{st}>{escape(s)}</text>')


def tv(x, y, s, size=11.5, fill=INK, weight=600):
    out.append(f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" fill="{fill}" '
               f'font-weight="{weight}" text-anchor="middle" transform="rotate(-90 {x} {y})">{escape(s)}</text>')


def r(x, y, w, h, fill, stroke, rx=10, sw=1.5, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
               f'stroke="{stroke}" stroke-width="{sw}"{d}/>')


def badge(x, y, label, color):
    w = 8 + 8 * len(label)
    r(x, y, w, 18, color, color, rx=9, sw=0)
    t(x + w / 2, y + 13, label, size=11, fill="#FFFFFF", weight=700, anchor="middle")


def arrow(pts, color=SUB, dash=None, label=None, lpos=None, lanchor="middle", marker="a"):
    d = "M" + " L".join(f"{x},{y}" for x, y in pts)
    ds = f' stroke-dasharray="{dash}"' if dash else ""
    out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.8"{ds} '
               f'marker-end="url(#{marker})"/>')
    if label and lpos:
        lx, ly = lpos
        wlab = 7.2 * len(label) + 10
        x0 = lx - wlab / 2 if lanchor == "middle" else lx - 5
        r(x0, ly - 13, wlab, 18, "#FFFFFF", "#FFFFFF", rx=4, sw=0)
        t(lx, ly, label, size=11.5, fill=color, weight=600, anchor=lanchor)


def zone(x, y, w, h, title, subtitle, fill, stroke, dash=None):
    r(x, y, w, h, fill, stroke, rx=14, sw=1.8, dash=dash)
    t(x + 16, y + 28, title, size=16, weight=700, fill=stroke)
    if subtitle:
        t(x + 16, y + 48, subtitle, size=12, fill=SUB)


def row(x, y, w, name, desc, stroke, ms=None, mscolor=None, h=42, name_mono=True):
    r(x, y, w, h, "#FFFFFF", stroke, rx=7, sw=1)
    t(x + 10, y + 18, name, size=12.5, weight=600, mono=name_mono)
    t(x + 10, y + 34, desc, size=11.5, fill=SUB)
    if ms:
        badge(x + w - 12 - (8 + 8 * len(ms)), y + 12, ms, mscolor)


def step(x, y, w, h, title, desc, stroke, fill="#FFFFFF", ms=None, mscolor=None):
    r(x, y, w, h, fill, stroke, rx=9, sw=1.4)
    t(x + 12, y + 22, title, size=13.5, weight=700)
    for i, line in enumerate(desc):
        t(x + 12, y + 41 + i * 16, line, size=11.5, fill=SUB)
    if ms:
        badge(x + w - 12 - (8 + 8 * len(ms)), y + 9, ms, mscolor)


# 색 — 영역별 하나
UP, UPF = "#475569", "#F1F5F9"      # 상위(공유 DB)
EX, EXF = "#64748B", "#F8FAFC"      # 외부 API
BA, BAF = "#0F766E", "#F0FDFA"      # 배치
DO = "#14B8A6"                      # domain
KS, KSF = "#6D28D9", "#F5F3FF"      # kss 전용 DB
WE, WEF = "#C2410C", "#FFF7ED"      # 웹
WARN = "#B91C1C"
MSC = "#334155"                     # 마일스톤 배지

out.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">')
out.append("""<defs>
<marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#475569"/></marker>
<marker id="k" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#6D28D9"/></marker>
<marker id="w" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#C2410C"/></marker>
<marker id="b" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#0F766E"/></marker>
</defs>""")
out.append(f'<rect width="{W}" height="{H}" fill="#FFFFFF"/>')

# ── 제목
t(40, 46, "krx-stock-scoring 아키텍처", size=24, weight=800)
t(40, 70, "PLAN v0.2 · SPEC v2.3 · 2026-09-19  —  상위는 읽기만, 계산은 순수 함수, 점수는 전용 DB에 실행별로 불변 저장 후 한 번에 게시",
  size=13, fill=SUB)

# ── 트리거 띠
TY = 96
step(40, TY, 330, 62, "krx-stock-charts · daily.yml", ["평일 수집 — 예약 18:00, 실제 시작 22:00~익일 01:30"], UP, UPF)
step(430, TY, 300, 62, "repository_dispatch  (주 트리거)", ["수집 완료 이벤트 · payload: run_id · sha · T"], BA, "#FFFFFF", ms="M4", mscolor=MSC)
step(790, TY, 330, 62, "예비 cron  (깃허브 Actions)", ["평일 23:37 KST 채점 · 매일 07:17 KST 복구 잡"], BA, "#FFFFFF", ms="M4", mscolor=MSC)
arrow([(370, TY + 31), (428, TY + 31)], color=BA, dash="6 4", marker="b")

# ── A열: 상위 공유 DB
AX, AW = 40, 330
zone(AX, 200, AW, 470, "공유 Supabase — 읽기만", "세션 default_transaction_read_only = on", UPF, UP)
rows_up = [
    ("ksc_bars  (D만)", "최근 400거래일 · W/M 저장 행은 쓰지 않음"),
    ("ksc_tickers · ksc_meta", "유니버스 · 업종 · 갱신/드리프트 메타"),
    ("ksc_investor_flows", "NULL은 합계 항등식으로 0 확정 (derived_zero)"),
    ("ksc_shorting", "공매도 비중 · 20거래일 평균"),
    ("ksc_index_bars", "거래일 달력 (T 결정 · 완성 봉 판정)"),
    ("ksa_signals · ksa_runs", "alerts 신호 — 점수와 기준일 대조"),
]
for i, (n, d) in enumerate(rows_up):
    row(AX + 14, 262 + i * 52, AW - 28, n, d, "#CBD5E1")
t(AX + 16, 590, "⚠ DB 513 MB — 무료 한도 500 MB 초과", size=12.5, fill=WARN, weight=700)
t(AX + 16, 610, "   (2026-09-19 M0 실측, ksc_bars 350 MB)", size=11.5, fill=WARN)
t(AX + 16, 640, "상위 결함은 고치지 않고 품질 게이트로 막는다", size=11.5, fill=SUB, italic=True)

# ── A열: 외부 API
zone(AX, 700, AW, 190, "외부 API", "기본 테스트는 응답 픽스처로 — 네트워크 없음", EXF, EX)
row(AX + 14, 760, AW - 28, "OpenDART", "공시 list.json 하루 4~5회 · fnlttMultiAcnt 15사/회", "#CBD5E1", ms="M2·M3", mscolor=MSC)
row(AX + 14, 812, AW - 28, "pykrx (KRX 로그인)", "PER · PBR 시장별 1회 → 업종 중앙값", "#CBD5E1", ms="M2", mscolor=MSC)

# ── B열: 배치
BX, BW = 430, 690
zone(BX, 200, BW, 690, "채점 배치 — 깃허브 Actions · Python 3.11 · psycopg", "LangGraph 얇은 그래프 층 (graph.py만 import) · run.py score --date T", BAF, BA)
SW, G = 210, 20
c1, c2, c3 = BX + 20, BX + 20 + SW + G, BX + 20 + 2 * (SW + G)
step(c1, 264, SW, 70, "① T 결정 · run 생성", ["kss_runs에 먼저 기록", "자정 넘어도 T 유지"], BA)
step(c2, 264, SW, 70, "② 출처별 품질 게이트", ["행 수 · NULL · 날짜 상한", "미달 → waiting_upstream"], BA)
step(c3, 264, SW, 70, "③ 입력 스냅샷", ["sources/ 일괄 적재", "d ≤ T 로 고정"], BA)
arrow([(c1 + SW, 299), (c2 - 2, 299)], color=BA, marker="b")
arrow([(c2 + SW, 299), (c3 - 2, 299)], color=BA, marker="b")

# domain
DX, DY, DW, DH = BX + 20, 370, BW - 40, 300
r(DX, DY, DW, DH, "#FFFFFF", DO, rx=12, sw=1.6)
t(DX + 14, DY + 26, "domain/ — 순수 함수 (I/O 없음, 손계산 골든 테스트)", size=14.5, weight=700, fill=BA)
arrow([(c3 + SW / 2, 334), (c3 + SW / 2, DY - 2)], color=BA, marker="b")
# calendar / resample
step(DX + 14, DY + 40, DW - 28, 44, "calendar · resample   D → W/M 직접 집계 (T까지 완성된 봉만, 기간당 1행)", [], DO, ms="M0", mscolor=MSC)
AXW, AG = 146, 12
ay = DY + 98
axes = [
    ("기술 35", ["정배열 11 · 추세 6", "거래량 6 · 매물대 7", "RSI·MACD 5"], "M1"),
    ("기본 35", ["PER 7 · PBR 6 (업종비)", "영업이익률 7 · 성장 7", "ROE 5 · 부채비율 3"], "M2"),
    ("공시 7", ["30일 창 · 기본 4", "호재 +1.5 · 악재 −2", "fatal → 0 덮어쓰기"], "M3"),
    ("수급 13", ["외국인 6 · 기관 5", "공매도 비중 2", "(시장별 경계)"], "M2"),
]
for i, (ttl, ds, ms) in enumerate(axes):
    step(DX + 14 + i * (AXW + AG), ay, AXW, 92, ttl, ds, DO, ms=ms, mscolor=MSC)
t(DX + 14, ay + 190, "별도 보조: 뉴스 8 (공통 점수·순위에 미사용)   ·   비활성: 신용 2", size=11.5, fill=MUTED, italic=True)
aggy = ay + 110
step(DX + 14, aggy, DW - 28, 60, "aggregate — 상태 · 관측률 · 자격",
     ["scored / provisional / insufficient_data / excluded · 공통 90점 → 100 환산 · rank_eligible · 등급 · passes_screen"], DO, ms="M1", mscolor=MSC)
for i in range(4):
    cx = DX + 14 + i * (AXW + AG) + AXW / 2
    arrow([(cx, ay + 92), (cx, aggy - 2)], color=DO, marker="b")

# 하단 단계
ly = 700
step(c1, ly, SW, 70, "rules/v0.toml", ["배점 · 임계값 단일 기준", "정규 해시 → run에 기록"], BA, "#ECFEFF", ms="M0", mscolor=MSC)
step(c2, ly, SW, 70, "④ 검증", ["종목 합계 = 유니버스", "점수 범위 · 규칙 해시"], BA)
step(c3, ly, SW, 70, "⑤ 저장 → ⑥ 게시", ["500행 청크 저장", "포인터 교체는 한 트랜잭션"], BA)
arrow([(c1 + SW / 2, ly), (c1 + SW / 2, DY + DH + 2)], color=BA, dash="5 4", marker="b",
      label="규칙 주입", lpos=(c1 + SW / 2, ly - 10))
arrow([(c2 + SW / 2, DY + DH), (c2 + SW / 2, ly - 2)], color=BA, marker="b")
arrow([(c2 + SW, ly + 35), (c3 - 2, ly + 35)], color=BA, marker="b")
step(c3, 800, SW, 70, "⑦ 아카이브 · 보존 삭제", ["manifest · hash · 복원 검증", "그 뒤에만 DB 행 삭제"], BA, ms="M4", mscolor=MSC)
t(c1, 815, "실패해도 이전 게시본 유지", size=12, fill=BA, weight=700)
t(c1, 834, "중복 이벤트는 T·프로필 잠금으로 차단", size=11.5, fill=SUB)
t(c1, 852, "재평가는 새 run_id (덮어쓰기 없음)", size=11.5, fill=SUB)

# 트리거 → 배치
arrow([(580, TY + 62), (580, 198)], color=BA, dash="6 4", marker="b")
arrow([(955, TY + 62), (955, 180), (582, 180)], color=BA, dash="6 4", marker="b")

# 입력 → 배치
arrow([(AX + AW, 440), (BX - 2, 440)], color=UP, marker="a")
t(400, 430, "SELECT", size=11, fill=UP, weight=700, anchor="middle")
arrow([(AX + AW, 800), (BX - 2, 800)], color=EX, marker="a")
t(400, 790, "HTTPS", size=11, fill=EX, weight=700, anchor="middle")

# ── C열: kss 전용 DB
KX, KW = 1160, 300
zone(KX, 200, KW, 560, "kss 전용 Supabase (무료)", "RLS 켬 · anon 정책 없음 · 경고 400 MB", KSF, KS)
rows_k = [
    ("kss_runs", "실행 상태 · 규칙/사전/코드 해시", "M1"),
    ("kss_source_checks", "출처·시장별 누락률 · 사유", "M1"),
    ("kss_scores", "요약 · 약 120거래일 보관", "M1"),
    ("kss_score_parts", "항목 근거 · 약 10거래일 보관", "M1"),
    ("kss_publications", "게시 포인터 (+ history)", "M1"),
    ("kss_universe_snap…", "당일 분류 · 제외 사유 스냅샷", "M1"),
    ("kss_sector_stats 외", "업종 중앙값 · 접수번호별 재무 버전", "M2"),
    ("kss_disclosures 외", "공시 정정 이력 · 위험 사건 해제 근거", "M3"),
    ("kss_lexicon", "사전 원본 + 불변 버전 스냅샷", "M3"),
    ("kss_signal_cross", "alerts 신호 ↔ 점수 대조", "M1"),
]
for i, (n, d, ms) in enumerate(rows_k):
    row(KX + 14, 258 + i * 49, KW - 28, n, d, "#DDD6FE", ms=ms, mscolor=MSC, h=43)

# 아카이브
zone(KX, 790, KW, 100, "Parquet 아카이브", "입력 스냅샷 · 오래된 근거 · 불변 버전", "#FAF5FF", KS, dash="6 4")
t(KX + 16, 870, "깃허브 Release 자산 (1안) · 장기 분석은 DuckDB", size=11.5, fill=SUB)
badge(KX + KW - 48, 806, "M4", MSC)

# 배치 → kss
arrow([(c3 + SW, ly + 35), (1140, ly + 35), (1140, 480), (KX - 2, 480)], color=KS, marker="k")
r(1131, 540, 18, 120, "#FFFFFF", "#FFFFFF", rx=3, sw=0)
tv(1144, 600, "쓰기 · kss_batch", fill=KS)
arrow([(c3 + SW, 835), (KX - 2, 835)], color=KS, marker="k")

# ── D열: 웹
WX, WW = 1550, 170
zone(WX, 200, WW, 420, "개인 웹", "Next.js · Vercel 인증 보호", WEF, WE)
badge(WX + WW - 48, 214, "M5", MSC)
routes = [("/", "완전 관측 랭킹"), ("/stock/[ticker]", "항목별 근거·결측"), ("/lexicon", "사전 편집"),
          ("/cross", "신호 대조"), ("/status", "게시·누락·용량")]
for i, (n, d) in enumerate(routes):
    row(WX + 14, 262 + i * 52, WW - 28, n, d, "#FED7AA")
t(WX + 16, 540, "투자 권고가 아닙니다 ·", size=11.5, fill=SUB)
t(WX + 16, 557, "참고용 개인 분석", size=11.5, fill=SUB)
t(WX + 16, 584, "브라우저에 DB 비번 없음", size=11.5, fill=WE, weight=600)
t(WX + 16, 601, "(서버 사이드 조회만)", size=11.5, fill=SUB)
arrow([(KX + KW, 330), (WX - 2, 330)], color=WE, marker="w")
t((KX + KW + WX) / 2, 305, "kss_reader", size=11, fill=WE, weight=700, anchor="middle")
t((KX + KW + WX) / 2, 320, "SELECT", size=11, fill=WE, anchor="middle")
arrow([(WX, 420), (KX + KW + 2, 420)], color=KS, dash="5 4", marker="k")
t((KX + KW + WX) / 2, 441, "kss_editor", size=11, fill=KS, weight=700, anchor="middle")
t((KX + KW + WX) / 2, 456, "사전 편집", size=11, fill=KS, anchor="middle")

# 사용자
r(WX, 660, WW, 70, "#FFFFFF", WE, rx=35, sw=1.5)
t(WX + WW / 2, 690, "본인 (로그인)", size=14, weight=700, anchor="middle", fill=WE)
t(WX + WW / 2, 710, "개인용 — 외부 알림 없음", size=11.5, anchor="middle", fill=SUB)
arrow([(WX + WW / 2, 660), (WX + WW / 2, 622)], color=WE, marker="w")

# ── 범례
LY = 930
r(40, LY, 1660, 96, "#F8FAFC", "#E2E8F0", rx=10, sw=1)
t(60, LY + 28, "범례", size=13, weight=700)
arrow([(110, LY + 24), (160, LY + 24)], color=SUB)
t(170, LY + 28, "데이터 흐름", size=12, fill=SUB)
arrow([(270, LY + 24), (320, LY + 24)], color=BA, dash="6 4", marker="b")
t(330, LY + 28, "트리거 · 주입", size=12, fill=SUB)
badge(440, LY + 14, "M1", MSC)
t(476, LY + 28, "도입 마일스톤 (단계형 — M0~M3 계산 정확성, M4 운영, M5 웹)", size=12, fill=SUB)
t(60, LY + 58, "점수 모델: 공통 90점(기술 35 · 기본 35 · 공시 7 · 수급 13) → 100 환산.  완전 관측 종목만 기본 랭킹 · 부분 관측은 provisional(추정치 별도) · 확정 등급은 M6 검증 후.",
  size=12, fill=INK)
t(60, LY + 80, "재현: run_id마다 규칙·사전·코드 해시와 입력 스냅샷을 연결 — 같은 입력이면 반올림·동점 정렬까지 같은 결과.",
  size=12, fill=SUB)

out.append("</svg>")
open(__import__("sys").argv[1], "w", encoding="utf-8").write("\n".join(out))
print("ok")
