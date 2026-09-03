# ONDOL - AI 기반 IT 운영 플랫폼
## AI WOW Competition · Team ONDOL · 온돌

> 온돌처럼 IT 운영의 모든 영역을 따뜻하게 연결하는 멀티 에이전트 AI 플랫폼입니다.

**한국어 | [English](README.md)**

![ONDOL 데모](demo/demo_-gif.gif)

[YouTube에서 전체 데모 보기](https://youtu.be/qUHExHGmuqs)

---

## ONDOL이란?

ONDOL은 IT 운영 데이터를 자연어로 조회하고, 전문 에이전트가 업무별 결과를 만들어 주는 Flask 기반 Agentic AI 데모입니다.

- 데이터베이스 스키마와 실제 샘플을 확인한 뒤 자연어 질문을 SQL로 변환
- Supervisor가 질문을 분석하고 적절한 전문 에이전트를 선택
- Schema Discovery, Semantic Layer, Text-to-SQL, Evaluator가 단계별로 협력
- 결과를 별도의 Evaluator가 0~100점으로 검증하고 필요하면 재시도
- SSE로 에이전트의 처리 단계를 실시간 스트리밍
- RBAC, PII 마스킹, 입력·출력 콘텐츠 필터, 감사 로그 제공
- IT 비용, 인시던트, 보안 알림, 접근 요청을 위한 BI 화면 제공

## 역할별 주요 기능

| 역할 | 주요 기능 |
| --- | --- |
| IT Admin | 전체 데이터, 비용 대시보드, 감사 로그, 모든 에이전트 |
| Architect | ARB 문서 작성, 기술 표준 확인, 데이터 조회 |
| Security Ops | 보안 알림 분류, 접근 요청, 보안 데이터 조회 |
| Infra Engineer | 인프라 운영, VM 비용 및 용량 분석 |
| Data Analyst | KPI, 추세, SLA 분석 (PII 마스킹) |
| IT Staff | 기본 인시던트 및 ARB 상태 조회 |

## 빠른 시작

```powershell
# 1. 가상환경 생성 및 활성화
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. 의존성 설치
python -m pip install -r requirements.txt

# 3. 로컬 설정 생성 (.env는 커밋하지 않음)
copy .env.example .env
# .env에 OPENAI_API_KEY와 고유한 SECRET_KEY 입력

# 4. 데모용 SQLite DB 생성
python data/seed.py

# 5. 실행
python app.py
# http://localhost:5001
```

API 키가 없으면 일부 에이전트는 데모 응답을 사용합니다. 데모 계정과 데이터는 모두 합성 데이터이며, 운영 환경에서 기본 계정을 그대로 사용하지 마세요.

## 데모 계정

| 이메일 | 비밀번호 | 역할 |
| --- | --- | --- |
| admin@ondol.demo | admin1 | IT Admin |
| arch@ondol.demo | arch1 | Architect |
| sec@ondol.demo | sec1 | Security Ops |
| infra@ondol.demo | infra1 | Infra Engineer |
| data@ondol.demo | data1 | Data Analyst |
| staff@ondol.demo | staff1 | IT Staff |

## 아키텍처

```text
사용자 브라우저
     |
     v
Flask API (app.py)
     |
     v
보안 게이트: PII / RBAC / 콘텐츠 필터 / 감사 로그
     |
     v
Supervisor -> Schema Discovery -> Semantic Layer
     |                                  |
     +--> 전문 에이전트 -----------------+
                |
                v
        Evaluator -> 결과 또는 재시도
```

### 기술 구성

| 영역 | 기술 |
| --- | --- |
| Web | Flask, Vanilla JavaScript, Chart.js |
| AI | OpenAI GPT-4o, GPT-4o-mini |
| 오케스트레이션 | LangGraph 패턴의 표준 라이브러리 구현 |
| 데이터베이스 | 로컬 데모용 SQLite |
| 설정 | YAML, 환경변수 |
| 스트리밍 | Server-Sent Events (SSE) |

## 데이터 소스 방향

`data/seed.py`는 로컬 데모와 CI를 위한 일회성 합성 데이터 생성기입니다. 반복 가능한 데모를 위해 샘플 레코드와 확률값이 코드에 포함되어 있으며, 실제 운영 정책이나 실데이터를 의미하지 않습니다.

운영 환경에서는 다음 방향으로 교체하는 것을 권장합니다.

- 운영 데이터: Azure SQL 또는 PostgreSQL adapter
- 분석 데이터: Databricks SQL Warehouse adapter
- 인증: Managed Identity 또는 환경변수 기반 자격 증명
- 공통화: 현재 Semantic Layer 인터페이스를 유지하고 배포 설정으로 데이터 소스 선택
- 마이그레이션: schema migration과 connection pool을 사용하고 `seed.py`를 운영 환경에서 호출하지 않음

## 보안 및 공개 저장소 주의사항

- `.env`, SQLite DB, 로그, Python 캐시 파일은 Git에서 제외됩니다.
- API 키와 운영 비밀값을 소스 코드나 README에 작성하지 마세요.
- 운영 환경에서는 `DEBUG=false`와 강력한 `SECRET_KEY`를 사용하세요.
- 데모 계정은 공개 예시용이므로 운영 배포 전에 인증 방식을 교체하세요.
- 과거에 실제 API 키를 `.env`에 넣었다면 저장소 공개 전에 키를 폐기하고 새로 발급하세요.

## 주요 엔드포인트

- `GET /` - 웹 애플리케이션
- `POST /api/login` - 로그인
- `POST /api/logout` - 로그아웃
- `POST /api/ask` - 질문 실행
- `GET /api/stats` - 대시보드 지표
- `GET /api/schema` - 역할별 접근 가능 스키마
- `GET /api/cost` - 비용 대시보드 (권한 필요)
- `GET /api/audit` - 감사 로그 (관리자 권한 필요)

## 프로젝트 구조

```text
ondol/
├── app.py                 # Flask 애플리케이션과 API 라우트
├── config.py              # 환경변수 기반 설정
├── agents/                # Supervisor, 전문 에이전트, Evaluator
├── core/                  # RBAC, PII 필터, Semantic Layer
├── data/seed.py           # 로컬 데모용 합성 DB 생성기
├── pipeline/              # 그래프, 노드, YAML 설정, 프롬프트
├── templates/index.html   # 웹 SPA
├── demo/                  # GIF 및 영상 데모
└── requirements.txt       # Python 의존성
```

자세한 에이전트 동작, LangGraph 전환 예시, 비용 모델, 전체 스키마는 [영문 README](README.md)를 참고하세요.

---

*ONDOL의 모든 데이터베이스 레코드는 합성 데이터입니다.*
